"""Strict entry safety: no lifecycle-fixture overrides in this suite."""
from __future__ import annotations
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

from api.admin_config.store import AdminConfigStore
from trading_engine_broker import FakeBroker, KiteBroker, EntryAcceptedVisibilityUnknown
from trading_engine_cycle import TradingEngineCycle
from trading_engine_quotes import TouchQuote, bounded_entry_limit
from trading_engine_store import TradingEngineStore

NOW = datetime(2026, 8, 17, 8, 30, tzinfo=timezone.utc)


class EntryBoundsTests(unittest.TestCase):
    def decision(self, **changes):
        args = dict(now=NOW, trigger_time=NOW.isoformat(), trigger=110., stop=100.,
            tick=.05, direction="UP", quote=TouchQuote(110, 110.05, NOW.isoformat()))
        args.update(changes)
        return bounded_entry_limit(**args)

    def test_long_and_short_touch_not_ltp(self):
        self.assertEqual(self.decision().price, 110.05)
        self.assertEqual(self.decision(direction="DOWN", stop=120).price, 110)

    def test_at_bound_and_outside_bound_both_directions(self):
        for direction, stop, touch in (("UP", 100, 111), ("DOWN", 120, 109)):
            self.assertIsNone(self.decision(direction=direction, stop=stop,
                quote=TouchQuote(touch, touch, NOW.isoformat())).reason)
            touch += .01 if direction == "UP" else -.01
            self.assertEqual(self.decision(direction=direction, stop=stop,
                quote=TouchQuote(touch, touch, NOW.isoformat())).reason, "entry_drift_exceeded")

    def test_unsatisfiable_tick_blocks(self):
        self.assertEqual(self.decision(tick=2, quote=TouchQuote(110.5, 110.5, NOW.isoformat())).reason,
                         "entry_tick_bound_unsatisfiable")

    def test_signal_boundaries_future_naive_missing_malformed(self):
        for delta, accepted in ((0, True), (30, True), (30.001, False), (-.001, False)):
            self.assertEqual(self.decision(trigger_time=(NOW-timedelta(seconds=delta)).isoformat()).reason is None, accepted)
        for stamp in (None, "bad", "2026-08-17T08:30:00"):
            self.assertEqual(self.decision(trigger_time=stamp).reason, "signal_expired_or_invalid")

    def test_quote_boundaries_future_missing_touch_and_crossed(self):
        for delta, accepted in ((0, True), (2, True), (2.001, False), (-.001, False)):
            quote = TouchQuote(110,110,(NOW-timedelta(seconds=delta)).isoformat())
            self.assertEqual(self.decision(quote=quote).reason is None, accepted)
        for quote in (None, TouchQuote(110,None,NOW.isoformat()), TouchQuote(112,110,NOW.isoformat())):
            self.assertIsNotNone(self.decision(quote=quote).reason)

    def test_nonfinite_and_bad_stop_block(self):
        for changes in ({"trigger": float("nan")}, {"tick": 0}, {"stop": 111}, {"drift_r": float("inf")}):
            self.assertIsNotNone(self.decision(**changes).reason)


class EntryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = TradingEngineStore(root / "engine.db")
        self.admin_path = root / "admin.db"
        admin = AdminConfigStore(self.admin_path)
        admin.close()
        self.broker = FakeBroker(last_prices={"AAA":110})
        self.broker.touch_quotes["AAA"] = TouchQuote(110,110,NOW.isoformat())
        self.run = self.store.start_run(session_date="2026-08-17", live_orders_enabled=False, pid=1)
        self.cycle = self.make_cycle()
        self.trade = self.store.insert_candidate(setup_id="one", continuation_rule_version="v1",
            session_date="2026-08-17", symbol="AAA", instrument_token=1, direction="UP",
            entry_estimate=110, tick_size=.05, trigger_time=NOW.isoformat(), qty=10,
            initial_stop=100, status="entry_submitting")
        self.trade = self.store.update_trade(self.trade.trade_id, intended_qty=10, remaining_entry_qty=10,
            run_id=self.run, entry_live_orders_enabled=False)

    def make_cycle(self):
        return TradingEngineCycle(self.store, self.broker, live_db=Path(self.tmp.name)/"observation.db",
            session_date="2026-08-17", started_at=NOW.isoformat(), run_id=self.run,
            live_orders_enabled=False, admin_config_db=self.admin_path,
            clock_fn=lambda: NOW, feed_age_seconds_fn=lambda: 0)

    def tearDown(self):
        self.cycle.close()
        self.store.close()
        self.tmp.cleanup()

    def test_limit_entry_and_immediate_protection(self):
        self.cycle._submit_entry(self.trade)
        trade = self.store.get_trade(self.trade.trade_id)
        self.assertEqual(self.broker.orders[trade.entry_order_id].order_type, "LIMIT")
        self.assertEqual(trade.remaining_position_qty, 10)
        self.assertEqual(trade.protected_qty, 10)

    def test_stale_signal_no_creation_time_fallback_no_write(self):
        trade = self.store.update_trade(self.trade.trade_id, trigger_time="malformed")
        self.cycle._submit_entry(trade)
        self.assertEqual(self.broker.market_place_count, 0)
        self.assertEqual(self.store.get_trade(trade.trade_id).skip_reason, "signal_expired_or_invalid")

    def test_missing_touch_no_order(self):
        self.broker.touch_quotes.clear()
        self.cycle._submit_entry(self.trade)
        self.assertEqual(self.broker.market_place_count, 0)

    def test_lost_limit_response_restart_never_resubmits(self):
        self.broker.market_place_error = "lost"
        self.broker.hide_market_tags = True
        self.cycle._submit_entry(self.trade)
        self.cycle.close()
        self.cycle = self.make_cycle()
        for _ in range(3):
            self.cycle._submit_entry(self.store.get_trade(self.trade.trade_id))
        self.assertEqual(self.broker.limit_place_count, 1)
        self.broker.reveal_tag(self.trade.broker_tag)
        self.cycle._submit_entry(self.store.get_trade(self.trade.trade_id))
        self.assertEqual(self.store.get_trade(self.trade.trade_id).protected_qty, 10)

    def test_quote_expires_between_intent_and_write(self):
        valid = self.broker.touch_quotes["AAA"]
        stale = TouchQuote(110,110,(NOW-timedelta(seconds=3)).isoformat())
        self.broker.touch_quote = Mock(side_effect=[valid, stale])
        self.cycle._submit_entry(self.trade)
        self.assertEqual(self.broker.limit_place_count, 0)

    def test_final_limit_resizes_quantity_without_changing_original_trigger(self):
        self.broker.touch_quotes["AAA"] = TouchQuote(111,111,NOW.isoformat())
        trade = self.store.update_trade(self.trade.trade_id, qty=90, intended_qty=90,
            remaining_entry_qty=90, risk_cap_used_inr=900)
        self.cycle._submit_entry(trade)
        saved = self.store.get_trade(trade.trade_id)
        self.assertLess(saved.filled_qty, 90)
        self.assertLessEqual(saved.filled_qty * 11, 900)
        self.assertEqual(saved.entry_estimate, 110)
        self.assertEqual(saved.entry_limit_price, 111)


class KiteLimitTests(unittest.TestCase):
    def test_quote_uses_depth_timestamp_not_last_trade_time(self):
        kite = Mock()
        kite.quote.return_value = {"NSE:AAA": {"timestamp":"2026-08-17 14:00:00",
            "last_trade_time":"2026-08-17 13:00:00", "last_price":90,
            "depth":{"buy":[{"price":110,"quantity":2}], "sell":[{"price":111,"quantity":3}]}}}
        quote = KiteBroker(kite, live_orders_enabled=False).touch_quote("AAA")
        self.assertEqual((quote.bid,quote.ask),(110,111))
        self.assertEqual(quote.as_of, NOW.isoformat())

    def test_paper_refuses_limit_write(self):
        kite = Mock()
        broker = KiteBroker(kite, live_orders_enabled=False)
        with self.assertRaisesRegex(RuntimeError, "live_orders_disabled"):
            broker.place_limit_mis(tradingsymbol="AAA", transaction_type="BUY", quantity=1, tag="test", price=110)
        kite.place_order.assert_not_called()

    def test_accept_poll_miss_preserves_id_single_write(self):
        kite = Mock()
        kite.orders.return_value = []
        kite.place_order.return_value = "accepted"
        broker = KiteBroker(kite, live_orders_enabled=True)
        broker.poll_order = Mock(return_value=None)
        with self.assertRaises(EntryAcceptedVisibilityUnknown) as caught:
            broker.place_limit_mis(tradingsymbol="AAA", transaction_type="BUY", quantity=1, tag="test", price=110)
        self.assertEqual(caught.exception.order_id, "accepted")
        self.assertEqual(kite.place_order.call_count, 1)
        self.assertEqual(kite.place_order.call_args.kwargs["order_type"], "LIMIT")
        self.assertEqual(kite.place_order.call_args.kwargs["price"], 110)
