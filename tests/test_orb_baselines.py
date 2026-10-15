"""ORB baselines: the arithmetic, and when a baseline is not trusted."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

from orb_baselines import (
    baseline_from_days,
    compute_baseline_values,
    load_orb_baselines,
    ready_count,
    summarise_sessions,
)
from orb_config import DEFAULT_ORB_CONFIG
from orb_types import (
    BASELINE_CALENDAR,
    BASELINE_DEGENERATE,
    BASELINE_GAP,
    BASELINE_INCOMPLETE,
    BASELINE_INSUFFICIENT,
    BASELINE_NO_HISTORY,
    BASELINE_STALE,
    DaySummary,
)

TOKEN, SYMBOL = 101, "AAA"


def weekdays_before(end: str, count: int) -> List[str]:
    """The ``count`` weekdays strictly before ``end``, oldest first."""
    day = date.fromisoformat(end)
    out: List[str] = []
    while len(out) < count:
        day -= timedelta(days=1)
        if day.weekday() < 5:
            out.append(day.isoformat())
    return list(reversed(out))


def sessions_before(end: str, count: int) -> List[str]:
    """The ``count`` NSE trading sessions strictly before ``end``, oldest first."""
    from nse_trading_calendar import prior_nse_trading_session

    out: List[str] = []
    day = end
    while len(out) < count:
        day = prior_nse_trading_session(day)
        out.append(day)
    return list(reversed(out))


def day_summary(session_date: str, *, high=110.0, low=100.0, close=105.0, or_volume=1000,
                or_bars=5, first_bar="09:15", last_bar="15:29") -> DaySummary:
    return DaySummary(session_date, high, low, close, or_volume, or_bars, first_bar, last_bar)


def flat_days(end: str, count: int = 15, **overrides) -> List[DaySummary]:
    return [day_summary(d, **overrides) for d in weekdays_before(end, count)]


class SummariseTests(unittest.TestCase):
    def test_one_row_per_day_with_high_low_last_close_and_opening_volume(self) -> None:
        rows = [
            ("2026-09-21T09:15:00+05:30", 101.0, 99.0, 100.0, 500),
            ("2026-09-21T09:19:00+05:30", 103.0, 100.0, 102.0, 300),
            ("2026-09-21T09:20:00+05:30", 104.0, 98.0, 101.0, 200),   # not opening volume
            ("2026-09-21T15:29:00+05:30", 102.0, 100.5, 101.5, 50),
            ("2026-09-22T09:15:00+05:30", 90.0, 88.0, 89.0, 700),
        ]
        days = summarise_sessions(rows)
        self.assertEqual([d.session_date for d in days], ["2026-09-21", "2026-09-22"])
        first = days[0]
        self.assertEqual((first.high, first.low, first.close), (104.0, 98.0, 101.5))
        self.assertEqual((first.or_volume, first.or_bars), (800, 2))
        self.assertEqual((first.first_bar, first.last_bar), ("09:15", "15:29"))
        self.assertEqual((days[1].or_volume, days[1].or_bars, days[1].last_bar), (700, 1, "09:15"))

    def test_no_candles_gives_no_days(self) -> None:
        self.assertEqual(summarise_sessions([]), [])


class ValuesTests(unittest.TestCase):
    def test_true_range_uses_the_previous_close(self) -> None:
        days = flat_days("2026-09-25")
        # Day 2 gaps up: its true range reaches back to day 1's close.
        days[1] = day_summary(days[1].session_date, high=130.0, low=125.0, close=128.0)
        days[2] = day_summary(days[2].session_date, high=129.0, low=127.0, close=128.0)
        atr, average = compute_baseline_values(days)
        # 14 true ranges: 25 (130 vs close 105), 2 (129-127), 28 (close 128 vs
        # low 100 on day 4), then eleven plain 10s.
        self.assertAlmostEqual(atr, (25 + 2 + 28 + 11 * 10) / 14)
        self.assertEqual(average, 1000.0)

    def test_only_the_last_fourteen_sessions_feed_the_volume_average(self) -> None:
        days = flat_days("2026-09-25")
        days[0] = day_summary(days[0].session_date, or_volume=10**9)  # the 15th-back day
        self.assertEqual(compute_baseline_values(days)[1], 1000.0)

    def test_extra_older_sessions_are_ignored(self) -> None:
        days = flat_days("2026-09-25", 30)
        self.assertEqual(compute_baseline_values(days), compute_baseline_values(days[-15:]))

    def test_too_few_sessions_is_an_error(self) -> None:
        with self.assertRaises(ValueError):
            compute_baseline_values(flat_days("2026-09-25", 14))


class ReadinessTests(unittest.TestCase):
    def build(self, days, prior: Optional[str] = "2026-09-24"):
        return baseline_from_days(
            instrument_token=TOKEN, tradingsymbol=SYMBOL, days=days, prior_session=prior
        )

    def test_fresh_whole_history_is_ready(self) -> None:
        baseline = self.build(flat_days("2026-09-25"))
        self.assertTrue(baseline.ready)
        self.assertEqual(baseline.atr14, 10.0)
        self.assertEqual(baseline.avg_or_volume, 1000.0)
        self.assertEqual(baseline.history_as_of, "2026-09-24")
        self.assertEqual(len(baseline.sessions), 15)
        self.assertEqual(baseline.min_or_bars, 5)

    def test_no_history(self) -> None:
        baseline = self.build([])
        self.assertFalse(baseline.ready)
        self.assertEqual(baseline.reason, BASELINE_NO_HISTORY)

    def test_fourteen_sessions_are_not_enough(self) -> None:
        baseline = self.build(flat_days("2026-09-25", 14))
        self.assertEqual(baseline.reason, BASELINE_INSUFFICIENT)
        self.assertIsNone(baseline.atr14)

    def test_missing_yesterday_is_stale(self) -> None:
        baseline = self.build(flat_days("2026-09-24"))  # ends on the 23rd
        self.assertEqual(baseline.reason, BASELINE_STALE)
        self.assertIn("2026-09-23", baseline.detail)

    def test_an_unknown_previous_trading_day_blocks(self) -> None:
        self.assertEqual(self.build(flat_days("2026-09-25"), prior=None).reason, BASELINE_CALENDAR)

    def test_a_session_newer_than_the_calendar_expects_is_not_stale(self) -> None:
        # A special weekend session the calendar does not list.
        self.assertTrue(self.build(flat_days("2026-09-25"), prior="2026-09-23").ready)

    def test_a_day_cut_short_is_incomplete(self) -> None:
        days = flat_days("2026-09-25")
        days[-1] = day_summary(days[-1].session_date, last_bar="13:02")
        baseline = self.build(days)
        self.assertEqual(baseline.reason, BASELINE_INCOMPLETE)
        self.assertIn("13:02", baseline.detail)

    def test_the_1514_close_of_closing_auction_stocks_is_a_whole_day(self) -> None:
        # Since 3 Aug 2026 most stocks' continuous session ends at 15:15.
        self.assertTrue(self.build(flat_days("2026-09-25", last_bar="15:14")).ready)

    def test_a_volume_day_without_opening_candles_is_incomplete(self) -> None:
        days = flat_days("2026-09-25")
        days[5] = day_summary(days[5].session_date, or_volume=0, or_bars=0)
        self.assertEqual(self.build(days).reason, BASELINE_INCOMPLETE)

    def test_fewer_opening_candles_are_reported_not_refused(self) -> None:
        days = flat_days("2026-09-25")
        days[5] = day_summary(days[5].session_date, or_bars=4)
        baseline = self.build(days)
        self.assertTrue(baseline.ready)
        self.assertEqual(baseline.min_or_bars, 4)

    def test_zero_range_or_zero_volume_is_degenerate(self) -> None:
        self.assertEqual(
            self.build(flat_days("2026-09-25", high=100.0, low=100.0, close=100.0)).reason,
            BASELINE_DEGENERATE,
        )
        self.assertEqual(
            self.build(flat_days("2026-09-25", or_volume=0)).reason, BASELINE_DEGENERATE
        )


class LoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "hist.db"
        self.conn = sqlite3.connect(self.db)
        self.conn.execute(
            """
            CREATE TABLE candles (
                instrument_token INTEGER NOT NULL, tradingsymbol TEXT NOT NULL,
                candle_time TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL,
                low REAL NOT NULL, close REAL NOT NULL, volume INTEGER NOT NULL,
                PRIMARY KEY (instrument_token, candle_time))
            """
        )
        self.addCleanup(self.conn.close)

    def add_day(self, day: str, *, token: int = TOKEN, last: str = "15:29", volume: int = 200) -> None:
        bars = [f"09:{m:02d}" for m in range(15, 21)] + [last]
        self.conn.executemany(
            "INSERT INTO candles VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (token, SYMBOL, f"{day}T{hm}:00+05:30", 100.0, 110.0, 100.0, 105.0, volume)
                for hm in bars
            ],
        )
        self.conn.commit()

    def load(self, session: str = "2026-09-25", **kwargs):
        return load_orb_baselines(self.db, session, {TOKEN: SYMBOL}, **kwargs)[TOKEN]

    def test_reads_only_sessions_before_today(self) -> None:
        for day in sessions_before("2026-09-25", 15):
            self.add_day(day)
        self.add_day("2026-09-25", volume=10**9)  # today's candles must not be used
        baseline = self.load()
        self.assertTrue(baseline.ready, baseline)
        self.assertEqual(baseline.avg_or_volume, 1000.0)  # five opening bars of 200
        self.assertEqual(baseline.atr14, 10.0)
        self.assertEqual(baseline.history_as_of, "2026-09-24")

    def test_stale_when_the_previous_trading_day_is_missing(self) -> None:
        for day in sessions_before("2026-09-24", 15):
            self.add_day(day)
        self.assertEqual(self.load().reason, BASELINE_STALE)

    def test_previous_trading_day_skips_the_holiday(self) -> None:
        # 14 Sep 2026 is an NSE holiday: on the 15th, Friday the 11th is fresh.
        days = [d for d in sessions_before("2026-09-15", 16) if d != "2026-09-14"]
        for day in days:
            self.add_day(day)
        self.assertTrue(self.load("2026-09-15").ready)

    def test_special_sessions_are_left_out_of_the_window(self) -> None:
        for day in sessions_before("2026-09-25", 15):
            self.add_day(day)
        self.add_day("2026-09-20", volume=10**9)  # a Sunday special session
        with_special = self.load()
        self.assertEqual(with_special.avg_or_volume > 10**6, True)
        skipped = self.load(is_special_fn=lambda d: d.isoformat() == "2026-09-20")
        self.assertEqual(skipped.avg_or_volume, 1000.0)

    def test_a_trading_session_missing_mid_window_is_a_gap(self) -> None:
        # The server's history stopped for three weeks: yesterday is fresh, but
        # ATR14 would fold those weeks into one true range.
        days = sessions_before("2026-09-25", 15)
        for day in days[:-1]:
            if day not in (days[5], days[6]):
                self.add_day(day)
        self.add_day(days[-1])
        for day in sessions_before(days[0], 5):  # older history still fills 15 rows
            self.add_day(day)
        baseline = self.load()
        self.assertFalse(baseline.ready)
        self.assertEqual(baseline.reason, BASELINE_GAP)
        self.assertEqual(baseline.detail, f"no candles for {days[5]} and 1 more")

    def test_the_gap_check_follows_the_calendar_not_weekdays(self) -> None:
        # 14 Sep 2026 is a holiday: no candles that day is not a gap.
        days = sessions_before("2026-09-25", 15)
        self.assertNotIn("2026-09-14", days)
        for day in days:
            self.add_day(day)
        self.assertTrue(self.load().ready)

    def test_a_calendar_that_cannot_reach_back_leaves_every_stock_out(self) -> None:
        for day in sessions_before("2026-09-25", 15):
            self.add_day(day)
        calendar = {"2026-09-25": "2026-09-24", "2026-09-24": "2026-09-23"}
        baseline = self.load(prior_session_fn=calendar.get)
        self.assertEqual(baseline.reason, BASELINE_CALENDAR)

    def test_yesterday_cut_short_is_incomplete(self) -> None:
        days = sessions_before("2026-09-25", 15)
        for day in days[:-1]:
            self.add_day(day)
        self.add_day(days[-1], last="12:40")
        self.assertEqual(self.load().reason, BASELINE_INCOMPLETE)

    def test_missing_database_leaves_every_stock_not_ready(self) -> None:
        out = load_orb_baselines(Path(self._tmp.name) / "nope.db", "2026-09-25", {1: "A", 2: "B"})
        self.assertEqual(ready_count(out), 0)
        self.assertEqual({b.reason for b in out.values()}, {BASELINE_NO_HISTORY})

    def test_a_database_without_the_table_does_not_raise(self) -> None:
        empty = Path(self._tmp.name) / "empty.db"
        sqlite3.connect(empty).close()
        out = load_orb_baselines(empty, "2026-09-25", {TOKEN: SYMBOL})
        self.assertFalse(out[TOKEN].ready)
        self.assertEqual(out[TOKEN].reason, BASELINE_NO_HISTORY)

    def test_a_stock_with_no_rows_is_not_ready(self) -> None:
        for day in sessions_before("2026-09-25", 15):
            self.add_day(day)
        out = load_orb_baselines(self.db, "2026-09-25", {TOKEN: SYMBOL, 999: "ZZZ"})
        self.assertTrue(out[TOKEN].ready)
        self.assertEqual(out[999].reason, BASELINE_NO_HISTORY)

    def test_sessions_needed_matches_the_backtest(self) -> None:
        self.assertEqual(DEFAULT_ORB_CONFIG.sessions_needed, 15)


if __name__ == "__main__":
    unittest.main()
