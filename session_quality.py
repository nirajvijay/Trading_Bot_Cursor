"""Shared historical session-quality validation (completed vs incomplete)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

# NSE cash session minutes (inclusive), minutes since midnight IST.
# Ideal/full Kite sessions end at 15:29 with 375 distinct minutes (09:15–15:29).
# Baseline eligibility is looser: require open at 09:15 and coverage through at
# least 15:00 (spike detection ends 14:00; 15:00 is the quality buffer).
SESSION_MINUTE_START = 9 * 60 + 15  # 09:15 → 555
SESSION_MINUTE_END = 15 * 60 + 29  # 15:29 → 929 (ideal/full close)
NOMINAL_SESSION_MINUTES = SESSION_MINUTE_END - SESSION_MINUTE_START + 1  # 375
REQUIRED_FIRST_MINUTE = SESSION_MINUTE_START  # exactly 09:15
REQUIRED_LAST_MINUTE_MIN = 15 * 60  # at least 15:00 → 900
IDEAL_LAST_MINUTE = SESSION_MINUTE_END  # 15:29 — preferred, not required
# 09:15–15:00 inclusive = 346 minutes; allow up to 6 missing interior minutes.
MIN_VALID_SESSION_MINUTES = 340

LOOKBACK_COMPLETED_SESSIONS = 21


@dataclass(frozen=True)
class SessionQualityResult:
    session_date: str
    is_completed: bool
    minute_count: int
    first_minute: Optional[int]
    last_minute: Optional[int]
    reason: str = ""

    @property
    def is_full_session(self) -> bool:
        """True when last candle reaches the ideal NSE close (15:29)."""
        return (
            self.is_completed
            and self.last_minute is not None
            and self.last_minute == IDEAL_LAST_MINUTE
        )


def minute_of_day_from_hhmm(hhmm: str) -> Optional[int]:
    try:
        hour_s, minute_s = hhmm.split(":", 1)
        return int(hour_s) * 60 + int(minute_s)
    except (ValueError, AttributeError):
        return None


def evaluate_session_minutes(minutes: Iterable[int]) -> tuple[bool, int, Optional[int], Optional[int], str]:
    unique = sorted({m for m in minutes if SESSION_MINUTE_START <= m <= SESSION_MINUTE_END})
    if not unique:
        return False, 0, None, None, "no_session_minutes"
    first, last = unique[0], unique[-1]
    count = len(unique)
    if first != REQUIRED_FIRST_MINUTE:
        return False, count, first, last, f"first_minute={first} (expected {REQUIRED_FIRST_MINUTE})"
    if last < REQUIRED_LAST_MINUTE_MIN:
        return (
            False,
            count,
            first,
            last,
            f"last_minute={last} (min {REQUIRED_LAST_MINUTE_MIN})",
        )
    if count < MIN_VALID_SESSION_MINUTES:
        return False, count, first, last, f"minute_count={count} (min {MIN_VALID_SESSION_MINUTES})"
    if last == IDEAL_LAST_MINUTE:
        return True, count, first, last, "completed_full"
    return True, count, first, last, "completed"


def evaluate_session_from_candle_times(candle_times: Sequence[str]) -> tuple[bool, int, Optional[int], Optional[int], str]:
    minutes: list[int] = []
    for ct in candle_times:
        # ISO: YYYY-MM-DDTHH:MM:SS+05:30 or similar
        if len(ct) < 16:
            continue
        hm = ct[11:16]
        mid = minute_of_day_from_hhmm(hm)
        if mid is not None:
            minutes.append(mid)
    return evaluate_session_minutes(minutes)


def load_session_minutes(
    conn: sqlite3.Connection,
    instrument_token: int,
    session_date: str,
    *,
    table: str = "candles",
) -> list[int]:
    rows = conn.execute(
        f"""
        SELECT DISTINCT substr(candle_time, 12, 5)
        FROM {table}
        WHERE instrument_token = ?
          AND substr(candle_time, 1, 10) = ?
        """,
        (instrument_token, session_date),
    ).fetchall()
    minutes: list[int] = []
    for (hm,) in rows:
        mid = minute_of_day_from_hhmm(str(hm))
        if mid is not None:
            minutes.append(mid)
    return minutes


def evaluate_symbol_session(
    conn: sqlite3.Connection,
    instrument_token: int,
    session_date: str,
    *,
    table: str = "candles",
) -> SessionQualityResult:
    minutes = load_session_minutes(conn, instrument_token, session_date, table=table)
    ok, count, first, last, reason = evaluate_session_minutes(minutes)
    return SessionQualityResult(
        session_date=session_date,
        is_completed=ok,
        minute_count=count,
        first_minute=first,
        last_minute=last,
        reason=reason,
    )


def list_candidate_session_dates(
    conn: sqlite3.Connection,
    instrument_token: int,
    *,
    as_of: Optional[str] = None,
    table: str = "candles",
) -> list[str]:
    if as_of is not None:
        rows = conn.execute(
            f"""
            SELECT DISTINCT substr(candle_time, 1, 10) AS session_date
            FROM {table}
            WHERE instrument_token = ?
              AND substr(candle_time, 1, 10) <= ?
            ORDER BY session_date DESC
            """,
            (instrument_token, as_of),
        ).fetchall()
    else:
        rows = conn.execute(
            f"""
            SELECT DISTINCT substr(candle_time, 1, 10) AS session_date
            FROM {table}
            WHERE instrument_token = ?
            ORDER BY session_date DESC
            """,
            (instrument_token,),
        ).fetchall()
    return [row[0] for row in rows]


def discover_completed_sessions(
    conn: sqlite3.Connection,
    instrument_token: int,
    *,
    lookback_sessions: int = LOOKBACK_COMPLETED_SESSIONS,
    as_of: Optional[str] = None,
    table: str = "candles",
) -> list[str]:
    """
    Return up to lookback_sessions completed session dates (chronological).
    Incomplete calendar dates are excluded from the lookback.
    """
    completed: list[str] = []
    for session_date in list_candidate_session_dates(
        conn, instrument_token, as_of=as_of, table=table
    ):
        result = evaluate_symbol_session(
            conn, instrument_token, session_date, table=table
        )
        if result.is_completed:
            completed.append(session_date)
            if len(completed) >= lookback_sessions:
                break
    completed.reverse()
    return completed


def count_completed_sessions(
    conn: sqlite3.Connection,
    instrument_token: int,
    *,
    as_of: Optional[str] = None,
    table: str = "candles",
) -> int:
    return len(
        discover_completed_sessions(
            conn,
            instrument_token,
            lookback_sessions=10_000,
            as_of=as_of,
            table=table,
        )
    )
