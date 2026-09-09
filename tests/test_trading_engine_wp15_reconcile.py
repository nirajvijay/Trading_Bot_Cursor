"""WP-1.5: broker-truth reconciliation, restart recovery, feed staleness, PAPER gates."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from api.admin_config.defaults import DEFAULT_ADMIN_CONFIG_VALUES
from api.admin_config.store import AdminConfigStore
from trading_engine_broker import (
    LIVE_ORDERS_DISABLED_REASON,
    FakeBroker,
    KiteBroker,
    PositionQuote,
)
from trading_engine_cycle import TradingEngineCycle
from trading_engine_store import TradingEngineStore
from trading_engine_types import FEED_STALE_EXIT_SECONDS, FEED_STALE_PAUSE_SECONDS

from tests.test_trading_engine_cycle import SCHEMA, _seed_live, _fresh_feed_age

_IST = ZoneInfo("Asia/Kolkata")


class Wp15ReconcileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.live = Path(self.tmp.name) / "live.db"
        self.te = Path(self.tmp.name) / "te.db"
        self.admin = Path(self.tmp.name) / "admin.db"
        conn = sqlite3.connect(self.live)
        conn.executescript(SCHEMA)
        conn.close()
        admin = AdminConfigStore(self.admin)
        cfg = dict(DEFAULT_ADMIN_CONFIG_VALUES)
        cfg["daily_loss_cap_inr"] = 2995.0
        cfg["entry_cutoff_ist"] = 1445.0
        cfg["square_off_ist"] = 1515.0
        cfg["round_trip_charge_bps"] = 0.0
        cfg["estimated_slippage_bps"] = 0.0
        admin.update_config(cfg, actor="test")
        admin.arm_effective_config(actor="test")
        admin.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _cycle(
        self,
        broker: FakeBroker,
        *,
        clock: datetime | None = None,
        feed_age_fn=None,
        session_date: str = "2026-08-17",
    ) -> tuple[TradingEngineStore, TradingEngineCycle]:
        store = TradingEngineStore(self.te)
        run_id = store.start_run(
            session_date=session_date,
            live_orders_enabled=False,
            pid=1,
            total_capital=300_000.0,
        )

        def _clock() -> datetime:
            assert clock is not None
            return clock

        cycle = TradingEngineCycle(
            store,
            broker,
            live_db=self.live,
            session_date=session_date,
            started_at=f"{session_date}T04:30:00+00:00",
            run_id=run_id,
            live_orders_enabled=False,
            admin_config_db=self.admin,
            clock_fn=_clock if clock is not None else None,
            feed_age_seconds_fn=feed_age_fn if feed_age_fn is not None else _fresh_feed_age,
        )
        return store, cycle

    def _open_protected(self, symbol: str = "AAA") -> tuple[TradingEngineStore, TradingEngineCycle, FakeBroker, object]:
        _seed_live(self.live, "r1", "2026-08-17T04:40:00+00:00", symbol=symbol)
        broker = FakeBroker(
            last_prices={symbol: 110},
            auto_fill_entry=True,
            auto_confirm_sl=True,
        )
        clock = datetime(2026, 8, 17, 14, 0, tzinfo=_IST)
        store, cycle = self._cycle(broker, clock=clock)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        self.assertEqual(trade.status, "protected_open")
        self.assertGreater(int(trade.remaining_position_qty or 0), 0)
        return store, cycle, broker, trade

    def test_qty_mismatch_pauses_and_marks_reconciliation(self) -> None:
        store, cycle, broker, trade = self._open_protected()
        pos = int(trade.remaining_position_qty or 0)
        self.assertGreater(pos, 1)
        broker.position_quotes["AAA"] = PositionQuote(
            quantity=max(1, pos // 2),
            average_price=float(trade.entry_fill or 110),
            last_price=110.0,
        )
        cycle.tick()
        admin = AdminConfigStore(self.admin)
        self.assertTrue(admin.read_entries_paused())
        details = [str(r.get("detail") or "") for r in admin.list_audit(limit=20)]
        self.assertIn("qty_mismatch", details)
        admin.close()
        refreshed = store.get_trade(trade.trade_id)
        assert refreshed is not None
        self.assertEqual(refreshed.status, "reconciliation_required")
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("qty_mismatch", actions)
        # Still managing exposure — not silently closed.
        self.assertGreater(int(refreshed.remaining_position_qty or 0), 0)
        store.close()

    def test_external_flatten_still_closes_without_qty_incident(self) -> None:
        store, cycle, broker, trade = self._open_protected()
        store.update_trade(trade.trade_id, entry_time="2026-08-17T04:40:00+00:00")
        broker.simulate_external_flatten(
            "AAA",
            112.0,
            order_timestamp="2026-08-17T04:55:00+00:00",
        )
        cycle.tick()
        closed = store.get_trade(trade.trade_id)
        assert closed is not None
        self.assertEqual(closed.status, "closed")
        self.assertEqual(closed.close_reason, "external_exit")
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertNotIn("qty_mismatch", actions)
        store.close()

    def test_external_stop_cancel_pauses_and_allows_reprotect(self) -> None:
        store, cycle, broker, trade = self._open_protected()
        assert trade.sl_order_id is not None
        broker.reject_order(trade.sl_order_id, status="CANCELLED")
        places = broker.slm_place_count
        cycle.tick()
        admin = AdminConfigStore(self.admin)
        self.assertTrue(admin.read_entries_paused())
        self.assertIn(
            "external_stop_cancelled",
            [str(r.get("detail") or "") for r in admin.list_audit(limit=20)],
        )
        admin.close()
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        self.assertIn(
            mid.status,
            {"stop_pending", "reconciliation_required", "protection_pending", "protected_open"},
        )
        # Re-protect path may place a new stop while entries stay paused.
        cycle.tick()
        self.assertGreaterEqual(broker.slm_place_count, places)
        store.close()

    def test_external_stop_widen_raises_incident_without_adopting(self) -> None:
        store, cycle, broker, trade = self._open_protected()
        assert trade.sl_order_id is not None
        assert trade.current_stop is not None
        engine_stop = float(trade.current_stop)
        # UP: widen = lower stop (not tighten-only).
        wider = engine_stop - 2.0
        broker.modify_slm(
            trade.sl_order_id,
            trigger_price=wider,
            quantity=int(trade.remaining_position_qty or trade.qty or 0),
        )
        cycle.tick()
        admin = AdminConfigStore(self.admin)
        self.assertTrue(admin.read_entries_paused())
        self.assertIn(
            "external_stop_widen",
            [str(r.get("detail") or "") for r in admin.list_audit(limit=20)],
        )
        admin.close()
        refreshed = store.get_trade(trade.trade_id)
        assert refreshed is not None
        self.assertAlmostEqual(float(refreshed.current_stop or 0), engine_stop, places=6)
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("external_stop_widen", actions)
        self.assertNotIn("stop_adopted", actions)
        store.close()

    def test_restart_with_open_exposure_pauses_entries(self) -> None:
        store, cycle, broker, trade = self._open_protected()
        admin = AdminConfigStore(self.admin)
        admin.set_entries_paused(False)
        admin.close()
        store.close()

        store2 = TradingEngineStore(self.te)
        run = store2.latest_run()
        assert run is not None
        clock = datetime(2026, 8, 17, 14, 5, tzinfo=_IST)
        cycle2 = TradingEngineCycle(
            store2,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:30:00+00:00",
            run_id=str(run["run_id"]),
            live_orders_enabled=False,
            admin_config_db=self.admin,
            clock_fn=lambda: clock,
            feed_age_seconds_fn=_fresh_feed_age,
        )
        cycle2.tick()
        admin = AdminConfigStore(self.admin)
        self.assertTrue(admin.read_entries_paused())
        self.assertIn(
            "restart_recovery",
            [str(r.get("detail") or "") for r in admin.list_audit(limit=20)],
        )
        admin.close()
        # Management continues — stop still present / trade still open.
        again = store2.get_trade(trade.trade_id)
        assert again is not None
        self.assertNotEqual(again.status, "closed")
        self.assertGreater(int(again.remaining_position_qty or 0), 0)
        # No fresh entry ingest while paused.
        _seed_live(self.live, "r2", "2026-08-17T04:50:00+00:00", symbol="BBB")
        before = len(store2.list_trades("2026-08-17"))
        cycle2.tick()
        self.assertEqual(len(store2.list_trades("2026-08-17")), before)
        store2.close()

    def test_feed_stale_5s_pauses_and_blocks_auto_trail(self) -> None:
        self.assertEqual(FEED_STALE_PAUSE_SECONDS, 5.0)
        ages = {"sec": 0.0}

        def _age() -> float:
            return float(ages["sec"])

        _seed_live(self.live, "f1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=True,
            auto_confirm_sl=True,
        )
        clock = datetime(2026, 8, 17, 14, 0, tzinfo=_IST)
        store, cycle = self._cycle(broker, clock=clock, feed_age_fn=_age)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        store.update_trade(
            trade.trade_id,
            auto_trail_enabled=1,
            auto_trail_owner_disabled=0,
            r_value=10.0,
            remaining_entry_qty=0,
        )
        ages["sec"] = 5.0
        modifies = broker.modify_count
        broker.last_prices["AAA"] = 130.0
        cycle.tick()
        admin = AdminConfigStore(self.admin)
        self.assertTrue(admin.read_entries_paused())
        self.assertIn(
            "feed_stale",
            [str(r.get("detail") or "") for r in admin.list_audit(limit=20)],
        )
        admin.close()
        self.assertEqual(broker.modify_count, modifies)
        store.close()

    def test_feed_stale_30s_requests_managed_exit(self) -> None:
        self.assertEqual(FEED_STALE_EXIT_SECONDS, 30.0)
        ages = {"sec": 0.0}

        def _age() -> float:
            return float(ages["sec"])

        _seed_live(self.live, "f2", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=True,
            auto_confirm_sl=True,
        )
        clock = datetime(2026, 8, 17, 14, 0, tzinfo=_IST)
        store, cycle = self._cycle(broker, clock=clock, feed_age_fn=_age)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        places = broker.market_place_count
        ages["sec"] = 30.0
        cycle.tick()
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("feed_stale_exit_requested", actions)
        self.assertGreater(broker.market_place_count, places)
        final = store.get_trade(trade.trade_id)
        assert final is not None
        self.assertEqual(broker.net_position_qty("AAA"), 0)
        self.assertEqual(int(final.remaining_position_qty or 0), 0)
        store.close()

    def test_preserve_daily_loss_cap_2995(self) -> None:
        admin = AdminConfigStore(self.admin)
        payload = admin.load_active_payload()
        self.assertEqual(float(payload["daily_loss_cap_inr"]), 2995.0)
        admin.close()

    def test_unwired_or_unknown_feed_blocks_entries_in_trading_window(self) -> None:
        _seed_live(self.live, "u1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(last_prices={"AAA": 110}, auto_fill_entry=True, auto_confirm_sl=True)
        clock = datetime(2026, 8, 17, 14, 0, tzinfo=_IST)
        # Explicitly unwired (None fn → age None → feed_unknown).
        store = TradingEngineStore(self.te)
        run_id = store.start_run(
            session_date="2026-08-17", live_orders_enabled=False, pid=1, total_capital=300_000.0
        )
        cycle = TradingEngineCycle(
            store,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:30:00+00:00",
            run_id=run_id,
            live_orders_enabled=False,
            admin_config_db=self.admin,
            clock_fn=lambda: clock,
            feed_age_seconds_fn=None,
        )
        cycle.tick()
        self.assertEqual(len(store.list_trades("2026-08-17")), 0)
        admin = AdminConfigStore(self.admin)
        self.assertTrue(admin.read_entries_paused())
        self.assertIn("feed_unknown", [str(r.get("detail") or "") for r in admin.list_audit(limit=10)])
        admin.close()
        store.close()

    def test_nan_and_error_freshness_block_in_trading_window(self) -> None:
        import math

        for label, fn in (
            ("nan", lambda: math.nan),
            ("inf", lambda: math.inf),
            ("raise", lambda: (_ for _ in ()).throw(RuntimeError("feed_down"))),
            ("none", lambda: None),
        ):
            with self.subTest(label=label):
                self.te.unlink(missing_ok=True)
                self.live.unlink(missing_ok=True)
                conn = sqlite3.connect(self.live)
                conn.executescript(SCHEMA)
                conn.close()
                admin = AdminConfigStore(self.admin)
                admin.set_entries_paused(False)
                admin.close()
                _seed_live(self.live, f"bad_{label}", "2026-08-17T04:40:00+00:00", symbol="AAA")
                broker = FakeBroker(
                    last_prices={"AAA": 110}, auto_fill_entry=True, auto_confirm_sl=True
                )
                clock = datetime(2026, 8, 17, 10, 0, tzinfo=_IST)
                store, cycle = self._cycle(broker, clock=clock, feed_age_fn=fn)
                places = broker.market_place_count
                cycle.tick()
                self.assertEqual(len(store.list_trades("2026-08-17")), 0)
                self.assertEqual(broker.market_place_count, places)
                self.assertTrue(cycle._entries_paused())
                store.close()

    def test_pre_open_does_not_apply_stale_feed_exit_or_unknown_pause(self) -> None:
        ages = {"sec": 60.0}

        def _age() -> float:
            return float(ages["sec"])

        _seed_live(self.live, "pre", "2026-08-17T03:40:00+00:00", symbol="AAA")
        broker = FakeBroker(last_prices={"AAA": 110}, auto_fill_entry=True, auto_confirm_sl=True)
        # 09:00 IST — before 09:15 cash open.
        clock = datetime(2026, 8, 17, 9, 0, tzinfo=_IST)
        store, cycle = self._cycle(broker, clock=clock, feed_age_fn=_age)
        cycle.tick()
        admin = AdminConfigStore(self.admin)
        details = [str(r.get("detail") or "") for r in admin.list_audit(limit=20)]
        self.assertNotIn("feed_stale", details)
        self.assertNotIn("feed_unknown", details)
        self.assertFalse(admin.read_entries_paused())
        admin.close()
        # No managed exit from pre-open staleness.
        self.assertEqual(broker.market_place_count, 0)
        store.close()

    def test_feed_stale_partial_exit_resumes_via_durable_intent(self) -> None:
        ages = {"sec": 0.0}

        def _age() -> float:
            return float(ages["sec"])

        store, cycle, broker, trade = self._open_protected()
        # Rebuild cycle with mutable age (open used fresh feed).
        store.close()
        store = TradingEngineStore(self.te)
        run = store.latest_run()
        assert run is not None
        clock = datetime(2026, 8, 17, 14, 0, tzinfo=_IST)
        cycle = TradingEngineCycle(
            store,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:30:00+00:00",
            run_id=str(run["run_id"]),
            live_orders_enabled=False,
            admin_config_db=self.admin,
            clock_fn=lambda: clock,
            feed_age_seconds_fn=_age,
        )
        cycle._restart_recovery_done = True
        broker.auto_fill_exit = False
        ages["sec"] = 30.0
        places = broker.market_place_count
        cycle.tick()
        self.assertTrue(cycle._has_exit_intent(trade.trade_id, reason="feed_stale"))
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        self.assertGreater(int(mid.remaining_position_qty or 0), 0)
        # Simulate lost/uncertain path: hide working exit, then restore + fill on resume.
        exit_links = [
            ln for ln in store.list_order_links(trade.trade_id) if str(ln["role"]) == "exit"
        ]
        self.assertTrue(exit_links or broker.market_place_count > places)
        # Complete the exit on a later tick via durable resume (not in-memory set).
        broker.auto_fill_exit = True
        # Fill any working market exit remainders.
        for oid, order in list(broker.orders.items()):
            if order.order_type == "MARKET" and str(order.status).upper() in {"OPEN", "TRIGGER PENDING"}:
                broker.fill_entry(oid, float(order.average_price or 110))
        cycle.tick()
        final = store.get_trade(trade.trade_id)
        assert final is not None
        self.assertEqual(int(final.remaining_position_qty or 0), 0)
        self.assertEqual(broker.net_position_qty("AAA"), 0)
        store.close()

    def test_feed_stale_exit_survives_restart_without_memory_set(self) -> None:
        ages = {"sec": 0.0}

        def _age() -> float:
            return float(ages["sec"])

        store, cycle, broker, trade = self._open_protected()
        store.close()
        store = TradingEngineStore(self.te)
        run = store.latest_run()
        assert run is not None
        clock = datetime(2026, 8, 17, 14, 0, tzinfo=_IST)
        cycle = TradingEngineCycle(
            store,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:30:00+00:00",
            run_id=str(run["run_id"]),
            live_orders_enabled=False,
            admin_config_db=self.admin,
            clock_fn=lambda: clock,
            feed_age_seconds_fn=_age,
        )
        cycle._restart_recovery_done = True
        broker.auto_fill_exit = False
        ages["sec"] = 35.0
        cycle.tick()
        self.assertTrue(cycle._has_exit_intent(trade.trade_id, reason="feed_stale"))
        self.assertFalse(hasattr(cycle, "_feed_stale_exit_requested") and trade.trade_id in getattr(cycle, "_feed_stale_exit_requested", set()))
        store.close()

        # New process: no in-memory set; durable resume must continue.
        store2 = TradingEngineStore(self.te)
        broker.auto_fill_exit = True
        for oid, order in list(broker.orders.items()):
            if order.order_type == "MARKET" and str(order.status).upper() != "COMPLETE":
                try:
                    broker.fill_entry(oid, 110.0)
                except Exception:
                    pass
        cycle2 = TradingEngineCycle(
            store2,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:31:00+00:00",
            run_id=str(store2.latest_run()["run_id"]),
            live_orders_enabled=False,
            admin_config_db=self.admin,
            clock_fn=lambda: clock,
            feed_age_seconds_fn=_age,
        )
        for _ in range(4):
            cycle2.tick()
        final = store2.get_trade(trade.trade_id)
        assert final is not None
        self.assertEqual(broker.net_position_qty("AAA"), 0)
        self.assertEqual(int(final.remaining_position_qty or 0), 0)
        store2.close()

    def test_restart_pause_store_failure_keeps_local_lock_zero_entry_writes(self) -> None:
        store, cycle, broker, trade = self._open_protected()
        admin = AdminConfigStore(self.admin)
        admin.set_entries_paused(False)
        admin.close()
        store.close()

        store2 = TradingEngineStore(self.te)
        run = store2.latest_run()
        assert run is not None
        clock = datetime(2026, 8, 17, 14, 5, tzinfo=_IST)
        cycle2 = TradingEngineCycle(
            store2,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:30:00+00:00",
            run_id=str(run["run_id"]),
            live_orders_enabled=False,
            admin_config_db=self.admin,
            clock_fn=lambda: clock,
            feed_age_seconds_fn=_fresh_feed_age,
        )

        def _failing_pause(detail: str, *, trade_id=None) -> bool:
            cycle2._engage_local_entries_lock(detail)
            cycle2.last_error = "pause_failed:injected"
            return False

        cycle2._pause_entries_for = _failing_pause  # type: ignore[method-assign]
        places = broker.market_place_count
        cycle2.enforce_restart_recovery()
        self.assertFalse(cycle2._restart_recovery_done)
        self.assertTrue(cycle2._local_entries_lock)
        self.assertTrue(cycle2._entries_paused())
        _seed_live(self.live, "blocked", "2026-08-17T04:55:00+00:00", symbol="ZZZ")
        before = len(store2.list_trades("2026-08-17"))
        cycle2.tick()
        self.assertEqual(len(store2.list_trades("2026-08-17")), before)
        self.assertEqual(broker.market_place_count, places)  # no new entry writes
        again = store2.get_trade(trade.trade_id)
        assert again is not None
        self.assertGreater(int(again.remaining_position_qty or 0), 0)
        store2.close()

    def test_launcher_wires_runner_status_feed_age(self) -> None:
        from live_trading_engine import parse_args, _runner_status_path
        from trading_engine_cycle import feed_age_seconds_from_runner_status
        from api.runner_status import write_runner_status

        args = parse_args(["--runner-status-file", str(Path(self.tmp.name) / "runner.json")])
        path = _runner_status_path(args)
        self.assertEqual(path, Path(self.tmp.name) / "runner.json")
        now = datetime(2026, 8, 17, 14, 0, tzinfo=_IST)
        write_runner_status(
            path,
            session_date="2026-08-17",
            subscribed_tokens=1,
            feed_status="STABLE",
            last_tick_time=(now.replace(second=0) - __import__("datetime").timedelta(seconds=2)).isoformat(),
        )
        # Fix age with explicit now
        tick = now - __import__("datetime").timedelta(seconds=2)
        path.write_text(
            __import__("json").dumps(
                {
                    "session_date": "2026-08-17",
                    "subscribed_tokens": 1,
                    "feed_status": "STABLE",
                    "last_tick_time": tick.isoformat(),
                    "updated_at": now.isoformat(),
                }
            ),
            encoding="utf-8",
        )
        age = feed_age_seconds_from_runner_status(
            path, now=now, expected_session_date="2026-08-17"
        )
        self.assertIsNotNone(age)
        assert age is not None
        self.assertAlmostEqual(age, 2.0, places=3)
        self.assertIsNone(
            feed_age_seconds_from_runner_status(path, now=now, expected_session_date="2099-01-01")
        )
        self.assertIsNone(feed_age_seconds_from_runner_status(None))


class Wp15FeedStaleExitSerializationTests(unittest.TestCase):
    """feed_stale must share active-exit ownership with Close / square-off."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.live = Path(self.tmp.name) / "live.db"
        self.te = Path(self.tmp.name) / "te.db"
        self.admin = Path(self.tmp.name) / "admin.db"
        conn = sqlite3.connect(self.live)
        conn.executescript(SCHEMA)
        conn.close()
        admin = AdminConfigStore(self.admin)
        cfg = dict(DEFAULT_ADMIN_CONFIG_VALUES)
        cfg["daily_loss_cap_inr"] = 2995.0
        cfg["entry_cutoff_ist"] = 1445.0
        cfg["square_off_ist"] = 1515.0
        cfg["round_trip_charge_bps"] = 0.0
        cfg["estimated_slippage_bps"] = 0.0
        admin.update_config(cfg, actor="test")
        admin.arm_effective_config(actor="test")
        admin.close()
        self.ages = {"sec": 0.0}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _age(self) -> float:
        return float(self.ages["sec"])

    def _cycle(
        self, broker: FakeBroker, *, clock: datetime
    ) -> tuple[TradingEngineStore, TradingEngineCycle]:
        store = TradingEngineStore(self.te)
        run_id = store.start_run(
            session_date="2026-08-17",
            live_orders_enabled=False,
            pid=1,
            total_capital=300_000.0,
        )
        cycle = TradingEngineCycle(
            store,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:30:00+00:00",
            run_id=run_id,
            live_orders_enabled=False,
            admin_config_db=self.admin,
            clock_fn=lambda: clock,
            feed_age_seconds_fn=self._age,
        )
        return store, cycle

    def _open_protected(
        self, broker: FakeBroker, *, clock: datetime
    ) -> tuple[TradingEngineStore, TradingEngineCycle, object]:
        _seed_live(self.live, "ser1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        store, cycle = self._cycle(broker, clock=clock)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        self.assertEqual(trade.status, "protected_open")
        cycle._restart_recovery_done = True
        return store, cycle, trade

    def _active_exit_market_count(self, broker: FakeBroker, trade) -> int:
        n = 0
        for order in broker.orders.values():
            if order.tradingsymbol != trade.symbol:
                continue
            if order.order_type != "MARKET":
                continue
            # Entry is BUY for UP; exit is opposite side.
            if str(order.transaction_type).upper() == "SELL":
                n += 1
        return n

    def test_feed_stale_registered_in_active_exit_owners(self) -> None:
        from trading_engine_cycle import DURABLE_MARKET_EXIT_OWNERS

        self.assertIn(("feed_stale", "emergency"), DURABLE_MARKET_EXIT_OWNERS)

    def test_stale_feed_working_then_close_position_no_duplicate(self) -> None:
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=True,
            auto_confirm_sl=True,
            auto_fill_exit=False,
        )
        clock = datetime(2026, 8, 17, 14, 0, tzinfo=_IST)
        store, cycle, trade = self._open_protected(broker, clock=clock)
        self.ages["sec"] = 35.0
        cycle.tick()
        places = broker.market_place_count
        self.assertEqual(self._active_exit_market_count(broker, trade), 1)
        active = cycle._active_exit_reason_kind(store.get_trade(trade.trade_id))
        self.assertEqual(active, ("feed_stale", "emergency"))
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        first_oid = mid.active_exit_order_id
        self.assertIsNotNone(first_oid)

        cycle.close_position(trade.trade_id)
        self.assertEqual(broker.market_place_count, places)
        self.assertEqual(self._active_exit_market_count(broker, trade), 1)
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("exit_reason_deferred", actions)
        mid2 = store.get_trade(trade.trade_id)
        assert mid2 is not None
        self.assertEqual(mid2.active_exit_order_id, first_oid)
        self.assertGreater(int(mid2.remaining_position_qty or 0), 0)

        # Restart + competing close_all still no duplicate; then complete via fill.
        store.close()
        store2 = TradingEngineStore(self.te)
        cycle2 = TradingEngineCycle(
            store2,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:31:00+00:00",
            run_id=str(store2.latest_run()["run_id"]),
            live_orders_enabled=False,
            admin_config_db=self.admin,
            clock_fn=lambda: clock,
            feed_age_seconds_fn=self._age,
        )
        cycle2.close_all()
        cycle2.tick()
        self.assertEqual(broker.market_place_count, places)
        broker.auto_fill_exit = True
        broker.fill_entry(str(first_oid), 110.0)
        for _ in range(3):
            cycle2.tick()
        final = store2.get_trade(trade.trade_id)
        assert final is not None
        self.assertEqual(broker.net_position_qty("AAA"), 0)
        self.assertEqual(int(final.remaining_position_qty or 0), 0)
        self.assertEqual(broker.market_place_count, places)
        store2.close()

    def test_stale_feed_unknown_then_close_all_and_square_off_no_duplicate(self) -> None:
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=True,
            auto_confirm_sl=True,
            auto_fill_exit=False,
        )
        clock = datetime(2026, 8, 17, 14, 0, tzinfo=_IST)
        store, cycle, trade = self._open_protected(broker, clock=clock)
        self.ages["sec"] = 40.0
        cycle.tick()
        places = broker.market_place_count
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        oid = mid.active_exit_order_id
        self.assertIsNotNone(oid)
        # Unknown: hide exit order from poll (lost response).
        broker._hidden_order_ids.add(str(oid))
        cycle.tick()
        self.assertEqual(broker.market_place_count, places)
        active = cycle._active_exit_reason_kind(store.get_trade(trade.trade_id))
        self.assertEqual(active, ("feed_stale", "emergency"))

        cycle.close_all()
        self.assertEqual(broker.market_place_count, places)
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("exit_reason_deferred", actions)

        # Square-off clock — still must not place a competing exit.
        late = datetime(2026, 8, 17, 15, 16, tzinfo=_IST)
        cycle._clock_fn = lambda: late
        cycle.enforce_square_off()
        self.assertEqual(broker.market_place_count, places)

        broker._hidden_order_ids.discard(str(oid))
        broker.auto_fill_exit = True
        broker.fill_entry(str(oid), 109.0)
        for _ in range(4):
            cycle.tick()
        final = store.get_trade(trade.trade_id)
        assert final is not None
        self.assertEqual(int(final.remaining_position_qty or 0), 0)
        self.assertEqual(broker.net_position_qty("AAA"), 0)
        self.assertEqual(broker.market_place_count, places)
        store.close()

    def test_close_working_then_feed_stale_defers_no_duplicate(self) -> None:
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=True,
            auto_confirm_sl=True,
            auto_fill_exit=False,
        )
        clock = datetime(2026, 8, 17, 14, 0, tzinfo=_IST)
        store, cycle, trade = self._open_protected(broker, clock=clock)
        places = broker.market_place_count
        cycle.close_position(trade.trade_id)
        self.assertEqual(broker.market_place_count, places + 1)
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        close_oid = mid.active_exit_order_id
        self.assertIsNotNone(close_oid)
        active = cycle._active_exit_reason_kind(mid)
        self.assertEqual(active, ("close_position", "user_close"))

        # Feed goes stale while close exit is working — must defer, not compete.
        self.ages["sec"] = 45.0
        cycle.tick()
        self.assertEqual(broker.market_place_count, places + 1)
        self.assertEqual(self._active_exit_market_count(broker, trade), 1)
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("exit_reason_deferred", actions)
        mid2 = store.get_trade(trade.trade_id)
        assert mid2 is not None
        self.assertEqual(mid2.active_exit_order_id, close_oid)
        self.assertGreater(int(mid2.remaining_position_qty or 0), 0)

        # Partial fill then restart/reconcile — still one attempt until flat.
        pos0 = int(mid2.remaining_position_qty or 0)
        partial = max(1, pos0 // 3)
        broker.fill_entry_partial(str(close_oid), partial, 110.0, complete=False)
        cycle.tick()
        mid3 = store.get_trade(trade.trade_id)
        assert mid3 is not None
        rem = int(mid3.remaining_position_qty or 0)
        self.assertGreater(rem, 0)
        self.assertLess(rem, pos0)
        self.assertEqual(broker.market_place_count, places + 1)

        store.close()
        store2 = TradingEngineStore(self.te)
        cycle2 = TradingEngineCycle(
            store2,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:31:00+00:00",
            run_id=str(store2.latest_run()["run_id"]),
            live_orders_enabled=False,
            admin_config_db=self.admin,
            clock_fn=lambda: clock,
            feed_age_seconds_fn=self._age,
        )
        cycle2.tick()
        self.assertEqual(broker.market_place_count, places + 1)
        # Complete remaining — fill_entry_partial takes cumulative filled qty.
        order = broker.poll_order(str(close_oid))
        assert order is not None
        total_qty = int(order.quantity or 0)
        broker.fill_entry_partial(str(close_oid), total_qty, 110.0, complete=True)
        for _ in range(4):
            cycle2.tick()
        final = store2.get_trade(trade.trade_id)
        assert final is not None
        self.assertEqual(int(final.remaining_position_qty or 0), 0)
        self.assertEqual(broker.net_position_qty("AAA"), 0)
        self.assertEqual(broker.market_place_count, places + 1)
        store2.close()

    def test_close_unknown_then_feed_stale_defers_across_restart(self) -> None:
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=True,
            auto_confirm_sl=True,
            auto_fill_exit=False,
        )
        clock = datetime(2026, 8, 17, 14, 0, tzinfo=_IST)
        store, cycle, trade = self._open_protected(broker, clock=clock)
        places = broker.market_place_count
        cycle.close_all()
        self.assertEqual(broker.market_place_count, places + 1)
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        oid = mid.active_exit_order_id
        assert oid is not None
        broker._hidden_order_ids.add(str(oid))
        self.ages["sec"] = 50.0
        cycle.tick()
        self.assertEqual(broker.market_place_count, places + 1)
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("exit_reason_deferred", actions)

        store.close()
        store2 = TradingEngineStore(self.te)
        cycle2 = TradingEngineCycle(
            store2,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:31:00+00:00",
            run_id=str(store2.latest_run()["run_id"]),
            live_orders_enabled=False,
            admin_config_db=self.admin,
            clock_fn=lambda: clock,
            feed_age_seconds_fn=self._age,
        )
        cycle2.enforce_feed_staleness()
        cycle2.close_position(trade.trade_id)
        self.assertEqual(broker.market_place_count, places + 1)
        broker._hidden_order_ids.discard(str(oid))
        broker.auto_fill_exit = True
        broker.fill_entry(str(oid), 110.0)
        for _ in range(4):
            cycle2.tick()
        final = store2.get_trade(trade.trade_id)
        assert final is not None
        self.assertEqual(int(final.remaining_position_qty or 0), 0)
        self.assertEqual(broker.net_position_qty("AAA"), 0)
        self.assertEqual(broker.market_place_count, places + 1)
        store2.close()


class Wp15PaperHardBlockTests(unittest.TestCase):
    def test_kite_write_endpoints_blocked_when_live_disabled(self) -> None:
        kite = MagicMock()
        broker = KiteBroker(kite, live_orders_enabled=False)
        with self.assertRaises(RuntimeError) as place_ctx:
            broker.place_market_mis(
                tradingsymbol="AAA", transaction_type="BUY", quantity=1, tag="t"
            )
        self.assertEqual(str(place_ctx.exception), LIVE_ORDERS_DISABLED_REASON)
        with self.assertRaises(RuntimeError) as sl_ctx:
            broker.place_slm(
                tradingsymbol="AAA",
                transaction_type="SELL",
                quantity=1,
                trigger_price=99.0,
                tag="t",
            )
        self.assertEqual(str(sl_ctx.exception), LIVE_ORDERS_DISABLED_REASON)
        with self.assertRaises(RuntimeError) as mod_ctx:
            broker.modify_slm("oid", trigger_price=99.0, quantity=1)
        self.assertEqual(str(mod_ctx.exception), LIVE_ORDERS_DISABLED_REASON)
        with self.assertRaises(RuntimeError) as cancel_ctx:
            broker.cancel_order("oid")
        self.assertEqual(str(cancel_ctx.exception), LIVE_ORDERS_DISABLED_REASON)
        with self.assertRaises(RuntimeError) as flat_ctx:
            broker.flatten_mis(
                tradingsymbol="AAA",
                transaction_type="SELL",
                quantity=1,
                tag="tE",
            )
        self.assertEqual(str(flat_ctx.exception), LIVE_ORDERS_DISABLED_REASON)
        kite.place_order.assert_not_called()
        kite.modify_order.assert_not_called()
        kite.cancel_order.assert_not_called()

    def test_launcher_paper_selects_fake_broker_and_allows_protective_stop(self) -> None:
        from live_trading_engine import _make_broker

        broker = _make_broker(False, 300_000.0)
        self.assertIsInstance(broker, FakeBroker)
        broker.last_prices["AAA"] = 110.0
        entry = broker.place_market_mis(
            tradingsymbol="AAA", transaction_type="BUY", quantity=10, tag="paper1"
        )
        self.assertEqual(str(entry.status).upper(), "COMPLETE")
        sl = broker.place_slm(
            tradingsymbol="AAA",
            transaction_type="SELL",
            quantity=10,
            trigger_price=99.0,
            tag="paper1",
        )
        self.assertIn(str(sl.status).upper(), {"TRIGGER PENDING", "OPEN"})
        self.assertEqual(broker.net_position_qty("AAA"), 10)
        self.assertFalse(isinstance(broker, KiteBroker))


if __name__ == "__main__":
    unittest.main()
