"""Persistent PAPER runtime; local accounts and injected read-only touch snapshots."""
import tempfile
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

from trading_engine_paper import PaperBroker
from trading_engine_quotes import TouchQuote
from trading_engine_broker import SlPlaceAcceptedVisibilityUnknown

NOW = datetime(2026, 8, 17, 8, 30, tzinfo=timezone.utc)


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "paper.db"
        self.quote = TouchQuote(100, 100.05, NOW.isoformat())
        self.broker = self.open()

    def open(self):
        return PaperBroker(self.path, quote_provider=lambda symbol: self.quote, clock_fn=lambda: NOW)

    def tearDown(self):
        self.broker.close()
        self.tmp.cleanup()

    def entry(self, **overrides):
        return self.broker.place_limit_mis(**dict(tradingsymbol="AAA", transaction_type="BUY",
            quantity=5, tag="entry1", price=100.05, **overrides))

    def test_restart_keeps_orders_and_positions_without_duplicate_entry(self):
        entry = self.entry()
        stop = self.broker.place_slm(tradingsymbol="AAA", transaction_type="SELL",
                                     quantity=5, trigger_price=95, tag="stop1")
        self.broker.close()
        self.broker = self.open()
        self.assertEqual(self.broker.net_position_qty("AAA"), 5)
        self.assertEqual(self.entry().order_id, entry.order_id)
        self.assertEqual(self.broker.limit_place_count, 1)
        self.assertEqual(self.broker.poll_order(stop.order_id).pending_quantity, 5)

    def test_limit_waits_when_touch_moves_outside_bound_then_fills_at_touch(self):
        self.quote = TouchQuote(101, 101.05, NOW.isoformat())
        entry = self.entry()
        self.assertEqual(entry.status, "OPEN")
        self.quote = TouchQuote(99.95, 100, NOW.isoformat())
        self.broker.clear_quote_cache()
        filled = self.broker.poll_order(entry.order_id)
        self.assertEqual(filled.status, "COMPLETE")
        self.assertEqual(filled.average_price, 100)

    def test_stale_or_missing_quote_never_fabricates_market_exit_price(self):
        self.entry()
        self.quote = TouchQuote(90, 91, (NOW-timedelta(seconds=3)).isoformat())
        self.broker.clear_quote_cache()
        exit_order = self.broker.flatten_mis(tradingsymbol="AAA", transaction_type="SELL", quantity=5, tag="exit1")
        self.assertEqual(exit_order.status, "OPEN")
        self.assertEqual(self.broker.net_position_qty("AAA"), 5)
        self.quote = TouchQuote(89, 90, NOW.isoformat())
        self.broker.clear_quote_cache()
        self.assertEqual(self.broker.poll_order(exit_order.order_id).average_price, 89)
        self.assertEqual(self.broker.net_position_qty("AAA"), 0)

    def test_stop_limit_gap_triggers_but_does_not_fill_beyond_limit(self):
        self.entry()
        stop = self.broker.place_slm(tradingsymbol="AAA", transaction_type="SELL", quantity=5, trigger_price=95, tag="stop1")
        self.quote = TouchQuote(90, 90.05, NOW.isoformat())
        self.broker.clear_quote_cache()
        self.assertEqual(self.broker.poll_order(stop.order_id).status, "OPEN")
        self.assertEqual(self.broker.net_position_qty("AAA"), 5)
        self.broker.close()
        self.broker = self.open()
        self.quote = TouchQuote(95, 95.05, NOW.isoformat())
        self.broker.clear_quote_cache()
        self.assertEqual(self.broker.poll_order(stop.order_id).status, "COMPLETE")
        self.assertEqual(self.broker.net_position_qty("AAA"), 0)

    def test_lost_stop_accept_is_durable_across_restart(self):
        self.entry()
        self.broker.slm_place_error = "lost_sl_response"
        self.broker.hide_new_slm_orders = True
        with self.assertRaises(SlPlaceAcceptedVisibilityUnknown) as error:
            self.broker.place_slm(tradingsymbol="AAA", transaction_type="SELL", quantity=5, trigger_price=95, tag="stop1")
        oid = error.exception.order_id
        self.broker.close()
        self.broker = self.open()
        self.assertIsNone(self.broker.poll_order(oid))
        self.broker.reveal_order(oid)
        self.assertEqual(self.broker.poll_order(oid).status, "TRIGGER PENDING")
        self.assertEqual(self.broker.slm_place_count, 1)

    def test_second_account_writer_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "paper_account_already_in_use"):
            self.open()

    def test_paper_mark_moves_with_fresh_quote_not_last_execution(self):
        self.entry()
        self.quote = TouchQuote(104, 104.05, NOW.isoformat())
        self.broker.clear_quote_cache()
        self.assertEqual(self.broker.ltp("AAA"), 104)
        self.assertEqual(self.broker.position_quote("AAA").last_price, 104)
        self.assertIsNone(self.broker.position_quote("AAA").pnl)

    def test_real_cycle_manual_restart_rearm_and_close_preserves_account(self):
        from api.admin_config.store import AdminConfigStore
        from trading_engine_cycle import TradingEngineCycle
        from trading_engine_store import TradingEngineStore
        from tests.test_trading_engine_cycle import _seed_live
        root = Path(self.tmp.name)
        admin_path, live_path = root/"admin.db", root/"live.db"
        admin = AdminConfigStore(admin_path)
        admin.update_config({"round_trip_charge_bps": 0, "estimated_slippage_bps": 0}, actor="test")
        version = admin.active_version_id()
        admin.close()
        _seed_live(live_path, "paper-signal", NOW.isoformat())
        with sqlite3.connect(live_path) as db:
            db.execute("UPDATE live_continuation_decisions SET trigger_exchange_ts=?", (NOW.isoformat(),))
        self.quote = TouchQuote(110, 110, NOW.isoformat())
        store = TradingEngineStore(root/"engine.db")
        def new_cycle():
            run = store.start_run(session_date="2026-08-17", live_orders_enabled=False, pid=1)
            cycle = TradingEngineCycle(store, self.broker, live_db=live_path,
                session_date="2026-08-17", started_at=NOW.isoformat(), run_id=run,
                live_orders_enabled=False, admin_config_db=admin_path,
                clock_fn=lambda: NOW, feed_age_seconds_fn=lambda: 0)
            cycle._arming_readiness = lambda: True
            return cycle
        cycle = new_cycle()
        def command(kind, **payload):
            trade_id = payload.pop("trade_id", None)
            cid = store.enqueue_command(kind, trade_id=trade_id, payload=payload)
            cycle.process_commands()
            return store.command_record(cid)
        def arm():
            result = command("arm_session", execution_mode="PAPER", entry_mode="MANUAL",
                             run_id=cycle.run_id, config_version_id=version)
            self.assertEqual(result["state"], "succeeded", result)
        try:
            arm()
            result = command("approve_entry", setup_id="paper-signal", continuation_rule_version="v1", qty_override=5)
            self.assertEqual(result["state"], "succeeded", result)
            original = store.list_trades(None)[0]
            self.assertEqual(original.protected_qty, 5)
            cycle.close()
            self.broker.close()
            self.broker = self.open()
            cycle = new_cycle()
            cycle.tick()
            self.assertEqual(self.broker.net_position_qty("AAA"), 5)
            self.assertEqual(self.broker.limit_place_count, 1)
            self.assertIsNotNone(cycle._entry_arm_block_reason())
            arm()
            result = command("close_position", trade_id=original.trade_id)
            self.assertEqual(result["state"], "succeeded", result)
            self.assertEqual(self.broker.net_position_qty("AAA"), 0)
            self.assertEqual(store.get_trade(original.trade_id).status, "closed")
            self.assertEqual(self.broker.market_place_count, 2)
            self.assertFalse(any(o.pending_quantity for o in self.broker.list_orders()))
        finally:
            cycle.close()
            store.close()

    def test_launcher_paper_uses_read_only_kite_quotes(self):
        from live_trading_engine import _make_broker
        kite = MagicMock()
        kite.quote.return_value = {"NSE:AAA": {"timestamp": NOW, "depth": {
            "buy": [{"price": 100, "quantity": 10}], "sell": [{"price": 100.05, "quantity": 10}]}}}
        with patch("login._get_kite", return_value=kite):
            broker = _make_broker(False, 300000, paper_db=Path(self.tmp.name)/"runtime.db")
            try:
                broker._clock = lambda: NOW
                entry = broker.place_limit_mis(tradingsymbol="AAA", transaction_type="BUY", quantity=5, tag="runtime", price=100.05)
                stop = broker.place_slm(tradingsymbol="AAA", transaction_type="SELL", quantity=5, tag="runtime-sl", trigger_price=95)
                broker.modify_slm(stop.order_id, 96, quantity=5)
                broker.cancel_order(stop.order_id)
                broker.flatten_mis(tradingsymbol="AAA", transaction_type="SELL", quantity=5, tag="runtime-exit")
                self.assertEqual(entry.status, "COMPLETE")
                self.assertEqual(broker.net_position_qty("AAA"), 0)
                self.assertTrue(kite.quote.called)
                kite.place_order.assert_not_called()
                kite.modify_order.assert_not_called()
                kite.cancel_order.assert_not_called()
            finally:
                broker.close()
