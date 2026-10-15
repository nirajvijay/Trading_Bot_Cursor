"""Parity of the live ORB rules with the backtest (Reference/arb_strategy_2026-09).

The fixtures in tests/fixtures/orb_golden were exported by
Reference/arb_strategy_2026-09/code/export_orb_golden.py from the research
folder, which re-derives them from the 12-month candle DB with research
orb.py's arithmetic and checks them against orb.json before writing. Each
test feeds the backtest's own inputs through the live code and demands the
backtest's own answers, to the last bit:

* baselines.json: every armed stock-day (431): ATR14 and average opening volume.
* days.json:      every 2026 day (182): the opening ranges, the RVOL ranking
                  and the three stocks armed at 09:20.
* breakouts.json: every armed stock-day: the breakout minute, entry and stop.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from orb_baselines import baseline_from_days, compute_baseline_values
from orb_config import DEFAULT_ORB_CONFIG
from orb_rules import (
    MinuteBar,
    first_breakout_bar,
    planned_stop,
    range_from_candles,
    rank_ranges,
    stop_distance,
)
from orb_types import DaySummary, OrbBaseline

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "orb_golden"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text())


def _ready(symbol: str, atr: float, average: float, token: int = 1) -> OrbBaseline:
    return OrbBaseline(
        instrument_token=token,
        tradingsymbol=symbol,
        ready=True,
        atr14=atr,
        avg_or_volume=average,
    )


class SummaryTests(unittest.TestCase):
    def test_the_fixture_covers_the_whole_backtest_period(self) -> None:
        summary = _load("summary.json")
        self.assertEqual(summary["days"], 182)
        self.assertEqual(summary["armed"], 431)
        self.assertEqual(summary["broke_out"], 314)
        rule = summary["rule"]
        self.assertEqual(rule["min_rvol"], DEFAULT_ORB_CONFIG.min_rvol)
        self.assertEqual(rule["top_n"], DEFAULT_ORB_CONFIG.top_n)
        self.assertEqual(rule["stop_atr_mult"], DEFAULT_ORB_CONFIG.stop_atr_mult)


class BaselineParityTests(unittest.TestCase):
    def test_atr_and_average_opening_volume_match_the_backtest_exactly(self) -> None:
        rows = _load("baselines.json")
        self.assertEqual(len(rows), 431)
        mismatches = []
        for symbol, day, sessions, expected_atr, expected_average in rows:
            days = [
                DaySummary(
                    session_date=d,
                    high=high,
                    low=low,
                    close=close,
                    or_volume=or_volume,
                    or_bars=or_bars,
                    first_bar=first_bar,
                    last_bar=last_bar,
                )
                for d, high, low, close, or_volume, or_bars, first_bar, last_bar in sessions
            ]
            atr, average = compute_baseline_values(days)
            if atr != expected_atr or average != expected_average:
                mismatches.append((symbol, day, atr, expected_atr, average, expected_average))
                continue
            # The live readiness checks must accept this same history.
            baseline = baseline_from_days(
                instrument_token=1,
                tradingsymbol=symbol,
                days=days,
                prior_session=days[-1].session_date,
            )
            if not baseline.ready or baseline.atr14 != expected_atr:
                mismatches.append((symbol, day, "not ready", baseline.reason, baseline.detail))
        self.assertEqual(mismatches, [])


class RankingParityTests(unittest.TestCase):
    def test_every_day_arms_the_same_three_stocks_at_the_same_levels(self) -> None:
        days = _load("days.json")
        self.assertEqual(len(days), 182)
        mismatches = []
        armed_total = 0
        for record in days:
            ranges = []
            for token, (symbol, atr, average, bars) in enumerate(record["stocks"], start=1):
                ranges.append(
                    range_from_candles(
                        instrument_token=token,
                        tradingsymbol=symbol,
                        baseline=_ready(symbol, atr, average, token),
                        bars=[MinuteBar(*bar) for bar in bars],
                    )
                )
            ranked = rank_ranges(ranges)
            got = [
                [e.range.tradingsymbol, e.range.direction, e.range.level, e.range.rvol, e.rank]
                for e in ranked
                if e.selected
            ]
            in_play = sum(1 for e in ranked if e.rank is not None)
            armed_total += len(got)
            if got != record["expected"] or in_play != record["in_play"]:
                mismatches.append((record["day"], got, record["expected"], in_play))
        self.assertEqual(mismatches, [])
        self.assertEqual(armed_total, 431)

    def test_the_fixture_exercises_invalid_ranges_near_the_line(self) -> None:
        # Stocks with heavy opening volume but no direction, or a missing
        # opening candle, must be left out before the ranking, as the
        # backtest did. Make sure the data actually contains such cases.
        invalid = 0
        for record in _load("days.json"):
            for token, (symbol, atr, average, bars) in enumerate(record["stocks"], start=1):
                built = range_from_candles(
                    instrument_token=token,
                    tradingsymbol=symbol,
                    baseline=_ready(symbol, atr, average, token),
                    bars=[MinuteBar(*bar) for bar in bars],
                )
                invalid += not built.valid
        self.assertGreater(invalid, 0)


class BreakoutParityTests(unittest.TestCase):
    def test_breakout_minute_entry_and_stop_match_the_backtest(self) -> None:
        rows = _load("breakouts.json")
        self.assertEqual(len(rows), 431)
        mismatches = []
        broke_out = 0
        for symbol, day, direction, level, atr, bars, expected in rows:
            minute_bars = [
                MinuteBar(time=hm, open=o, high=h, low=l, close=o, volume=0)
                for hm, o, h, l in bars
            ]
            hit = first_breakout_bar(minute_bars, direction=direction, level=level)
            if expected is None:
                if hit is not None:
                    mismatches.append((symbol, day, "unexpected breakout", hit[0].time))
                continue
            broke_out += 1
            expected_minute, expected_entry, expected_stop = expected
            if hit is None or hit[0].time != expected_minute or hit[1] != expected_entry:
                mismatches.append((symbol, day, hit and (hit[0].time, hit[1]), expected))
                continue
            sign = 1 if direction == "UP" else -1
            distance = stop_distance(atr)
            if abs((expected_entry - sign * distance) - expected_stop) > 1e-9:
                mismatches.append((symbol, day, "stop", expected_entry - sign * distance, expected_stop))
                continue
            # The live stop is the same price moved onto the tick grid, away
            # from the trade: never tighter, and less than one tick wider.
            live = planned_stop(
                direction=direction,
                reference_price=expected_entry,
                distance=distance,
                tick_size=0.05,
            )
            widened = (expected_stop - live) * sign if live is not None else None
            if widened is None or not (-1e-9 <= widened < 0.05 + 1e-9):
                mismatches.append((symbol, day, "tick stop", live, expected_stop))
        self.assertEqual(mismatches, [])
        self.assertEqual(broke_out, 314)


if __name__ == "__main__":
    unittest.main()
