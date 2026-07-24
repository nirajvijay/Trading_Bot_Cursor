"""
Append-only persistence for continuation arms and terminal decisions.

Strategy-owned. Never modifies market-data, spike, or pullback identity tables.
Exactly one terminal decision per (setup_id, continuation_rule_version).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from live_one_minute_candle_writer import DEFAULT_DB_PATH, is_retryable_sqlite_error

CREATE_ARMS_SQL = """
CREATE TABLE IF NOT EXISTS live_continuation_arms (
    setup_id                 TEXT    NOT NULL,
    continuation_rule_version TEXT   NOT NULL,
    instrument_token         INTEGER NOT NULL,
    tradingsymbol            TEXT    NOT NULL,
    session_date             TEXT    NOT NULL,
    direction                TEXT    NOT NULL,
    pullback_swing_high      REAL,
    pullback_swing_low       REAL,
    tick_size                REAL    NOT NULL,
    buffer_ticks             INTEGER NOT NULL,
    trigger_price            REAL    NOT NULL,
    trigger_price_ticks      INTEGER NOT NULL,
    pullback_type            TEXT,
    ready_5m_candle_time     TEXT,
    armed_at                 TEXT    NOT NULL,
    payload_json             TEXT    NOT NULL,
    PRIMARY KEY (setup_id, continuation_rule_version)
);
"""

CREATE_DECISIONS_SQL = """
CREATE TABLE IF NOT EXISTS live_continuation_decisions (
    setup_id                 TEXT    NOT NULL,
    continuation_rule_version TEXT   NOT NULL,
    decision_type            TEXT    NOT NULL
        CHECK (decision_type IN ('TRIGGERED', 'REJECTED', 'DISARMED')),
    reason                   TEXT,
    trigger_tick_sequence    INTEGER,
    trigger_exchange_ts      TEXT,
    last_price               REAL,
    last_price_ticks         INTEGER,
    breakout_candle_time     TEXT,
    breakout_candle_volume   INTEGER,
    avg_prior_3_1m_volume    REAL,
    volume_ok                INTEGER,
    volume_reliable          INTEGER,
    payload_json             TEXT    NOT NULL,
    created_at               TEXT    NOT NULL,
    PRIMARY KEY (setup_id, continuation_rule_version)
);
"""

INSERT_ARM_SQL = """
INSERT OR IGNORE INTO live_continuation_arms (
    setup_id, continuation_rule_version, instrument_token, tradingsymbol,
    session_date, direction, pullback_swing_high, pullback_swing_low,
    tick_size, buffer_ticks, trigger_price, trigger_price_ticks,
    pullback_type, ready_5m_candle_time, armed_at, payload_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

SELECT_ARM_SQL = """
SELECT instrument_token, tradingsymbol, session_date, direction,
       pullback_swing_high, pullback_swing_low, tick_size, buffer_ticks,
       trigger_price, trigger_price_ticks, pullback_type, ready_5m_candle_time,
       payload_json
FROM live_continuation_arms
WHERE setup_id = ? AND continuation_rule_version = ?
"""

INSERT_DECISION_SQL = """
INSERT OR IGNORE INTO live_continuation_decisions (
    setup_id, continuation_rule_version, decision_type, reason,
    trigger_tick_sequence, trigger_exchange_ts, last_price, last_price_ticks,
    breakout_candle_time, breakout_candle_volume, avg_prior_3_1m_volume,
    volume_ok, volume_reliable, payload_json, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

SELECT_DECISION_SQL = """
SELECT decision_type, reason, trigger_tick_sequence, trigger_exchange_ts,
       last_price, last_price_ticks, breakout_candle_time,
       breakout_candle_volume, avg_prior_3_1m_volume, volume_ok,
       volume_reliable, payload_json
FROM live_continuation_decisions
WHERE setup_id = ? AND continuation_rule_version = ?
"""


class ContinuationConflictError(Exception):
    """Raised when a PK collides with a divergent payload."""


@dataclass(frozen=True)
class ContinuationWriterMetrics:
    arms_inserted: int
    arm_duplicates_ignored: int
    decisions_inserted: int
    decision_duplicates_ignored: int
    conflicting_duplicates: int
    write_retries: int
    write_failures: int


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dt_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat(timespec="seconds")


def _payload_dumps(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, default=str)


class IntradayContinuationWriter:
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
        self._conn.execute(CREATE_ARMS_SQL)
        self._conn.execute(CREATE_DECISIONS_SQL)
        self._lock = threading.RLock()
        self._closed = False
        self._arms_inserted = 0
        self._arm_duplicates_ignored = 0
        self._decisions_inserted = 0
        self._decision_duplicates_ignored = 0
        self._conflicting_duplicates = 0
        self._write_retries = 0
        self._write_failures = 0

    @property
    def metrics(self) -> ContinuationWriterMetrics:
        with self._lock:
            return ContinuationWriterMetrics(
                arms_inserted=self._arms_inserted,
                arm_duplicates_ignored=self._arm_duplicates_ignored,
                decisions_inserted=self._decisions_inserted,
                decision_duplicates_ignored=self._decision_duplicates_ignored,
                conflicting_duplicates=self._conflicting_duplicates,
                write_retries=self._write_retries,
                write_failures=self._write_failures,
            )

    def insert_arm(
        self,
        *,
        setup_id: str,
        continuation_rule_version: str,
        instrument_token: int,
        tradingsymbol: str,
        session_date: str,
        direction: str,
        pullback_swing_high: Optional[float],
        pullback_swing_low: Optional[float],
        tick_size: float,
        buffer_ticks: int,
        trigger_price: float,
        trigger_price_ticks: int,
        pullback_type: Optional[str],
        ready_5m_candle_time: Optional[datetime],
        armed_at: Optional[datetime] = None,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> bool:
        """Insert arm. Returns True if newly inserted."""
        params = (
            setup_id,
            continuation_rule_version,
            instrument_token,
            tradingsymbol,
            session_date,
            direction,
            pullback_swing_high,
            pullback_swing_low,
            tick_size,
            buffer_ticks,
            trigger_price,
            trigger_price_ticks,
            pullback_type,
            _dt_iso(ready_5m_candle_time),
            _dt_iso(armed_at) or _utc_now_iso(),
            _payload_dumps(payload or {}),
        )
        with self._lock:
            self._ensure_open()
            inserted = self._execute_insert(INSERT_ARM_SQL, params)
            if inserted:
                self._arms_inserted += 1
                return True
            existing = self._conn.execute(
                SELECT_ARM_SQL, (setup_id, continuation_rule_version)
            ).fetchone()
            if existing is None:
                self._write_failures += 1
                raise RuntimeError("arm insert ignored but row missing")
            expected = (
                instrument_token,
                tradingsymbol,
                session_date,
                direction,
                pullback_swing_high,
                pullback_swing_low,
                tick_size,
                buffer_ticks,
                trigger_price,
                trigger_price_ticks,
                pullback_type,
                _dt_iso(ready_5m_candle_time),
                _payload_dumps(payload or {}),
            )
            # Compare excluding armed_at (wall-clock).
            got = (
                existing[0],
                existing[1],
                existing[2],
                existing[3],
                existing[4],
                existing[5],
                existing[6],
                existing[7],
                existing[8],
                existing[9],
                existing[10],
                existing[11],
                existing[12],
            )
            if got != expected:
                self._conflicting_duplicates += 1
                raise ContinuationConflictError(
                    "divergent continuation arm for %s/%s"
                    % (setup_id, continuation_rule_version)
                )
            self._arm_duplicates_ignored += 1
            return False

    def insert_decision(
        self,
        *,
        setup_id: str,
        continuation_rule_version: str,
        decision_type: str,
        reason: Optional[str] = None,
        trigger_tick_sequence: Optional[int] = None,
        trigger_exchange_ts: Optional[datetime] = None,
        last_price: Optional[float] = None,
        last_price_ticks: Optional[int] = None,
        breakout_candle_time: Optional[datetime] = None,
        breakout_candle_volume: Optional[int] = None,
        avg_prior_3_1m_volume: Optional[float] = None,
        volume_ok: Optional[bool] = None,
        volume_reliable: Optional[bool] = None,
        payload: Optional[Mapping[str, Any]] = None,
        created_at: Optional[datetime] = None,
    ) -> bool:
        """Insert terminal decision. Returns True if newly inserted."""
        if decision_type not in ("TRIGGERED", "REJECTED", "DISARMED"):
            raise ValueError("invalid decision_type: %s" % decision_type)
        vol_ok = None if volume_ok is None else (1 if volume_ok else 0)
        vol_rel = None if volume_reliable is None else (1 if volume_reliable else 0)
        params = (
            setup_id,
            continuation_rule_version,
            decision_type,
            reason,
            trigger_tick_sequence,
            _dt_iso(trigger_exchange_ts),
            last_price,
            last_price_ticks,
            _dt_iso(breakout_candle_time),
            breakout_candle_volume,
            avg_prior_3_1m_volume,
            vol_ok,
            vol_rel,
            _payload_dumps(payload or {}),
            _dt_iso(created_at) or _utc_now_iso(),
        )
        with self._lock:
            self._ensure_open()
            inserted = self._execute_insert(INSERT_DECISION_SQL, params)
            if inserted:
                self._decisions_inserted += 1
                return True
            existing = self._conn.execute(
                SELECT_DECISION_SQL, (setup_id, continuation_rule_version)
            ).fetchone()
            if existing is None:
                self._write_failures += 1
                raise RuntimeError("decision insert ignored but row missing")
            # Compare excluding created_at.
            expected = (
                decision_type,
                reason,
                trigger_tick_sequence,
                _dt_iso(trigger_exchange_ts),
                last_price,
                last_price_ticks,
                _dt_iso(breakout_candle_time),
                breakout_candle_volume,
                avg_prior_3_1m_volume,
                vol_ok,
                vol_rel,
                _payload_dumps(payload or {}),
            )
            got = tuple(existing)
            if got != expected:
                self._conflicting_duplicates += 1
                raise ContinuationConflictError(
                    "divergent continuation decision for %s/%s existing=%s new=%s"
                    % (setup_id, continuation_rule_version, got[0], decision_type)
                )
            self._decision_duplicates_ignored += 1
            return False

    def _execute_insert(self, sql: str, params: tuple) -> bool:
        attempt = 0
        while True:
            try:
                cur = self._conn.execute(sql, params)
                self._conn.commit()
                return cur.rowcount == 1
            except sqlite3.Error as exc:
                if is_retryable_sqlite_error(exc) and attempt < self._max_write_retries:
                    self._write_retries += 1
                    time.sleep(self._retry_base_delay_seconds * (2**attempt))
                    attempt += 1
                    continue
                self._write_failures += 1
                raise RuntimeError("continuation writer failed: %s" % exc) from exc

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("continuation writer is closed")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._owns_conn:
                self._conn.close()
