"""Post-market ORB parity: live ticks against the backtest's rule on candles."""
from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple
from zoneinfo import ZoneInfo

from orb_baselines import load_orb_baselines
from orb_detector import OrbDetector
from orb_parity import ParityError, compare_day, format_report
from orb_writer import OrbWriter
from scripts import orb_parity_check
from tests.test_orb_baselines import weekdays_before
from tick_event import Ohlc, TickEvent

IST = ZoneInfo("Asia/Kolkata")
DAY = "2026-09-25"
SYMBOLS = {1: "AAA", 2: "BBB", 3: "CCC"}
# symbol -> (open, high, low, close) of the opening range, opening volume, pre-open volume
OPENING: Dict[str, Tuple[float, float, float, float, int, int]] = {
    "AAA": (1000.0, 1012.0, 998.0, 1010.0, 5000, 700),   # 5x, up
    "BBB": (500.0, 501.0, 495.0, 496.0, 4000, 300),      # 4x, down
    "CCC": (200.0, 201.0, 199.0, 200.5, 1500, 100),      # 1.5x: not in play
}


class ParityTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.hist = Path(self._tmp.name) / "hist.db"
        self.live = Path(self._tmp.name) / "live.db"
        conn = sqlite3.connect(self.hist)
        conn.execute(
            "CREATE TABLE candles (instrument_token INTEGER NOT NULL, tradingsymbol TEXT NOT NULL, "
            "candle_time TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, "
            "close REAL NOT NULL, volume INTEGER NOT NULL, PRIMARY KEY (instrument_token, candle_time))"
        )
        # Fifteen plain prior sessions: 1,000 shares in the first five minutes,
        # a 20-point daily range (ATR 20).
        for token, symbol in SYMBOLS.items():
            base = OPENING[symbol][0]
            for day in weekdays_before(DAY, 15):
                for i, hm in enumerate(["09:15", "09:16", "09:17", "09:18", "09:19", "15:29"]):
                    conn.execute(
                        "INSERT INTO candles VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (token, symbol, f"{day}T{hm}:00+05:30", base, base + 10, base - 10, base,
                         200 if i < 5 else 50),
                    )
        conn.commit()
        conn.close()

    # -- the historical side: today's candles -----------------------------

    def candles(self, symbol: str, post: List[Tuple[str, float, float, float]], *,
                opening: Tuple[float, float, float, float, int, int] = None) -> None:
        token = next(t for t, s in SYMBOLS.items() if s == symbol)
        o, h, l, c, volume, _ = opening or OPENING[symbol]
        rows = [
            ("09:15", o, max(o, c) if h is None else h, l, o, volume - 400),
            ("09:16", o, h, l, o, 100), ("09:17", o, h, l, o, 100),
            ("09:18", o, h, l, o, 100), ("09:19", o, h, l, c, 100),
        ] + [(hm, po, ph, pl, po, 10) for hm, po, ph, pl in post]
        with sqlite3.connect(self.hist) as conn:
            conn.executemany(
                "INSERT INTO candles VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(token, symbol, f"{DAY}T{hm}:00+05:30", bo, bh, bl, bc, bv)
                 for hm, bo, bh, bl, bc, bv in rows],
            )

    # -- the live side: the detector on ticks ------------------------------

    def run_live(self, opening: Dict[str, Tuple] = None,
                 after: List[Tuple[str, int, int, int, float]] = ()) -> None:
        opening = {**OPENING, **(opening or {})}
        baselines = load_orb_baselines(self.hist, DAY, SYMBOLS)
        writer = OrbWriter(self.live)
        self.addCleanup(writer.close)
        seen_at = {}
        detector = OrbDetector(session_date=DAY, token_to_symbol=SYMBOLS,
                               tick_sizes={t: 0.05 for t in SYMBOLS}, baselines=baselines, writer=writer,
                               now_fn=lambda: seen_at["now"])
        seq = 0

        def tick(symbol: str, hh: int, mm: int, ss: int, price: float, volume: int) -> None:
            nonlocal seq
            seq += 1
            token = next(t for t, s in SYMBOLS.items() if s == symbol)
            o, h, l, _, _, _ = opening[symbol]
            when = datetime(2026, 9, 25, hh, mm, ss, tzinfo=IST)
            seen_at["now"] = when
            detector.on_tick(TickEvent(seq, token, price, when, when, volume, 1, price,
                                       Ohlc(open=o, high=h, low=l, close=o)))

        for symbol, (o, h, l, c, volume, preopen) in opening.items():
            tick(symbol, 9, 8, 5, o, preopen)
            tick(symbol, 9, 19, 59, c, preopen + volume)
        for symbol in opening:
            tick(symbol, 9, 20, 0, opening[symbol][3], 10**6)
        for symbol, hh, mm, ss, price in after:
            tick(symbol, hh, mm, ss, price, 10**6)

    def compare(self) -> dict:
        return compare_day(live_db=self.live, historical_db=self.hist, session_date=DAY)


class AgreementTests(ParityTestCase):
    def test_a_morning_that_matches_the_backtest(self) -> None:
        self.candles("AAA", [("09:20", 1010.0, 1011.0, 1009.0), ("09:23", 1011.0, 1013.0, 1010.5)])
        self.candles("BBB", [("09:20", 496.0, 497.0, 495.5), ("10:00", 496.0, 496.5, 495.5)])
        self.candles("CCC", [("09:20", 200.5, 200.8, 200.0)])
        self.run_live(after=[("AAA", 9, 23, 11, 1012.0)])
        result = self.compare()
        self.assertEqual(result["stocks_compared"], 3)
        self.assertEqual(result["equal"], {"open": 3, "high": 3, "low": 3, "close": 3,
                                           "direction": 3, "baseline": 3})
        self.assertEqual(result["volume_ratio_live_to_candles"]["median"], 1.0)
        self.assertGreater(result["volume_ratio_with_auction_added_back"]["median"], 1.05)
        self.assertEqual([a["symbol"] for a in result["live_arms"]], ["AAA", "BBB"])
        self.assertTrue(result["same_selection"])
        self.assertTrue(result["same_arms"])
        self.assertTrue(result["same_outcomes"])
        self.assertEqual(result["range_differences_near_the_line"], [])
        aaa, bbb = result["breakouts"]
        self.assertEqual((aaa["live"]["decision"], aaa["replay"]["minute"], aaa["minutes_apart"]),
                         ("TRIGGERED", "09:23", 0))
        self.assertEqual(aaa["price_vs_replay_bps"], 0.0)
        self.assertEqual((bbb["live"]["decision"], bbb["replay"]["minute"], bbb["same_outcome"]),
                         ("ARMED", None, True))
        report = format_report(result)
        self.assertIn("same stocks, directions and levels", report)
        self.assertNotIn("differs", report)

    def test_a_live_trigger_price_worse_than_the_backtest_entry_is_measured(self) -> None:
        self.candles("AAA", [("09:23", 1011.0, 1013.0, 1010.5)])
        self.candles("BBB", [])
        self.candles("CCC", [])
        self.run_live(after=[("AAA", 9, 23, 11, 1013.0)])      # one rupee through the level
        aaa = self.compare()["breakouts"][0]
        self.assertAlmostEqual(aaa["price_vs_replay_bps"], 1.0 / 1012.0 * 1e4, places=2)


class DisagreementTests(ParityTestCase):
    def test_a_breakout_the_live_feed_never_saw(self) -> None:
        self.candles("AAA", [("09:23", 1011.0, 1012.0, 1010.5)])   # the candle touched the level
        self.candles("BBB", [])
        self.candles("CCC", [])
        self.run_live()                                             # no live tick reached it
        result = self.compare()
        aaa = result["breakouts"][0]
        self.assertEqual((aaa["live"]["decision"], aaa["replay"]["minute"], aaa["same_outcome"]),
                         ("ARMED", "09:23", False))
        self.assertFalse(result["same_outcomes"])
        self.assertIn("<- differs", format_report(result))

    def test_a_level_the_ticks_and_the_candles_disagree_on(self) -> None:
        # Kite's candles say the range high was 1013; the live day-high said 1012.
        self.candles("AAA", [], opening=(1000.0, 1013.0, 998.0, 1010.0, 5000, 700))
        self.candles("BBB", [])
        self.candles("CCC", [])
        self.run_live()
        result = self.compare()
        self.assertTrue(result["same_selection"])
        self.assertFalse(result["same_arms"])
        self.assertEqual(result["equal"]["high"], 2)
        self.assertEqual([d["symbol"] for d in result["range_differences_near_the_line"]], ["AAA"])
        self.assertIn("different level or direction", format_report(result))

    def test_a_different_stock_armed(self) -> None:
        # In the candles CCC traded 4,500 in the range (4.5x); live saw 1,500.
        self.candles("AAA", [])
        self.candles("BBB", [])
        self.candles("CCC", [], opening=(200.0, 201.0, 199.0, 200.5, 4500, 100))
        self.run_live()
        result = self.compare()
        self.assertEqual([a["symbol"] for a in result["replay_arms"]], ["AAA", "CCC", "BBB"])
        self.assertFalse(result["same_selection"])
        self.assertIn("DIFFERENT stocks", format_report(result))


class NotYetTests(ParityTestCase):
    def test_no_ranking_recorded(self) -> None:
        OrbWriter(self.live).close()
        with self.assertRaisesRegex(ParityError, "no ranking"):
            self.compare()

    def test_no_candles_for_the_day_yet(self) -> None:
        self.run_live()
        with self.assertRaisesRegex(ParityError, "collect history first"):
            self.compare()

    def test_missing_files(self) -> None:
        with self.assertRaisesRegex(ParityError, "live db not found"):
            self.compare()

    def test_the_command_reports_and_exits_cleanly(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = orb_parity_check.main(["--session-date", DAY, "--live-db", str(self.live),
                                          "--historical-db", str(self.hist)])
        self.assertEqual(code, 2)
        self.assertIn("Cannot compare", err.getvalue())

        for symbol in SYMBOLS.values():
            self.candles(symbol, [])
        self.run_live()
        out = io.StringIO()
        with redirect_stdout(out):
            code = orb_parity_check.main(["--session-date", DAY, "--live-db", str(self.live),
                                          "--historical-db", str(self.hist)])
        self.assertEqual(code, 0)
        self.assertIn(f"ORB parity for {DAY}", out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out):
            orb_parity_check.main(["--session-date", DAY, "--live-db", str(self.live),
                                   "--historical-db", str(self.hist), "--json"])
        self.assertIn('"same_selection": true', out.getvalue())


if __name__ == "__main__":
    unittest.main()
