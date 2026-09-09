"""WP-1.4: Close Position / Close All, entry cutoff, square-off (isolated)."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from api.admin_config.defaults import DEFAULT_ADMIN_CONFIG_VALUES
from api.admin_config.store import AdminConfigStore
from nse_trading_calendar import SpecialSessionSchedule
from trading_engine_broker import FakeBroker
from trading_engine_cycle import TradingEngineCycle
from trading_engine_store import TradingEngineStore
from trading_engine_types import CommandKind

from tests.test_trading_engine_cycle import SCHEMA, _seed_live, _fresh_feed_age

_IST = ZoneInfo("Asia/Kolkata")


class Wp14CloseAndSessionGateTests(unittest.TestCase):
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
        session_date: str = "2026-08-17",
        special_session_schedule: SpecialSessionSchedule | None = None,
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
            feed_age_seconds_fn=_fresh_feed_age,
            special_session_schedule=special_session_schedule,
        )
        return store, cycle

    def test_command_kinds_include_close_surface(self) -> None:
        from typing import get_args

        kinds = get_args(CommandKind)
        self.assertIn("close_all", kinds)
        self.assertIn("close_position", kinds)

    def test_close_position_flattens_via_durable_exit(self) -> None:
        _seed_live(self.live, "cp1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110}, auto_fill_entry=True, auto_confirm_sl=True
        )
        clock = datetime(2026, 8, 17, 14, 0, tzinfo=_IST)
        store, cycle = self._cycle(broker, clock=clock)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        places = broker.market_place_count
        store.enqueue_command("close_position", trade_id=trade.trade_id)
        cycle.tick()
        final = store.get_trade(trade.trade_id)
        assert final is not None
        self.assertEqual(broker.market_place_count, places + 1)
        self.assertEqual(broker.net_position_qty("AAA"), 0)
        self.assertEqual(int(final.remaining_position_qty or 0), 0)
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("close_position_requested", actions)
        self.assertIn("exit_intent", actions)
        store.close()

    def test_close_all_pauses_and_serializes_without_duplicate(self) -> None:
        _seed_live(self.live, "ca1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110}, auto_fill_entry=True, auto_confirm_sl=True
        )
        clock = datetime(2026, 8, 17, 14, 0, tzinfo=_IST)
        store, cycle = self._cycle(broker, clock=clock)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        places = broker.market_place_count
        store.enqueue_command("close_all")
        cycle.tick()
        cycle.tick()
        admin = AdminConfigStore(self.admin)
        self.assertTrue(admin.read_entries_paused())
        admin.close()
        final = store.get_trade(trade.trade_id)
        assert final is not None
        self.assertEqual(broker.market_place_count, places + 1)
        self.assertEqual(broker.net_position_qty("AAA"), 0)
        self.assertEqual(int(final.remaining_position_qty or 0), 0)
        store.close()

    def test_competing_close_does_not_duplicate_in_flight_exit(self) -> None:
        _seed_live(self.live, "comp1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=True,
            auto_confirm_sl=True,
            auto_fill_exit=False,
        )
        clock = datetime(2026, 8, 17, 14, 0, tzinfo=_IST)
        store, cycle = self._cycle(broker, clock=clock)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        places = broker.market_place_count
        cycle.close_position(trade.trade_id)
        self.assertEqual(broker.market_place_count, places + 1)
        # Competing close_all while exit working — must not place second market.
        cycle.close_all()
        self.assertEqual(broker.market_place_count, places + 1)
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("exit_reason_deferred", actions)
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        self.assertGreater(int(mid.remaining_position_qty or 0), 0)
        store.close()

    def test_rejected_exit_allows_retry_for_residual(self) -> None:
        _seed_live(self.live, "rej1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=True,
            auto_confirm_sl=True,
            auto_fill_exit=False,
        )
        clock = datetime(2026, 8, 17, 14, 0, tzinfo=_IST)
        store, cycle = self._cycle(broker, clock=clock)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        places = broker.market_place_count
        cycle.close_position(trade.trade_id)
        self.assertEqual(broker.market_place_count, places + 1)
        # Find working exit and reject it with zero fill.
        exit_oid = None
        for link in store.list_order_links(trade.trade_id):
            if str(link["role"]) == "exit":
                exit_oid = str(link["order_id"])
        self.assertIsNotNone(exit_oid)
        broker.reject_order(str(exit_oid), status="REJECTED")
        cycle.tick()
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("exit_order_terminal_failed", actions)
        self.assertIn("exit_submit_cleared", actions)
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        self.assertGreater(int(mid.remaining_position_qty or 0), 0)
        self.assertNotEqual(broker.net_position_qty("AAA"), 0)
        # Retry after clear — one additional market write, then complete.
        broker.auto_fill_exit = True
        cycle.close_position(trade.trade_id)
        self.assertEqual(broker.market_place_count, places + 2)
        final = store.get_trade(trade.trade_id)
        assert final is not None
        self.assertEqual(broker.net_position_qty("AAA"), 0)
        self.assertEqual(int(final.remaining_position_qty or 0), 0)
        store.close()

    def test_cancelled_partial_exit_keeps_residual_and_retries(self) -> None:
        _seed_live(self.live, "cx1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=True,
            auto_confirm_sl=True,
            auto_fill_exit=False,
        )
        clock = datetime(2026, 8, 17, 14, 0, tzinfo=_IST)
        store, cycle = self._cycle(broker, clock=clock)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        pos = int(trade.remaining_position_qty or 0)
        places = broker.market_place_count
        cycle.close_position(trade.trade_id)
        exit_oid = None
        for link in store.list_order_links(trade.trade_id):
            if str(link["role"]) == "exit":
                exit_oid = str(link["order_id"])
        assert exit_oid is not None
        half = max(1, pos // 2)
        broker.fill_entry_partial(exit_oid, half, 109.0, complete=False)
        # Reuse fill helper semantics on the exit MARKET order.
        order = broker.orders[exit_oid]
        from trading_engine_broker import _copy_order

        broker.orders[exit_oid] = _copy_order(
            order,
            status="CANCELLED",
            filled_quantity=half,
            pending_quantity=0,
            cancelled_quantity=pos - half,
            average_price=109.0,
        )
        cycle.tick()
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        self.assertGreater(int(mid.exited_qty or 0), 0)
        self.assertGreater(int(mid.remaining_position_qty or 0), 0)
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("exit_submit_cleared", actions)
        broker.auto_fill_exit = True
        cycle.close_position(trade.trade_id)
        self.assertEqual(broker.market_place_count, places + 2)
        final = store.get_trade(trade.trade_id)
        assert final is not None
        self.assertEqual(int(final.remaining_position_qty or 0), 0)
        self.assertEqual(broker.net_position_qty("AAA"), 0)
        store.close()

    def test_tick_blocks_fresh_trigger_and_pending_submit_at_cutoff(self) -> None:
        """Cutoff before ingest; pre-broker recheck blocks a pending submission."""
        _seed_live(self.live, "cut_fresh", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(last_prices={"AAA": 110}, auto_fill_entry=True)
        cutoff = datetime(2026, 8, 17, 14, 45, tzinfo=_IST)
        store, cycle = self._cycle(broker, clock=cutoff)
        places = broker.market_place_count
        cycle.tick()
        self.assertEqual(broker.market_place_count, places)
        self.assertEqual(store.list_trades("2026-08-17"), [])
        admin = AdminConfigStore(self.admin)
        self.assertTrue(admin.read_entries_paused())
        admin.close()
        store.close()

        # Pending sized submission (no intent yet) crossing 14:45 via normal tick().
        self.te.unlink(missing_ok=True)
        broker2 = FakeBroker(last_prices={"BBB": 110}, auto_fill_entry=True)
        early = datetime(2026, 8, 17, 14, 44, tzinfo=_IST)
        store2, cycle2 = self._cycle(broker2, clock=early)
        pending = store2.insert_candidate(
            setup_id="pend1",
            continuation_rule_version="v1",
            session_date="2026-08-17",
            symbol="BBB",
            instrument_token=2,
            direction="UP",
            entry_estimate=110.0,
            tick_size=1.0,
            trigger_time="2026-08-17T09:00:00+00:00",
            qty=10,
            initial_stop=100.0,
            notional=1100.0,
            margin_blocked=110.0,
            status="entry_submitting",
        )
        assert pending is not None
        store2.update_trade(
            pending.trade_id,
            intended_qty=10,
            remaining_entry_qty=10,
            remaining_position_qty=0,
            filled_qty=0,
            exited_qty=0,
            status="entry_submitting",
        )
        places2 = broker2.market_place_count
        cycle2._clock_fn = lambda: datetime(2026, 8, 17, 14, 45, tzinfo=_IST)
        cycle2.tick()
        after = store2.get_trade(pending.trade_id)
        assert after is not None
        self.assertEqual(broker2.market_place_count, places2)
        self.assertEqual(after.status, "skipped")
        self.assertIn(after.skip_reason, {"entry_cutoff", "entries_paused"})
        store2.close()

    def test_close_all_and_square_off_cancel_zero_fill_working_entries(self) -> None:
        _seed_live(self.live, "zf_ca", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110}, auto_fill_entry=False, auto_confirm_sl=True
        )
        clock = datetime(2026, 8, 17, 14, 0, tzinfo=_IST)
        store, cycle = self._cycle(broker, clock=clock)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        self.assertEqual(int(trade.filled_qty or 0), 0)
        self.assertGreater(int(trade.remaining_entry_qty or 0), 0)
        oid = str(trade.entry_order_id)
        cycle.close_all()
        after = store.get_trade(trade.trade_id)
        assert after is not None
        entry = broker.poll_order(oid)
        assert entry is not None
        self.assertEqual(str(entry.status).upper(), "CANCELLED")
        self.assertEqual(int(after.remaining_entry_qty or 0), 0)
        self.assertEqual(after.status, "skipped")
        store.close()

        # Square-off path with cancel-time fill → must flatten the late fill.
        self.te.unlink(missing_ok=True)
        admin = AdminConfigStore(self.admin)
        admin.set_entries_paused(False)
        admin.close()
        # Fresh live DB so only the square-off candidate is present.
        self.live.unlink(missing_ok=True)
        conn = sqlite3.connect(self.live)
        conn.executescript(SCHEMA)
        conn.close()
        _seed_live(self.live, "zf_so", "2026-08-17T04:40:00+00:00", symbol="CCC")
        broker2 = FakeBroker(
            last_prices={"CCC": 110},
            auto_fill_entry=False,
            auto_confirm_sl=True,
            auto_fill_exit=True,
            cancel_additional_fill=5,
        )
        early = datetime(2026, 8, 17, 14, 0, tzinfo=_IST)
        store2, cycle2 = self._cycle(broker2, clock=early)
        cycle2.tick()
        trades2 = store2.list_trades("2026-08-17")
        self.assertTrue(trades2)
        trade2 = trades2[0]
        self.assertEqual(int(trade2.filled_qty or 0), 0)
        places = broker2.market_place_count
        cycle2._clock_fn = lambda: datetime(2026, 8, 17, 15, 15, tzinfo=_IST)
        cycle2.tick()
        final = store2.get_trade(trade2.trade_id)
        assert final is not None
        self.assertEqual(int(final.remaining_entry_qty or 0), 0)
        self.assertEqual(broker2.net_position_qty("CCC"), 0)
        self.assertGreaterEqual(broker2.market_place_count, places + 1)
        self.assertEqual(int(final.remaining_position_qty or 0), 0)
        store2.close()

    def test_rejected_exit_active_attempt_survives_poll_restart_competing_close(
        self,
    ) -> None:
        """Rejected first exit must not clear a working second; no third write."""
        _seed_live(self.live, "act1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=True,
            auto_confirm_sl=True,
            auto_fill_exit=False,
        )
        clock = datetime(2026, 8, 17, 14, 0, tzinfo=_IST)
        store, cycle = self._cycle(broker, clock=clock)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        places = broker.market_place_count
        cycle.close_position(trade.trade_id)
        self.assertEqual(broker.market_place_count, places + 1)
        first_oid = None
        for link in store.list_order_links(trade.trade_id):
            if str(link["role"]) == "exit":
                first_oid = str(link["order_id"])
        assert first_oid is not None
        broker.reject_order(first_oid, status="REJECTED")
        cycle.tick()
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        self.assertIsNone(mid.active_exit_order_id)

        cycle.close_position(trade.trade_id)
        self.assertEqual(broker.market_place_count, places + 2)
        mid2 = store.get_trade(trade.trade_id)
        assert mid2 is not None
        second_oid = mid2.active_exit_order_id
        self.assertIsNotNone(second_oid)
        self.assertNotEqual(second_oid, first_oid)
        second_order = broker.poll_order(str(second_oid))
        assert second_order is not None
        self.assertIn(str(second_order.status).upper(), {"OPEN", "TRIGGER PENDING"})

        # Repeated polling / resume / competing close must not place a third write.
        cycle.tick()
        cycle.resume_pending_market_exits()
        cycle.close_all()
        cycle.tick()
        self.assertEqual(broker.market_place_count, places + 2)
        clears = [
            r
            for r in store.list_events(trade.trade_id)
            if str(r["action"]) == "exit_submit_cleared"
        ]
        self.assertEqual(len(clears), 1)
        final = store.get_trade(trade.trade_id)
        assert final is not None
        self.assertEqual(final.active_exit_order_id, second_oid)
        store.close()

    def test_calendar_gates_block_entries_not_recovery(self) -> None:
        broker = FakeBroker(last_prices={"AAA": 110})

        # Session-date mismatch vs IST clock.
        mismatch_clock = datetime(2026, 8, 18, 10, 0, tzinfo=_IST)
        store, cycle = self._cycle(broker, clock=mismatch_clock, session_date="2026-08-17")
        self.assertEqual(cycle._entry_calendar_block_reason(), "session_date_mismatch")
        cycle.tick()
        admin = AdminConfigStore(self.admin)
        self.assertTrue(admin.read_entries_paused())
        admin.set_entries_paused(False)
        admin.close()
        store.close()

        # Holiday (Republic Day) with matching session date — block without pause stamp.
        self.te.unlink(missing_ok=True)
        holiday_clock = datetime(2026, 1, 26, 10, 0, tzinfo=_IST)
        store2, cycle2 = self._cycle(
            broker, clock=holiday_clock, session_date="2026-01-26"
        )
        self.assertEqual(cycle2._entry_calendar_block_reason(), "nse_holiday_or_weekend")
        pending = store2.insert_candidate(
            setup_id="hol1",
            continuation_rule_version="v1",
            session_date="2026-01-26",
            symbol="AAA",
            instrument_token=1,
            direction="UP",
            entry_estimate=110.0,
            tick_size=1.0,
            trigger_time="2026-01-26T04:00:00+00:00",
            qty=10,
            initial_stop=100.0,
            status="entry_submitting",
        )
        assert pending is not None
        store2.update_trade(
            pending.trade_id,
            intended_qty=10,
            remaining_entry_qty=10,
            status="entry_submitting",
        )
        places = broker.market_place_count
        cycle2.drive_open()
        after = store2.get_trade(pending.trade_id)
        assert after is not None
        self.assertEqual(broker.market_place_count, places)
        self.assertEqual(after.status, "skipped")
        self.assertEqual(after.skip_reason, "nse_holiday_or_weekend")
        store2.close()

        # Unconfigured special session — date alone never authorizes.
        self.te.unlink(missing_ok=True)
        admin = AdminConfigStore(self.admin)
        admin.set_entries_paused(False)
        admin.close()
        special_clock = datetime(2026, 11, 8, 10, 0, tzinfo=_IST)
        store3, cycle3 = self._cycle(
            broker, clock=special_clock, session_date="2026-11-08"
        )
        self.assertEqual(
            cycle3._entry_calendar_block_reason(), "special_session_unconfigured"
        )
        cycle3.tick()
        admin = AdminConfigStore(self.admin)
        self.assertTrue(admin.read_entries_paused())
        admin.close()

        # Explicit schedule required; uses schedule hours, not normal 14:45/15:15.
        self.te.unlink(missing_ok=True)
        admin = AdminConfigStore(self.admin)
        admin.set_entries_paused(False)
        admin.close()
        muhurat = SpecialSessionSchedule(
            session_date="2026-11-08",
            session_open_ist=1815.0,
            entry_cutoff_ist=1825.0,
            square_off_ist=1830.0,
        )
        # Before schedule open — calendar ok, entries blocked as not open.
        pre_open = datetime(2026, 11, 8, 18, 0, tzinfo=_IST)
        store4, cycle4 = self._cycle(
            broker,
            clock=pre_open,
            session_date="2026-11-08",
            special_session_schedule=muhurat,
        )
        self.assertIsNone(cycle4._entry_calendar_block_reason())
        self.assertEqual(cycle4._new_entry_block_reason(), "special_session_not_open")
        # Mid-session: open for entries; cutoff/square-off follow schedule.
        mid = datetime(2026, 11, 8, 18, 20, tzinfo=_IST)
        cycle4._clock_fn = lambda: mid
        self.assertIsNone(cycle4._new_entry_block_reason())
        self.assertFalse(cycle4.entry_cutoff_reached())
        self.assertFalse(cycle4.square_off_reached())
        # Past schedule cutoff but before normal-day 14:45 equivalent — still cutoff.
        after_cut = datetime(2026, 11, 8, 18, 26, tzinfo=_IST)
        cycle4._clock_fn = lambda: after_cut
        self.assertEqual(cycle4._new_entry_block_reason(), "entry_cutoff")
        self.assertTrue(cycle4.entry_cutoff_reached())
        self.assertFalse(cycle4.square_off_reached())
        after_sq = datetime(2026, 11, 8, 18, 30, tzinfo=_IST)
        cycle4._clock_fn = lambda: after_sq
        self.assertTrue(cycle4.square_off_reached())
        store4.close()

        # Unconfigured special day: new entries blocked, but close still recovers exposure.
        self.te.unlink(missing_ok=True)
        admin = AdminConfigStore(self.admin)
        admin.set_entries_paused(False)
        admin.close()
        recover_clock = datetime(2026, 11, 8, 12, 0, tzinfo=_IST)
        store5, cycle5 = self._cycle(
            broker, clock=recover_clock, session_date="2026-11-08"
        )
        self.assertEqual(
            cycle5._entry_calendar_block_reason(), "special_session_unconfigured"
        )
        exposed = store5.insert_candidate(
            setup_id="rec1",
            continuation_rule_version="v1",
            session_date="2026-11-08",
            symbol="AAA",
            instrument_token=1,
            direction="UP",
            entry_estimate=110.0,
            tick_size=1.0,
            trigger_time="2026-11-08T04:00:00+00:00",
            qty=10,
            initial_stop=100.0,
            status="protected_open",
        )
        assert exposed is not None
        store5.update_trade(
            exposed.trade_id,
            filled_qty=10,
            remaining_position_qty=10,
            remaining_entry_qty=0,
            entry_fill=110.0,
            protected_qty=0,
            status="protected_open",
        )
        # Seed broker long so flatten has something to exit.
        broker.place_market_mis(
            tradingsymbol="AAA",
            transaction_type="BUY",
            quantity=10,
            tag="seed",
        )
        places = broker.market_place_count
        cycle5.close_position(exposed.trade_id)
        self.assertGreaterEqual(broker.market_place_count, places + 1)
        store5.close()
        store3.close()

    def test_entry_cutoff_pauses_new_entries(self) -> None:
        _seed_live(self.live, "cut1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(last_prices={"AAA": 110})
        clock = datetime(2026, 8, 17, 14, 45, tzinfo=_IST)
        store, cycle = self._cycle(broker, clock=clock)
        cycle.enforce_entry_cutoff()
        admin = AdminConfigStore(self.admin)
        self.assertTrue(admin.read_entries_paused())
        admin.close()
        self.assertTrue(cycle.entry_cutoff_reached())
        self.assertFalse(cycle.square_off_reached())
        store.close()

    def test_square_off_flattens_open_position(self) -> None:
        _seed_live(self.live, "so1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110}, auto_fill_entry=True, auto_confirm_sl=True
        )
        # Build protected position before square-off clock.
        early = datetime(2026, 8, 17, 14, 0, tzinfo=_IST)
        store, cycle = self._cycle(broker, clock=early)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        places = broker.market_place_count
        cycle._clock_fn = lambda: datetime(2026, 8, 17, 15, 15, tzinfo=_IST)
        cycle.tick()
        final = store.get_trade(trade.trade_id)
        assert final is not None
        self.assertEqual(broker.market_place_count, places + 1)
        self.assertEqual(broker.net_position_qty("AAA"), 0)
        self.assertEqual(int(final.remaining_position_qty or 0), 0)
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertTrue(any(a == "exit_intent" for a in actions))
        admin = AdminConfigStore(self.admin)
        self.assertTrue(admin.read_entries_paused())
        admin.close()
        store.close()


if __name__ == "__main__":
    unittest.main()
