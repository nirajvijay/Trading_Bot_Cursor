"""
Append-only persistence for pullback setups and lifecycle events.

Strategy-owned. Never modifies market-data or spike tables.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from live_one_minute_candle_writer import DEFAULT_DB_PATH, is_retryable_sqlite_error
from pullback_types import GapAnalytics, PullbackSetup, SetupEventType, SetupState

CREATE_SETUPS_SQL = """
CREATE TABLE IF NOT EXISTS live_pullback_setups (
    setup_id            TEXT PRIMARY KEY,
    instrument_token    INTEGER NOT NULL,
    tradingsymbol       TEXT    NOT NULL,
    session_date        TEXT    NOT NULL,
    direction           TEXT    NOT NULL,
    spike_candle_time   TEXT    NOT NULL,
    spike_rule_version  TEXT    NOT NULL,
    spike_open          REAL    NOT NULL,
    spike_high          REAL    NOT NULL,
    spike_low           REAL    NOT NULL,
    spike_close         REAL    NOT NULL,
    spike_volume        INTEGER NOT NULL,
    impulse_5m_candle_time TEXT NOT NULL,
    pullback_rule_version TEXT  NOT NULL,
    previous_session_close REAL,
    session_open        REAL,
    gap_absolute        REAL,
    gap_percent         REAL,
    gap_direction       TEXT,
    created_at          TEXT    NOT NULL,
    UNIQUE (
        instrument_token, spike_candle_time,
        spike_rule_version, pullback_rule_version
    )
);
"""

CREATE_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS live_pullback_setup_events (
    setup_id            TEXT    NOT NULL,
    sequence_number     INTEGER NOT NULL,
    event_type          TEXT    NOT NULL,
    resulting_state     TEXT    NOT NULL,
    evaluation_candle_time TEXT,
    payload_json        TEXT    NOT NULL,
    created_at          TEXT    NOT NULL,
    PRIMARY KEY (setup_id, sequence_number)
);
"""

INSERT_SETUP_SQL = """
INSERT OR IGNORE INTO live_pullback_setups (
    setup_id, instrument_token, tradingsymbol, session_date, direction,
    spike_candle_time, spike_rule_version,
    spike_open, spike_high, spike_low, spike_close, spike_volume,
    impulse_5m_candle_time, pullback_rule_version,
    previous_session_close, session_open, gap_absolute, gap_percent, gap_direction,
    created_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

INSERT_EVENT_SQL = """
INSERT OR IGNORE INTO live_pullback_setup_events (
    setup_id, sequence_number, event_type, resulting_state,
    evaluation_candle_time, payload_json, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?)
"""

SELECT_EVENT_SQL = """
SELECT event_type, resulting_state, evaluation_candle_time, payload_json
FROM live_pullback_setup_events
WHERE setup_id = ? AND sequence_number = ?
"""


class PullbackConflictError(Exception):
    """Raised when an event PK collides with a divergent payload."""


@dataclass(frozen=True)
class PullbackWriterMetrics:
    setups_inserted: int
    setup_duplicates_ignored: int
    events_inserted: int
    event_duplicates_ignored: int
    conflicting_duplicates: int
    write_retries: int
    write_failures: int


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dt_iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


class IntradayPullbackWriter:
    def __init__(
        self,
        db_path: Path = DEFAULT_DB_PATH,
        busy_timeout_ms: int = 5000,
        max_write_retries: int = 5,
        retry_base_delay_seconds: float = 0.05,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        self._db_path = db_path
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
        self._conn.execute(CREATE_SETUPS_SQL)
        self._conn.execute(CREATE_EVENTS_SQL)
        self._lock = threading.RLock()
        self._closed = False
        self._setups_inserted = 0
        self._setup_duplicates_ignored = 0
        self._events_inserted = 0
        self._event_duplicates_ignored = 0
        self._conflicting_duplicates = 0
        self._write_retries = 0
        self._write_failures = 0

    @property
    def metrics(self) -> PullbackWriterMetrics:
        with self._lock:
            return PullbackWriterMetrics(
                setups_inserted=self._setups_inserted,
                setup_duplicates_ignored=self._setup_duplicates_ignored,
                events_inserted=self._events_inserted,
                event_duplicates_ignored=self._event_duplicates_ignored,
                conflicting_duplicates=self._conflicting_duplicates,
                write_retries=self._write_retries,
                write_failures=self._write_failures,
            )

    def insert_setup(self, setup: PullbackSetup) -> bool:
        """Insert setup identity. Returns True if newly inserted."""
        gap = setup.gap
        params = (
            setup.setup_id,
            setup.instrument_token,
            setup.tradingsymbol,
            setup.session_date,
            setup.direction,
            _dt_iso(setup.spike_candle_time),
            setup.spike_rule_version,
            setup.spike_open,
            setup.spike_high,
            setup.spike_low,
            setup.spike_close,
            setup.spike_volume,
            _dt_iso(setup.impulse_5m_candle_time),
            setup.pullback_rule_version,
            gap.previous_session_close,
            gap.session_open,
            gap.gap_absolute,
            gap.gap_percent,
            gap.gap_direction,
            _dt_iso(setup.created_at),
        )
        with self._lock:
            if self._closed:
                raise RuntimeError("writer is closed")
            return self._insert_setup_with_retry(params)

    def append_event(
        self,
        *,
        setup_id: str,
        sequence_number: int,
        event_type: SetupEventType,
        resulting_state: SetupState,
        evaluation_candle_time: Optional[datetime] = None,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> bool:
        """Append lifecycle event. Returns True if newly inserted."""
        payload_json = json.dumps(payload or {}, sort_keys=True, default=str)
        eval_time = (
            _dt_iso(evaluation_candle_time) if evaluation_candle_time is not None else None
        )
        params = (
            setup_id,
            sequence_number,
            event_type,
            resulting_state,
            eval_time,
            payload_json,
            _utc_now_iso(),
        )
        with self._lock:
            if self._closed:
                raise RuntimeError("writer is closed")
            return self._insert_event_with_retry(params, payload_json)

    def load_setup_rows(self, session_date: str) -> List[sqlite3.Row]:
        with self._lock:
            self._conn.row_factory = sqlite3.Row
            rows = self._conn.execute(
                "SELECT * FROM live_pullback_setups WHERE session_date = ?",
                (session_date,),
            ).fetchall()
            return list(rows)

    def load_events(self, setup_id: str) -> List[sqlite3.Row]:
        with self._lock:
            self._conn.row_factory = sqlite3.Row
            rows = self._conn.execute(
                """
                SELECT * FROM live_pullback_setup_events
                WHERE setup_id = ?
                ORDER BY sequence_number ASC
                """,
                (setup_id,),
            ).fetchall()
            return list(rows)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._owns_conn:
                self._conn.commit()
                self._conn.close()
            self._closed = True

    def _insert_setup_with_retry(self, params: tuple) -> bool:
        for attempt in range(self._max_write_retries + 1):
            try:
                cursor = self._conn.execute(INSERT_SETUP_SQL, params)
                if cursor.rowcount == 1:
                    self._conn.commit()
                    self._setups_inserted += 1
                    return True
                self._conn.commit()
                self._setup_duplicates_ignored += 1
                return False
            except sqlite3.OperationalError as exc:
                self._conn.rollback()
                if not is_retryable_sqlite_error(exc):
                    self._write_failures += 1
                    raise
                self._write_retries += 1
                if attempt == self._max_write_retries:
                    self._write_failures += 1
                    raise
                time.sleep(self._retry_base_delay_seconds * (2 ** attempt))
        return False

    def _insert_event_with_retry(self, params: tuple, payload_json: str) -> bool:
        for attempt in range(self._max_write_retries + 1):
            try:
                cursor = self._conn.execute(INSERT_EVENT_SQL, params)
                if cursor.rowcount == 1:
                    self._conn.commit()
                    self._events_inserted += 1
                    return True
                existing = self._conn.execute(
                    SELECT_EVENT_SQL, (params[0], params[1])
                ).fetchone()
                if existing is None:
                    raise RuntimeError("event INSERT OR IGNORE no-op but row missing")
                if (
                    existing[0] == params[2]
                    and existing[1] == params[3]
                    and existing[2] == params[4]
                    and existing[3] == payload_json
                ):
                    self._conn.commit()
                    self._event_duplicates_ignored += 1
                    return False
                self._conflicting_duplicates += 1
                self._conn.rollback()
                raise PullbackConflictError(
                    "event conflict setup=%s seq=%s" % (params[0], params[1])
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
                time.sleep(self._retry_base_delay_seconds * (2 ** attempt))
        return False
