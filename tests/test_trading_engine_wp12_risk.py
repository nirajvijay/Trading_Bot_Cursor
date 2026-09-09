"""WP-1.2 multi-cycle integration regressions (isolated FakeBroker / temp DBs only)."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from api.admin_config.defaults import DEFAULT_ADMIN_CONFIG_VALUES
from api.admin_config.store import AdminConfigStore
from trading_engine_broker import FakeBroker
from trading_engine_cycle import TradingEngineCycle
from trading_engine_risk import (
    estimated_cost_per_share,
    open_notional_total,
    reserved_setups_today,
    risk_snapshot,
    trade_remaining_risk,
)
from trading_engine_store import TradingEngineStore

from tests.test_trading_engine_cycle import SCHEMA, _seed_live, _session_clock


class Wp12RiskIntegrationTests(unittest.TestCase):
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
        cfg["max_concurrent_positions"] = 5.0
        cfg["max_filled_setups_per_day"] = 5.0
        cfg["daily_loss_cap_inr"] = 2995.0
        cfg["round_trip_charge_bps"] = 50.0
        cfg["estimated_slippage_bps"] = 0.0
        admin.update_config(cfg, actor="test")
        # Concurrency/fill increases wait for an explicit arm (cycle never auto-arms).
        admin.arm_effective_config(actor="test")
        admin.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _cycle(
        self,
        broker: Any,
        *,
        live_orders: bool = False,
        total_capital: float = 300_000.0,
        store: TradingEngineStore | None = None,
        pid: int = 1,
    ) -> tuple[TradingEngineStore, TradingEngineCycle]:
        if store is None:
            store = TradingEngineStore(self.te)
        run_id = store.start_run(
            session_date="2026-08-17",
            live_orders_enabled=live_orders,
            pid=pid,
            total_capital=total_capital,
        )
        cycle = TradingEngineCycle(
            store,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:30:00+00:00",
            run_id=run_id,
            live_orders_enabled=live_orders,
            admin_config_db=self.admin,
            clock_fn=_session_clock(),
        )
        return store, cycle

    def _seed_closed_filled(
        self,
        store: TradingEngineStore,
        *,
        setup_id: str,
        symbol: str,
        trade_id_suffix: str,
    ) -> None:
        trade = store.insert_candidate(
            setup_id=setup_id,
            continuation_rule_version="v1",
            session_date="2026-08-17",
            symbol=symbol,
            instrument_token=1,
            direction="UP",
            entry_estimate=110.0,
            tick_size=1.0,
            trigger_time="2026-08-17T10:01:16",
        )
        assert trade is not None
        store.update_trade(
            trade.trade_id,
            status="closed",
            qty=10,
            intended_qty=10,
            filled_qty=10,
            exited_qty=10,
            remaining_entry_qty=0,
            remaining_position_qty=0,
            protected_qty=0,
            entry_fill=110.0,
            exit_fill=99.0,
            initial_stop=99.0,
            current_stop=99.0,
            closed_loss_contribution=0.0,
            realised_pnl=0.0,
            notional=0.0,
            margin_blocked=0.0,
            broker_tag=f"te{trade_id_suffix}",
        )

    def test_partial_fill_keeps_pending_reservation_across_cycles(self) -> None:
        _seed_live(self.live, "p1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(last_prices={"AAA": 110}, auto_fill_entry=False)
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        self.assertEqual(trade.status, "entry_submitting")
        oid = trade.entry_order_id
        self.assertIsNotNone(oid)
        intended = int(trade.intended_qty or trade.qty)
        self.assertGreater(intended, 30)
        broker.fill_entry_partial(oid, 30, 110.0)
        cycle.tick()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertEqual(trade.filled_qty, 30)
        self.assertEqual(trade.remaining_entry_qty, intended - 30)
        risk_ps = abs(110.0 - float(trade.initial_stop or 99.0))
        cps = estimated_cost_per_share(110.0, cost_bps=50.0)
        self.assertAlmostEqual(
            trade_remaining_risk(trade),
            intended * (risk_ps + cps),
            places=4,
        )
        self.assertAlmostEqual(open_notional_total([trade]), intended * 110.0, places=4)

        # Restart: new cycle on same DB must keep pending reservation.
        _, cycle2 = self._cycle(broker, store=store, pid=2)
        cycle2.tick()
        again = store.get_trade(trade.trade_id)
        assert again is not None
        self.assertEqual(again.remaining_entry_qty, intended - 30)
        self.assertAlmostEqual(
            trade_remaining_risk(again),
            intended * (risk_ps + cps),
            places=4,
        )
        store.close()

    def test_pending_setup_slot_blocks_sixth_until_zero_fill_cancel(self) -> None:
        broker = FakeBroker(last_prices={"ZZZ": 110, "AFTER": 110}, auto_fill_entry=True)
        store, cycle = self._cycle(broker)
        for i in range(4):
            self._seed_closed_filled(
                store, setup_id=f"setup{i}", symbol=f"S{i}", trade_id_suffix=str(i)
            )

        _seed_live(self.live, "setup_pending", "2026-08-17T04:45:00+00:00", symbol="PEND")
        broker.last_prices["PEND"] = 110
        broker.auto_fill_entry = False
        cycle.tick()
        pending = [t for t in store.list_trades("2026-08-17") if t.setup_id == "setup_pending"][0]
        self.assertEqual(pending.status, "entry_submitting")
        self.assertEqual(int(pending.filled_qty), 0)
        self.assertEqual(reserved_setups_today(store.list_trades("2026-08-17")), 5)

        _seed_live(self.live, "setup_new", "2026-08-17T04:50:00+00:00", symbol="ZZZ")
        cycle.tick()
        blocked = [t for t in store.list_trades("2026-08-17") if t.setup_id == "setup_new"][0]
        self.assertEqual(blocked.status, "skipped")
        self.assertEqual(blocked.skip_reason, "daily_filled_setups_limit")

        # Broker cancel → engine reconcile (no manual status rewrite).
        self.assertIsNotNone(pending.entry_order_id)
        broker.cancel_order(pending.entry_order_id)
        cycle.tick()
        pending = store.get_trade(pending.trade_id)
        assert pending is not None
        self.assertEqual(pending.status, "rejected")
        self.assertEqual(int(pending.filled_qty), 0)
        self.assertEqual(reserved_setups_today(store.list_trades("2026-08-17")), 4)

        _seed_live(self.live, "setup_after", "2026-08-17T04:51:00+00:00", symbol="AFTER")
        broker.auto_fill_entry = True
        cycle.tick()
        after = [t for t in store.list_trades("2026-08-17") if t.setup_id == "setup_after"][0]
        self.assertIn(
            after.status,
            {"protected_open", "entry_filled", "protection_pending", "entry_submitting"},
        )
        store.close()

    def test_cancellation_time_fill_retains_setup_slot(self) -> None:
        broker = FakeBroker(
            last_prices={"PEND": 110, "ZZZ": 110},
            auto_fill_entry=False,
            cancel_additional_fill=3,
        )
        store, cycle = self._cycle(broker)
        for i in range(4):
            self._seed_closed_filled(
                store, setup_id=f"setup{i}", symbol=f"S{i}", trade_id_suffix=str(i)
            )
        _seed_live(self.live, "setup_pending", "2026-08-17T04:45:00+00:00", symbol="PEND")
        cycle.tick()
        pending = [t for t in store.list_trades("2026-08-17") if t.setup_id == "setup_pending"][0]
        self.assertEqual(reserved_setups_today(store.list_trades("2026-08-17")), 5)
        broker.cancel_order(pending.entry_order_id)
        cycle.tick()
        pending = store.get_trade(pending.trade_id)
        assert pending is not None
        self.assertGreaterEqual(int(pending.filled_qty), 3)
        self.assertNotEqual(pending.status, "rejected")
        # Cancellation-time fills retain the daily setup slot.
        self.assertEqual(reserved_setups_today(store.list_trades("2026-08-17")), 5)
        _seed_live(self.live, "setup_new", "2026-08-17T04:50:00+00:00", symbol="ZZZ")
        cycle.tick()
        blocked = [t for t in store.list_trades("2026-08-17") if t.setup_id == "setup_new"][0]
        self.assertEqual(blocked.skip_reason, "daily_filled_setups_limit")
        store.close()

    def test_live_unknown_margin_blocks_and_no_demo_leverage_on_fill(self) -> None:
        _seed_live(self.live, "live1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=True,
            margins_unavailable=True,
            remaining_capital=300_000.0,
        )
        store, cycle = self._cycle(broker, live_orders=True)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        self.assertEqual(trade.status, "rejected")
        self.assertEqual(trade.reject_reason, "margin_unavailable")
        store.close()

        _seed_live(self.live, "live2", "2026-08-17T04:41:00+00:00", symbol="BBB")
        broker2 = FakeBroker(
            last_prices={"BBB": 110},
            auto_fill_entry=True,
            available_margin=50_000.0,
            remaining_capital=300_000.0,
            demo_leverage=5.0,
        )
        store2, cycle2 = self._cycle(broker2, live_orders=True, pid=3)
        cycle2.tick()
        trade2 = [t for t in store2.list_trades("2026-08-17") if t.setup_id == "live2"][0]
        self.assertIn(
            trade2.status,
            {"protected_open", "entry_filled", "protection_pending", "partial_entry"},
        )
        if trade2.filled_qty > 0 and trade2.entry_fill:
            reserved = int(trade2.filled_qty) + int(trade2.remaining_entry_qty or 0)
            expected = float(reserved) * float(trade2.entry_fill)
            self.assertAlmostEqual(float(trade2.margin_blocked), expected, places=2)
            # Must not be demo-levered (expected/5).
            self.assertGreater(float(trade2.margin_blocked), expected / 5.0 + 1.0)
        store2.close()

    def test_be_stop_and_partial_loss_reduce_daily_budget(self) -> None:
        _seed_live(self.live, "be1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(last_prices={"AAA": 110}, auto_fill_entry=True)
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        self.assertEqual(trade.status, "protected_open")
        updated = cycle.apply_trail(trade.trade_id, 110.0, last_price=112.0)
        cps = estimated_cost_per_share(110.0, cost_bps=50.0)
        qty = int(updated.remaining_position_qty or updated.qty)
        self.assertAlmostEqual(trade_remaining_risk(updated), qty * cps, places=4)

        filled = int(updated.filled_qty or updated.qty)
        half = max(1, filled // 2)
        broker.fill_sl_partial(updated.sl_order_id, half, 99.0, complete=False)
        cycle.tick()
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        self.assertGreater(mid.exited_qty, 0)
        self.assertGreater(mid.remaining_position_qty, 0)
        self.assertGreater(mid.closed_loss_contribution, 0.0)
        snap = risk_snapshot(
            store.list_trades("2026-08-17"), daily_loss_cap=2995.0
        )
        self.assertGreaterEqual(snap.closed_loss_today, mid.closed_loss_contribution)
        self.assertLess(
            snap.remaining_daily, 2995.0 - mid.closed_loss_contribution + 1.0
        )
        store.close()

    def test_allocated_capital_from_admin_drives_sizing(self) -> None:
        admin = AdminConfigStore(self.admin)
        cfg = dict(admin.load_active_payload())
        cfg["allocated_capital_inr"] = 5_000.0
        cfg["aggregate_notional_cap_equals_allocated_capital"] = 1.0
        admin.update_config(cfg, actor="test")
        admin.arm_effective_config(actor="test")
        admin.close()

        _seed_live(self.live, "cap1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(last_prices={"AAA": 110}, auto_fill_entry=True)
        store, cycle = self._cycle(broker, total_capital=300_000.0)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        self.assertGreater(trade.qty, 0)
        self.assertLessEqual(float(trade.notional), 5_000.0 + 1e-6)
        store.close()

    def test_cancel_race_keeps_cancellation_time_fill_reserved(self) -> None:
        _seed_live(self.live, "race1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=False,
            cancel_additional_fill=5,
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        oid = trade.entry_order_id
        broker.fill_entry_partial(oid, 20, 110.0)
        cycle.tick()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertEqual(trade.filled_qty, 20)
        self.assertGreater(trade.remaining_entry_qty, 0)

        # Cancellation race via broker cancel + engine reconcile (not raw DB rewrite).
        broker.cancel_order(oid)
        cycle.tick()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertGreaterEqual(int(trade.filled_qty), 25)
        self.assertEqual(int(trade.remaining_entry_qty), 0)
        self.assertAlmostEqual(
            open_notional_total([trade]),
            float(trade.remaining_position_qty) * 110.0,
            places=4,
        )
        store.close()

    def test_provisional_loss_preserves_confirmed_then_reconciles(self) -> None:
        _seed_live(self.live, "prov1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(last_prices={"AAA": 110}, auto_fill_entry=True)
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        filled = int(trade.filled_qty or trade.qty)
        half = max(1, filled // 2)
        # Confirmed partial loss.
        broker.fill_sl_partial(trade.sl_order_id, half, 99.0, complete=False)
        cycle.tick()
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        confirmed_loss = float(mid.closed_loss_contribution)
        self.assertGreater(confirmed_loss, 0.0)
        committed_before = trade_remaining_risk(mid)
        self.assertGreater(committed_before, 0.0)

        # Further exit with missing price must not zero confirmed loss or release risk.
        rem = int(mid.remaining_position_qty or 0)
        self.assertGreater(rem, 0)
        broker.fill_sl_partial(mid.sl_order_id, half + rem, price=None, complete=True)
        cycle.tick()
        provisional = store.get_trade(trade.trade_id)
        assert provisional is not None
        self.assertTrue(int(getattr(provisional, "pnl_provisional", 0) or 0))
        self.assertEqual(float(provisional.closed_loss_contribution), confirmed_loss)
        self.assertGreater(trade_remaining_risk(provisional), 0.0)

        # Restart preserves state.
        _, cycle2 = self._cycle(broker, store=store, pid=9)
        cycle2.tick()
        restarted = store.get_trade(trade.trade_id)
        assert restarted is not None
        self.assertEqual(float(restarted.closed_loss_contribution), confirmed_loss)
        self.assertTrue(int(getattr(restarted, "pnl_provisional", 0) or 0))

        # Authoritative reconciliation of the missing price.
        broker.set_order_average_price(restarted.sl_order_id, 99.0)
        cycle2.tick()
        final = store.get_trade(trade.trade_id)
        assert final is not None
        self.assertFalse(int(getattr(final, "pnl_provisional", 0) or 0))
        self.assertGreaterEqual(float(final.closed_loss_contribution), confirmed_loss)
        store.close()

    def test_capital_increase_waits_for_next_arm(self) -> None:
        admin = AdminConfigStore(self.admin)
        cfg = dict(admin.load_active_payload())
        cfg["allocated_capital_inr"] = 300_000.0
        admin.update_config(cfg, actor="test")
        admin.arm_effective_config(actor="test")
        admin.close()

        _seed_live(self.live, "arm1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(last_prices={"AAA": 110, "BBB": 110}, auto_fill_entry=True)
        store, cycle = self._cycle(broker, total_capital=300_000.0)
        # Mid-session capital cut is Saved but Effective waits for next arm.
        admin = AdminConfigStore(self.admin)
        cfg = dict(admin.load_active_payload())
        cfg["allocated_capital_inr"] = 5_000.0
        admin.update_config(cfg, actor="test")
        self.assertEqual(admin.load_effective_payload()["allocated_capital_inr"], 300_000.0)
        admin.close()
        # Same cycle (no re-arm) still sizes against prior Effective capital.
        _seed_live(self.live, "arm2", "2026-08-17T04:41:00+00:00", symbol="BBB")
        cycle.tick()
        t2 = [t for t in store.list_trades("2026-08-17") if t.setup_id == "arm2"][0]
        # With 300k effective, notional can exceed the unsaved-as-effective 5k.
        self.assertGreater(float(t2.notional), 5_000.0)
        row = store._conn.execute(
            "SELECT daily_loss_cap_inr, admin_config_version_id FROM trades WHERE trade_id = ?",
            (t2.trade_id,),
        ).fetchone()
        self.assertEqual(float(row["daily_loss_cap_inr"]), 2995.0)
        self.assertTrue(str(row["admin_config_version_id"]))
        store.close()

    def test_save_increase_restart_does_not_auto_arm(self) -> None:
        """Saved capital increase survives crash/restart; Effective waits for explicit arm."""
        admin = AdminConfigStore(self.admin)
        cfg = dict(admin.load_active_payload())
        cfg["allocated_capital_inr"] = 100_000.0
        admin.update_config(cfg, actor="test")
        admin.arm_effective_config(actor="test")
        self.assertEqual(admin.load_effective_payload()["allocated_capital_inr"], 100_000.0)

        cfg = dict(admin.load_active_payload())
        cfg["allocated_capital_inr"] = 250_000.0
        admin.update_config(cfg, actor="test")
        self.assertEqual(admin.load_active_payload()["allocated_capital_inr"], 250_000.0)
        self.assertEqual(admin.load_effective_payload()["allocated_capital_inr"], 100_000.0)
        admin.close()

        # Simulate crash/restart: new cycle construction must not promote Saved.
        broker = FakeBroker(last_prices={"AAA": 110}, auto_fill_entry=False)
        store, cycle = self._cycle(broker, total_capital=100_000.0)
        admin = AdminConfigStore(self.admin)
        self.assertEqual(admin.load_effective_payload()["allocated_capital_inr"], 100_000.0)
        self.assertEqual(admin.load_active_payload()["allocated_capital_inr"], 250_000.0)

        admin.arm_effective_config(actor="test_explicit_arm")
        self.assertEqual(admin.load_effective_payload()["allocated_capital_inr"], 250_000.0)
        logs = admin.list_audit(limit=5)
        self.assertTrue(
            any(
                str(r["action"]) == "arm_effective_config"
                and "explicit_arm_boundary" in str(r.get("detail") or "")
                for r in logs
            )
        )
        admin.close()
        store.close()
        del cycle

    def test_trade_cost_profile_persists_partial_restart_close(self) -> None:
        """Stamped charge/slippage survive partial exit, restart, and authoritative close."""
        admin = AdminConfigStore(self.admin)
        cfg = dict(admin.load_active_payload())
        # Tightening vs setUp (50/0) applies immediately for new accepts.
        cfg["round_trip_charge_bps"] = 80.0
        cfg["estimated_slippage_bps"] = 15.0
        admin.update_config(cfg, actor="test")
        self.assertEqual(admin.load_effective_payload()["round_trip_charge_bps"], 80.0)
        self.assertEqual(admin.load_effective_payload()["estimated_slippage_bps"], 15.0)
        admin.close()

        _seed_live(self.live, "prof1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(last_prices={"AAA": 110}, auto_fill_entry=True)
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        self.assertEqual(float(trade.charge_bps or 0), 80.0)
        self.assertEqual(float(trade.slippage_bps or 0), 15.0)

        # Mid-flight admin change must not rewrite the open trade's stamped profile.
        admin = AdminConfigStore(self.admin)
        cfg = dict(admin.load_active_payload())
        cfg["round_trip_charge_bps"] = 5.0
        cfg["estimated_slippage_bps"] = 1.0
        admin.update_config(cfg, actor="test")
        admin.arm_effective_config(actor="test")
        admin.close()

        filled = int(trade.filled_qty or trade.qty)
        half = max(1, filled // 2)
        broker.fill_sl_partial(trade.sl_order_id, half, 99.0, complete=False)
        cycle.tick()
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        self.assertEqual(float(mid.charge_bps or 0), 80.0)
        self.assertEqual(float(mid.slippage_bps or 0), 15.0)

        _, cycle2 = self._cycle(broker, store=store, pid=11)
        cycle2.tick()
        restarted = store.get_trade(trade.trade_id)
        assert restarted is not None
        self.assertEqual(float(restarted.charge_bps or 0), 80.0)
        self.assertEqual(float(restarted.slippage_bps or 0), 15.0)

        rem = int(restarted.remaining_position_qty or 0)
        broker.fill_sl_partial(
            restarted.sl_order_id, half + rem, 99.0, complete=True
        )
        cycle2.tick()
        final = store.get_trade(trade.trade_id)
        assert final is not None
        self.assertEqual(final.status, "closed")
        self.assertEqual(float(final.charge_bps or 0), 80.0)
        self.assertEqual(float(final.slippage_bps or 0), 15.0)
        # Folded loss includes stamped charges (80 bps), not the later 5 bps admin.
        from trading_engine_risk import charge_per_share, fold_exit_costs_into_loss

        qty = int(final.exited_qty or final.filled_qty or 0)
        entry = float(final.entry_fill or 110.0)
        expected = fold_exit_costs_into_loss(
            price_pnl=float(final.realised_pnl),
            qty=qty,
            entry=entry,
            charge_bps=80.0,
        )
        self.assertAlmostEqual(float(final.closed_loss_contribution), expected, places=4)
        self.assertGreater(
            float(final.closed_loss_contribution),
            abs(min(0.0, float(final.realised_pnl)))
            + charge_per_share(entry, charge_bps=5.0) * qty,
        )
        store.close()

    def test_mixed_price_exit_qty_exact_through_restart(self) -> None:
        """Confirmed/unpriced exit qty stays exact; high confirmed px cannot release slip."""
        from trading_engine_risk import (
            _exited_confirmed_qty,
            exited_cost_reservation,
            slippage_per_share,
        )

        admin = AdminConfigStore(self.admin)
        cfg = dict(admin.load_active_payload())
        cfg["round_trip_charge_bps"] = 0.0
        cfg["estimated_slippage_bps"] = 50.0
        admin.update_config(cfg, actor="test")
        admin.arm_effective_config(actor="test")
        admin.close()

        _seed_live(self.live, "mix1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=False,
            auto_confirm_sl=True,
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        qty = int(trade.intended_qty or trade.qty)
        self.assertGreaterEqual(qty, 4)
        broker.fill_entry_partial(trade.entry_order_id, qty, 110.0, complete=True)
        cycle.tick()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        first_sl = trade.sl_order_id
        assert first_sl is not None

        # Slice A: confirmed at a *higher* exit price than the structural stop estimate.
        exit_a = max(1, qty // 3)
        high_px = 150.0
        broker.fill_sl_partial(first_sl, exit_a, high_px, complete=False)
        cycle.tick()
        broker.cancel_order(first_sl)
        cycle.tick()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        second_sl = trade.sl_order_id
        self.assertIsNotNone(second_sl)
        self.assertNotEqual(second_sl, first_sl)

        # Slice B: filled but unpriced (missing average).
        exit_b = int(trade.remaining_position_qty or 0)
        self.assertGreater(exit_b, 0)
        broker.fill_sl_partial(second_sl, exit_b, price=None, complete=True)
        cycle.tick()
        # Repeated polling must not drift qty stamps.
        for _ in range(3):
            cycle.tick()

        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        self.assertEqual(int(mid.exit_confirmed_qty or 0), exit_a)
        self.assertEqual(int(mid.exit_est_qty or 0), exit_b)
        self.assertEqual(int(mid.exited_qty or 0), exit_a + exit_b)
        self.assertEqual(_exited_confirmed_qty(mid), exit_a)
        # Durable per-order link totals agree with trade stamps.
        link_c, link_e = store.exit_execution_qty_totals(mid.trade_id)
        self.assertEqual(link_c, exit_a)
        self.assertEqual(link_e, exit_b)
        # High confirmed notional must not shrink unpriced slip reservation via ₹-ratio.
        sps = slippage_per_share(110.0, slippage_bps=50.0)
        ratio_inflated = int(
            round(
                float(mid.exited_qty)
                * (float(mid.exit_value) / (float(mid.exit_value) + float(mid.exit_value_est)))
            )
        )
        self.assertGreater(ratio_inflated, exit_a)
        self.assertAlmostEqual(exited_cost_reservation(mid), exit_b * sps, places=6)

        # Restart: exact qty + reservation survive without broker re-inference from ₹.
        _, cycle2 = self._cycle(broker, store=store, pid=21)
        for _ in range(2):
            cycle2.tick()
        restarted = store.get_trade(trade.trade_id)
        assert restarted is not None
        self.assertEqual(int(restarted.exit_confirmed_qty or 0), exit_a)
        self.assertEqual(int(restarted.exit_est_qty or 0), exit_b)
        self.assertEqual(_exited_confirmed_qty(restarted), exit_a)
        self.assertAlmostEqual(
            exited_cost_reservation(restarted), exit_b * sps, places=6
        )

        # Final price reconciliation clears unpriced qty and releases only that slip.
        broker.set_order_average_price(second_sl, 99.0)
        for _ in range(4):
            cycle2.tick()
        final = store.get_trade(trade.trade_id)
        assert final is not None
        self.assertEqual(final.status, "closed")
        self.assertEqual(int(final.exit_confirmed_qty or 0), exit_a + exit_b)
        self.assertEqual(int(final.exit_est_qty or 0), 0)
        self.assertEqual(_exited_confirmed_qty(final), exit_a + exit_b)
        self.assertAlmostEqual(exited_cost_reservation(final), 0.0, places=6)
        store.close()

    def test_hidden_exit_order_preserves_durable_snapshot(self) -> None:
        """Two exits → hide one → poll/restart → restore with price correction; no erase/double-count."""
        from trading_engine_risk import exited_cost_reservation, trade_remaining_risk

        admin = AdminConfigStore(self.admin)
        cfg = dict(admin.load_active_payload())
        cfg["round_trip_charge_bps"] = 0.0
        cfg["estimated_slippage_bps"] = 0.0
        admin.update_config(cfg, actor="test")
        admin.arm_effective_config(actor="test")
        admin.close()

        _seed_live(self.live, "hide1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=False,
            auto_confirm_sl=True,
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        qty = int(trade.intended_qty or trade.qty)
        self.assertGreaterEqual(qty, 4)
        broker.fill_entry_partial(trade.entry_order_id, qty, 110.0, complete=True)
        cycle.tick()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        first_sl = trade.sl_order_id
        assert first_sl is not None

        exit_a = max(1, qty // 3)
        exit_b = qty - exit_a
        broker.fill_sl_partial(first_sl, exit_a, 100.0, complete=False)
        cycle.tick()
        broker.cancel_order(first_sl)
        cycle.tick()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        second_sl = trade.sl_order_id
        self.assertIsNotNone(second_sl)
        self.assertNotEqual(second_sl, first_sl)
        broker.fill_sl(second_sl, 95.0)
        for _ in range(4):
            cycle.tick()

        both = store.get_trade(trade.trade_id)
        assert both is not None
        self.assertEqual(both.status, "closed")
        expected_exit_value = exit_a * 100.0 + exit_b * 95.0
        expected_pnl = expected_exit_value - qty * 110.0
        self.assertEqual(int(both.exit_confirmed_qty or 0), qty)
        self.assertEqual(int(both.exit_est_qty or 0), 0)
        self.assertAlmostEqual(float(both.exit_value), expected_exit_value, places=6)
        self.assertEqual(float(both.exit_value_est), 0.0)
        self.assertAlmostEqual(float(both.realised_pnl), expected_pnl, places=6)
        link_cv, link_ev, link_cq, link_eq = store.exit_execution_totals(both.trade_id)
        self.assertEqual(link_cq, qty)
        self.assertEqual(link_eq, 0)
        self.assertAlmostEqual(link_cv, expected_exit_value, places=6)
        self.assertEqual(link_ev, 0.0)
        reserved_closed = exited_cost_reservation(both)
        self.assertEqual(reserved_closed, 0.0)

        # Hide the first exit order from broker visibility.
        broker.hide_order(first_sl)
        self.assertIsNone(broker.poll_order(first_sl))
        for _ in range(3):
            cycle.tick()
        hidden = store.get_trade(trade.trade_id)
        assert hidden is not None
        self.assertEqual(int(hidden.exited_qty or 0), qty)
        self.assertEqual(int(hidden.exit_confirmed_qty or 0), qty)
        self.assertAlmostEqual(float(hidden.exit_value), expected_exit_value, places=6)
        self.assertAlmostEqual(float(hidden.realised_pnl), expected_pnl, places=6)
        self.assertAlmostEqual(exited_cost_reservation(hidden), 0.0, places=6)
        # Snapshot still holds both orders; visible-only sum would drop exit_a * 100.
        snap_cv, _se, snap_cq, snap_eq = store.exit_execution_totals(hidden.trade_id)
        self.assertEqual(snap_cq, qty)
        self.assertEqual(snap_eq, 0)
        self.assertAlmostEqual(snap_cv, expected_exit_value, places=6)
        self.assertGreater(snap_cv, exit_b * 95.0)

        # Restart while still hidden.
        _, cycle2 = self._cycle(broker, store=store, pid=31)
        for _ in range(3):
            cycle2.tick()
        restarted = store.get_trade(trade.trade_id)
        assert restarted is not None
        self.assertEqual(int(restarted.exit_confirmed_qty or 0), qty)
        self.assertAlmostEqual(float(restarted.exit_value), expected_exit_value, places=6)
        self.assertAlmostEqual(float(restarted.realised_pnl), expected_pnl, places=6)
        self.assertEqual(trade_remaining_risk(restarted), 0.0)

        # Authoritative price correction appears with the order.
        corrected_a = 98.0
        raw = broker.orders[first_sl]
        from trading_engine_broker import _copy_order

        broker.orders[first_sl] = _copy_order(raw, average_price=corrected_a)
        broker.reveal_order(first_sl)
        for _ in range(4):
            cycle2.tick()
        final = store.get_trade(trade.trade_id)
        assert final is not None
        corrected_exit_value = exit_a * corrected_a + exit_b * 95.0
        corrected_pnl = corrected_exit_value - qty * 110.0
        self.assertEqual(int(final.exit_confirmed_qty or 0), qty)
        self.assertEqual(int(final.exited_qty or 0), qty)
        self.assertAlmostEqual(float(final.exit_value), corrected_exit_value, places=6)
        self.assertAlmostEqual(float(final.realised_pnl), corrected_pnl, places=6)
        # No double-count: durable totals match a single pass over both orders.
        final_cv, final_ev, final_cq, final_eq = store.exit_execution_totals(final.trade_id)
        self.assertEqual(final_cq, qty)
        self.assertEqual(final_eq, 0)
        self.assertEqual(final_ev, 0.0)
        self.assertAlmostEqual(final_cv, corrected_exit_value, places=6)
        store.close()


if __name__ == "__main__":
    unittest.main()
