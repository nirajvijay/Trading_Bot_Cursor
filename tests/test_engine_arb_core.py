"""ARB wired into ExecutionEngine: entries, budget, day controls, restarts.

FakeBroker plus the real SqlitePositionStore, driven through tick() with a
fake clock. The market context is a stub returning chosen FunnelFeatures, so
each test controls exactly what the funnel sees.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from engine_arb import ARB_EXTRA_KEY, ArbSettings, FunnelFeatures
from engine_config import STRATEGY_ARB, SessionRiskConfig
from engine_core import (
    EVENT_ARB_DAY_HALT,
    REASON_ARB_CONTEXT_UNAVAILABLE,
    REASON_ARB_PAST_CUTOFF,
    REASON_ARB_TRIGGER_STALE,
    ExecutionEngine,
)
from engine_exit import CloseReason
from engine_feed import FeedHealth
from engine_risk import RiskPolicy
from engine_sizing import RiskCappedSizing
from engine_store import SqlitePositionStore
from engine_types import ExecutionState, Position, RiskLimits, TriggerCandidate
from trading_engine_broker import FakeBroker

DAY = "2026-09-22"
MORNING = datetime(2026, 9, 22, 5, 0, tzinfo=timezone.utc)  # 10:30 IST


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class HealthyFeed:
    def check(self, *, now=None) -> FeedHealth:
        return FeedHealth(healthy=True, age_seconds=0.5)


def features(score: int = 0, **overrides) -> FunnelFeatures:
    """Features that pass every filter, with the requested score."""
    values = dict(
        trigger_time_ist=time(11, 0),
        volume_ratio=3.0,
        stop_pct=2.77,
        is_first_trigger_today=True,
        align_market_pct=0.1,
        align_sector_pct=0.0,
    )
    if score >= 1:
        values["align_market_pct"] = -0.5
    if score >= 2:
        values["align_sector_pct"] = 0.3
    if score >= 3:
        values["volume_ratio"] = 7.0
    values.update(overrides)
    return FunnelFeatures(**values)


class StubContext:
    def __init__(self) -> None:
        self.by_setup: Dict[str, FunnelFeatures] = {}
        self.error: Optional[Exception] = None
        self.calls: List[str] = []

    def features_for(self, candidate: TriggerCandidate, stop_price) -> FunnelFeatures:
        self.calls.append(candidate.setup_id)
        if self.error is not None:
            raise self.error
        return self.by_setup.get(candidate.setup_id, features())


class ArbEngineTestCase(unittest.TestCase):
    scale = 1.0

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SqlitePositionStore(Path(self._tmp.name) / "engine.db")
        self.broker = FakeBroker()
        self.broker.remaining_capital = 500_000.0
        for symbol in ("AAA", "BBB", "CCC", "DDD"):
            self.broker.last_prices[symbol] = 110.0
        self.clock = Clock(MORNING)
        self.context = StubContext()
        self.candidates: List[TriggerCandidate] = []
        self.config = SessionRiskConfig(
            total_capital_rupees=500_000.0,
            strategy_mode=STRATEGY_ARB,
            arb=ArbSettings(risk_scale=self.scale),
        )

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def engine(self, **overrides) -> ExecutionEngine:
        kwargs = dict(
            broker=self.broker,
            store=self.store,
            feed_monitor=HealthyFeed(),
            risk_policy=RiskPolicy(self.config.to_risk_limits()),
            sizing_policy=RiskCappedSizing(),
            candidate_source=lambda: list(self.candidates),
            session_config=self.config,
            session_date=DAY,
            run_id="run-1",
            now_fn=self.clock,
            arb_context=self.context,
        )
        kwargs.update(overrides)
        return ExecutionEngine(**kwargs)

    def trigger(self, setup_id: str, symbol: str, *, score: int = 0, price: float = 110.0,
                swing_low: float = 107.0, **feature_overrides) -> TriggerCandidate:
        candidate = TriggerCandidate(
            setup_id=setup_id,
            continuation_rule_version="v1",
            session_date=DAY,
            tradingsymbol=symbol,
            instrument_token=1,
            direction="UP",
            trigger_price=price,
            pullback_swing_high=price + 3,
            pullback_swing_low=swing_low,
            tick_size=0.05,
            buffer_ticks=1,
            trigger_exchange_ts=self.clock.now.isoformat(),
            created_at=self.clock.now.isoformat(),
            vwap_classification=None,
            breakout_candle_volume=3000,
            avg_prior_3_1m_volume=1000.0,
        )
        self.context.by_setup[setup_id] = features(score, **feature_overrides)
        return candidate

    def run_triggers(self, engine: ExecutionEngine, *candidates: TriggerCandidate) -> None:
        self.candidates = list(candidates)
        engine.tick()
        self.candidates = []

    def arb(self, trade_id: str) -> dict:
        stored = self.store.get(trade_id)
        assert stored is not None
        return stored.extra.get(ARB_EXTRA_KEY) or {}

    def skip_reason(self, trade_id: str) -> Optional[str]:
        stored = self.store.get(trade_id)
        assert stored is not None
        return stored.extra.get("skip_reason")

    def save_closed(self, trade_id: str, symbol: str, realised: float) -> None:
        candidate = self.trigger(trade_id, symbol)
        self.store.save(
            Position(
                trade_id=trade_id,
                candidate=candidate,
                state=ExecutionState.CLOSED,
                qty=100,
                entry_price=110.0,
                stop_price=107.0,
                realised_pnl=realised,
            )
        )


class WiringTests(ArbEngineTestCase):
    def test_arb_refuses_to_run_without_a_context(self) -> None:
        with self.assertRaises(ValueError):
            self.engine(arb_context=None)

    def test_arb_refuses_a_risk_policy_built_from_other_numbers(self) -> None:
        legacy = RiskPolicy(RiskLimits(900.0, 450.0, 3000.0))
        with self.assertRaises(ValueError):
            self.engine(risk_policy=legacy)

    def test_no_entry_before_the_first_day_check(self) -> None:
        engine = self.engine()
        self.assertIsNone(engine.handle_trigger(self.trigger("s1", "AAA", score=2)))
        self.assertEqual(self.store.list_positions(), [])

    def test_legacy_mode_never_touches_arb(self) -> None:
        legacy = SessionRiskConfig()
        engine = self.engine(
            session_config=legacy,
            risk_policy=RiskPolicy(legacy.to_risk_limits()),
            arb_context=None,
        )
        candidate = self.trigger("s1", "AAA")
        self.candidates = [candidate]
        self.clock.advance(5)  # past the VWAP wait
        engine.tick()
        self.assertEqual(self.skip_reason("s1"), "vwap_unavailable")
        self.assertIsNone(self.store.load_arb_day(DAY))
        self.assertEqual(self.context.calls, [])


class EntryTests(ArbEngineTestCase):
    def test_a_score_two_trigger_enters_at_4500_without_waiting_for_vwap(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.trigger("s1", "AAA", score=2))
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(stored.state, ExecutionState.PROTECTED)
        self.assertEqual(stored.extra["risk_cap_rupees"], 4500)
        self.assertEqual(stored.qty, 1475)  # floor(4500 / (110 - 106.95))
        info = self.arb("s1")
        self.assertEqual((info["strategy"], info["score"], info["risk_used"]), ("FUNNEL", 2, 4500))
        self.assertFalse(info["half_size"])

    def test_a_filter_failure_is_recorded_with_its_features(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.trigger("s1", "AAA", volume_ratio=1.5))
        self.assertEqual(self.skip_reason("s1"), "arb_volume_below_min")
        self.assertEqual(self.arb("s1")["features"]["volume_ratio"], 1.5)
        self.assertEqual(self.broker.market_place_count, 0)

    def test_half_size_after_a_losing_trade(self) -> None:
        self.save_closed("old", "DDD", realised=-800.0)
        engine = self.engine()
        self.run_triggers(engine, self.trigger("s1", "AAA", score=2))
        self.assertEqual(self.store.get("s1").extra["risk_cap_rupees"], 2250)
        self.assertTrue(self.arb("s1")["half_size"])
        self.assertEqual(self.arb("s1")["losers_today"], 1)

    def test_open_risk_leaves_no_room_for_a_second_big_trade(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.trigger("s1", "AAA", score=3))  # ~Rs 6,000 live
        self.run_triggers(engine, self.trigger("s2", "BBB", score=2))
        self.assertEqual(self.skip_reason("s2"), "arb_below_min_risk")
        self.assertLess(self.arb("s2")["room"], 500)

    def test_a_stop_moved_past_entry_frees_the_budget(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.trigger("s1", "AAA", score=3))
        stored = self.store.get("s1")
        self.broker.last_prices["AAA"] = 114.0
        self.broker.modify_slm(stored.stop_order_id, 110.5)  # e.g. edited in Kite
        engine.tick()
        self.assertGreaterEqual(self.store.get("s1").stop_price, 110.5)
        self.run_triggers(engine, self.trigger("s2", "BBB", score=2))
        self.assertEqual(self.store.get("s2").extra["risk_cap_rupees"], 4500)

    def test_only_two_score_zero_trades_a_day(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.trigger("s1", "AAA"), self.trigger("s2", "BBB"))
        self.run_triggers(engine, self.trigger("s3", "CCC"))
        self.assertEqual(self.store.get("s1").state, ExecutionState.PROTECTED)
        self.assertEqual(self.store.get("s2").state, ExecutionState.PROTECTED)
        self.assertEqual(self.skip_reason("s3"), "arb_score0_limit")

    def test_no_entry_after_1300(self) -> None:
        self.clock.now = datetime(2026, 9, 22, 7, 30, 5, tzinfo=timezone.utc)  # 13:00:05 IST
        engine = self.engine()
        candidate = self.trigger("s1", "AAA", score=2, trigger_time_ist=time(12, 59, 58))
        self.run_triggers(engine, candidate)
        self.assertEqual(self.skip_reason("s1"), REASON_ARB_PAST_CUTOFF)
        self.assertEqual(self.broker.market_place_count, 0)

    def test_a_stale_trigger_is_not_entered(self) -> None:
        engine = self.engine()
        candidate = self.trigger("s1", "AAA", score=2)
        self.clock.advance(16)
        self.run_triggers(engine, candidate)
        self.assertEqual(self.skip_reason("s1"), REASON_ARB_TRIGGER_STALE)

    def test_no_context_means_no_trade(self) -> None:
        engine = self.engine()
        self.context.error = RuntimeError("db locked")
        self.run_triggers(engine, self.trigger("s1", "AAA", score=2))
        self.assertEqual(self.skip_reason("s1"), REASON_ARB_CONTEXT_UNAVAILABLE)
        self.assertEqual(self.broker.market_place_count, 0)

    def test_a_failing_day_check_holds_entries_until_it_recovers(self) -> None:
        engine = self.engine()
        original = engine._arb_day_now

        def broken(now):
            raise RuntimeError("store unreadable")

        engine._arb_day_now = broken
        candidate = self.trigger("s1", "AAA", score=2)
        self.run_triggers(engine, candidate)
        self.assertIsNone(self.store.get("s1"))  # not consumed
        engine._arb_day_now = original
        self.run_triggers(engine, candidate)
        self.assertEqual(self.store.get("s1").state, ExecutionState.PROTECTED)


class DayControlTests(ArbEngineTestCase):
    def open_two(self, engine: ExecutionEngine) -> None:
        # AAA at Rs 4,500 (1,475 sh), then BBB with the Rs ~1,500 room left.
        self.run_triggers(engine, self.trigger("s1", "AAA", score=2))
        self.run_triggers(engine, self.trigger("s2", "BBB", score=2))
        self.assertEqual(self.store.get("s2").qty, 492)

    def test_the_hard_day_stop_flattens_everything_and_is_persisted(self) -> None:
        engine = self.engine()
        self.open_two(engine)
        self.broker.last_prices["AAA"] = 107.2
        self.broker.last_prices["BBB"] = 107.2
        engine.tick()
        self.assertEqual(engine.shutdown_reason, CloseReason.HARD_DAY_STOP)
        engine.tick()
        for trade_id in ("s1", "s2"):
            stored = self.store.get(trade_id)
            self.assertEqual(stored.state, ExecutionState.CLOSED)
            self.assertEqual(stored.extra["close_reason"], "hard_day_stop")
        record = self.store.load_arb_day(DAY)
        self.assertEqual(record["halted_reason"], "hard_day_stop")
        self.assertLessEqual(record["halted_mtm"], -5000)

    def test_a_failing_store_write_never_stops_the_flatten(self) -> None:
        engine = self.engine()
        self.open_two(engine)
        self.broker.last_prices["AAA"] = 107.2
        self.broker.last_prices["BBB"] = 107.2

        def broken(*_args, **_kwargs):
            raise OSError("disk full")

        self.store.save_arb_day = broken
        engine.tick()
        self.assertEqual(engine.shutdown_reason, CloseReason.HARD_DAY_STOP)
        engine.tick()
        self.assertEqual(self.store.get("s1").state, ExecutionState.CLOSED)
        self.assertEqual(self.store.get("s2").state, ExecutionState.CLOSED)

    def test_a_restart_keeps_the_day_stopped(self) -> None:
        engine = self.engine()
        self.open_two(engine)
        self.broker.last_prices["AAA"] = 107.2
        self.broker.last_prices["BBB"] = 107.2
        engine.tick()
        engine.tick()
        restarted = self.engine()
        self.run_triggers(restarted, self.trigger("s9", "CCC", score=3))
        self.assertEqual(restarted.shutdown_reason, CloseReason.HARD_DAY_STOP)
        self.assertIsNone(self.store.get("s9"))
        self.assertEqual(self.broker.market_place_count, 2 + 2)  # 2 entries + 2 flattens
        halts = [
            json.loads(r["payload_json"])
            for r in self.store.list_events("__engine__")
            if r["event_type"] == EVENT_ARB_DAY_HALT
        ]
        self.assertEqual([h["restored"] for h in halts], [False, True])

    def test_the_day_lock_arms_then_flattens_on_the_next_minute_sample(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.trigger("s1", "AAA", score=2))
        self.broker.last_prices["AAA"] = 112.5  # ~ +Rs 3,600 net
        self.clock.advance(60)
        engine.tick()
        peak = self.store.load_arb_day(DAY)["peak_mtm"]
        self.assertGreater(peak, 3000)
        # A dip in the same minute is not a sample.
        self.broker.last_prices["AAA"] = 110.9
        self.clock.advance(10)
        engine.tick()
        self.assertIsNone(engine.shutdown_reason)
        self.clock.advance(50)
        engine.tick()
        self.assertEqual(engine.shutdown_reason, CloseReason.DAY_LOCK)
        engine.tick()
        self.assertEqual(self.store.get("s1").extra["close_reason"], "day_lock")

    def test_a_restart_restores_the_peak(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.trigger("s1", "AAA", score=2))
        self.broker.last_prices["AAA"] = 112.5
        self.clock.advance(60)
        engine.tick()
        peak = self.store.load_arb_day(DAY)["peak_mtm"]
        restarted = self.engine()
        self.assertEqual(restarted._arb_lock.peak, peak)
        self.broker.last_prices["AAA"] = 110.9
        self.clock.advance(60)
        restarted.tick()
        self.assertEqual(restarted.shutdown_reason, CloseReason.DAY_LOCK)

    def test_a_stale_price_counts_the_position_at_its_stop(self) -> None:
        engine = self.engine()
        self.open_two(engine)
        engine.tick()  # BBB was entered after that tick's price read; price it once
        self.assertTrue(engine.arb_day.complete)
        self.broker.quote_budget_busy = True
        self.clock.advance(2)  # last good prices are still fresh
        engine.tick()
        self.assertIsNone(engine.shutdown_reason)
        self.assertTrue(engine.arb_day.complete)
        self.clock.advance(2)  # now 4 s old: both count at their stops
        engine.tick()
        self.assertFalse(engine.arb_day.complete)
        self.assertEqual(engine.shutdown_reason, CloseReason.HARD_DAY_STOP)

    def test_a_position_never_priced_counts_at_its_stop(self) -> None:
        engine = self.engine()
        self.open_two(engine)
        self.broker.quote_budget_busy = True
        engine.tick()
        self.assertEqual(engine.arb_day.stale_symbols, ("BBB",))

    def test_the_lock_is_not_sampled_on_stale_prices(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.trigger("s1", "AAA", score=2))
        self.broker.last_prices["AAA"] = 112.5
        self.clock.advance(60)
        engine.tick()
        self.broker.quote_budget_busy = True
        self.clock.advance(60)
        engine.tick()  # stale: worst case is at the stop, but no lock sample
        self.assertIsNone(engine.shutdown_reason)


class MicroStageTests(ArbEngineTestCase):
    scale = 0.05

    def test_micro_sizes_are_scaled(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.trigger("s1", "AAA", score=2))
        self.assertAlmostEqual(self.store.get("s1").extra["risk_cap_rupees"], 225)
        self.assertEqual(self.store.get("s1").qty, 73)  # floor(225 / 3.05)

    def test_micro_buys_one_share_when_the_size_rounds_to_zero(self) -> None:
        self.broker.last_prices["AAA"] = 5000.0
        engine = self.engine()
        # Stop 4940.00 (one tick under 4940.05): one share risks Rs 60, cap Rs 50.
        self.run_triggers(engine, self.trigger("s1", "AAA", price=5000.0, swing_low=4940.05))
        stored = self.store.get("s1")
        self.assertEqual(stored.qty, 1)
        self.assertTrue(self.arb("s1")["one_share_floor"])
        self.assertAlmostEqual(stored.extra["risk_cap_rupees"], 60.0)
        self.assertEqual(stored.state, ExecutionState.PROTECTED)

    def test_micro_hard_stop_is_scaled(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.trigger("s1", "AAA", score=3))  # Rs 300 cap
        self.broker.last_prices["AAA"] = 106.96  # just above the stop: ~ -Rs 300
        engine.tick()
        self.assertEqual(engine.shutdown_reason, CloseReason.HARD_DAY_STOP)


if __name__ == "__main__":
    unittest.main()
