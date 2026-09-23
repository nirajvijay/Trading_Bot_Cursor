"""
Persist completed 1-minute candles from the live builder into SQLite.

Single responsibility: CompletedOneMinuteCandle -> SQLite.
No candle building, strategy, or receiver wiring.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional

from candle_aggregation import (
    CompletionReason,
    CompletedOneMinuteCandle,
    ensure_ist,
    is_in_session,
    minute_of_day_from_datetime,
)
from api.config import LIVE_DB_PATH
from historical_collector import DEFAULT_INSTRUMENTS_DB_PATH, load_nifty50_tokens

ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = LIVE_DB_PATH

SQLITE_BUSY = 5
SQLITE_LOCKED = 6

_COMPLETION_REASONS: tuple[CompletionReason, ...] = (
    "minute_transition",
    "session_end",
    "shutdown_flush",
    "day_rollover",
)

CREATE_LIVE_1M_CANDLES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS live_1m_candles (
    instrument_token INTEGER NOT NULL,
    tradingsymbol    TEXT    NOT NULL,
    candle_time      TEXT    NOT NULL,
    session_date     TEXT    NOT NULL,
    open             REAL    NOT NULL,
    high             REAL    NOT NULL,
    low              REAL    NOT NULL,
    close            REAL    NOT NULL,
    volume           INTEGER NOT NULL,
    tick_count       INTEGER NOT NULL,
    volume_reliable  INTEGER NOT NULL,
    completion_reason TEXT   NOT NULL
        CHECK (completion_reason IN (
            'minute_transition', 'session_end', 'shutdown_flush', 'day_rollover'
        )),
    has_full_minute_coverage INTEGER NOT NULL
        CHECK (has_full_minute_coverage IN (0, 1)),
    is_partial       INTEGER NOT NULL
        CHECK (is_partial IN (0, 1)),
    inserted_at      TEXT    NOT NULL,
    PRIMARY KEY (instrument_token, candle_time)
);
"""

CREATE_SESSION_DATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_live_1m_session_date
    ON live_1m_candles(session_date);
"""

CREATE_SYMBOL_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_live_1m_symbol
    ON live_1m_candles(tradingsymbol);
"""

CREATE_IS_PARTIAL_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_live_1m_is_partial
    ON live_1m_candles(is_partial);
"""

INSERT_CANDLE_SQL = """
INSERT OR IGNORE INTO live_1m_candles (
    instrument_token, tradingsymbol, candle_time, session_date,
    open, high, low, close, volume,
    tick_count, volume_reliable, completion_reason,
    has_full_minute_coverage, is_partial, inserted_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

SELECT_CANDLE_BY_PK_SQL = """
SELECT
    tradingsymbol, open, high, low, close, volume,
    tick_count, volume_reliable, completion_reason,
    has_full_minute_coverage, is_partial
FROM live_1m_candles
WHERE instrument_token = ? AND candle_time = ?
"""


class CandleConflictError(Exception):
    """Raised when a PK collision has a different candle payload."""


@dataclass(frozen=True)
class WriterMetrics:
    candles_inserted: int
    duplicates_ignored: int
    conflicting_duplicates: int
    validation_errors: int
    write_retries: int
    write_failures: int


@dataclass(frozen=True)
class _CandleRow:
    instrument_token: int
    tradingsymbol: str
    candle_time: str
    session_date: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    tick_count: int
    volume_reliable: int
    completion_reason: str
    has_full_minute_coverage: int
    is_partial: int
    inserted_at: str


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(CREATE_LIVE_1M_CANDLES_TABLE_SQL)
    conn.execute(CREATE_SESSION_DATE_INDEX_SQL)
    conn.execute(CREATE_SYMBOL_INDEX_SQL)
    conn.execute(CREATE_IS_PARTIAL_INDEX_SQL)
    return conn


def is_retryable_sqlite_error(exc: sqlite3.OperationalError) -> bool:
    return exc.sqlite_errorcode in (SQLITE_BUSY, SQLITE_LOCKED)


def is_unrecoverable_persistence_error(exc: BaseException) -> bool:
    """True for errors that must halt the pipeline when escaping writer.on_candle()."""
    if isinstance(exc, CandleConflictError):
        return True
    if isinstance(exc, ValueError):
        return True
    if isinstance(exc, sqlite3.OperationalError):
        return True
    if isinstance(exc, RuntimeError):
        message = str(exc)
        return "writer is closed" in message or "INSERT OR IGNORE" in message
    return False


def _is_lock_error(exc: sqlite3.OperationalError) -> bool:
    return is_retryable_sqlite_error(exc)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_candle(
    candle: CompletedOneMinuteCandle,
    tradingsymbol: str,
) -> None:
    if candle.completion_reason not in _COMPLETION_REASONS:
        raise ValueError("invalid completion_reason: %s" % candle.completion_reason)

    candle_time = ensure_ist(candle.candle_time)
    if candle_time.second != 0 or candle_time.microsecond != 0:
        raise ValueError("candle_time must be floored to minute start")

    minute_of_day = minute_of_day_from_datetime(candle_time)
    if not is_in_session(minute_of_day):
        raise ValueError("candle_time is outside NSE session")

    if candle.tick_count <= 0:
        raise ValueError("tick_count must be positive")

    if candle.instrument_token <= 0:
        raise ValueError("instrument_token must be positive")

    if candle.volume < 0:
        raise ValueError("volume must be non-negative")

    for price in (candle.open, candle.high, candle.low, candle.close):
        if price <= 0:
            raise ValueError("OHLC prices must be positive")

    if candle.high < candle.low:
        raise ValueError("high must be >= low")

    if candle.completion_reason == "day_rollover" and not candle.is_partial:
        raise ValueError("day_rollover candles must be partial")

    if not candle.has_full_minute_coverage and not candle.is_partial:
        raise ValueError("candles without full coverage must be partial")

    if (
        candle.has_full_minute_coverage
        and candle.completion_reason == "minute_transition"
        and candle.is_partial
    ):
        raise ValueError("minute_transition with full coverage must not be partial")

    if not tradingsymbol:
        raise ValueError("tradingsymbol must be non-empty")


def _row_from_candle(
    candle: CompletedOneMinuteCandle,
    tradingsymbol: str,
    inserted_at: str,
) -> _CandleRow:
    bar = candle.to_one_minute_candle()
    return _CandleRow(
        instrument_token=candle.instrument_token,
        tradingsymbol=tradingsymbol,
        candle_time=bar.candle_time,
        session_date=bar.candle_time[:10],
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        tick_count=candle.tick_count,
        volume_reliable=1 if candle.volume_reliable else 0,
        completion_reason=candle.completion_reason,
        has_full_minute_coverage=1 if candle.has_full_minute_coverage else 0,
        is_partial=1 if candle.is_partial else 0,
        inserted_at=inserted_at,
    )


def _row_tuple(row: _CandleRow) -> tuple:
    return (
        row.instrument_token,
        row.tradingsymbol,
        row.candle_time,
        row.session_date,
        row.open,
        row.high,
        row.low,
        row.close,
        row.volume,
        row.tick_count,
        row.volume_reliable,
        row.completion_reason,
        row.has_full_minute_coverage,
        row.is_partial,
        row.inserted_at,
    )


def _rows_match(existing: tuple, row: _CandleRow) -> bool:
    (
        tradingsymbol,
        open_,
        high,
        low,
        close,
        volume,
        tick_count,
        volume_reliable,
        completion_reason,
        has_full_minute_coverage,
        is_partial,
    ) = existing
    return (
        tradingsymbol == row.tradingsymbol
        and open_ == row.open
        and high == row.high
        and low == row.low
        and close == row.close
        and volume == row.volume
        and tick_count == row.tick_count
        and volume_reliable == row.volume_reliable
        and completion_reason == row.completion_reason
        and has_full_minute_coverage == row.has_full_minute_coverage
        and is_partial == row.is_partial
    )


class LiveOneMinuteCandleWriter:
    def __init__(
        self,
        db_path: Path = DEFAULT_DB_PATH,
        instruments_db: Path = DEFAULT_INSTRUMENTS_DB_PATH,
        token_to_symbol: Optional[Mapping[int, str]] = None,
        busy_timeout_ms: int = 5000,
        max_write_retries: int = 5,
        retry_base_delay_seconds: float = 0.05,
    ) -> None:
        self._db_path = db_path
        self._busy_timeout_ms = busy_timeout_ms
        self._max_write_retries = max_write_retries
        self._retry_base_delay_seconds = retry_base_delay_seconds
        self._conn = init_db(db_path)
        self._conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        self._conn.execute("PRAGMA synchronous=NORMAL")

        if token_to_symbol is not None:
            self._token_to_symbol = dict(token_to_symbol)
        else:
            stocks = load_nifty50_tokens(instruments_db)
            self._token_to_symbol = {
                stock.instrument_token: stock.tradingsymbol for stock in stocks
            }

        self._lock = threading.RLock()
        self._closed = False
        self._candles_inserted = 0
        self._duplicates_ignored = 0
        self._conflicting_duplicates = 0
        self._validation_errors = 0
        self._write_retries = 0
        self._write_failures = 0

    @property
    def metrics(self) -> WriterMetrics:
        with self._lock:
            return WriterMetrics(
                candles_inserted=self._candles_inserted,
                duplicates_ignored=self._duplicates_ignored,
                conflicting_duplicates=self._conflicting_duplicates,
                validation_errors=self._validation_errors,
                write_retries=self._write_retries,
                write_failures=self._write_failures,
            )

    def on_candle(self, candle: CompletedOneMinuteCandle) -> None:
        """Persist one completed candle.

        Lock contention (SQLITE_BUSY / SQLITE_LOCKED) is retried internally.
        Only unrecoverable persistence errors propagate to callers.
        """
        tradingsymbol = self._token_to_symbol.get(candle.instrument_token)
        if tradingsymbol is None:
            with self._lock:
                if self._closed:
                    raise RuntimeError("writer is closed")
                self._validation_errors += 1
            raise ValueError(
                "unknown instrument_token: %d" % candle.instrument_token
            )

        try:
            _validate_candle(candle, tradingsymbol)
        except ValueError:
            with self._lock:
                if self._closed:
                    raise RuntimeError("writer is closed")
                self._validation_errors += 1
            raise

        row = _row_from_candle(candle, tradingsymbol, _utc_now_iso())
        with self._lock:
            if self._closed:
                raise RuntimeError("writer is closed")
            self._insert_with_retry(row)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._conn.commit()
            self._conn.close()
            self._closed = True

    def _insert_with_retry(self, row: _CandleRow) -> None:
        for attempt in range(self._max_write_retries + 1):
            try:
                cursor = self._conn.execute(INSERT_CANDLE_SQL, _row_tuple(row))
                if cursor.rowcount == 1:
                    self._conn.commit()
                    self._candles_inserted += 1
                    return

                existing = self._conn.execute(
                    SELECT_CANDLE_BY_PK_SQL,
                    (row.instrument_token, row.candle_time),
                ).fetchone()
                if existing is None:
                    raise RuntimeError(
                        "INSERT OR IGNORE no-op but row not found for %s %s"
                        % (row.instrument_token, row.candle_time)
                    )
                if _rows_match(existing, row):
                    self._conn.commit()
                    self._duplicates_ignored += 1
                    return

                self._conflicting_duplicates += 1
                self._conn.rollback()
                raise CandleConflictError(
                    "PK conflict for token=%d time=%s"
                    % (row.instrument_token, row.candle_time)
                )
            except sqlite3.OperationalError as exc:
                self._conn.rollback()
                if not _is_lock_error(exc):
                    self._write_failures += 1
                    raise
                self._write_retries += 1
                if attempt == self._max_write_retries:
                    self._write_failures += 1
                    raise
                delay = self._retry_base_delay_seconds * (2 ** attempt)
                time.sleep(delay)
