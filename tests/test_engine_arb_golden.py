"""Parity with the ARB backtest (Reference/arb_strategy_2026-09).

The fixtures in tests/fixtures/arb_golden were exported by
Reference/arb_strategy_2026-09/code/export_golden.py from the research
folder. Each test feeds the backtest's own inputs through the live port and
demands the backtest's own answers:

* funnel.json:  every 2026 funnel trigger (4,217): filter pass and score.
* budget.json:  every ARB sizing decision (1,154): risk taken, or skipped.
* days.json:    every trading day (182): the minute and reason of any halt.
* context.json: sample triggers: market and sector alignment from 1m bars.
"""
from __future__ import annotations

import json
import math
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import engine_clock
from engine_arb import (
    ArbSettings,
    DayLockTracker,
    DayMtm,
    FunnelFeatures,
    allowed_risk,
    classify_funnel,
    hard_stop_hit,
)
from engine_arb_context import LiveArbContext, stop_pct_of, trigger_moment_ist
from engine_priority import volume_ratio
from engine_types import TriggerCandidate

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "arb_golden"
FULL = ArbSettings()


def _load(name: str):
    return json.loads((FIXTURES / name).read_text())


def _candidate(**overrides) -> TriggerCandidate:
    fields = dict(
        setup_id="s",
        continuation_rule_version="v1",
        session_date="2026-01-01",
        tradingsymbol="AAA",
        instrument_token=1,
        direction="UP",
        trigger_price=100.0,
        pullback_swing_high=None,
        pullback_swing_low=None,
        tick_size=0.05,
        buffer_ticks=1,
        trigger_exchange_ts=None,
        created_at="2026-01-01T04:00:00+00:00",
    )
    fields.update(overrides)
    return TriggerCandidate(**fields)


class FunnelParityTests(unittest.TestCase):
    def test_every_2026_trigger_filters_and_scores_like_the_backtest(self) -> None:
        rows = _load("funnel.json")
        self.assertEqual(len(rows), 4217)
        mismatches = []
        for (direction, trigger_ts, price, stop, volume, average, first,
             align_mkt, align_sec, expected_ok, expected_score) in rows:
            candidate = _candidate(
                direction=direction,
                trigger_price=float(price),
                trigger_exchange_ts=trigger_ts,
                breakout_candle_volume=volume,
                avg_prior_3_1m_volume=average,
            )
            moment = trigger_moment_ist(candidate)
            features = FunnelFeatures(
                trigger_time_ist=moment.time().replace(tzinfo=None),
                volume_ratio=volume_ratio(candidate),
                stop_pct=stop_pct_of(candidate, stop),
                is_first_trigger_today=bool(first),
                align_market_pct=align_mkt,
                align_sector_pct=align_sec,
            )
            verdict = classify_funnel(features, FULL, score0_taken_today=0)
            if verdict.ok != expected_ok or (expected_ok and verdict.score != expected_score):
                mismatches.append((trigger_ts, verdict, expected_ok, expected_score))
        self.assertEqual(mismatches, [])
        self.assertEqual(sum(r[9] for r in rows), 869)


class BudgetParityTests(unittest.TestCase):
    def test_every_arb_sizing_decision_matches_the_backtest(self) -> None:
        rows = _load("budget.json")
        self.assertEqual(len(rows), 1154)
        skips = 0
        for row in rows:
            decision = allowed_risk(
                FULL,
                base_rupees=float(row["base"]),
                losers_today=int(row["losers"]),
                day_mtm_rupees=float(row["mtm"]),
                live_risk_rupees=float(row["live"]),
            )
            expected = float(row["expected_risk"])
            with self.subTest(day=row["day"], ts=row["ts"]):
                if expected == 0:
                    skips += 1
                    self.assertEqual(decision.risk_rupees, 0.0)
                else:
                    self.assertAlmostEqual(decision.risk_rupees, expected, places=6)
                    self.assertEqual(decision.cut, int(row["losers"]) >= 1)
        self.assertEqual(skips, 56)


def _minute(day: str, minute_of_day: int) -> datetime:
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, minute_of_day // 60, minute_of_day % 60, tzinfo=engine_clock.IST)


class DayControlParityTests(unittest.TestCase):
    def test_every_day_halts_at_the_same_minute_for_the_same_reason(self) -> None:
        days = _load("days.json")
        self.assertEqual(len(days), 182)
        halts = both_held = 0
        for day in days:
            tracker = DayLockTracker(FULL)
            got_minute = got_reason = None
            for minute_of_day, eq in day["path"]:
                mtm = DayMtm(mtm=float(eq), closed_net=0.0, open_net=float(eq))
                # The engine's order: the hard stop first, then the lock sample.
                if hard_stop_hit(FULL, mtm.mtm):
                    got_minute, got_reason = minute_of_day, "hard_day_stop"
                    if tracker.observe(_minute(day["day"], minute_of_day), mtm):
                        both_held += 1
                        got_reason = "day_lock"  # the backtest checked the lock first
                    break
                if tracker.observe(_minute(day["day"], minute_of_day), mtm):
                    got_minute, got_reason = minute_of_day, "day_lock"
                    break
            with self.subTest(day=day["day"]):
                self.assertEqual(got_minute, day["halt_minute"])
                self.assertEqual(got_reason, day["halt_reason"])
            halts += got_minute is not None
        self.assertEqual(halts, 117)
        self.assertEqual(both_held, 0)


_BARS_DDL = """
CREATE TABLE live_1m_candles (
    tradingsymbol TEXT, candle_time TEXT, session_date TEXT, open REAL, close REAL
)
"""
_ARMS_DDL = """
CREATE TABLE live_continuation_arms (
    setup_id TEXT, continuation_rule_version TEXT, session_date TEXT, tradingsymbol TEXT
)
"""
_DECISIONS_DDL = """
CREATE TABLE live_continuation_decisions (
    setup_id TEXT, continuation_rule_version TEXT, decision_type TEXT,
    trigger_exchange_ts TEXT, created_at TEXT
)
"""


class ContextParityTests(unittest.TestCase):
    def test_alignment_from_1m_bars_matches_the_research_features(self) -> None:
        samples = _load("context.json")
        self.assertEqual(len(samples), 8)
        for sample in samples:
            with tempfile.TemporaryDirectory() as tmp, self.subTest(setup=sample["setup_id"]):
                db = Path(tmp) / "live.db"
                conn = sqlite3.connect(db)
                conn.execute(_BARS_DDL)
                conn.execute(_ARMS_DDL)
                conn.execute(_DECISIONS_DDL)
                conn.executemany(
                    "INSERT INTO live_1m_candles VALUES (?, ?, ?, ?, ?)",
                    [(s, t, sample["session_date"], o, c) for s, t, o, c in sample["bars"]],
                )
                conn.commit()
                conn.close()
                candidate = _candidate(
                    session_date=sample["session_date"],
                    tradingsymbol=sample["symbol"],
                    direction=sample["direction"],
                    trigger_exchange_ts=sample["trigger_ts"],
                )
                features = LiveArbContext(db).features_for(candidate, None)
                self.assertTrue(
                    math.isclose(
                        features.align_market_pct, sample["expected_align_mkt"], rel_tol=1e-9, abs_tol=1e-12
                    )
                )
                expected_sector = sample["expected_align_sec"]
                if expected_sector is None:
                    self.assertIsNone(features.align_sector_pct)
                else:
                    self.assertTrue(
                        math.isclose(
                            features.align_sector_pct, expected_sector, rel_tol=1e-9, abs_tol=1e-12
                        )
                    )


if __name__ == "__main__":
    unittest.main()
