"""
Persist accepted IntradaySpikeEvent rows into the live SQLite DB.

Strategy-owned table only: live_intraday_spikes.
Never modifies live_1m_candles or baseline tables.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional

from candle_aggregation import ensure_ist
from historical_collector import DEFAULT_INSTRUMENTS_DB_PATH, load_nifty50_tokens
from live_one_minute_candle_writer import DEFAULT_DB_PATH, is_retryable_sqlite_error
from spike_types import IntradaySpikeEvent

CREATE_LIVE_INTRADAY_SPIKES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS live_intraday_spikes (
    instrument_token INTEGER NOT NULL,
    tradingsymbol    TEXT    NOT NULL,
    candle_time      TEXT    NOT NULL,
    session_date     TEXT    NOT NULL,
    rule_version     TEXT    NOT NULL,
    direction        TEXT    NOT NULL
        CHECK (direction IN ('UP', 'DOWN', 'FLAT')),
    open             REAL    NOT NULL,
    high             REAL    NOT NULL,
    low              REAL    NOT NULL,
    close            REAL    NOT NULL,
    volume           INTEGER NOT NULL,
    tick_count       INTEGER NOT NULL,
    volume_reliable  INTEGER NOT NULL
        CHECK (volume_reliable IN (0, 1)),
    minute_of_day    INTEGER NOT NULL,
    absolute_return  REAL    NOT NULL,
    signed_return    REAL    NOT NULL,
    relative_volume_median  REAL NOT NULL,
    relative_volume_trimmed REAL NOT NULL,
    abs_return_vs_baseline  REAL NOT NULL,
    body_ratio       REAL    NOT NULL,
    close_location   REAL    NOT NULL,
    baseline_as_of_date TEXT NOT NULL,
    median_volume    REAL    NOT NULL,
    trimmed_mean_volume REAL NOT NULL,
    median_abs_return REAL NOT NULL,
    valid_session_count INTEGER NOT NULL,
    is_reliable      INTEGER NOT NULL
        CHECK (is_reliable IN (0, 1)),
    detected_at      TEXT    NOT NULL,
    PRIMARY KEY (instrument_token, candle_time, rule_version)
);
"""

CREATE_SPIKES_SESSION_DATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_live_intraday_spikes_session_date
    ON live_intraday_spikes(session_date);
"""

CREATE_SPIKES_SYMBOL_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_live_intraday_spikes_symbol
    ON live_intraday_spikes(tradingsymbol);
"""

INSERT_SPIKE_SQL = """
INSERT OR IGNORE INTO live_intraday_spikes (
    instrument_token, tradingsymbol, candle_time, session_date, rule_version,
    direction, open, high, low, close, volume,
    tick_count, volume_reliable, minute_of_day,
    absolute_return, signed_return,
    relative_volume_median, relative_volume_trimmed, abs_return_vs_baseline,
    body_ratio, close_location,
    baseline_as_of_date, median_volume, trimmed_mean_volume, median_abs_return,
    valid_session_count, is_reliable, detected_at
) VALUES (
    ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?,
    ?, ?, ?,
    ?, ?,
    ?, ?, ?,
    ?, ?,
    ?, ?, ?, ?,
    ?, ?, ?
)
"""

# Conflict compare excludes detected_at (wall-clock metadata).
SELECT_SPIKE_BY_PK_SQL = """
SELECT
    tradingsymbol, direction, open, high, low, close, volume,
    tick_count, volume_reliable, minute_of_day,
    absolute_return, signed_return,
    relative_volume_median, relative_volume_trimmed, abs_return_vs_baseline,
    body_ratio, close_location,
    baseline_as_of_date, median_volume, trimmed_mean_volume, median_abs_return,
    valid_session_count, is_reliable
FROM live_intraday_spikes
WHERE instrument_token = ? AND candle_time = ? AND rule_version = ?
"""


class SpikeConflictError(Exception):
    """Raised when a PK collision has a different spike payload."""


@dataclass(frozen=True)
class SpikeWriterMetrics:
    spikes_inserted: int
    duplicates_ignored: int
    conflicting_duplicates: int
    write_retries: int
    write_failures: int


@dataclass(frozen=True)
class _SpikeRow:
    instrument_token: int
    tradingsymbol: str
    candle_time: str
    session_date: str
    rule_version: str
    direction: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    tick_count: int
    volume_reliable: int
    minute_of_day: int
    absolute_return: float
    signed_return: float
    relative_volume_median: float
    relative_volume_trimmed: float
    abs_return_vs_baseline: float
    body_ratio: float
    close_location: float
    baseline_as_of_date: str
    median_volume: float
    trimmed_mean_volume: float
    median_abs_return: float
    valid_session_count: int
    is_reliable: int
    detected_at: str


def init_spikes_db(db_path: Path) -> sqlite3.Connection:
    """Open/create the live DB and ensure strategy spike table exists."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(CREATE_LIVE_INTRADAY_SPIKES_TABLE_SQL)
    conn.execute(CREATE_SPIKES_SESSION_DATE_INDEX_SQL)
    conn.execute(CREATE_SPIKES_SYMBOL_INDEX_SQL)
    return conn


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_from_event(event: IntradaySpikeEvent) -> _SpikeRow:
    features = event.features
    candle_time = ensure_ist(event.candle_time).isoformat(timespec="seconds")
    detected_at = event.detected_at
    if detected_at.tzinfo is None:
        detected_at_iso = detected_at.replace(tzinfo=timezone.utc).isoformat(
            timespec="seconds"
        )
    else:
        detected_at_iso = detected_at.astimezone(timezone.utc).isoformat(
            timespec="seconds"
        )
    return _SpikeRow(
        instrument_token=event.instrument_token,
        tradingsymbol=event.tradingsymbol,
        candle_time=candle_time,
        session_date=event.session_date,
        rule_version=event.rule_version,
        direction=event.direction,
        open=event.open,
        high=event.high,
        low=event.low,
        close=event.close,
        volume=event.volume,
        tick_count=features.tick_count,
        volume_reliable=1 if features.volume_reliable else 0,
        minute_of_day=features.minute_of_day,
        absolute_return=features.absolute_return,
        signed_return=features.signed_return,
        relative_volume_median=features.relative_volume_median,
        relative_volume_trimmed=features.relative_volume_trimmed,
        abs_return_vs_baseline=features.abs_return_vs_baseline,
        body_ratio=features.body_ratio,
        close_location=features.close_location,
        baseline_as_of_date=features.baseline_as_of_date,
        median_volume=features.median_volume,
        trimmed_mean_volume=features.trimmed_mean_volume,
        median_abs_return=features.median_abs_return,
        valid_session_count=features.valid_session_count,
        is_reliable=1 if features.is_reliable else 0,
        detected_at=detected_at_iso,
    )


def _row_tuple(row: _SpikeRow) -> tuple:
    return (
        row.instrument_token,
        row.tradingsymbol,
        row.candle_time,
        row.session_date,
        row.rule_version,
        row.direction,
        row.open,
        row.high,
        row.low,
        row.close,
        row.volume,
        row.tick_count,
        row.volume_reliable,
        row.minute_of_day,
        row.absolute_return,
        row.signed_return,
        row.relative_volume_median,
        row.relative_volume_trimmed,
        row.abs_return_vs_baseline,
        row.body_ratio,
        row.close_location,
        row.baseline_as_of_date,
        row.median_volume,
        row.trimmed_mean_volume,
        row.median_abs_return,
        row.valid_session_count,
        row.is_reliable,
        row.detected_at,
    )


def _rows_match(existing: tuple, row: _SpikeRow) -> bool:
    (
        tradingsymbol,
        direction,
        open_,
        high,
        low,
        close,
        volume,
        tick_count,
        volume_reliable,
        minute_of_day,
        absolute_return,
        signed_return,
        relative_volume_median,
        relative_volume_trimmed,
        abs_return_vs_baseline,
        body_ratio,
        close_location,
        baseline_as_of_date,
        median_volume,
        trimmed_mean_volume,
        median_abs_return,
        valid_session_count,
        is_reliable,
    ) = existing
    return (
        tradingsymbol == row.tradingsymbol
        and direction == row.direction
        and open_ == row.open
        and high == row.high
        and low == row.low
        and close == row.close
        and volume == row.volume
        and tick_count == row.tick_count
        and volume_reliable == row.volume_reliable
        and minute_of_day == row.minute_of_day
        and absolute_return == row.absolute_return
        and signed_return == row.signed_return
        and relative_volume_median == row.relative_volume_median
        and relative_volume_trimmed == row.relative_volume_trimmed
        and abs_return_vs_baseline == row.abs_return_vs_baseline
        and body_ratio == row.body_ratio
        and close_location == row.close_location
        and baseline_as_of_date == row.baseline_as_of_date
        and median_volume == row.median_volume
        and trimmed_mean_volume == row.trimmed_mean_volume
        and median_abs_return == row.median_abs_return
        and valid_session_count == row.valid_session_count
        and is_reliable == row.is_reliable
    )


class IntradaySpikeWriter:
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
        self._max_write_retries = max_write_retries
        self._retry_base_delay_seconds = retry_base_delay_seconds
        self._conn = init_spikes_db(db_path)
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
        self._spikes_inserted = 0
        self._duplicates_ignored = 0
        self._conflicting_duplicates = 0
        self._write_retries = 0
        self._write_failures = 0

    @property
    def metrics(self) -> SpikeWriterMetrics:
        with self._lock:
            return SpikeWriterMetrics(
                spikes_inserted=self._spikes_inserted,
                duplicates_ignored=self._duplicates_ignored,
                conflicting_duplicates=self._conflicting_duplicates,
                write_retries=self._write_retries,
                write_failures=self._write_failures,
            )

    def on_spike(self, event: IntradaySpikeEvent) -> None:
        """Persist one accepted spike. Retries lock errors; conflicts raise."""
        if event.tradingsymbol != self._token_to_symbol.get(event.instrument_token):
            mapped = self._token_to_symbol.get(event.instrument_token)
            if mapped is None:
                raise ValueError(
                    "unknown instrument_token: %d" % event.instrument_token
                )
            if event.tradingsymbol != mapped:
                raise ValueError(
                    "tradingsymbol mismatch for token %d: event=%s map=%s"
                    % (event.instrument_token, event.tradingsymbol, mapped)
                )

        row = _row_from_event(event)
        with self._lock:
            if self._closed:
                raise RuntimeError("spike writer is closed")
            self._insert_with_retry(row)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._conn.commit()
            self._conn.close()
            self._closed = True

    def _insert_with_retry(self, row: _SpikeRow) -> None:
        for attempt in range(self._max_write_retries + 1):
            try:
                cursor = self._conn.execute(INSERT_SPIKE_SQL, _row_tuple(row))
                if cursor.rowcount == 1:
                    self._conn.commit()
                    self._spikes_inserted += 1
                    return

                existing = self._conn.execute(
                    SELECT_SPIKE_BY_PK_SQL,
                    (row.instrument_token, row.candle_time, row.rule_version),
                ).fetchone()
                if existing is None:
                    raise RuntimeError(
                        "INSERT OR IGNORE no-op but spike row not found for %s %s %s"
                        % (row.instrument_token, row.candle_time, row.rule_version)
                    )
                if _rows_match(existing, row):
                    self._conn.commit()
                    self._duplicates_ignored += 1
                    return

                self._conflicting_duplicates += 1
                self._conn.rollback()
                raise SpikeConflictError(
                    "PK conflict for token=%d time=%s rule_version=%s"
                    % (row.instrument_token, row.candle_time, row.rule_version)
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
