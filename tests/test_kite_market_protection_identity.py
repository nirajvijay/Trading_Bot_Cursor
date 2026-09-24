"""Kite books protected MARKET orders as LIMIT; the retry guard must still see them.

Verified live 2026-09-25: every engine entry and exit sent as MARKET with
market_protection=-1 appears in Kite's order book and history as LIMIT with a
price. place_market_mis's "already placed under this tag?" guard filtered on
order_type == "MARKET", so it never matched, and a flatten retried after an
accepted-but-unconfirmed exit would sell a second time.
"""

from __future__ import annotations

import unittest

from kiteconnect.exceptions import NetworkException

from trading_engine_broker import KiteBroker


class ProtectedMarketKite:
    """Books every MARKET as LIMIT, like Kite with market protection."""

    def __init__(self) -> None:
        self.book = []
        self.placed = 0
        self.history_failures = 0

    def set_session_expiry_hook(self, _method) -> None:
        pass

    def orders(self):
        return [dict(o) for o in self.book]

    def order_history(self, order_id):
        if self.history_failures:
            self.history_failures -= 1
            raise NetworkException("Read timed out", code=503)
        return [dict(o) for o in self.book if o["order_id"] == order_id]

    def place_order(self, **kw):
        self.placed += 1
        order_id = f"ord{self.placed}"
        order_type = "LIMIT" if kw["order_type"] == "MARKET" else kw["order_type"]
        self.book.append(
            {
                "order_id": order_id,
                "tag": kw["tag"],
                "tradingsymbol": kw["tradingsymbol"],
                "transaction_type": kw["transaction_type"],
                "order_type": order_type,
                "quantity": kw["quantity"],
                "status": "COMPLETE" if order_type == "LIMIT" else "TRIGGER PENDING",
                "filled_quantity": kw["quantity"] if order_type == "LIMIT" else 0,
                "average_price": 100.0 if order_type == "LIMIT" else 0,
                "price": 100.5,
                "trigger_price": kw.get("trigger_price") or 0,
                "product": "MIS",
            }
        )
        return order_id


def exit_once(broker):
    return broker.flatten_mis(
        tradingsymbol="AAA", transaction_type="SELL", quantity=10, tag="te1-x"
    )


class RetryGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.kite = ProtectedMarketKite()
        self.broker = KiteBroker(self.kite, live_orders_enabled=True)

    def test_a_retried_exit_after_a_lost_confirmation_does_not_sell_twice(self) -> None:
        self.kite.history_failures = 1  # accepted, then the confirming read fails
        with self.assertRaises(NetworkException):
            exit_once(self.broker)
        self.assertEqual(self.kite.placed, 1)

        order = exit_once(self.broker)  # the engine's next-tick retry
        self.assertEqual(self.kite.placed, 1)
        self.assertEqual(order.order_id, "ord1")
        self.assertEqual(order.order_type, "LIMIT")

    def test_a_rejected_earlier_attempt_does_not_block_a_real_exit(self) -> None:
        exit_once(self.broker)
        self.kite.book[0]["status"] = "REJECTED"
        exit_once(self.broker)
        self.assertEqual(self.kite.placed, 2)

    def test_a_stop_under_the_same_tag_is_not_taken_for_the_entry(self) -> None:
        self.broker.place_slm(
            tradingsymbol="AAA",
            transaction_type="SELL",
            quantity=10,
            trigger_price=95.0,
            tag="te1",
        )
        order = self.broker.place_market_mis(
            tradingsymbol="AAA", transaction_type="BUY", quantity=10, tag="te1"
        )
        self.assertEqual(self.kite.placed, 2)
        self.assertEqual(order.order_type, "LIMIT")


if __name__ == "__main__":
    unittest.main()
