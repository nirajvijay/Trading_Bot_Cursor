"""ORB breakouts through the engine's ARB path, and the handoff that feeds it.

Same harness as test_engine_arb_core: FakeBroker, the real store, a fake
clock. Nothing here can start in production: ARB mode is still not startable
(PR C adds the switch), so these are the only callers.
"""
from __future__ import annotations

import dataclasses
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from engine_arb import (
    REASON_ORB_NO_STOP,
    REASON_ORB_OUTSIDE_WINDOW,
    REASON_UNKNOWN_STRATEGY,
)
from engine_config import SessionRiskConfig
from engine_core import (
    REASON_ARB_PAST_ORB_CUTOFF,
    REASON_ARB_TRIGGER_STALE,
    REASON_STRATEGY_NOT_ENABLED,
)
from engine_entry import compute_stop_price, planned_stop_price
from engine_risk import RiskPolicy
from engine_store import candidate_from_json, candidate_to_json
from engine_types import ExecutionState, TriggerCandidate
from orb_detector import OrbDetector
from orb_types import OrbBaseline
from orb_writer import OrbWriter
from tests.test_engine_arb_core import DAY, ArbEngineTestCase
from tick_event import Ohlc, TickEvent
from trading_engine_handoff import (
    fetch_arb_candidates_since,
    fetch_orb_triggered_since,
    fetch_triggered_with_vwap_since,
)
from trading_engine_types import STRATEGY_FUNNEL, STRATEGY_ORB

IST = ZoneInfo("Asia/Kolkata")


class OrbEngineTestCase(ArbEngineTestCase):
    def orb(self, setup_id: str, symbol: str, *, direction: str = "UP", price: float = 110.0,
            stop: object = 107.5, **overrides) -> TriggerCandidate:
        """An ORB breakout fired now. Risk per share is 2.50 by default."""
        fields = dict(
            setup_id=setup_id,
            continuation_rule_version="orb_v1",
            session_date=DAY,
            tradingsymbol=symbol,
            instrument_token=1,
            direction=direction,
            trigger_price=price,
            pullback_swing_high=None,
            pullback_swing_low=None,
            tick_size=0.05,
            buffer_ticks=0,
            trigger_exchange_ts=self.clock.now.astimezone(IST).isoformat(timespec="seconds"),
            created_at=self.clock.now.isoformat(timespec="seconds"),
            last_price=price,
            strategy=STRATEGY_ORB,
            planned_stop=stop,
            orb_level=price,
            orb_rvol=5.2,
            orb_atr14=7.142857,
            orb_rank=1,
        )
        fields.update(overrides)
        return TriggerCandidate(**fields)


class OrbEntryTests(OrbEngineTestCase):
    def test_a_breakout_enters_at_2500_with_the_detectors_stop(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.orb("o1", "AAA"))
        stored = self.store.get("o1")
        self.assertEqual(stored.state, ExecutionState.PROTECTED)
        self.assertEqual(stored.stop_price, 107.5)
        self.assertEqual(stored.extra["risk_cap_rupees"], 2500)
        self.assertEqual(stored.qty, 1000)  # 2500 / (110 - 107.5)
        info = self.arb("o1")
        self.assertEqual((info["strategy"], info["risk_used"], info["half_size"]), ("ORB", 2500, False))
        self.assertEqual(info["orb"]["rvol"], 5.2)
        self.assertNotIn("score", info)
        # No funnel filters, no score, no market context for an ORB trade.
        self.assertEqual(self.context.calls, [])
        self.assertEqual(self.broker.orders[stored.stop_order_id].trigger_price, 107.5)

    def test_a_short_breakout_has_its_stop_above(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.orb("o1", "AAA", direction="DOWN", stop=112.5))
        stored = self.store.get("o1")
        self.assertEqual((stored.state, stored.stop_price, stored.qty),
                         (ExecutionState.PROTECTED, 112.5, 1000))

    def test_half_size_after_a_loser(self) -> None:
        self.save_closed("old", "DDD", realised=-800.0)
        engine = self.engine()
        self.run_triggers(engine, self.orb("o1", "AAA"))
        self.assertEqual(self.store.get("o1").extra["risk_cap_rupees"], 1250)
        self.assertTrue(self.arb("o1")["half_size"])

    def test_the_open_risk_cap_limits_an_orb_trade_too(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.trigger("s1", "AAA", score=2))   # about 4,500 live
        self.run_triggers(engine, self.orb("o1", "BBB"))
        risk = self.store.get("o1").extra["risk_cap_rupees"]
        self.assertTrue(1400 < risk < 1600, risk)                       # the room left under 6,000
        self.assertEqual(self.arb("o1")["base_risk"], 2500)

    def test_skipped_when_the_budget_has_no_room(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.trigger("s1", "AAA", score=3))   # about 6,000 live
        self.run_triggers(engine, self.orb("o1", "BBB"))
        self.assertEqual(self.skip_reason("o1"), "arb_below_min_risk")
        self.assertEqual(self.arb("o1")["strategy"], "ORB")

    def test_orb_trades_do_not_use_the_score_zero_allowance(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.orb("o1", "AAA"))
        self.run_triggers(engine, self.trigger("s1", "BBB"), self.trigger("s2", "CCC"))
        self.assertEqual(self.store.get("s1").state, ExecutionState.PROTECTED)
        self.assertEqual(self.store.get("s2").state, ExecutionState.PROTECTED)

    def test_a_stock_already_open_blocks_its_orb_breakout(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.trigger("s1", "AAA", score=1))
        self.run_triggers(engine, self.orb("o1", "AAA"))
        self.assertEqual(self.skip_reason("o1"), "symbol_already_open")


class OrbRefusalTests(OrbEngineTestCase):
    def test_no_stop_no_trade(self) -> None:
        cases = {
            "missing": None,
            "wrong_side": 112.0,      # above a long's trigger price
            "at_price": 110.0,
            "zero": 0.0,
            "nan": float("nan"),
            "text": "abc",
        }
        engine = self.engine()
        for name, stop in cases.items():
            with self.subTest(case=name):
                self.run_triggers(engine, self.orb(f"o-{name}", "AAA", stop=stop))
                self.assertEqual(self.skip_reason(f"o-{name}"), REASON_ORB_NO_STOP)
        self.assertEqual(self.broker.market_place_count, 0)

    def test_a_breakout_stamped_at_or_after_the_cutoff_is_refused(self) -> None:
        engine = self.engine()
        for name, stamp in (("at", "2026-09-22T11:01:00+05:30"), ("none", None),
                            ("no_zone", "2026-09-22T10:30:00")):
            with self.subTest(case=name):
                self.run_triggers(engine, self.orb(f"o-{name}", "AAA", trigger_exchange_ts=stamp))
                self.assertEqual(self.skip_reason(f"o-{name}"), REASON_ORB_OUTSIDE_WINDOW)
        self.assertEqual(self.broker.market_place_count, 0)

    def test_the_last_second_is_allowed(self) -> None:
        self.clock.now = datetime(2026, 9, 22, 5, 30, 59, tzinfo=timezone.utc)   # 11:00:59 IST
        engine = self.engine()
        self.run_triggers(engine, self.orb("o1", "AAA"))
        self.assertEqual(self.store.get("o1").state, ExecutionState.PROTECTED)

    def test_fired_in_time_but_reached_the_engine_after_the_cutoff(self) -> None:
        self.clock.now = datetime(2026, 9, 22, 5, 30, 58, tzinfo=timezone.utc)   # 11:00:58 IST
        engine = self.engine()
        candidate = self.orb("o1", "AAA")
        self.clock.advance(4)                                                    # 11:01:02
        self.run_triggers(engine, candidate)
        self.assertEqual(self.skip_reason("o1"), REASON_ARB_PAST_ORB_CUTOFF)
        self.assertEqual(self.broker.market_place_count, 0)

    def test_the_orb_cutoff_does_not_stop_the_funnel(self) -> None:
        self.clock.now = datetime(2026, 9, 22, 6, 0, 0, tzinfo=timezone.utc)     # 11:30 IST
        engine = self.engine()
        self.run_triggers(engine, self.trigger("s1", "AAA", score=1))
        self.assertEqual(self.store.get("s1").state, ExecutionState.PROTECTED)

    def test_a_stale_breakout_is_not_entered(self) -> None:
        engine = self.engine()
        candidate = self.orb("o1", "AAA")
        self.clock.advance(16)
        self.run_triggers(engine, candidate)
        self.assertEqual(self.skip_reason("o1"), REASON_ARB_TRIGGER_STALE)

    def test_a_trigger_that_cannot_be_dated_is_treated_as_stale(self) -> None:
        engine = self.engine()
        self.run_triggers(
            engine,
            self.orb("o1", "AAA", created_at="not-a-time"),
            dataclasses.replace(self.trigger("s1", "BBB", score=2), created_at=""),
        )
        self.assertEqual(self.skip_reason("o1"), REASON_ARB_TRIGGER_STALE)
        self.assertEqual(self.skip_reason("s1"), REASON_ARB_TRIGGER_STALE)
        self.assertEqual(self.broker.market_place_count, 0)

    def test_an_unknown_strategy_is_refused(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.orb("o1", "AAA", strategy="MYSTERY"))
        self.assertEqual(self.skip_reason("o1"), REASON_UNKNOWN_STRATEGY)
        self.assertEqual(self.broker.market_place_count, 0)

    def test_a_legacy_session_never_trades_an_orb_breakout(self) -> None:
        legacy = SessionRiskConfig()
        engine = self.engine(
            session_config=legacy,
            risk_policy=RiskPolicy(legacy.to_risk_limits()),
            arb_context=None,
        )
        self.run_triggers(engine, self.orb("o1", "AAA", vwap_classification="ACCEPT"))
        self.assertEqual(self.skip_reason("o1"), REASON_STRATEGY_NOT_ENABLED)
        self.assertEqual(self.broker.market_place_count, 0)


class MicroOrbTests(OrbEngineTestCase):
    scale = 0.05

    def test_micro_risk_is_125(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.orb("o1", "AAA"))
        stored = self.store.get("o1")
        self.assertEqual((stored.extra["risk_cap_rupees"], stored.qty), (125, 50))

    def test_one_share_when_a_single_share_risks_a_little_over_125(self) -> None:
        self.broker.last_prices["AAA"] = 4000.0
        engine = self.engine()
        self.run_triggers(engine, self.orb("o1", "AAA", price=4000.0, stop=3860.0))   # 140 a share
        stored = self.store.get("o1")
        self.assertEqual((stored.qty, stored.extra["risk_cap_rupees"]), (1, 140.0))
        self.assertTrue(self.arb("o1")["one_share_floor"])

    def test_no_share_when_one_share_risks_far_more_than_125(self) -> None:
        self.broker.last_prices["AAA"] = 4000.0
        engine = self.engine()
        self.run_triggers(engine, self.orb("o1", "AAA", price=4000.0, stop=3800.0))   # 200 a share
        self.assertEqual(self.skip_reason("o1"), "sized_to_zero")


class PlannedStopTests(unittest.TestCase):
    def candidate(self, **overrides) -> TriggerCandidate:
        fields = dict(
            setup_id="o", continuation_rule_version="orb_v1", session_date=DAY,
            tradingsymbol="AAA", instrument_token=1, direction="UP", trigger_price=110.0,
            pullback_swing_high=120.0, pullback_swing_low=100.0, tick_size=0.05, buffer_ticks=1,
            trigger_exchange_ts=None, created_at="2026-09-22T05:00:00+00:00",
            strategy=STRATEGY_ORB, planned_stop=107.5,
        )
        fields.update(overrides)
        return TriggerCandidate(**fields)

    def test_orb_uses_the_planned_stop_and_never_the_swings(self) -> None:
        self.assertEqual(compute_stop_price(self.candidate()), 107.5)
        self.assertIsNone(compute_stop_price(self.candidate(planned_stop=None)))

    def test_the_funnel_ignores_a_planned_stop(self) -> None:
        funnel = self.candidate(strategy=STRATEGY_FUNNEL)
        self.assertEqual(compute_stop_price(funnel), 99.95)   # one tick under the swing low

    def test_an_off_grid_stop_moves_away_from_the_trade(self) -> None:
        self.assertEqual(planned_stop_price(self.candidate(planned_stop=107.52)), 107.5)
        self.assertEqual(
            planned_stop_price(self.candidate(direction="DOWN", planned_stop=112.52)), 112.55
        )

    def test_the_new_fields_survive_the_store(self) -> None:
        candidate = self.candidate(orb_level=109.9, orb_rvol=4.4, orb_atr14=7.1, orb_rank=2)
        self.assertEqual(candidate_from_json(candidate_to_json(candidate)), candidate)

    def test_a_row_stored_before_these_fields_existed_loads_as_a_funnel_trigger(self) -> None:
        old = ('{"setup_id":"s","continuation_rule_version":"v1","session_date":"2026-09-01",'
               '"tradingsymbol":"AAA","instrument_token":1,"direction":"UP","trigger_price":100.0,'
               '"pullback_swing_high":101.0,"pullback_swing_low":99.0,"tick_size":0.05,'
               '"buffer_ticks":1,"trigger_exchange_ts":null,"created_at":"x"}')
        loaded = candidate_from_json(old)
        self.assertEqual((loaded.strategy, loaded.planned_stop), (STRATEGY_FUNNEL, None))


class HandoffTests(unittest.TestCase):
    """The detector's rows, read back the way the engine reads them."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "live.db"
        self.writer = OrbWriter(self.db)
        self.addCleanup(self.writer.close)
        symbols = {11: "AAA", 22: "BBB"}
        self.detector = OrbDetector(
            session_date=DAY,
            token_to_symbol=symbols,
            tick_sizes={11: 0.05, 22: 0.05},
            baselines={t: OrbBaseline(t, s, True, atr14=20.0, avg_or_volume=1000.0)
                       for t, s in symbols.items()},
            writer=self.writer,
            now_fn=lambda: self.now,
        )
        self.sequence = 0
        self.now = datetime(2026, 9, 22, 8, 0, tzinfo=IST)

    def tick(self, token: int, hh: int, mm: int, ss: int, price: float, volume: int) -> None:
        self.sequence += 1
        when = datetime(2026, 9, 22, hh, mm, ss, tzinfo=IST)
        self.now = when
        self.detector.on_tick(
            TickEvent(self.sequence, token, price, when, when, volume, 1, price,
                      Ohlc(open=1000.0, high=1012.0, low=998.0, close=990.0))
        )

    def arm_both(self) -> None:
        for token, close in ((11, 1010.0), (22, 999.0)):
            self.tick(token, 9, 8, 5, 1000.0, 500)
            self.tick(token, 9, 19, 59, close, 5500)
        for token, price in ((11, 1010.0), (22, 999.0)):
            self.tick(token, 9, 20, 0, price, 6000)

    def test_a_breakout_becomes_an_orb_candidate(self) -> None:
        self.arm_both()
        self.assertEqual(fetch_orb_triggered_since(self.db, created_at_gte="2026"), [])
        self.tick(11, 9, 23, 11, 1012.5, 7000)
        (candidate,) = fetch_orb_triggered_since(self.db, created_at_gte="2026")
        self.assertEqual(candidate.setup_id, "11|2026-09-22|ORB|orb_v1")
        self.assertEqual((candidate.strategy, candidate.tradingsymbol, candidate.direction),
                         (STRATEGY_ORB, "AAA", "UP"))
        self.assertEqual((candidate.trigger_price, candidate.planned_stop), (1012.5, 1005.5))
        self.assertEqual((candidate.orb_level, candidate.orb_rvol, candidate.orb_atr14, candidate.orb_rank),
                         (1012.0, 5.0, 20.0, 1))
        self.assertEqual(candidate.trigger_exchange_ts, "2026-09-22T09:23:11+05:30")
        self.assertEqual((candidate.pullback_swing_high, candidate.pullback_swing_low), (None, None))
        self.assertEqual(compute_stop_price(candidate), 1005.5)

    def test_missed_and_expired_arms_are_not_candidates(self) -> None:
        self.arm_both()
        self.tick(11, 9, 23, 11, 1030.0, 7000)      # far through the level: missed
        self.tick(22, 11, 1, 0, 999.0, 7000)        # cutoff: BBB expires
        self.assertEqual(fetch_orb_triggered_since(self.db, created_at_gte="2026"), [])

    def test_only_rows_at_or_after_the_floor(self) -> None:
        self.arm_both()
        self.tick(11, 9, 23, 11, 1012.0, 7000)
        self.assertEqual(len(fetch_orb_triggered_since(self.db, created_at_gte="2000")), 1)
        self.assertEqual(fetch_orb_triggered_since(self.db, created_at_gte="2999"), [])

    def test_a_database_the_detector_never_touched_gives_nothing(self) -> None:
        bare = Path(self._tmp.name) / "bare.db"
        sqlite3.connect(bare).close()
        self.assertEqual(fetch_orb_triggered_since(bare, created_at_gte="2000"), [])
        self.assertEqual(fetch_orb_triggered_since(Path(self._tmp.name) / "none.db", created_at_gte="2000"), [])
        self.assertEqual(fetch_arb_candidates_since(bare, created_at_gte="2000"), [])

    def test_the_legacy_source_never_returns_an_orb_row(self) -> None:
        self.arm_both()
        self.tick(11, 9, 23, 11, 1012.0, 7000)
        self.assertEqual(fetch_triggered_with_vwap_since(self.db, created_at_gte="2000"), [])
        both = fetch_arb_candidates_since(self.db, created_at_gte="2000")
        self.assertEqual([c.strategy for c in both], [STRATEGY_ORB])


class DetectorToEngineTests(OrbEngineTestCase):
    """End to end: ticks, the detector's rows, the handoff, an ARB entry."""

    def test_a_detected_breakout_is_entered_with_its_stop(self) -> None:
        live_db = Path(self._tmp.name) / "live.db"
        # The detector records the breakout two seconds before the engine looks.
        writer = OrbWriter(live_db, now_fn=lambda: self.clock.now - timedelta(seconds=2))
        self.addCleanup(writer.close)
        seen_at = {}
        detector = OrbDetector(
            session_date=DAY,
            token_to_symbol={11: "AAA"},
            tick_sizes={11: 0.05},
            baselines={11: OrbBaseline(11, "AAA", True, atr14=10.0, avg_or_volume=1000.0)},
            writer=writer,
            now_fn=lambda: seen_at["now"],
        )

        def tick(seq: int, hh: int, mm: int, ss: int, price: float, volume: int) -> None:
            when = datetime(2026, 9, 22, hh, mm, ss, tzinfo=IST)
            seen_at["now"] = when
            detector.on_tick(TickEvent(seq, 11, price, when, when, volume, 1, price,
                                       Ohlc(open=108.0, high=110.0, low=107.5, close=107.0)))

        tick(1, 9, 8, 5, 108.0, 500)
        tick(2, 9, 19, 59, 109.5, 4500)     # RVOL 4, range high 110
        tick(3, 9, 20, 0, 109.5, 4600)      # ranked and armed
        tick(4, 10, 29, 58, 110.0, 9000)    # the breakout; the engine clock reads 10:30:00

        floor = (self.clock.now - timedelta(minutes=1)).isoformat()
        engine = self.engine(
            candidate_source=lambda: fetch_arb_candidates_since(live_db, created_at_gte=floor)
        )
        engine.tick()

        stored = self.store.get("11|2026-09-22|ORB|orb_v1")
        self.assertIsNotNone(stored)
        self.assertEqual(stored.state, ExecutionState.PROTECTED)
        self.assertEqual(stored.stop_price, 106.5)          # 110 - 0.35 x 10
        self.assertEqual(stored.qty, 714)                   # floor(2500 / 3.5)
        self.assertEqual(stored.candidate.strategy, STRATEGY_ORB)
        info = self.arb(stored.trade_id)
        self.assertEqual((info["strategy"], info["orb"]["level"], info["orb"]["rvol"]), ("ORB", 110.0, 4.0))
        # The same row is never entered twice.
        engine.tick()
        self.assertEqual(self.broker.market_place_count, 1)


if __name__ == "__main__":
    unittest.main()
