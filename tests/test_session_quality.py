"""Tests for shared session-quality completed-session rule."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from session_quality import (
    IDEAL_LAST_MINUTE,
    MIN_VALID_SESSION_MINUTES,
    NOMINAL_SESSION_MINUTES,
    REQUIRED_LAST_MINUTE_MIN,
    SESSION_MINUTE_END,
    SESSION_MINUTE_START,
    discover_completed_sessions,
    evaluate_session_minutes,
    evaluate_symbol_session,
)


def _contiguous(start: int, end: int) -> list[int]:
    return list(range(start, end + 1))


class SessionQualityTests(unittest.TestCase):
    def test_nominal_span_is_375(self) -> None:
        self.assertEqual(NOMINAL_SESSION_MINUTES, 375)
        self.assertEqual(SESSION_MINUTE_START, 555)
        self.assertEqual(SESSION_MINUTE_END, 929)
        self.assertEqual(REQUIRED_LAST_MINUTE_MIN, 900)  # 15:00
        self.assertEqual(IDEAL_LAST_MINUTE, 929)  # 15:29
        self.assertEqual(MIN_VALID_SESSION_MINUTES, 340)

    def test_full_session_completed(self) -> None:
        minutes = _contiguous(SESSION_MINUTE_START, SESSION_MINUTE_END)
        ok, count, first, last, reason = evaluate_session_minutes(minutes)
        self.assertTrue(ok)
        self.assertEqual(count, 375)
        self.assertEqual(first, SESSION_MINUTE_START)
        self.assertEqual(last, SESSION_MINUTE_END)
        self.assertEqual(reason, "completed_full")

    def test_1514_with_360_candles_accepted(self) -> None:
        # 09:15–15:14 inclusive = 360 minutes (matches truncated Kite days).
        end = 15 * 60 + 14
        minutes = _contiguous(SESSION_MINUTE_START, end)
        self.assertEqual(len(minutes), 360)
        ok, count, first, last, reason = evaluate_session_minutes(minutes)
        self.assertTrue(ok)
        self.assertEqual(count, 360)
        self.assertEqual(first, SESSION_MINUTE_START)
        self.assertEqual(last, end)
        self.assertEqual(reason, "completed")

    def test_1500_with_346_candles_accepted(self) -> None:
        # 09:15–15:00 inclusive = 346 minutes.
        end = REQUIRED_LAST_MINUTE_MIN
        minutes = _contiguous(SESSION_MINUTE_START, end)
        self.assertEqual(len(minutes), 346)
        ok, count, first, last, reason = evaluate_session_minutes(minutes)
        self.assertTrue(ok)
        self.assertEqual(count, 346)
        self.assertEqual(last, end)
        self.assertEqual(reason, "completed")

    def test_1459_rejected(self) -> None:
        end = 14 * 60 + 59
        minutes = _contiguous(SESSION_MINUTE_START, end)
        ok, count, first, last, reason = evaluate_session_minutes(minutes)
        self.assertFalse(ok)
        self.assertEqual(last, end)
        self.assertIn("last_minute", reason)

    def test_noon_early_failure_rejected(self) -> None:
        minutes = _contiguous(SESSION_MINUTE_START, 12 * 60)
        ok, _count, _first, last, reason = evaluate_session_minutes(minutes)
        self.assertFalse(ok)
        self.assertEqual(last, 12 * 60)
        self.assertIn("last_minute", reason)

    def test_1500_with_too_many_holes_rejected(self) -> None:
        # Ends at 15:00 but only 339 distinct minutes (< 340 min).
        end = REQUIRED_LAST_MINUTE_MIN
        full = _contiguous(SESSION_MINUTE_START, end)
        # Drop 7 interior minutes → 346 - 7 = 339.
        drop = set(full[10:17])
        minutes = [m for m in full if m not in drop]
        self.assertEqual(len(minutes), 339)
        self.assertEqual(minutes[0], SESSION_MINUTE_START)
        self.assertEqual(minutes[-1], end)
        ok, count, _first, _last, reason = evaluate_session_minutes(minutes)
        self.assertFalse(ok)
        self.assertEqual(count, 339)
        self.assertIn("minute_count", reason)

    def test_1500_with_six_holes_accepted(self) -> None:
        end = REQUIRED_LAST_MINUTE_MIN
        full = _contiguous(SESSION_MINUTE_START, end)
        drop = set(full[10:16])  # 6 holes → 340
        minutes = [m for m in full if m not in drop]
        self.assertEqual(len(minutes), 340)
        ok, count, *_rest = evaluate_session_minutes(minutes)
        self.assertTrue(ok)
        self.assertEqual(count, 340)

    def test_sparse_but_anchored_full_close(self) -> None:
        minutes = [SESSION_MINUTE_START] + list(
            range(SESSION_MINUTE_START + 10, SESSION_MINUTE_END)
        ) + [SESSION_MINUTE_END]
        ok, count, *_rest = evaluate_session_minutes(minutes)
        self.assertGreaterEqual(count, MIN_VALID_SESSION_MINUTES)
        self.assertTrue(ok)

    def test_discover_excludes_incomplete_dates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "h.db"
            conn = sqlite3.connect(db)
            conn.execute(
                """
                CREATE TABLE candles (
                    instrument_token INTEGER,
                    tradingsymbol TEXT,
                    candle_time TEXT,
                    open REAL, high REAL, low REAL, close REAL, volume INTEGER,
                    PRIMARY KEY (instrument_token, candle_time)
                )
                """
            )
            token = 1

            def insert_day(day: str, start: int, end: int) -> None:
                for m in range(start, end + 1):
                    hh, mm = divmod(m, 60)
                    ct = f"{day}T{hh:02d}:{mm:02d}:00+05:30"
                    conn.execute(
                        "INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)",
                        (token, "TEST", ct, 1, 1, 1, 1, 100),
                    )

            # Incomplete (partial morning / noon)
            insert_day("2026-07-01", SESSION_MINUTE_START, 11 * 60 + 25)
            # Eligible but not full close (ends 15:14)
            insert_day("2026-07-02", SESSION_MINUTE_START, 15 * 60 + 14)
            # Ideal full close
            insert_day("2026-07-03", SESSION_MINUTE_START, SESSION_MINUTE_END)
            conn.commit()

            completed = discover_completed_sessions(conn, token, lookback_sessions=21)
            self.assertEqual(completed, ["2026-07-02", "2026-07-03"])

            partial = evaluate_symbol_session(conn, token, "2026-07-01")
            self.assertFalse(partial.is_completed)

            eligible = evaluate_symbol_session(conn, token, "2026-07-02")
            self.assertTrue(eligible.is_completed)
            self.assertFalse(eligible.is_full_session)

            full = evaluate_symbol_session(conn, token, "2026-07-03")
            self.assertTrue(full.is_completed)
            self.assertTrue(full.is_full_session)
            conn.close()


if __name__ == "__main__":
    unittest.main()
