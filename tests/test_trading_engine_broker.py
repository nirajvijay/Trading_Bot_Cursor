"""FakeBroker + KiteBroker gating and order_margins."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from trading_engine_broker import (
    LIVE_ORDERS_DISABLED_REASON,
    FakeBroker,
    KiteBroker,
    _kite_order_to_broker,
    normalize_broker_timestamp,
    parse_timestamp_text,
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


    def test_modify_partial_stop_preserves_fills_and_sets_remaining(self) -> None:
        broker = FakeBroker(last_prices={"AAA": 110}, auto_confirm_sl=True)
        sl = broker.place_slm(
            tradingsymbol="AAA",
            transaction_type="SELL",
            quantity=10,
            trigger_price=99,
            tag="te1",
        )
        broker.fill_sl_partial(sl.order_id, 3, 99.0, complete=False)
        updated = broker.modify_slm(sl.order_id, 98.0, quantity=9, transaction_type="SELL")
        self.assertEqual(str(updated.status).upper(), "OPEN")
        self.assertEqual(int(updated.filled_quantity or 0), 3)
        self.assertEqual(int(updated.pending_quantity or 0), 9)
        self.assertEqual(int(updated.quantity or 0), 12)

    def test_fill_sl_partial_uses_open_not_trigger_pending(self) -> None:
        broker = FakeBroker(last_prices={"AAA": 110}, auto_confirm_sl=True)
        sl = broker.place_slm(
            tradingsymbol="AAA",
            transaction_type="SELL",
            quantity=10,
            trigger_price=99,
            tag="te1",
        )
        partial = broker.fill_sl_partial(sl.order_id, 2, 99.0, complete=False)
        self.assertEqual(str(partial.status).upper(), "OPEN")
        self.assertEqual(int(partial.pending_quantity or 0), 8)


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

    def test_flatten_mis_uses_place_market_mis_path(self) -> None:
        kite = MagicMock()
        kite.place_order.return_value = {"order_id": "ex1"}
        placed = {
            "order_id": "ex1",
            "tag": "teabcE",
            "tradingsymbol": "AAA",
            "transaction_type": "SELL",
            "order_type": "MARKET",
            "quantity": 10,
            "status": "COMPLETE",
            "average_price": 111,
            "filled_quantity": 10,
            "pending_quantity": 0,
        }
        kite.orders.side_effect = [[], [placed], [placed]]
        broker = KiteBroker(kite, live_orders_enabled=True)
        order = broker.flatten_mis(
            tradingsymbol="AAA",
            transaction_type="SELL",
            quantity=10,
            tag="teabcE",
        )
        self.assertEqual(order.order_id, "ex1")
        kwargs = kite.place_order.call_args.kwargs
        self.assertEqual(kwargs["order_type"], "MARKET")
        self.assertEqual(kwargs["transaction_type"], "SELL")
        self.assertEqual(kwargs["quantity"], 10)
        self.assertEqual(kwargs["tag"], "teabcE")

    def test_place_sl_equal_trigger_and_price(self) -> None:
        kite = MagicMock()
        kite.place_order.return_value = {"order_id": "sl1"}
        order_row = {
            "order_id": "sl1",
            "tag": "te1",
            "tradingsymbol": "AAA",
            "transaction_type": "SELL",
            "order_type": "SL",
            "quantity": 10,
            "status": "TRIGGER PENDING",
            "trigger_price": 99.0,
            "price": 99.0,
            "filled_quantity": 0,
            "pending_quantity": 10,
        }
        # Empty until after place — otherwise idempotent tag reuse skips place_order.
        kite.orders.side_effect = [[], [order_row], [order_row]]
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
        self.assertEqual(kite.place_order.call_count, 1)

    def test_place_sl_no_worse_price_retry_on_exception(self) -> None:
        kite = MagicMock()
        kite.place_order.side_effect = RuntimeError("transport_glitch")
        broker = KiteBroker(kite, live_orders_enabled=True)
        with self.assertRaises(RuntimeError):
            broker.place_slm(
                tradingsymbol="AAA",
                transaction_type="SELL",
                quantity=10,
                trigger_price=99,
                tag="te1",
                tick_size=1,
            )
        self.assertEqual(kite.place_order.call_count, 1)

    def test_place_sl_accepted_visibility_unknown_preserves_order_id(self) -> None:
        from trading_engine_broker import SlPlaceAcceptedVisibilityUnknown

        kite = MagicMock()
        kite.place_order.return_value = {"order_id": "sl-accepted"}
        kite.orders.return_value = []  # accept, then poll miss
        broker = KiteBroker(kite, live_orders_enabled=True)
        with self.assertRaises(SlPlaceAcceptedVisibilityUnknown) as ctx:
            broker.place_slm(
                tradingsymbol="AAA",
                transaction_type="SELL",
                quantity=10,
                trigger_price=99,
                tag="te1",
                tick_size=1,
            )
        self.assertEqual(ctx.exception.order_id, "sl-accepted")
        self.assertEqual(kite.place_order.call_count, 1)

    def test_position_quote_prefers_net_mis_pnl(self) -> None:
        kite = MagicMock()
        kite.positions.return_value = {
            "net": [
                {
                    "tradingsymbol": "AAA",
                    "product": "MIS",
                    "quantity": 10,
                    "average_price": 100,
                    "last_price": 105,
                    "pnl": 50,
                    "unrealised": 50,
                    "realised": 0,
                }
            ],
            "day": [],
        }
        broker = KiteBroker(kite, live_orders_enabled=True)
        quote = broker.position_quote("AAA")
        assert quote is not None
        self.assertEqual(quote.quantity, 10)
        self.assertEqual(quote.unrealised, 50)
        self.assertEqual(broker.net_position_qty("AAA"), 10)

    def test_modify_slm_translates_remaining_cover_to_total_qty(self) -> None:
        kite = MagicMock()
        order_state = {
            "order_id": "sl1",
            "tag": "te1",
            "tradingsymbol": "AAA",
            "transaction_type": "SELL",
            "order_type": "SL",
            "quantity": 10,
            "status": "OPEN",
            "average_price": 99.0,
            "filled_quantity": 4,
            "pending_quantity": 6,
            "trigger_price": 99.0,
            "price": 99.0,
        }

        def orders() -> list:
            return [dict(order_state)]

        def modify_order(**kwargs: object) -> dict:
            total = int(kwargs["quantity"])  # type: ignore[arg-type]
            filled = int(order_state["filled_quantity"])
            order_state["quantity"] = total
            order_state["pending_quantity"] = max(0, total - filled)
            return {"order_id": "sl1"}

        kite.orders.side_effect = orders
        kite.modify_order.side_effect = modify_order
        broker = KiteBroker(kite, live_orders_enabled=True)
        # Port contract: quantity=8 means desired remaining cover.
        # Triggered OPEN remainder → quantity-only request (no order_type/price/trigger).
        updated = broker.modify_slm("sl1", 98.0, quantity=8, transaction_type="SELL")
        sent = kite.modify_order.call_args.kwargs
        self.assertEqual(int(sent["quantity"]), 12)
        self.assertNotIn("trigger_price", sent)
        self.assertNotIn("order_type", sent)
        self.assertNotIn("price", sent)
        self.assertEqual(set(sent.keys()), {"variety", "order_id", "quantity"})
        self.assertEqual(int(updated.quantity or 0), 12)
        self.assertEqual(int(updated.filled_quantity or 0), 4)
        self.assertEqual(int(updated.pending_quantity or 0), 8)

    def test_modify_slm_requires_confirmed_order_state(self) -> None:
        kite = MagicMock()
        kite.orders.return_value = []
        broker = KiteBroker(kite, live_orders_enabled=True)
        with self.assertRaises(RuntimeError) as ctx:
            broker.modify_slm("missing", 99.0, quantity=5, transaction_type="SELL")
        self.assertIn("confirmed_order_state", str(ctx.exception))
        kite.modify_order.assert_not_called()

    def test_modify_waiting_stop_sends_sl_trigger(self) -> None:
        kite = MagicMock()
        order_state = {
            "order_id": "slw",
            "tag": "te1",
            "tradingsymbol": "AAA",
            "transaction_type": "SELL",
            "order_type": "SL",
            "quantity": 10,
            "status": "TRIGGER PENDING",
            "average_price": None,
            "filled_quantity": 0,
            "pending_quantity": 10,
            "trigger_price": 100.0,
            "price": 100.0,
        }

        def orders() -> list:
            return [dict(order_state)]

        def modify_order(**kwargs: object) -> dict:
            order_state["trigger_price"] = kwargs.get("trigger_price")
            order_state["price"] = kwargs.get("price")
            if "quantity" in kwargs:
                order_state["quantity"] = int(kwargs["quantity"])  # type: ignore[arg-type]
                order_state["pending_quantity"] = int(kwargs["quantity"])  # type: ignore[arg-type]
            return {"order_id": "slw"}

        kite.orders.side_effect = orders
        kite.modify_order.side_effect = modify_order
        broker = KiteBroker(kite, live_orders_enabled=True)
        broker.modify_slm("slw", 97.0, quantity=10, transaction_type="SELL", tick_size=1)
        sent = kite.modify_order.call_args.kwargs
        self.assertEqual(sent["order_type"], "SL")
        self.assertEqual(sent["trigger_price"], 97)
        self.assertEqual(sent["price"], 97)
        self.assertEqual(int(sent["quantity"]), 10)

    def test_modify_open_zero_fills_is_uncertain_not_waiting(self) -> None:
        """OPEN with zero fills must not be treated as a waiting SL trigger rewrite."""
        kite = MagicMock()
        order_state = {
            "order_id": "sl0",
            "tag": "te1",
            "tradingsymbol": "AAA",
            "transaction_type": "SELL",
            "order_type": "SL",
            "quantity": 10,
            "status": "OPEN",
            "average_price": None,
            "filled_quantity": 0,
            "pending_quantity": 10,
            "trigger_price": 100.0,
            "price": 100.0,
        }
        kite.orders.side_effect = lambda: [dict(order_state)]
        broker = KiteBroker(kite, live_orders_enabled=True)
        with self.assertRaises(RuntimeError) as ctx:
            broker.modify_slm("sl0", 97.0, quantity=10, transaction_type="SELL", tick_size=1)
        self.assertIn("uncertain_order_state", str(ctx.exception))
        kite.modify_order.assert_not_called()

    def test_modify_triggered_rejects_trigger_only_rewrite(self) -> None:
        kite = MagicMock()
        order_state = {
            "order_id": "sl1",
            "tag": "te1",
            "tradingsymbol": "AAA",
            "transaction_type": "SELL",
            "order_type": "LIMIT",
            "quantity": 10,
            "status": "OPEN",
            "average_price": 99.0,
            "filled_quantity": 4,
            "pending_quantity": 6,
            "trigger_price": None,
            "price": 99.0,
        }
        kite.orders.side_effect = lambda: [dict(order_state)]
        broker = KiteBroker(kite, live_orders_enabled=True)
        with self.assertRaises(RuntimeError) as ctx:
            broker.modify_slm("sl1", 98.0, transaction_type="SELL")
        self.assertIn("triggered_quantity_only", str(ctx.exception))
        kite.modify_order.assert_not_called()

    def test_modify_exception_reconciles_without_retry(self) -> None:
        kite = MagicMock()
        order_state = {
            "order_id": "slx",
            "tag": "te1",
            "tradingsymbol": "AAA",
            "transaction_type": "SELL",
            "order_type": "SL",
            "quantity": 10,
            "status": "TRIGGER PENDING",
            "average_price": None,
            "filled_quantity": 0,
            "pending_quantity": 10,
            "trigger_price": 100.0,
            "price": 100.0,
        }

        def orders() -> list:
            return [dict(order_state)]

        kite.orders.side_effect = orders
        kite.modify_order.side_effect = RuntimeError("transient")
        broker = KiteBroker(kite, live_orders_enabled=True)
        result = broker.modify_slm("slx", 99.0, quantity=10, transaction_type="SELL")
        self.assertEqual(kite.modify_order.call_count, 1)
        self.assertEqual(result.order_id, "slx")
        self.assertEqual(str(result.status).upper(), "TRIGGER PENDING")

    def test_position_quote_failure_is_unknown_not_zero(self) -> None:
        kite = MagicMock()
        kite.positions.side_effect = RuntimeError("down")
        broker = KiteBroker(kite, live_orders_enabled=True)
        self.assertIsNone(broker.position_quote("AAA"))
        self.assertIsNone(broker.net_position_qty("AAA"))

    def test_kite_normalizes_timezone_less_exchange_timestamp_as_ist(self) -> None:
        raw = {
            "order_id": "o1",
            "tag": "te1",
            "tradingsymbol": "AAA",
            "transaction_type": "SELL",
            "order_type": "LIMIT",
            "quantity": 10,
            "status": "COMPLETE",
            "average_price": 100.0,
            "filled_quantity": 10,
            "pending_quantity": 0,
            "exchange_timestamp": "2026-08-17 10:25:00",
        }
        order = _kite_order_to_broker(raw)
        self.assertEqual(order.order_timestamp, "2026-08-17T04:55:00+00:00")
        # Unknown convention must not invent UTC for naive stamps.
        self.assertIsNone(
            normalize_broker_timestamp("2026-08-17 10:25:00", naive_tz=None)
        )
        aware = parse_timestamp_text("2026-08-17T04:55:00+00:00")
        assert aware is not None
        self.assertIsNotNone(aware.tzinfo)


if __name__ == "__main__":
    unittest.main()
