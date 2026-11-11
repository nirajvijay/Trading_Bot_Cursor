"""The GUC sleeve in the ARB engine: its own switch, regime risk, budget and
day stop; ORB's controls see only ORB; the entry path is ORB's.

Same harness as test_engine_arb_orb (FakeBroker, the real store, a fake
clock). Risk per share is 2.50 (trigger 110, stop 107.5) unless noted.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from engine_arb import (
    ALLOWED_GUC_RISK_SCALES,
    ARB_EXTRA_KEY,
    REASON_GUC_DAY_STOP,
    REASON_GUC_DISABLED,
    REASON_GUC_OPEN_CAP,
    REASON_GUC_OUTSIDE_WINDOW,
    ArbSettings,
    InvalidArbSettings,
    guc_sleeve,
    orb_sleeve,
    validate_settings,
)
from engine_config import STRATEGY_ARB, SessionRiskConfig, arb_session_config
from engine_core import EVENT_ARB_DAY_HALT, REASON_ARB_DAY_HALTED
from engine_exit import CloseReason
from engine_feed import FeedHealth
from engine_types import ExecutionState, Position
from tests.test_engine_arb_orb import DAY, IST, OrbEngineTestCase
from trading_engine_types import STRATEGY_GUC, STRATEGY_ORB, TriggerCandidate

class StaleFeed:
    def check(self, *, now=None):
        return FeedHealth(healthy=False, age_seconds=30.0)


GAP_DAY = (100.5, 100.0)      # NIFTY open +0.5 %
FLAT_DAY = (100.1, 100.0)     # +0.1 %: an ordinary day


class GucEngineTestCase(OrbEngineTestCase):
    scale = 0.5          # ORB at 50 %, as live
    guc_scale = 1.0
    guc_enabled = True
    settings_overrides: dict = {}

    def setUp(self) -> None:
        super().setUp()
        self.config = SessionRiskConfig(
            total_capital_rupees=500_000.0,
            strategy_mode=STRATEGY_ARB,
            arb=ArbSettings(
                risk_scale=self.scale,
                guc_enabled=self.guc_enabled,
                guc_risk_scale=self.guc_scale,
                **self.settings_overrides,
            ),
        )
        self.broker.index_ohlc_value = GAP_DAY

    def guc(self, setup_id: str, symbol: str, *, price: float = 110.0, stop: object = 107.5,
            gap: float = 0.02, confirm: bool = False, **overrides) -> TriggerCandidate:
        fields = dict(
            setup_id=setup_id,
            continuation_rule_version="guc_v1",
            session_date=DAY,
            tradingsymbol=symbol,
            instrument_token=1,
            direction="UP",
            trigger_price=price,
            pullback_swing_high=None,
            pullback_swing_low=None,
            tick_size=0.05,
            buffer_ticks=0,
            trigger_exchange_ts=self.clock.now.astimezone(IST).isoformat(timespec="seconds"),
            created_at=self.clock.now.isoformat(timespec="seconds"),
            last_price=price,
            strategy=STRATEGY_GUC,
            planned_stop=stop,
            orb_level=price,
            orb_rvol=2.0,
            orb_atr14=7.142857,
            orb_rank=1,
            guc_gap_pct=gap,
            guc_market_gap=0.004,
            guc_confirm_fill=confirm,
        )
        fields.update(overrides)
        return TriggerCandidate(**fields)

    def save_closed_guc(self, trade_id: str, symbol: str, realised: float) -> None:
        self.store.save(
            Position(
                trade_id=trade_id,
                candidate=self.guc(trade_id, symbol),
                state=ExecutionState.CLOSED,
                qty=100,
                entry_price=110.0,
                stop_price=107.5,
                realised_pnl=realised,
                extra={ARB_EXTRA_KEY: {"strategy": STRATEGY_GUC}},
            )
        )


class DisabledTests(GucEngineTestCase):
    guc_enabled = False

    def test_off_by_default_a_guc_trigger_is_skipped_and_orb_trades_as_before(self) -> None:
        self.assertFalse(ArbSettings().guc_enabled)
        engine = self.engine()
        self.run_triggers(engine, self.guc("g1", "AAA"), self.orb("o1", "BBB"))
        self.assertEqual(self.skip_reason("g1"), REASON_GUC_DISABLED)
        self.assertEqual(self.arb("g1")["strategy"], STRATEGY_GUC)
        stored = self.store.get("o1")
        self.assertEqual((stored.state, stored.extra["risk_cap_rupees"]), (ExecutionState.PROTECTED, 1250))


class EntryTests(GucEngineTestCase):
    def test_a_gap_day_trade_risks_5000_with_the_fixed_stop_through_orbs_path(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.guc("g1", "AAA", confirm=True))
        stored = self.store.get("g1")
        self.assertEqual(stored.state, ExecutionState.PROTECTED)
        self.assertEqual((stored.stop_price, stored.qty, stored.extra["risk_cap_rupees"]), (107.5, 2000, 5000))
        info = self.arb("g1")
        self.assertEqual((info["strategy"], info["risk_used"], info["gap_day"]), (STRATEGY_GUC, 5000, True))
        self.assertAlmostEqual(info["market_gap"], 0.005, places=9)
        self.assertTrue(info["guc"]["confirm_fill"])
        self.assertNotIn("half_size", info)
        # Same orders as an ORB trade: a market entry, then an SL stop at the planned price.
        self.assertEqual(self.broker.orders[stored.stop_order_id].trigger_price, 107.5)
        self.assertEqual(self.context.calls, [])

    def test_an_ordinary_day_risks_half(self) -> None:
        self.broker.index_ohlc_value = FLAT_DAY
        engine = self.engine()
        self.run_triggers(engine, self.guc("g1", "AAA"))
        self.assertEqual(self.store.get("g1").extra["risk_cap_rupees"], 2500)
        self.assertFalse(self.arb("g1")["gap_day"])

    def test_an_unreadable_index_is_an_ordinary_day(self) -> None:
        self.broker.index_ohlc_value = None
        engine = self.engine()
        self.run_triggers(engine, self.guc("g1", "AAA"))
        self.assertEqual(self.store.get("g1").extra["risk_cap_rupees"], 2500)
        self.assertIsNone(self.arb("g1")["market_gap"])

    def test_the_index_is_read_once_a_day(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.guc("g1", "AAA"))
        self.broker.index_ohlc_value = FLAT_DAY
        self.run_triggers(engine, self.guc("g2", "BBB"))
        self.assertEqual(self.store.get("g2").extra["risk_cap_rupees"], 5000)

    def test_cutoff_at_1101(self) -> None:
        engine = self.engine()
        late = self.guc("g1", "AAA", trigger_exchange_ts=f"{DAY}T11:01:00+05:30")
        self.run_triggers(engine, late)
        self.assertEqual(self.skip_reason("g1"), REASON_GUC_OUTSIDE_WINDOW)

    def test_a_stock_already_open_for_orb_blocks_the_guc_trigger(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.orb("o1", "AAA"))
        self.run_triggers(engine, self.guc("g1", "AAA"))
        self.assertEqual(self.skip_reason("g1"), "symbol_already_open")

    def test_no_stop_no_trade(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.guc("g1", "AAA", stop=None))
        self.assertEqual(self.skip_reason("g1"), "arb_guc_no_stop")


class HalfScaleTests(GucEngineTestCase):
    guc_scale = 0.5

    def test_the_trial_session_risks_2500_and_1250(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.guc("g1", "AAA"))
        self.assertEqual(self.store.get("g1").extra["risk_cap_rupees"], 2500)
        self.broker.index_ohlc_value = FLAT_DAY
        other = self.engine()
        self.run_triggers(other, self.guc("g2", "BBB"))
        self.assertEqual(self.store.get("g2").extra["risk_cap_rupees"], 1250)


class BudgetTests(GucEngineTestCase):
    settings_overrides = {"guc_open_risk_cap": 7000.0}

    def test_the_open_cap_trims_then_blocks(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.guc("g1", "AAA"))
        self.run_triggers(engine, self.guc("g2", "BBB"))
        self.assertEqual(self.store.get("g2").extra["risk_cap_rupees"], 2000)   # 7000 - 5000
        self.run_triggers(engine, self.guc("g3", "CCC"))
        self.assertEqual(self.skip_reason("g3"), REASON_GUC_OPEN_CAP)

    def test_guc_positions_do_not_use_orbs_open_cap(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.guc("g1", "AAA"))                 # 5,000 of GUC risk open
        self.run_triggers(engine, self.orb("o1", "BBB"))
        self.assertEqual(self.store.get("o1").extra["risk_cap_rupees"], 1250)   # ORB 50 %, no trimming
        self.assertEqual(self.arb("o1")["live_risk"], 0)


class DayStopTests(GucEngineTestCase):
    def test_guc_losses_stop_guc_but_not_orb(self) -> None:
        self.save_closed_guc("old", "DDD", realised=-10_500.0)
        engine = self.engine()
        self.run_triggers(engine, self.guc("g1", "AAA"), self.orb("o1", "BBB"))
        self.assertEqual(self.skip_reason("g1"), REASON_GUC_DAY_STOP)
        stored = self.store.get("o1")
        # ORB: full 50 % size, not halved by the GUC loser; engine not paused.
        self.assertEqual((stored.state, stored.extra["risk_cap_rupees"]), (ExecutionState.PROTECTED, 1250))
        self.assertFalse(self.arb("o1")["half_size"])
        self.assertFalse(engine.entries_paused)
        self.assertIsNone(engine.shutdown_reason)
        self.assertTrue(engine.arb_snapshot()["guc"]["day_stopped"])

    def test_a_guc_loss_beyond_the_closed_loss_backstop_does_not_shut_the_day(self) -> None:
        # ORB's backstop is 3,000 at 50 %; a 7,000 GUC loss must not trip it.
        self.save_closed_guc("old", "DDD", realised=-7_000.0)
        engine = self.engine()
        engine.tick()
        self.assertFalse(engine.breached)
        self.assertIsNone(engine.shutdown_reason)
        self.run_triggers(engine, self.orb("o1", "BBB"))
        self.assertEqual(self.store.get("o1").state, ExecutionState.PROTECTED)

    def test_an_orb_loser_does_not_halve_guc(self) -> None:
        self.save_closed("old", "DDD", realised=-800.0)
        engine = self.engine()
        self.run_triggers(engine, self.guc("g1", "AAA"))
        self.assertEqual(self.store.get("g1").extra["risk_cap_rupees"], 5000)


class CapitalTests(GucEngineTestCase):
    settings_overrides = {"guc_margin_budget": 30_000.0}

    def test_the_guc_margin_budget_caps_the_size(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.guc("g1", "AAA"))
        stored = self.store.get("g1")
        self.assertEqual(stored.qty, 1363)   # 30,000 x 5 / 110
        self.assertEqual(self.arb("g1")["guc_capital_cap"], 30_000.0)


class SnapshotTests(GucEngineTestCase):
    def test_the_snapshot_carries_a_guc_block(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.guc("g1", "AAA"))
        snap = engine.arb_snapshot()
        guc = snap["guc"]
        self.assertEqual((guc["enabled"], guc["open"], guc["risk_gap_day"], guc["risk_ordinary_day"]),
                         (True, 1, 5000.0, 2500.0))
        self.assertEqual(guc["open_risk_cap"], 20000.0)
        self.assertAlmostEqual(guc["market_gap"], 0.005, places=9)
        # ORB's own figures exclude the GUC position.
        self.assertEqual(snap["live_risk"], 0)


class CrossSleevePriceTests(GucEngineTestCase):
    """The two sleeves share one LTP cache. Pricing one sleeve must never drop
    the other's last good prices: a refused Kite read on the next tick would
    otherwise mark that sleeve at its stops and fire a day stop on nothing."""

    guc_scale = 0.5

    def _orb_open_then_guc_then_refused_read(self, with_guc: bool):
        engine = self.engine()
        self.run_triggers(engine, self.orb("o1", "BBB"), self.orb("o2", "CCC"))
        for setup_id in ("o1", "o2"):
            self.assertEqual(self.store.get(setup_id).state, ExecutionState.PROTECTED)
        self.clock.advance(1)
        self.run_triggers(engine, *([self.guc("g1", "AAA")] if with_guc else []))
        self.assertEqual(set(engine._arb_prices) >= {"BBB", "CCC"}, True)
        self.clock.advance(1)
        self.broker.quote_budget_busy = True  # Kite's per-second quote budget spent
        self.run_triggers(engine)
        return engine

    def test_a_guc_trigger_leaves_orb_prices_cached(self) -> None:
        control = self._orb_open_then_guc_then_refused_read(with_guc=False)
        self.assertEqual(control.arb_day.stale_symbols, ())
        self.assertIsNone(control.shutdown_reason)
        self.setUp()
        engine = self._orb_open_then_guc_then_refused_read(with_guc=True)
        self.assertEqual(engine.arb_day.stale_symbols, ())
        self.assertIsNone(engine.shutdown_reason)
        self.assertAlmostEqual(engine.arb_day.mtm, control.arb_day.mtm, places=2)
        self.assertEqual(self.store.get("o1").state, ExecutionState.PROTECTED)

    def test_orb_ticks_leave_guc_prices_cached(self) -> None:
        for symbol in ("AAA", "BBB", "CCC", "DDD"):
            self.broker.last_prices[symbol] = 110.0
        engine = self.engine()
        self.run_triggers(engine, self.guc("g1", "AAA"))
        self.clock.advance(1)
        self.run_triggers(engine, self.guc("g2", "BBB"))
        self.clock.advance(1)
        engine.tick()  # an ORB-only tick: prices GUC too, drops nothing
        self.assertEqual(set(engine._arb_prices), {"AAA", "BBB"})
        self.clock.advance(1)
        self.broker.quote_budget_busy = True
        self.run_triggers(engine, self.guc("g3", "CCC"))
        self.assertNotEqual(self.skip_reason("g3"), REASON_GUC_DAY_STOP)
        self.assertEqual(engine.guc_day.stale_symbols, ())
        self.assertFalse(engine._guc_day_stopped)


class OrbHaltTests(GucEngineTestCase):
    """An ORB day halt (hard day stop or day lock) is sleeve-scoped: it flattens
    and blocks ORB and the funnel only. GUC keeps trading under its own stop,
    its own day stop and the 14:50 square-off (Reference/guc_orb_halt_fix_
    backtest_2026-11-11.md: the engine-wide halt cost the pair 4.9k/month)."""

    def open_guc_and_two_orb(self):
        engine = self.engine()
        # GUC AAA: 5,000 risk, 2,000 sh. ORB BBB and CCC: 1,250 each, 500 sh.
        self.run_triggers(engine, self.guc("g1", "AAA"), self.orb("o1", "BBB"), self.orb("o2", "CCC"))
        for trade_id in ("g1", "o1", "o2"):
            self.assertEqual(self.store.get(trade_id).state, ExecutionState.PROTECTED)
        return engine

    def halt_orb(self, engine) -> None:
        # ORB marks -6,000 against a 2,500 hard day stop (50 % scale).
        self.broker.last_prices["BBB"] = 104.0
        self.broker.last_prices["CCC"] = 104.0
        self.clock.advance(1)
        engine.tick()

    def halt_events(self):
        return [
            json.loads(r["payload_json"])
            for r in self.store.list_events("__engine__")
            if r["event_type"] == EVENT_ARB_DAY_HALT
        ]

    def test_the_hard_day_stop_flattens_orb_and_leaves_guc_protected(self) -> None:
        engine = self.open_guc_and_two_orb()
        self.halt_orb(engine)
        self.assertIsNone(engine.shutdown_reason)
        self.assertFalse(engine.entries_paused)
        self.clock.advance(1)
        engine.tick()
        for trade_id in ("o1", "o2"):
            stored = self.store.get(trade_id)
            self.assertEqual(stored.state, ExecutionState.CLOSED)
            self.assertEqual(stored.extra["close_reason"], "hard_day_stop")
        guc = self.store.get("g1")
        self.assertEqual(guc.state, ExecutionState.PROTECTED)
        self.assertNotIn(self.broker.orders[guc.stop_order_id].status.upper(), {"CANCELLED", "COMPLETE"})
        record = self.store.load_arb_day(DAY)
        self.assertEqual(record["halted_reason"], "hard_day_stop")
        self.assertLessEqual(record["halted_mtm"], -2500)
        snap = engine.arb_snapshot()
        self.assertEqual(snap["halted_reason"], "hard_day_stop")
        self.assertEqual(snap["guc"]["open"], 1)
        self.assertEqual([h["restored"] for h in self.halt_events()], [False])

    def test_a_guc_trigger_after_the_orb_halt_is_taken(self) -> None:
        engine = self.open_guc_and_two_orb()
        self.halt_orb(engine)
        self.clock.advance(1)
        self.run_triggers(engine, self.guc("g2", "DDD"))
        stored = self.store.get("g2")
        self.assertEqual((stored.state, stored.extra["risk_cap_rupees"]), (ExecutionState.PROTECTED, 5000))

    def test_orb_and_funnel_triggers_after_the_halt_are_refused(self) -> None:
        engine = self.open_guc_and_two_orb()
        self.halt_orb(engine)
        self.clock.advance(1)
        self.run_triggers(engine, self.orb("o3", "DDD"), self.trigger("s1", "DDD", score=3))
        self.assertEqual(self.skip_reason("o3"), REASON_ARB_DAY_HALTED)
        self.assertEqual(self.skip_reason("s1"), REASON_ARB_DAY_HALTED)
        self.assertEqual(self.broker.market_place_count, 3 + 2)  # 3 entries + 2 flattens

    def test_the_guc_day_stop_still_fires_after_the_orb_halt(self) -> None:
        engine = self.open_guc_and_two_orb()
        self.halt_orb(engine)
        self.broker.last_prices["AAA"] = 104.0  # GUC marks -12,000 against its 10,000 stop
        self.clock.advance(1)
        self.run_triggers(engine, self.guc("g2", "DDD"))
        self.assertEqual(self.skip_reason("g2"), REASON_GUC_DAY_STOP)
        self.assertIsNone(engine.shutdown_reason)
        self.assertEqual(self.store.get("g1").state, ExecutionState.PROTECTED)

    def test_orb_closed_losses_beyond_the_backstop_halt_orb_not_guc(self) -> None:
        # ORB's closed-loss backstop is 3,000 at 50 %; an ORB loser past it
        # halts ORB only. (Any hard day stop lands here on the next tick.)
        self.save_closed("old", "DDD", realised=-3_500.0)
        engine = self.engine()
        self.run_triggers(engine, self.guc("g1", "AAA"), self.orb("o1", "BBB"))
        self.assertIsNone(engine.shutdown_reason)
        self.assertFalse(engine.breached)   # the session latch stays off: GUC must still resume
        self.assertEqual(engine.orb_halt_reason, CloseReason.DAILY_LOSS_BREACH)
        breaches = [r for r in self.store.list_events("__engine__") if r["event_type"] == "daily_loss_breached"]
        self.assertEqual(len(breaches), 1)
        self.assertEqual(self.store.get("g1").state, ExecutionState.PROTECTED)
        self.assertEqual(self.skip_reason("o1"), REASON_ARB_DAY_HALTED)
        self.assertEqual(self.store.load_arb_day(DAY)["halted_reason"], "daily_loss_breach")

    def test_the_day_lock_is_sleeve_scoped_too(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.guc("g1", "AAA"), self.orb("o1", "BBB"))
        self.broker.last_prices["BBB"] = 115.0   # ORB +2,500 net: arms the lock (50 % scale)
        self.clock.advance(60)
        engine.tick()
        self.assertGreater(self.store.load_arb_day(DAY)["peak_mtm"], 1500)
        self.broker.last_prices["BBB"] = 110.2
        self.clock.advance(60)
        engine.tick()
        self.assertIsNone(engine.shutdown_reason)
        self.clock.advance(1)
        engine.tick()
        self.assertEqual(self.store.get("o1").extra["close_reason"], "day_lock")
        self.assertEqual(self.store.get("g1").state, ExecutionState.PROTECTED)
        self.assertEqual(self.store.load_arb_day(DAY)["halted_reason"], "day_lock")

    def test_eod_squareoff_still_closes_guc_after_the_orb_halt(self) -> None:
        engine = self.open_guc_and_two_orb()
        self.halt_orb(engine)
        self.clock.advance(4 * 3600 + 20 * 60)  # 14:50 IST
        engine.tick()
        self.assertEqual(engine.shutdown_reason, CloseReason.EOD_SQUAREOFF)
        self.clock.advance(1)
        engine.tick()
        self.assertEqual(self.store.get("g1").state, ExecutionState.CLOSED)
        self.assertEqual(self.store.get("g1").extra["close_reason"], "eod_squareoff")

    def test_a_restart_after_a_persisted_orb_halt_reflattens_orb_only(self) -> None:
        self.open_guc_and_two_orb()
        # The halt was persisted but the engine died before the flatten.
        self.store.save_arb_day(DAY, halted_reason="hard_day_stop", halted_at="x", halted_mtm=-3000.0)
        restarted = self.engine()
        self.clock.advance(1)
        self.run_triggers(restarted, self.orb("o3", "DDD"))
        self.assertIsNone(restarted.shutdown_reason)
        self.assertEqual(self.skip_reason("o3"), REASON_ARB_DAY_HALTED)
        self.clock.advance(1)
        restarted.tick()
        for trade_id in ("o1", "o2"):
            self.assertEqual(self.store.get(trade_id).state, ExecutionState.CLOSED)
        self.assertEqual(self.store.get("g1").state, ExecutionState.PROTECTED)
        self.assertEqual([h["restored"] for h in self.halt_events()], [True])
        self.clock.advance(1)
        self.run_triggers(restarted, self.guc("g2", "DDD"))
        self.assertEqual(self.store.get("g2").state, ExecutionState.PROTECTED)

    def test_a_restart_keeps_the_original_halt_reason_past_the_backstop(self) -> None:
        # Yesterday's run: hard day stop, ORB flattened at -3,500 (past the
        # 3,000 backstop), halt persisted. The restart must restore that halt,
        # not record a fresh daily_loss_breach over it.
        self.save_closed("old", "DDD", realised=-3_500.0)
        self.store.save_arb_day(DAY, halted_reason="hard_day_stop", halted_at="x", halted_mtm=-3500.0)
        restarted = self.engine()
        self.run_triggers(restarted, self.guc("g1", "AAA"))
        self.assertIsNone(restarted.shutdown_reason)
        self.assertEqual(restarted.orb_halt_reason, CloseReason.HARD_DAY_STOP)
        self.assertEqual(self.store.load_arb_day(DAY)["halted_reason"], "hard_day_stop")
        self.assertEqual([h["restored"] for h in self.halt_events()], [True])
        self.assertEqual(self.store.get("g1").state, ExecutionState.PROTECTED)

    def test_a_feed_blip_after_the_backstop_does_not_stop_guc_for_the_day(self) -> None:
        # Hard day stop, flattened with a loss past the 3,000 backstop. ORB's
        # backstop must not leave the engine's sticky breached latch set: a
        # later one-tick pause (stale feed) would then never resume, and
        # every GUC trigger for the rest of the day would be dropped silently.
        engine = self.open_guc_and_two_orb()
        self.halt_orb(engine)
        self.clock.advance(1)
        engine.tick()   # ORB closed at -6,000: the backstop tick
        self.assertEqual(engine.orb_halt_reason, CloseReason.HARD_DAY_STOP)
        self.assertFalse(engine.breached)
        healthy = engine.feed_monitor
        engine.feed_monitor = StaleFeed()
        self.clock.advance(1)
        engine.tick()
        self.assertTrue(engine.entries_paused)
        engine.feed_monitor = healthy
        self.clock.advance(1)
        self.run_triggers(engine, self.guc("g2", "DDD"))
        self.assertFalse(engine.entries_paused)
        self.assertEqual(self.store.get("g2").state, ExecutionState.PROTECTED)


class OrbHaltWithGucOffTests(GucEngineTestCase):
    guc_enabled = False

    def test_with_guc_off_the_halt_shuts_the_engine_as_before(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.orb("o1", "BBB"), self.orb("o2", "CCC"))
        self.broker.last_prices["BBB"] = 104.0
        self.broker.last_prices["CCC"] = 104.0
        self.clock.advance(1)
        engine.tick()
        self.assertEqual(engine.shutdown_reason, CloseReason.HARD_DAY_STOP)
        self.assertTrue(engine.entries_paused)
        self.clock.advance(1)
        engine.tick()
        self.assertEqual(self.store.get("o1").state, ExecutionState.CLOSED)
        self.assertEqual(self.store.get("o2").state, ExecutionState.CLOSED)
        self.assertEqual(self.store.load_arb_day(DAY)["halted_reason"], "hard_day_stop")

    def test_with_guc_off_the_backstop_shuts_the_engine_as_before(self) -> None:
        self.save_closed("old", "DDD", realised=-3_500.0)
        engine = self.engine()
        self.run_triggers(engine, self.orb("o1", "BBB"))
        self.assertEqual(engine.shutdown_reason, CloseReason.DAILY_LOSS_BREACH)
        self.assertTrue(engine.entries_paused)
        self.assertIsNone(self.store.get("o1"))
        self.assertIsNone((self.store.load_arb_day(DAY) or {}).get("halted_reason"))


class SleeveHelperTests(unittest.TestCase):
    def test_rows_without_a_strategy_belong_to_orb(self) -> None:
        funnel = TriggerCandidate(
            setup_id="f", continuation_rule_version="v1", session_date=DAY, tradingsymbol="AAA",
            instrument_token=1, direction="UP", trigger_price=1.0, pullback_swing_high=None,
            pullback_swing_low=None, tick_size=0.05, buffer_ticks=0, trigger_exchange_ts=None, created_at="x",
        )
        p = Position(trade_id="f", candidate=funnel, state=ExecutionState.CLOSED)
        self.assertEqual((len(orb_sleeve([p])), len(guc_sleeve([p]))), (1, 0))

    def test_settings_validation(self) -> None:
        validate_settings(ArbSettings(guc_enabled=True, guc_risk_scale=0.5))
        with self.assertRaises(InvalidArbSettings):
            validate_settings(ArbSettings(guc_risk_scale=0.05))
        with self.assertRaises(InvalidArbSettings):
            validate_settings(ArbSettings(guc_ordinary_day_fraction=1.5))
        with self.assertRaises(InvalidArbSettings):
            validate_settings(ArbSettings(guc_open_risk_cap=1000.0))
        self.assertEqual(ALLOWED_GUC_RISK_SCALES, (0.25, 0.5, 1.0))

    def test_regime_risk(self) -> None:
        s = ArbSettings(guc_risk_scale=0.5, guc_ordinary_day_fraction=0.5)
        self.assertEqual((s.guc_risk_rupees(0.004), s.guc_risk_rupees(0.001), s.guc_risk_rupees(None)),
                         (2500.0, 1250.0, 1250.0))
        self.assertEqual(ArbSettings(guc_ordinary_day_fraction=1.0).guc_risk_rupees(None), 5000.0)
        self.assertEqual(ArbSettings(guc_ordinary_day_fraction=0.0).guc_risk_rupees(None), 0.0)


class SettingsFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "execution_settings.json"

    def test_missing_keys_mean_guc_off(self) -> None:
        from api.services import execution_settings
        self.path.write_text(json.dumps({"autostart_strategy": "arb", "arb_risk_scale": 0.5,
                                         "arb_total_capital_rupees": 450000, "arb_funnel_enabled": False}))
        settings = execution_settings.load(self.path)
        self.assertEqual((settings.arb_guc_enabled, settings.arb_guc_risk_scale, settings.arb_guc_ordinary_day_fraction),
                         (False, 1.0, 0.5))
        arb = execution_settings.session_config_for(settings).arb
        self.assertEqual((arb.risk_scale, arb.funnel_enabled, arb.guc_enabled), (0.5, False, False))

    def test_round_trip_of_the_trial_settings(self) -> None:
        from api.services import execution_settings
        saved = execution_settings.save(
            execution_settings.ExecutionSettings(
                autostart_strategy="arb", arb_risk_scale=0.5, arb_total_capital_rupees=450_000.0,
                arb_funnel_enabled=False, arb_guc_enabled=True, arb_guc_risk_scale=0.5,
                arb_guc_ordinary_day_fraction=0.5,
            ),
            self.path,
        )
        loaded = execution_settings.load(self.path)
        self.assertEqual(loaded, saved)
        arb = execution_settings.session_config_for(loaded).arb
        self.assertEqual((arb.guc_enabled, arb.guc_risk_scale, arb.guc_risk_rupees(0.01), arb.guc_risk_rupees(None)),
                         (True, 0.5, 2500.0, 1250.0))

    def test_an_invalid_guc_scale_falls_back_to_defaults(self) -> None:
        from api.services import execution_settings
        self.path.write_text(json.dumps({"autostart_strategy": "arb", "arb_risk_scale": 0.5,
                                         "arb_guc_enabled": True, "arb_guc_risk_scale": 0.05}))
        self.assertEqual(execution_settings.load(self.path), execution_settings.ExecutionSettings())

    def test_arb_session_config_passes_the_sleeve_through(self) -> None:
        cfg = arb_session_config(risk_scale=0.5, guc_enabled=True, guc_risk_scale=0.5, guc_ordinary_day_fraction=0.0)
        self.assertEqual((cfg.arb.guc_enabled, cfg.arb.guc_risk_scale, cfg.arb.guc_ordinary_day_fraction), (True, 0.5, 0.0))


if __name__ == "__main__":
    unittest.main()
