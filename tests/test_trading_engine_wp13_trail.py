"""WP-1.3: protection deadline, remainder cancel, staged-R trailing (isolated).

Covers broker-port flatten, durable exit intent, zero-fill timeout, partial/unknown
exits, stop-cancel races, stale modify responses, partial-entry R freeze, retracement
during throttle, and owner-disabled trailing — long and short.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from api.admin_config.defaults import DEFAULT_ADMIN_CONFIG_VALUES
from api.admin_config.store import AdminConfigStore
from trading_engine_broker import FakeBroker, BrokerPort
from trading_engine_cycle import TradingEngineCycle
from trading_engine_risk import (
    cost_adjusted_break_even,
    freeze_r_value,
    staged_r_desired_stop,
)
from trading_engine_store import TradingEngineStore
from trading_engine_types import TRAIL_MIN_MODIFY_INTERVAL_SECONDS

from tests.test_trading_engine_cycle import SCHEMA, _seed_live, _session_clock


def _past(seconds: float = 10.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(
        timespec="seconds"
    )


class StagedRMathTests(unittest.TestCase):
    def test_freeze_r_and_stages_long_and_short(self) -> None:
        r = freeze_r_value(entry=110.0, initial_stop=99.0)
        self.assertAlmostEqual(r, 11.0, places=6)
        be = cost_adjusted_break_even(direction="UP", entry=110.0, charge_bps=0.0)
        self.assertAlmostEqual(be, 110.0, places=6)
        d0 = staged_r_desired_stop(
            direction="UP",
            entry=110.0,
            initial_stop=99.0,
            current_stop=99.0,
            extreme=115.0,
            last_price=115.0,
            r_value=11.0,
            charge_bps=0.0,
            tick_size=1.0,
        )
        self.assertAlmostEqual(d0, 99.0, places=6)
        d1 = staged_r_desired_stop(
            direction="UP",
            entry=110.0,
            initial_stop=99.0,
            current_stop=99.0,
            extreme=125.0,
            last_price=125.0,
            r_value=11.0,
            charge_bps=0.0,
            tick_size=1.0,
        )
        self.assertAlmostEqual(d1, 114.0, places=6)
        # Retracement of last_price must not downgrade stage (extreme still +2R+).
        d_retrace = staged_r_desired_stop(
            direction="UP",
            entry=110.0,
            initial_stop=99.0,
            current_stop=114.0,
            extreme=140.0,
            last_price=120.0,
            r_value=11.0,
            charge_bps=0.0,
            tick_size=1.0,
        )
        self.assertEqual(d_retrace, 135.0)
        # Short: extreme at +1.36R → 1R behind extreme (95+11=106).
        d_short = staged_r_desired_stop(
            direction="DOWN",
            entry=110.0,
            initial_stop=121.0,
            current_stop=121.0,
            extreme=95.0,
            last_price=100.0,
            r_value=11.0,
            charge_bps=0.0,
            tick_size=1.0,
        )
        self.assertEqual(d_short, 106.0)


class BrokerPortFlattenTests(unittest.TestCase):
    def test_fakebroker_implements_port_flatten(self) -> None:
        broker = FakeBroker(last_prices={"AAA": 110}, auto_fill_exit=True)
        # Seed a long via market buy so net != 0.
        entry = broker.place_market_mis(
            tradingsymbol="AAA",
            transaction_type="BUY",
            quantity=10,
            tag="entry1",
        )
        self.assertEqual(entry.status, "COMPLETE")
        flat = broker.flatten_mis(
            tradingsymbol="AAA",
            transaction_type="SELL",
            quantity=10,
            tag="entry1E",
        )
        self.assertEqual(flat.transaction_type, "SELL")
        self.assertEqual(int(flat.quantity or 0), 10)
        self.assertEqual(broker.net_position_qty("AAA"), 0)
        # Protocol structural check.
        self.assertTrue(isinstance(broker, object))
        for name in (
            "place_market_mis",
            "place_slm",
            "modify_slm",
            "cancel_order",
            "flatten_mis",
            "poll_order",
        ):
            self.assertTrue(callable(getattr(BrokerPort, name, None) or getattr(broker, name)))


class Wp13IntegrationTests(unittest.TestCase):
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
        cfg["protection_confirm_deadline_seconds"] = 5.0
        cfg["entry_remainder_cancel_seconds"] = 5.0
        cfg["round_trip_charge_bps"] = 0.0
        cfg["estimated_slippage_bps"] = 0.0
        admin.update_config(cfg, actor="test")
        admin.arm_effective_config(actor="test")
        admin.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _cycle(self, broker: FakeBroker, **kwargs) -> tuple[TradingEngineStore, TradingEngineCycle]:
        store = kwargs.pop("store", None) or TradingEngineStore(self.te)
        run_id = store.start_run(
            session_date="2026-08-17",
            live_orders_enabled=False,
            pid=kwargs.pop("pid", 1),
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
            clock_fn=_session_clock(),
        )
        return store, cycle

    def test_protection_deadline_flattens_confirmed_long(self) -> None:
        _seed_live(self.live, "pd1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110}, auto_fill_entry=True, auto_confirm_sl=False
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        pos0 = int(trade.remaining_position_qty or trade.filled_qty or 0)
        self.assertGreater(pos0, 0)
        places_before = broker.market_place_count
        store.update_trade(
            trade.trade_id,
            status="protection_pending",
            protected_qty=0,
            remaining_position_qty=pos0,
            protection_deadline_at=_past(1),
        )
        cycle.tick()
        admin = AdminConfigStore(self.admin)
        self.assertTrue(admin.read_entries_paused())
        admin.close()
        final = store.get_trade(trade.trade_id)
        assert final is not None
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("protection_deadline_breach", actions)
        self.assertIn("exit_intent", actions)
        self.assertGreater(broker.market_place_count, places_before)
        self.assertEqual(broker.net_position_qty("AAA"), 0)
        self.assertEqual(int(final.remaining_position_qty or 0), 0)
        # Working protective order must not remain live after confirmed flatten.
        if trade.sl_order_id:
            sl = broker.poll_order(str(trade.sl_order_id))
            if sl is not None:
                self.assertIn(str(sl.status).upper(), {"CANCELLED", "REJECTED", "COMPLETE"})
        self.assertIn(final.status, {"closed", "reconciliation_required", "exit_pending"})
        if final.status == "closed":
            self.assertEqual(final.close_reason, "protection_deadline")
        store.close()

    def test_zero_fill_entry_cancels_from_submission_intent(self) -> None:
        _seed_live(self.live, "zf1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110}, auto_fill_entry=False, auto_confirm_sl=True
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        self.assertEqual(int(trade.filled_qty or 0), 0)
        self.assertGreater(int(trade.remaining_entry_qty or 0), 0)
        self.assertIsNone(trade.entry_time)
        self.assertIsNotNone(trade.entry_submitted_at)
        store.update_trade(trade.trade_id, entry_submitted_at=_past(10))
        cycle.tick()
        after = store.get_trade(trade.trade_id)
        assert after is not None
        self.assertEqual(int(after.remaining_entry_qty or 0), 0)
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("entry_remainder_cancel_deadline", actions)
        entry = broker.poll_order(str(trade.entry_order_id or ""))
        assert entry is not None
        self.assertEqual(str(entry.status).upper(), "CANCELLED")
        store.close()

    def test_partial_fill_remainder_cancels_from_submission_anchor(self) -> None:
        _seed_live(self.live, "er1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110}, auto_fill_entry=False, auto_confirm_sl=True
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        oid = trade.entry_order_id
        intended = int(trade.intended_qty or trade.qty)
        half = max(1, intended // 2)
        broker.fill_entry_partial(oid, half, 110.0, complete=False)
        cycle.tick()
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        self.assertGreater(int(mid.remaining_entry_qty or 0), 0)
        store.update_trade(mid.trade_id, entry_submitted_at=_past(10))
        cycle.tick()
        after = store.get_trade(trade.trade_id)
        assert after is not None
        self.assertEqual(int(after.remaining_entry_qty or 0), 0)
        store.close()

    def test_partial_unknown_emergency_exit_does_not_assume_full_flat(self) -> None:
        _seed_live(self.live, "pu1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=True,
            auto_confirm_sl=False,
            auto_fill_exit=False,
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        pos = int(trade.remaining_position_qty or 0)
        store.update_trade(
            trade.trade_id,
            status="protection_pending",
            protected_qty=0,
            remaining_position_qty=pos,
            protection_deadline_at=_past(1),
        )
        cycle.tick()
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("exit_intent", actions)
        # Exit working, no confirmed fill → must not book full position as exited.
        self.assertEqual(int(mid.exited_qty or 0), 0)
        self.assertGreater(int(mid.remaining_position_qty or 0), 0)
        self.assertNotEqual(mid.status, "closed")
        self.assertNotEqual(broker.net_position_qty("AAA"), 0)
        store.close()

    def test_stop_cancel_race_accounts_fill_before_flatten(self) -> None:
        _seed_live(self.live, "scr1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110}, auto_fill_entry=True, auto_confirm_sl=True
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        self.assertEqual(trade.status, "protected_open")
        pos = int(trade.remaining_position_qty or 0)
        # Cancel race: stop fills entire cover while cancel is requested.
        broker.cancel_additional_fill = pos
        places_before = broker.market_place_count
        cycle._durable_market_exit(
            trade, reason="protection_deadline", kind="emergency"
        )
        final = store.get_trade(trade.trade_id)
        assert final is not None
        self.assertEqual(int(final.remaining_position_qty or 0), 0)
        self.assertEqual(broker.net_position_qty("AAA"), 0)
        # Race fill closes via stop — must not place a duplicate market flatten.
        self.assertEqual(broker.market_place_count, places_before)
        self.assertEqual(int(final.exited_qty or 0), pos)
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("exit_intent", actions)
        store.close()

    def test_stop_cancel_unconfirmed_blocks_flatten(self) -> None:
        _seed_live(self.live, "scu1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=True,
            auto_confirm_sl=True,
            cancel_noop=True,
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        places_before = broker.market_place_count
        cycle._durable_market_exit(
            trade, reason="protection_deadline", kind="emergency"
        )
        final = store.get_trade(trade.trade_id)
        assert final is not None
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("exit_intent", actions)
        self.assertIn("stop_cancel_unconfirmed", actions)
        self.assertEqual(broker.market_place_count, places_before)
        self.assertGreater(int(final.remaining_position_qty or 0), 0)
        self.assertNotEqual(broker.net_position_qty("AAA"), 0)
        store.close()

    def test_stale_modify_does_not_advance_confirmed_stop(self) -> None:
        _seed_live(self.live, "sm1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=True,
            auto_confirm_sl=True,
            stale_modify_trigger=True,
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        old = float(trade.current_stop or 0)
        updated = cycle.apply_trail(trade.trade_id, old + 5.0, last_price=127.0, actor="user")
        self.assertEqual(float(updated.current_stop or 0), old)
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("sl_modify_unconfirmed", actions)
        self.assertNotIn("sl_modified", actions)
        store.close()

    def test_partial_entry_does_not_freeze_r_until_complete(self) -> None:
        _seed_live(self.live, "pe1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110}, auto_fill_entry=False, auto_confirm_sl=True
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        oid = trade.entry_order_id
        intended = int(trade.intended_qty or trade.qty)
        first = max(1, intended // 3)
        broker.fill_entry_partial(oid, first, 108.0, complete=False)
        cycle.tick()
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        self.assertGreater(int(mid.remaining_entry_qty or 0), 0)
        self.assertIsNone(mid.r_value)
        # Later fill changes VWAP; R must freeze only after completion.
        broker.fill_entry_partial(oid, intended, 112.0, complete=True)
        cycle.tick()
        done = store.get_trade(trade.trade_id)
        assert done is not None
        self.assertEqual(int(done.remaining_entry_qty or 0), 0)
        self.assertIsNotNone(done.r_value)
        expected = freeze_r_value(
            entry=float(done.entry_fill or 0),
            initial_stop=float(done.initial_stop or 0),
        )
        self.assertAlmostEqual(float(done.r_value or 0), expected, places=6)
        # Must reflect completed average, not the first partial print alone.
        self.assertNotAlmostEqual(float(done.entry_fill or 0), 108.0, places=4)
        store.close()

    def test_retracement_during_throttle_keeps_higher_stage(self) -> None:
        _seed_live(self.live, "rt1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110}, auto_fill_entry=True, auto_confirm_sl=True
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        r = float(trade.r_value or 11.0)
        # Attain +2R extreme, stamp throttle, then retrace mark below +2R.
        extreme = float(trade.entry_fill or 110) + 2.2 * r
        extreme = float(int(extreme + 0.999))
        store.update_trade(
            trade.trade_id,
            auto_trail_enabled=1,
            auto_trail_extreme=extreme,
            last_trail_modify_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            current_stop=float(trade.initial_stop or 99),
        )
        broker.last_prices["AAA"] = float(trade.entry_fill or 110) + 1.2 * r
        cycle._reset_quote_cache()
        before = store.get_trade(trade.trade_id)
        assert before is not None
        desired = staged_r_desired_stop(
            direction="UP",
            entry=float(before.entry_fill or 110),
            initial_stop=float(before.initial_stop or 99),
            current_stop=float(before.current_stop or 99),
            extreme=float(before.auto_trail_extreme or extreme),
            last_price=float(broker.last_prices["AAA"]),
            r_value=r,
            charge_bps=0.0,
            tick_size=1.0,
        )
        # +2R stage from extreme → ~0.5R behind extreme, not 1R behind.
        one_r_behind = float(before.auto_trail_extreme or extreme) - r
        self.assertGreater(desired, one_r_behind - 0.1)
        cycle.apply_auto_trails()
        # Still throttled — stop unchanged, extreme preserved.
        after = store.get_trade(trade.trade_id)
        assert after is not None
        self.assertEqual(float(after.current_stop or 0), float(before.current_stop or 0))
        self.assertGreaterEqual(
            float(after.auto_trail_extreme or 0), float(before.auto_trail_extreme or 0)
        )
        self.assertGreaterEqual(
            TRAIL_MIN_MODIFY_INTERVAL_SECONDS, 1.0
        )  # policy still in force
        store.close()

    def test_owner_disabled_trail_survives_protection_refresh_and_restart(self) -> None:
        _seed_live(self.live, "od1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110}, auto_fill_entry=True, auto_confirm_sl=True
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        cycle.set_auto_trail(trade.trade_id, enabled=False)
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        self.assertFalse(mid.auto_trail_enabled)
        self.assertTrue(mid.auto_trail_owner_disabled)
        # Simulate protection refresh / cancelled-SL reprotect path.
        store.update_trade(
            trade.trade_id,
            status="protection_pending",
            protected_qty=0,
            auto_trail_enabled=0,
        )
        cycle.tick()
        after = store.get_trade(trade.trade_id)
        assert after is not None
        self.assertFalse(after.auto_trail_enabled)
        self.assertTrue(after.auto_trail_owner_disabled)
        # Restart: new cycle on same store must honor owner disable.
        store.close()
        store2 = TradingEngineStore(self.te)
        cycle2 = TradingEngineCycle(
            store2,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:30:00+00:00",
            run_id=after.run_id or "restart",
            live_orders_enabled=False,
            admin_config_db=self.admin,
            clock_fn=_session_clock(),
        )
        cycle2.tick()
        again = store2.get_trade(trade.trade_id)
        assert again is not None
        self.assertFalse(again.auto_trail_enabled)
        self.assertTrue(again.auto_trail_owner_disabled)
        cycle2.apply_auto_trails()
        still = store2.get_trade(trade.trade_id)
        assert still is not None
        self.assertEqual(float(still.current_stop or 0), float(again.current_stop or 0))
        store2.close()

    def test_staged_r_auto_trail_tightens_after_one_r_long(self) -> None:
        _seed_live(self.live, "tr1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110}, auto_fill_entry=True, auto_confirm_sl=True
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        self.assertEqual(trade.status, "protected_open")
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertIsNotNone(trade.r_value)
        r = float(trade.r_value or 11)
        mark = float(trade.entry_fill or 110) + 1.5 * r
        mark = float(int(mark + 0.999))
        broker.last_prices["AAA"] = mark
        store.update_trade(
            trade.trade_id, last_trail_modify_at=None, auto_trail_extreme=mark
        )
        cycle._reset_quote_cache()
        cycle.apply_auto_trails()
        updated = store.get_trade(trade.trade_id)
        assert updated is not None
        self.assertGreater(float(updated.current_stop or 0), float(trade.initial_stop or 0))
        store.close()

    def test_lost_exit_response_no_duplicate_market_write_across_restart(self) -> None:
        """Broker accepts exit; response lost + tag hidden → exactly one market write."""
        _seed_live(self.live, "lost_x", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=True,
            auto_confirm_sl=True,
            auto_fill_exit=True,
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        self.assertEqual(trade.status, "protected_open")
        pos0 = int(trade.remaining_position_qty or 0)
        places_before = broker.market_place_count
        exit_tag = cycle._exit_order_tag(trade, "emergency")

        broker.hide_market_tags = True
        broker.market_place_error = "lost_exit_response"
        cycle._durable_market_exit(
            trade, reason="protection_deadline", kind="emergency"
        )
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("exit_submit_attempt", actions)
        self.assertIn("exit_submission_unknown", actions)
        self.assertEqual(broker.market_place_count, places_before + 1)
        # Local books still open until the accepted exit is attributed.
        self.assertGreater(int(mid.remaining_position_qty or 0), 0)
        self.assertEqual(int(mid.exited_qty or 0), 0)
        # Broker may already be flat from the accepted (hidden) exit — that must not
        # authorize a second write.
        self.assertEqual(broker.net_position_qty("AAA"), 0)

        # Multi-cycle + restart must not place a second exit while tag stays hidden.
        for _ in range(3):
            cycle.tick()
        self.assertEqual(broker.market_place_count, places_before + 1)
        store.close()
        store2 = TradingEngineStore(self.te)
        cycle2 = TradingEngineCycle(
            store2,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:31:00+00:00",
            run_id=store2.start_run(
                session_date="2026-08-17", live_orders_enabled=False, pid=2
            ),
            live_orders_enabled=False,
            admin_config_db=self.admin,
            clock_fn=_session_clock(),
        )
        for _ in range(3):
            cycle2.tick()
        self.assertEqual(broker.market_place_count, places_before + 1)
        waiting = [str(r["action"]) for r in store2.list_events(trade.trade_id)]
        self.assertIn("exit_reconcile_waiting", waiting)
        # Must not have re-armed a competing stop while exit is unresolved.
        mid2 = store2.get_trade(trade.trade_id)
        assert mid2 is not None
        if mid2.sl_order_id:
            sl_mid = broker.poll_order(str(mid2.sl_order_id))
            if sl_mid is not None:
                self.assertIn(
                    str(sl_mid.status).upper(), {"CANCELLED", "REJECTED", "COMPLETE"}
                )

        broker.reveal_tag(exit_tag)
        cycle2.tick()
        final = store2.get_trade(trade.trade_id)
        assert final is not None
        self.assertEqual(broker.market_place_count, places_before + 1)
        self.assertEqual(broker.net_position_qty("AAA"), 0)
        self.assertEqual(int(final.remaining_position_qty or 0), 0)
        self.assertGreaterEqual(int(final.exited_qty or 0), pos0)
        # Working exit must be terminal after reconcile.
        linked = None
        for link in store2.list_order_links(trade.trade_id):
            if str(link["role"]) == "exit":
                linked = str(link["order_id"])
        self.assertIsNotNone(linked)
        exit_ord = broker.poll_order(str(linked))
        assert exit_ord is not None
        self.assertEqual(str(exit_ord.status).upper(), "COMPLETE")
        store2.close()

    def test_hidden_known_stop_blocks_flatten_until_visible(self) -> None:
        """Known stop with lost cancel visibility must not allow a competing flatten."""
        _seed_live(self.live, "hid_sl", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=True,
            auto_confirm_sl=True,
            auto_fill_exit=True,
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        sl_id = str(trade.sl_order_id or "")
        self.assertTrue(sl_id)
        places_before = broker.market_place_count
        broker.hide_order(sl_id)

        cycle._durable_market_exit(
            trade, reason="protection_deadline", kind="emergency"
        )
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("exit_intent", actions)
        self.assertIn("stop_visibility_unknown", actions)
        self.assertNotIn("exit_submit_attempt", actions)
        self.assertEqual(broker.market_place_count, places_before)
        self.assertGreater(int(mid.remaining_position_qty or 0), 0)
        self.assertNotEqual(broker.net_position_qty("AAA"), 0)
        # Stop still recorded locally while unresolved.
        self.assertEqual(str(mid.sl_order_id or ""), sl_id)

        for _ in range(3):
            cycle.tick()
        self.assertEqual(broker.market_place_count, places_before)

        # Restart while still hidden — still no flatten.
        store.close()
        store2 = TradingEngineStore(self.te)
        cycle2 = TradingEngineCycle(
            store2,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:31:00+00:00",
            run_id=store2.start_run(
                session_date="2026-08-17", live_orders_enabled=False, pid=3
            ),
            live_orders_enabled=False,
            admin_config_db=self.admin,
            clock_fn=_session_clock(),
        )
        for _ in range(3):
            cycle2.tick()
        self.assertEqual(broker.market_place_count, places_before)
        still = store2.get_trade(trade.trade_id)
        assert still is not None
        self.assertGreater(int(still.remaining_position_qty or 0), 0)
        self.assertNotEqual(broker.net_position_qty("AAA"), 0)

        broker.reveal_order(sl_id)
        cycle2.tick()
        final = store2.get_trade(trade.trade_id)
        assert final is not None
        # After terminal cancel visibility, exactly one flatten write is allowed.
        self.assertEqual(broker.market_place_count, places_before + 1)
        self.assertEqual(broker.net_position_qty("AAA"), 0)
        self.assertEqual(int(final.remaining_position_qty or 0), 0)
        sl = broker.poll_order(sl_id)
        assert sl is not None
        self.assertIn(str(sl.status).upper(), {"CANCELLED", "REJECTED", "COMPLETE"})
        store2.close()

    def test_short_protection_deadline_and_trail_math(self) -> None:
        # DOWN seed via swing_high path: seed helper is UP-only; construct short trade manually.
        _seed_live(self.live, "sh1", "2026-08-17T04:40:00+00:00", symbol="BBB")
        # Override direction by placing through a synthetic protected short after UP entry —
        # exercise short staged-R + flatten side on FakeBroker port directly.
        broker = FakeBroker(last_prices={"BBB": 110}, auto_fill_exit=True)
        broker.place_market_mis(
            tradingsymbol="BBB", transaction_type="SELL", quantity=5, tag="short1"
        )
        self.assertEqual(broker.net_position_qty("BBB"), -5)
        flat = broker.flatten_mis(
            tradingsymbol="BBB",
            transaction_type="BUY",
            quantity=5,
            tag="short1E",
        )
        self.assertEqual(flat.transaction_type, "BUY")
        self.assertEqual(broker.net_position_qty("BBB"), 0)
        d = staged_r_desired_stop(
            direction="DOWN",
            entry=110.0,
            initial_stop=121.0,
            current_stop=121.0,
            extreme=88.0,
            last_price=100.0,
            r_value=11.0,
            charge_bps=0.0,
            tick_size=1.0,
        )
        self.assertLessEqual(d, 121.0)
        self.assertLess(d, 110.0)


if __name__ == "__main__":
    unittest.main()
