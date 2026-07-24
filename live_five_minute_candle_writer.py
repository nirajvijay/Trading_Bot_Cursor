"""
Persist completed live 5-minute candles into SQLite.

Market-data owned. Never writes strategy tables.
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
    CompletedFiveMinuteCandle,
    ensure_ist,
    floor_five_minute_bucket_start,
    is_in_session,
    minute_of_day_from_datetime,
)
from historical_collector import DEFAULT_INSTRUMENTS_DB_PATH, load_nifty50_tokens
from live_one_minute_candle_writer import (
    DEFAULT_DB_PATH,
    CandleConflictError,
    is_retryable_sqlite_error,
    is_unrecoverable_persistence_error,
)

CREATE_LIVE_5M_CANDLES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS live_5m_candles (
    instrument_token INTEGER NOT NULL,
    tradingsymbol    TEXT    NOT NULL,
    candle_time      TEXT    NOT NULL,
    session_date     TEXT    NOT NULL,
    open             REAL    NOT NULL,
    high             REAL    NOT NULL,
    low              REAL    NOT NULL,
    close            REAL    NOT NULL,
    volume           INTEGER NOT NULL,
    constituent_count INTEGER NOT NULL,
    all_volume_reliable INTEGER NOT NULL CHECK (all_volume_reliable IN (0, 1)),
    any_partial      INTEGER NOT NULL CHECK (any_partial IN (0, 1)),
    all_full_coverage INTEGER NOT NULL CHECK (all_full_coverage IN (0, 1)),
    tick_count       INTEGER NOT NULL,
    inserted_at      TEXT    NOT NULL,
    PRIMARY KEY (instrument_token, candle_time)
);
"""

CREATE_SESSION_DATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_live_5m_session_date
    ON live_5m_candles(session_date);
"""

INSERT_5M_SQL = """
INSERT OR IGNORE INTO live_5m_candles (
    instrument_token, tradingsymbol, candle_time, session_date,
    open, high, low, close, volume,
    constituent_count, all_volume_reliable, any_partial,
    all_full_coverage, tick_count, inserted_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

SELECT_5M_BY_PK_SQL = """
SELECT
    tradingsymbol, open, high, low, close, volume,
    constituent_count, all_volume_reliable, any_partial,
    all_full_coverage, tick_count
FROM live_5m_candles
WHERE instrument_token = ? AND candle_time = ?
"""


@dataclass(frozen=True)
class FiveMinuteWriterMetrics:
    candles_inserted: int
    duplicates_ignored: int
    conflicting_duplicates: int
    validation_errors: int
    write_retries: int
    write_failures: int


@dataclass(frozen=True)
class _FiveMinuteRow:
    instrument_token: int
    tradingsymbol: str
    candle_time: str
    session_date: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    constituent_count: int
    all_volume_reliable: int
    any_partial: int
    all_full_coverage: int
    tick_count: int
    inserted_at: str


def init_live_5m_db(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_LIVE_5M_CANDLES_TABLE_SQL)
    conn.execute(CREATE_SESSION_DATE_INDEX_SQL)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate(candle: CompletedFiveMinuteCandle, tradingsymbol: str) -> None:
    if not tradingsymbol:
        raise ValueError("tradingsymbol must be non-empty")
    if candle.instrument_token <= 0:
        raise ValueError("instrument_token must be positive")
    if candle.constituent_count != 5:
        raise ValueError("constituent_count must be 5")
    candle_time = ensure_ist(candle.candle_time)
    if candle_time.second != 0 or candle_time.microsecond != 0:
        raise ValueError("candle_time must be floored to minute start")
    minute = minute_of_day_from_datetime(candle_time)
    if floor_five_minute_bucket_start(minute) != minute:
        raise ValueError("candle_time must be a 5m bucket start")
    if not is_in_session(minute):
        raise ValueError("candle_time is outside NSE session")
    if candle.volume < 0 or candle.tick_count <= 0:
        raise ValueError("invalid volume or tick_count")
    for price in (candle.open, candle.high, candle.low, candle.close):
        if price <= 0:
            raise ValueError("OHLC prices must be positive")
    if candle.high < candle.low:
        raise ValueError("high must be >= low")


def _row_from_candle(
    candle: CompletedFiveMinuteCandle,
    tradingsymbol: str,
    inserted_at: str,
) -> _FiveMinuteRow:
    candle_time = ensure_ist(candle.candle_time).isoformat(timespec="seconds")
    return _FiveMinuteRow(
        instrument_token=candle.instrument_token,
        tradingsymbol=tradingsymbol,
        candle_time=candle_time,
        session_date=candle.session_date,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        volume=candle.volume,
        constituent_count=candle.constituent_count,
        all_volume_reliable=1 if candle.all_volume_reliable else 0,
        any_partial=1 if candle.any_partial else 0,
        all_full_coverage=1 if candle.all_full_coverage else 0,
        tick_count=candle.tick_count,
        inserted_at=inserted_at,
    )


def _row_tuple(row: _FiveMinuteRow) -> tuple:
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
        row.constituent_count,
        row.all_volume_reliable,
        row.any_partial,
        row.all_full_coverage,
        row.tick_count,
        row.inserted_at,
    )


def _rows_match(existing: tuple, row: _FiveMinuteRow) -> bool:
    (
        tradingsymbol,
        open_,
        high,
        low,
        close,
        volume,
        constituent_count,
        all_volume_reliable,
        any_partial,
        all_full_coverage,
        tick_count,
    ) = existing
    return (
        tradingsymbol == row.tradingsymbol
        and open_ == row.open
        and high == row.high
        and low == row.low
        and close == row.close
        and volume == row.volume
        and constituent_count == row.constituent_count
        and all_volume_reliable == row.all_volume_reliable
        and any_partial == row.any_partial
        and all_full_coverage == row.all_full_coverage
        and tick_count == row.tick_count
    )


class LiveFiveMinuteCandleWriter:
    def __init__(
        self,
        db_path: Path = DEFAULT_DB_PATH,
        instruments_db: Path = DEFAULT_INSTRUMENTS_DB_PATH,
        token_to_symbol: Optional[Mapping[int, str]] = None,
        busy_timeout_ms: int = 5000,
        max_write_retries: int = 5,
        retry_base_delay_seconds: float = 0.05,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        self._db_path = db_path
        self._busy_timeout_ms = busy_timeout_ms
        self._max_write_retries = max_write_retries
        self._retry_base_delay_seconds = retry_base_delay_seconds
        self._owns_conn = conn is None
        if conn is None:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        else:
            self._conn = conn
        init_live_5m_db(self._conn)

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
    def metrics(self) -> FiveMinuteWriterMetrics:
        with self._lock:
            return FiveMinuteWriterMetrics(
                candles_inserted=self._candles_inserted,
                duplicates_ignored=self._duplicates_ignored,
                conflicting_duplicates=self._conflicting_duplicates,
                validation_errors=self._validation_errors,
                write_retries=self._write_retries,
                write_failures=self._write_failures,
            )

    def on_candle(self, candle: CompletedFiveMinuteCandle) -> None:
        tradingsymbol = self._token_to_symbol.get(candle.instrument_token)
        if tradingsymbol is None:
            with self._lock:
                if self._closed:
                    raise RuntimeError("writer is closed")
                self._validation_errors += 1
            raise ValueError("unknown instrument_token: %d" % candle.instrument_token)

        try:
            _validate(candle, tradingsymbol)
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
            if self._owns_conn:
                self._conn.commit()
                self._conn.close()
            self._closed = True

    def _insert_with_retry(self, row: _FiveMinuteRow) -> None:
        for attempt in range(self._max_write_retries + 1):
            try:
                cursor = self._conn.execute(INSERT_5M_SQL, _row_tuple(row))
                if cursor.rowcount == 1:
                    self._conn.commit()
                    self._candles_inserted += 1
                    return

                existing = self._conn.execute(
                    SELECT_5M_BY_PK_SQL,
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
                if not is_retryable_sqlite_error(exc):
                    self._write_failures += 1
                    raise
                self._write_retries += 1
                if attempt == self._max_write_retries:
                    self._write_failures += 1
                    raise
                delay = self._retry_base_delay_seconds * (2 ** attempt)
                time.sleep(delay)


# Re-export for coordinator convenience
__all__ = [
    "LiveFiveMinuteCandleWriter",
    "FiveMinuteWriterMetrics",
    "init_live_5m_db",
    "is_unrecoverable_persistence_error",
    "CandleConflictError",
]
