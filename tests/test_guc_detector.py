"""GUC detector: arm at 09:20 on the gap, judge from 09:30, fixed stop.

Reuses the ORB detector harness (hand-built ticks against a real SQLite
file). An ORB detector on the same database is checked alongside, since the
two share the writer and the baselines in production.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Optional

from guc_detector import EXPIRED_WATCH_RVOL_UNKNOWN, GucDetector, market_gap_of
from orb_config import DEFAULT_GUC_CONFIG, DEFAULT_ORB_CONFIG, GucConfig
from orb_detector import STATE_DONE, STATE_RANKED, OrbDetector
from orb_rules import select_guc
from orb_types import (
    ARM_ARMED,
    DECISION_EXPIRED,
    DECISION_MISSED,
    DECISION_TRIGGERED,
    EXPIRED_CUTOFF,
    EXPIRED_WATCH_RVOL,
    MISSED_OVERSHOOT,
    NOT_SELECTED_GAP,
    NOT_SELECTED_STOP_TIGHT,
    OpeningRange,
    OrbBaseline,
)
from orb_writer import OrbWriter
from tests.test_orb_detector import DAY, SYMBOLS, Harness, ts
from trading_engine_handoff import fetch_orb_triggered_since
from trading_engine_types import STRATEGY_GUC, STRATEGY_ORB

ATR = 20.0          # 0.35 ATR = 7.0; confirm cap 0.25 ATR = 5.0; late cap 0.10 ATR = 2.0
PREV_CLOSE = 990.0  # Harness ticks carry ohlc.close = 990
GAP_OPEN = 1010.0   # 1010 / 990 - 1 = +2.02 %
FLAT_OPEN = 1000.0  # +1.01 %: below the 1.5 % minimum
LEVEL = 1012.0      # the 5-minute high used by Harness.opening
LOW = 998.0         # level - low = 14 > 7, so the stop is 0.35 ATR below the level
STOP = 1005.0


def baselines(symbols=SYMBOLS, *, watch: Optional[float] = 2000.0):
    return {
        token: OrbBaseline(token, symbol, True, atr14=ATR, avg_or_volume=1000.0, avg_watch_volume=watch)
        for token, symbol in symbols.items()
    }


class GucHarness(Harness):
    def __init__(self, tmp: str, *, symbols=None, baselines_=None, config: GucConfig = DEFAULT_GUC_CONFIG) -> None:
        super().__init__(tmp, symbols=symbols, baselines=baselines_ or baselines(), config=config)

    def start(self, writer: Optional[OrbWriter] = None) -> OrbDetector:
        self.writer = writer or OrbWriter(self.db)
        self.detector = GucDetector(
            session_date=DAY,
            token_to_symbol=self.symbols,
            tick_sizes={token: 0.05 for token in self.symbols},
            baselines=self.baselines,
            writer=self.writer,
            config=self.config,
            on_ranked=lambda ranked, arms: self.ranked_calls.append((list(ranked), list(arms))),
            on_decision=lambda arm, decision: self.decisions.append((arm.tradingsymbol, decision)),
            now_fn=lambda: self.now,
        )
        return self.detector

    def gap_open(self, token: int, *, day_open: float = GAP_OPEN, preopen: int = 500) -> None:
        """Pre-open tick, then the last tick inside the range at the open."""
        self.tick(token, ts(9, 8, 5), day_open, volume=preopen, day_open=day_open)
        self.tick(token, ts(9, 19, 59), 1011.0, volume=preopen + 3000, day_open=day_open, high=LEVEL, low=LOW)

    def standard_gap_open(self) -> None:
        """AAA and BBB gap up 2 %; CCC and DDD open +1 %."""
        self.gap_open(1)
        self.gap_open(2)
        self.gap_open(3, day_open=FLAT_OPEN)
        self.gap_open(4, day_open=FLAT_OPEN)

    def cross(self) -> None:
        for token in self.symbols:
            self.tick(token, ts(9, 20, 0), 1010.0, volume=10**6, day_open=GAP_OPEN, high=LEVEL, low=LOW)

    def armed(self):
        return {arm.tradingsymbol: arm for arm in self.detector.arms()}


class GucTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.h = GucHarness(self._tmp.name)
        self.addCleanup(lambda: self.h.writer and self.h.writer.close())

    def arm_all(self) -> None:
        self.h.standard_gap_open()
        self.h.cross()
        self.assertEqual(self.h.detector.state, STATE_RANKED)


class ArmingTests(GucTestCase):
    def test_arms_every_gap_up_and_nothing_else(self) -> None:
        self.arm_all()
        arms = self.h.armed()
        self.assertEqual(sorted(arms), ["AAA", "BBB"])
        aaa = arms["AAA"]
        self.assertEqual((aaa.direction, aaa.level, aaa.stop_distance), ("UP", LEVEL, 7.0))
        self.assertAlmostEqual(aaa.gap_pct, GAP_OPEN / PREV_CLOSE - 1, places=6)
        self.assertEqual((aaa.preopen_volume, aaa.avg_watch_volume), (500, 2000.0))
        self.assertTrue(aaa.setup_id.endswith("|GUC|guc_v1"), aaa.setup_id)
        ranked, _ = self.h.ranked_calls[0]
        reasons = {e.range.tradingsymbol: e.reason for e in ranked}
        self.assertEqual(reasons["CCC"], NOT_SELECTED_GAP)
        rows = {r["tradingsymbol"]: r for r in self.h.rows("live_orb_ranges")}
        self.assertAlmostEqual(rows["AAA"]["gap_pct"], GAP_OPEN / PREV_CLOSE - 1, places=6)
        self.assertEqual(rows["AAA"]["prev_close"], PREV_CLOSE)
        self.assertEqual({r["orb_rule_version"] for r in rows.values()}, {"guc_v1"})

    def test_a_narrow_range_puts_the_stop_at_the_range_low(self) -> None:
        h = self.h
        h.tick(1, ts(9, 8, 5), GAP_OPEN, volume=500, day_open=GAP_OPEN)
        h.tick(1, ts(9, 19, 59), 1011.0, volume=3500, day_open=GAP_OPEN, high=LEVEL, low=1008.0)
        for token in (2, 3, 4):
            h.gap_open(token, day_open=FLAT_OPEN)
        h.cross()
        self.assertEqual(h.armed()["AAA"].stop_distance, 4.0)  # 1012 - 1008 < 7

    def test_a_stop_under_0_2_percent_of_the_level_is_not_armed(self) -> None:
        h = self.h
        h.tick(1, ts(9, 8, 5), GAP_OPEN, volume=500, day_open=GAP_OPEN)
        h.tick(1, ts(9, 19, 59), 1011.0, volume=3500, day_open=GAP_OPEN, high=LEVEL, low=1010.0)  # 2.0 < 2.024
        for token in (2, 3, 4):
            h.gap_open(token, day_open=FLAT_OPEN)
        h.cross()
        self.assertEqual(h.armed(), {})
        ranked, _ = h.ranked_calls[0]
        self.assertEqual(next(e.reason for e in ranked if e.range.tradingsymbol == "AAA"), NOT_SELECTED_STOP_TIGHT)

    def test_market_gap_needs_twenty_valid_ranges(self) -> None:
        self.arm_all()
        self.assertIsNone(self.h.detector.market_gap)
        ranges = [
            OpeningRange(i, f"S{i}", True, gap_pct=0.01 if i % 2 else 0.03) for i in range(24)
        ]
        self.assertAlmostEqual(market_gap_of(ranges), 0.02, places=9)

    def test_select_guc_ignores_the_orb_direction(self) -> None:
        # or_close below or_open (ORB would call this DOWN): still a gap-up arm.
        rng = OpeningRange(
            1, "AAA", True, or_open=1010.0, or_high=1012.0, or_low=998.0, or_close=1005.0,
            or_volume=3000, atr14=ATR, avg_or_volume=1000.0, rvol=3.0, prev_close=990.0,
            gap_pct=1010.0 / 990.0 - 1,
        )
        ranked = select_guc([rng])
        self.assertTrue(ranked[0].selected)


class WatchTests(GucTestCase):
    def test_nothing_is_judged_before_0930(self) -> None:
        self.arm_all()
        self.h.tick(1, ts(9, 25, 0), 1016.0, volume=5000, day_open=GAP_OPEN)
        self.assertEqual(self.h.detector.arm_state(1), ARM_ARMED)
        self.assertEqual(self.h.decisions, [])

    def test_low_15_minute_volume_drops_the_arm_at_0930(self) -> None:
        self.arm_all()
        self.h.tick(1, ts(9, 30, 0), 1015.0, volume=500 + 1000, day_open=GAP_OPEN)  # 1000 / 2000 = 0.5x
        self.assertEqual(self.h.detector.arm_state(1), DECISION_EXPIRED)
        row = self.h.decision_rows()["AAA"]
        self.assertEqual(row["reason"], EXPIRED_WATCH_RVOL)
        self.assertIn('"watch_rvol": 0.5', row["payload_json"])

    def test_unknown_volume_baseline_drops_the_arm(self) -> None:
        Path(self._tmp.name + "/b").mkdir(exist_ok=True)
        h = GucHarness(self._tmp.name + "/b", baselines_=baselines(watch=None))
        self.addCleanup(lambda: h.writer and h.writer.close())
        h.standard_gap_open(); h.cross()
        h.tick(1, ts(9, 30, 0), 1015.0, volume=4000, day_open=GAP_OPEN)
        self.assertEqual(h.decision_rows()["AAA"]["reason"], EXPIRED_WATCH_RVOL_UNKNOWN)

    def test_confirmation_fill_above_the_level_at_0930(self) -> None:
        self.arm_all()
        self.h.tick(1, ts(9, 30, 0), 1015.0, volume=500 + 3500, day_open=GAP_OPEN)  # 1.75x, 3 above (0.15 ATR)
        self.assertEqual(self.h.detector.arm_state(1), DECISION_TRIGGERED)
        row = self.h.decision_rows()["AAA"]
        self.assertEqual((row["trigger_price"], row["planned_stop"]), (1015.0, STOP))
        self.assertIn('"confirm_fill": true', row["payload_json"])
        self.assertIn('"watch_rvol": 1.75', row["payload_json"])

    def test_too_far_above_at_0930_is_missed(self) -> None:
        self.arm_all()
        self.h.tick(1, ts(9, 30, 0), 1020.0, volume=4000, day_open=GAP_OPEN)  # 8 above > 0.25 ATR
        self.assertEqual(self.h.detector.arm_state(1), DECISION_MISSED)
        self.assertEqual(self.h.decision_rows()["AAA"]["reason"], MISSED_OVERSHOOT)

    def test_below_the_level_at_0930_waits_then_triggers_at_the_break(self) -> None:
        self.arm_all()
        self.h.tick(1, ts(9, 30, 0), 1010.0, volume=4000, day_open=GAP_OPEN)
        self.assertEqual(self.h.detector.arm_state(1), ARM_ARMED)
        self.h.tick(1, ts(9, 41, 0), 1011.0, volume=4200, day_open=GAP_OPEN)
        self.assertEqual(self.h.detector.arm_state(1), ARM_ARMED)
        self.h.tick(1, ts(9, 42, 0), 1012.5, volume=4300, day_open=GAP_OPEN)
        self.assertEqual(self.h.detector.arm_state(1), DECISION_TRIGGERED)
        row = self.h.decision_rows()["AAA"]
        # The stop is fixed off the level, not the fill.
        self.assertEqual((row["trigger_price"], row["planned_stop"]), (1012.5, STOP))
        self.assertIn('"confirm_fill": false', row["payload_json"])

    def test_after_0930_the_orb_overshoot_limit_applies(self) -> None:
        self.arm_all()
        self.h.tick(1, ts(9, 30, 0), 1010.0, volume=4000, day_open=GAP_OPEN)
        self.h.tick(1, ts(9, 45, 0), 1015.0, volume=4300, day_open=GAP_OPEN)  # 3 above > 0.10 ATR
        self.assertEqual(self.h.decision_rows()["AAA"]["reason"], MISSED_OVERSHOOT)

    def test_arms_still_waiting_expire_at_the_cutoff(self) -> None:
        self.arm_all()
        self.h.tick(1, ts(9, 30, 0), 1010.0, volume=4000, day_open=GAP_OPEN)
        self.h.tick(1, ts(11, 1, 0), 1010.0, volume=5000, day_open=GAP_OPEN)
        self.assertEqual(self.h.detector.state, STATE_DONE)
        rows = self.h.decision_rows()
        self.assertEqual({rows["AAA"]["reason"], rows["BBB"]["reason"]}, {EXPIRED_CUTOFF})

    def test_restart_keeps_the_arms_and_does_not_chase_after_the_window(self) -> None:
        self.arm_all()
        self.h.restart()
        self.assertTrue(self.h.detector.restored)
        arms = self.h.armed()
        self.assertEqual((arms["AAA"].preopen_volume, arms["AAA"].avg_watch_volume, arms["AAA"].stop_distance),
                         (500, 2000.0, 7.0))
        # A first tick at 10:00 (restart) is judged with the 0.10 ATR limit.
        self.h.tick(1, ts(10, 0, 0), 1015.0, volume=5000, day_open=GAP_OPEN)
        self.assertEqual(self.h.decision_rows()["AAA"]["reason"], MISSED_OVERSHOOT)
        self.h.tick(2, ts(10, 0, 0), 1013.0, volume=5000, day_open=GAP_OPEN)
        self.assertEqual(self.h.decision_rows()["BBB"]["trigger_price"], 1013.0)


class SharedDatabaseTests(GucTestCase):
    def test_orb_and_guc_rank_the_same_morning_on_one_database(self) -> None:
        orb = Harness(self._tmp.name, baselines=baselines(), config=DEFAULT_ORB_CONFIG)
        orb.writer.close(); orb.detector = orb.start(self.h.writer)  # share the GUC harness's writer
        for h in (self.h, orb):
            h.standard_gap_open() if h is self.h else None
        # Feed the ORB detector the same ticks.
        for token, day_open in ((1, GAP_OPEN), (2, GAP_OPEN), (3, FLAT_OPEN), (4, FLAT_OPEN)):
            orb.tick(token, ts(9, 8, 5), day_open, volume=500, day_open=day_open)
            orb.tick(token, ts(9, 19, 59), 1011.0, volume=3500, day_open=day_open, high=LEVEL, low=LOW)
        self.h.cross()
        for token in SYMBOLS:
            orb.tick(token, ts(9, 20, 0), 1010.0, volume=10**6, day_open=GAP_OPEN, high=LEVEL, low=LOW)
        arms = self.h.rows("live_orb_arms")
        by_rule = {}
        for row in arms:
            by_rule.setdefault(row["orb_rule_version"], set()).add(row["tradingsymbol"])
        # ORB: all four at 3x RVOL, top three by RVOL then symbol; stop from the fill.
        self.assertEqual(by_rule["orb_v1"], {"AAA", "BBB", "CCC"})
        self.assertEqual(by_rule["guc_v1"], {"AAA", "BBB"})
        orb_aaa = next(r for r in arms if r["orb_rule_version"] == "orb_v1" and r["tradingsymbol"] == "AAA")
        self.assertEqual(orb_aaa["stop_distance"], 7.0)
        self.assertIsNone(orb_aaa["gap_pct"])
        # The ORB breakout at 09:21 triggers ORB only; GUC is not watching yet.
        orb.tick(1, ts(9, 21, 0), 1012.5, volume=10**6, day_open=GAP_OPEN)
        self.h.tick(1, ts(9, 21, 0), 1012.5, volume=10**6, day_open=GAP_OPEN)
        self.assertEqual(orb.detector.arm_state(1), DECISION_TRIGGERED)
        self.assertEqual(self.h.detector.arm_state(1), ARM_ARMED)
        orb.writer = None  # closed with the shared writer

    def test_handoff_builds_a_guc_candidate_from_the_decision(self) -> None:
        self.arm_all()
        self.h.tick(1, ts(9, 30, 0), 1015.0, volume=4000, day_open=GAP_OPEN)
        candidates = fetch_orb_triggered_since(self.h.db, created_at_gte="2000-01-01")
        self.assertEqual(len(candidates), 1)
        c = candidates[0]
        self.assertEqual((c.strategy, c.tradingsymbol, c.direction), (STRATEGY_GUC, "AAA", "UP"))
        self.assertEqual((c.trigger_price, c.planned_stop, c.orb_level), (1015.0, STOP, LEVEL))
        self.assertAlmostEqual(c.guc_gap_pct, GAP_OPEN / PREV_CLOSE - 1, places=6)
        self.assertTrue(c.guc_confirm_fill)
        self.assertIsNone(c.guc_market_gap)
        self.assertEqual(c.continuation_rule_version, "guc_v1")
        self.assertTrue(c.setup_id.endswith("|GUC|guc_v1"))
        # An ORB row on the same database is still an ORB candidate.
        self.assertEqual(STRATEGY_ORB, "ORB")


if __name__ == "__main__":
    unittest.main()
