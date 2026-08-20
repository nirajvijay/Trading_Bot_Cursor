"""FakeBroker + KiteBroker gating and order_margins."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from trading_engine_broker import (
    LIVE_ORDERS_DISABLED_REASON,
    FakeBroker,
    KiteBroker,
)


class FakeBrokerTests(unittest.TestCase):
    def test_does_not_resubmit_market_for_same_tag(self) -> None:
        broker = FakeBroker(last_prices={"AAA": 110})
        first = broker.place_market_mis(
            tradingsymbol="AAA", transaction_type="BUY", quantity=10, tag="teabc"
        )
        second = broker.place_market_mis(
            tradingsymbol="AAA", transaction_type="BUY", quantity=10, tag="teabc"
        )
        self.assertEqual(first.order_id, second.order_id)
        self.assertEqual(broker.market_place_count, 1)

    def test_market_then_slm(self) -> None:
        broker = FakeBroker(last_prices={"AAA": 110}, auto_confirm_sl=True)
        entry = broker.place_market_mis(
            tradingsymbol="AAA", transaction_type="BUY", quantity=10, tag="te1"
        )
        self.assertEqual(entry.status, "COMPLETE")
        sl = broker.place_slm(
            tradingsymbol="AAA",
            transaction_type="SELL",
            quantity=10,
            trigger_price=99,
            tag="te1",
        )
        self.assertEqual(sl.status, "TRIGGER PENDING")
        self.assertEqual(broker.slm_place_count, 1)
        again = broker.place_slm(
            tradingsymbol="AAA",
            transaction_type="SELL",
            quantity=10,
            trigger_price=99,
            tag="te1",
        )
        self.assertEqual(again.order_id, sl.order_id)

    def test_modify_trail(self) -> None:
        broker = FakeBroker(last_prices={"AAA": 110})
        sl = broker.place_slm(
            tradingsymbol="AAA",
            transaction_type="SELL",
            quantity=10,
            trigger_price=99,
            tag="te1",
        )
        updated = broker.modify_slm(sl.order_id, 105)
        self.assertEqual(updated.trigger_price, 105)
        self.assertEqual(updated.price, 105)
        self.assertEqual(broker.modify_count, 1)

    def test_sl_limit_price_matches_trigger(self) -> None:
        broker = FakeBroker(last_prices={"AAA": 110})
        sl = broker.place_slm(
            tradingsymbol="AAA",
            transaction_type="SELL",
            quantity=10,
            trigger_price=99,
            tag="te1",
            tick_size=1,
        )
        self.assertEqual(sl.order_type, "SL")
        self.assertEqual(sl.trigger_price, 99)
        self.assertEqual(sl.price, 99)


class KiteBrokerTests(unittest.TestCase):
    def test_live_flag_false_blocks_place_order(self) -> None:
        kite = MagicMock()
        broker = KiteBroker(kite, live_orders_enabled=False)
        with self.assertRaises(RuntimeError) as ctx:
            broker.place_market_mis(
                tradingsymbol="AAA", transaction_type="BUY", quantity=1, tag="t"
            )
        self.assertEqual(str(ctx.exception), LIVE_ORDERS_DISABLED_REASON)
        kite.place_order.assert_not_called()

    def test_order_margins_reject(self) -> None:
        kite = MagicMock()
        kite.order_margins.return_value = [{"status": "error", "error": "MIS not allowed"}]
        broker = KiteBroker(kite, live_orders_enabled=True)
        quote = broker.order_margins(
            tradingsymbol="AAA", transaction_type="BUY", quantity=10
        )
        self.assertFalse(quote.ok)
        self.assertIn("MIS", quote.reason)

    def test_reconcile_skips_place_when_tag_exists(self) -> None:
        kite = MagicMock()
        kite.orders.return_value = [
            {
                "order_id": "oid1",
                "tag": "teabc",
                "tradingsymbol": "AAA",
                "transaction_type": "BUY",
                "order_type": "MARKET",
                "quantity": 10,
                "status": "COMPLETE",
                "average_price": 110,
            }
        ]
        broker = KiteBroker(kite, live_orders_enabled=True)
        order = broker.place_market_mis(
            tradingsymbol="AAA", transaction_type="BUY", quantity=10, tag="teabc"
        )
        self.assertEqual(order.order_id, "oid1")
        kite.place_order.assert_not_called()

    def test_place_sl_equal_trigger_and_price(self) -> None:
        kite = MagicMock()
        kite.place_order.return_value = {"order_id": "sl1"}
        kite.orders.return_value = []
        broker = KiteBroker(kite, live_orders_enabled=True)
        broker.place_slm(
            tradingsymbol="AAA",
            transaction_type="SELL",
            quantity=10,
            trigger_price=99,
            tag="te1",
            tick_size=1,
        )
        kwargs = kite.place_order.call_args.kwargs
        self.assertEqual(kwargs["order_type"], "SL")
        self.assertEqual(kwargs["trigger_price"], 99)
        self.assertEqual(kwargs["price"], 99)


if __name__ == "__main__":
    unittest.main()
