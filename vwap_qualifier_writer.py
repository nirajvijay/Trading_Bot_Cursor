"""
Append-only async persistence for VWAP qualifications.

Strategy-owned. Never modifies observation continuation or market-data tables.
Never overwrites a live row (including UNAVAILABLE).
"""

from __future__ import annotations

import itertools
import json
import logging
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from live_one_minute_candle_writer import DEFAULT_DB_PATH, is_retryable_sqlite_error
from vwap_qualifier_types import VWAP_CLASSIFICATIONS, VwapQualification

logger = logging.getLogger(__name__)

_SENTINEL = object()

PRIORITY_TRIGGER = 0
PRIORITY_NORMAL = 1
PRIORITY_STOP = 2

TABLE_NAME = "live_vwap_qualifications"
_MIGRATE_TEMP = "live_vwap_qualifications_new"

OLD_PK_COLUMNS = ("setup_id", "continuation_rule_version", "vwap_rule_version")
NEW_PK_COLUMNS = (
    "session_date",
    "setup_id",
    "continuation_rule_version",
    "vwap_rule_version",
)

_COLUMNS = (
    "setup_id",
    "continuation_rule_version",
    "vwap_rule_version",
    "instrument_token",
    "tradingsymbol",
    "session_date",
    "direction",
    "trigger_price",
    "last_price",
    "trigger_tick_sequence",
    "trigger_exchange_ts",
    "vwap",
    "gap",
    "classification",
    "quality_ok",
    "quality_reason",
    "vwap_provenance",
    "bootstrap_cutoff_exchange_ts",
    "completed_5m_count",
    "in_progress_bucket_start",
    "in_progress_volume",
    "payload_json",
    "created_at",
)

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS live_vwap_qualifications (
    setup_id                      TEXT    NOT NULL,
    continuation_rule_version     TEXT    NOT NULL,
    vwap_rule_version             TEXT    NOT NULL,
    instrument_token              INTEGER NOT NULL,
    tradingsymbol                 TEXT    NOT NULL,
    session_date                  TEXT    NOT NULL,
    direction                     TEXT    NOT NULL,
    trigger_price                 REAL    NOT NULL,
    last_price                    REAL    NOT NULL,
    trigger_tick_sequence         INTEGER NOT NULL,
    trigger_exchange_ts           TEXT    NOT NULL,
    vwap                          REAL,
    gap                           REAL,
    classification                TEXT    NOT NULL
        CHECK (classification IN ('ACCEPT', 'LIMITED', 'REJECT', 'UNAVAILABLE')),
    quality_ok                    INTEGER NOT NULL
        CHECK (quality_ok IN (0, 1)),
    quality_reason                TEXT,
    vwap_provenance               TEXT,
    bootstrap_cutoff_exchange_ts  TEXT,
    completed_5m_count            INTEGER NOT NULL,
    in_progress_bucket_start      TEXT,
    in_progress_volume            INTEGER NOT NULL,
    payload_json                  TEXT    NOT NULL,
    created_at                    TEXT    NOT NULL,
    PRIMARY KEY (session_date, setup_id, continuation_rule_version, vwap_rule_version)
);
"""

CREATE_NEW_TABLE_SQL = CREATE_SQL.replace(
    "CREATE TABLE IF NOT EXISTS live_vwap_qualifications",
    "CREATE TABLE live_vwap_qualifications_new",
)

INSERT_SQL = """
INSERT OR IGNORE INTO live_vwap_qualifications (
    setup_id, continuation_rule_version, vwap_rule_version,
    instrument_token, tradingsymbol, session_date, direction,
    trigger_price, last_price, trigger_tick_sequence, trigger_exchange_ts,
    vwap, gap, classification, quality_ok, quality_reason, vwap_provenance,
    bootstrap_cutoff_exchange_ts, completed_5m_count, in_progress_bucket_start,
    in_progress_volume, payload_json, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

SELECT_SQL = """
SELECT classification, quality_ok, quality_reason, payload_json
FROM live_vwap_qualifications
WHERE session_date = ?
  AND setup_id = ?
  AND continuation_rule_version = ?
  AND vwap_rule_version = ?
"""

_COPY_SQL = (
    "INSERT INTO %s (%s) SELECT %s FROM %s"
    % (_MIGRATE_TEMP, ", ".join(_COLUMNS), ", ".join(_COLUMNS), TABLE_NAME)
)


class VwapQualifierConflictError(Exception):
    """Raised when a PK collides with a divergent payload."""


class VwapQualifierMigrationError(Exception):
    """Raised when the live VWAP table PK cannot be migrated."""


@dataclass(frozen=True)
class VwapWriterMetrics:
    inserted: int
    duplicates_ignored: int
    conflicting_duplicates: int
    write_retries: int
    write_failures: int
    queue_full: int


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dt_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat(timespec="seconds")


def _payload_dumps(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, default=str)


def qualification_pk_columns(conn: sqlite3.Connection) -> tuple[str, ...]:
    rows = conn.execute("PRAGMA table_info(%s)" % TABLE_NAME).fetchall()
    if not rows:
        return ()
    pk = sorted((int(row[5]), str(row[1])) for row in rows if int(row[5]) > 0)
    return tuple(name for _order, name in pk)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


class VwapQualifierWriter:
    def __init__(
        self,
        db_path: Path = DEFAULT_DB_PATH,
        busy_timeout_ms: int = 5000,
        max_write_retries: int = 5,
        retry_base_delay_seconds: float = 0.05,
        queue_maxsize: int = 256,
        conn: Optional[sqlite3.Connection] = None,
        *,
        start_worker: bool = True,
    ) -> None:
        self._db_path = db_path
        self._max_write_retries = max_write_retries
        self._retry_base_delay_seconds = retry_base_delay_seconds
        self._owns_conn = conn is None
        self._lock = threading.RLock()
        self._closed = False
        self._closing = False
        self._inserted = 0
        self._duplicates_ignored = 0
        self._conflicting_duplicates = 0
        self._write_retries = 0
        self._write_failures = 0
        self._queue_full = 0
        self._seq = itertools.count()
        self._queue: queue.PriorityQueue = queue.PriorityQueue(maxsize=queue_maxsize)
        self._worker: Optional[threading.Thread] = None
        if conn is None:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        else:
            self._conn = conn
        try:
            self._prepare_schema()
        except Exception:
            self._closed = True
            if self._owns_conn:
                self._conn.close()
            raise
        if start_worker:
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="vwap-qualifier-writer",
                daemon=True,
            )
            self._worker.start()

    def _prepare_schema(self) -> None:
        if not _table_exists(self._conn, TABLE_NAME):
            self._conn.execute(CREATE_SQL)
            self._conn.commit()
            return
        pk = qualification_pk_columns(self._conn)
        if pk == NEW_PK_COLUMNS:
            return
        if pk == OLD_PK_COLUMNS:
            self._migrate_pk()
            return
        raise VwapQualifierMigrationError(
            "unexpected live_vwap_qualifications primary key: %s" % (pk,)
        )

    def _migrate_pk(self) -> None:
        previous = self._conn.isolation_level
        self._conn.isolation_level = None
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute("DROP TABLE IF EXISTS %s" % _MIGRATE_TEMP)
                self._conn.execute(CREATE_NEW_TABLE_SQL)
                self._copy_qualification_rows()
                self._conn.execute("DROP TABLE %s" % TABLE_NAME)
                self._conn.execute(
                    "ALTER TABLE %s RENAME TO %s" % (_MIGRATE_TEMP, TABLE_NAME)
                )
                self._conn.execute("COMMIT")
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
        finally:
            self._conn.isolation_level = previous

    def _copy_qualification_rows(self) -> None:
        self._conn.execute(_COPY_SQL)

    @property
    def metrics(self) -> VwapWriterMetrics:
        with self._lock:
            return VwapWriterMetrics(
                inserted=self._inserted,
                duplicates_ignored=self._duplicates_ignored,
                conflicting_duplicates=self._conflicting_duplicates,
                write_retries=self._write_retries,
                write_failures=self._write_failures,
                queue_full=self._queue_full,
            )

    def enqueue(self, row: VwapQualification) -> None:
        """Non-blocking persist for ordinary background writes."""
        self._enqueue(row, PRIORITY_NORMAL)

    def enqueue_priority(self, row: VwapQualification) -> None:
        """Non-blocking persist for raw-trigger qualifications. Wakes an idle worker."""
        self._enqueue(row, PRIORITY_TRIGGER)

    def _enqueue(self, row: VwapQualification, priority: int) -> None:
        if row.classification not in VWAP_CLASSIFICATIONS:
            raise ValueError("invalid classification: %s" % row.classification)
        with self._lock:
            if self._closed or self._closing:
                return
        item = (priority, next(self._seq), row)
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            with self._lock:
                self._queue_full += 1
            logger.error(
                "vwap qualifier persist queue full; dropping write setup=%s",
                row.setup_id,
            )

    def insert_sync(self, row: VwapQualification) -> bool:
        """Synchronous insert for tests / drain worker."""
        return self._insert_row(row)

    def _worker_loop(self) -> None:
        while True:
            try:
                _priority, _seq, payload = self._queue.get()
            except Exception:  # noqa: BLE001
                logger.exception("vwap qualifier writer queue get failed")
                continue
            if payload is _SENTINEL:
                return
            try:
                self._insert_row(payload)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "vwap qualifier persist failed setup=%s",
                    getattr(payload, "setup_id", "?"),
                )

    def _insert_row(self, row: VwapQualification) -> bool:
        contributions = [
            {
                "bucket_start": c.bucket_start.isoformat(timespec="seconds"),
                "volume": c.volume,
                "pv": c.pv,
                "provenance": c.provenance,
            }
            for c in row.contributions
        ]
        payload = _payload_dumps({"contributions": contributions})
        params = (
            row.setup_id,
            row.continuation_rule_version,
            row.vwap_rule_version,
            row.instrument_token,
            row.tradingsymbol,
            row.session_date,
            row.direction,
            row.trigger_price,
            row.last_price,
            row.trigger_tick_sequence,
            _dt_iso(row.trigger_exchange_ts),
            row.vwap,
            row.gap,
            row.classification,
            1 if row.quality_ok else 0,
            row.quality_reason,
            row.vwap_provenance,
            _dt_iso(row.bootstrap_cutoff_exchange_ts),
            row.completed_5m_count,
            _dt_iso(row.in_progress_bucket_start),
            row.in_progress_volume,
            payload,
            _utc_now_iso(),
        )
        with self._lock:
            self._ensure_open()
            inserted = self._execute_insert(INSERT_SQL, params)
            if inserted:
                self._inserted += 1
                return True
            existing = self._conn.execute(
                SELECT_SQL,
                (
                    row.session_date,
                    row.setup_id,
                    row.continuation_rule_version,
                    row.vwap_rule_version,
                ),
            ).fetchone()
            if existing is None:
                self._write_failures += 1
                raise RuntimeError("vwap insert ignored but row missing")
            if str(existing[0]) != row.classification:
                self._conflicting_duplicates += 1
                raise VwapQualifierConflictError(
                    "divergent vwap qualification for %s existing=%s new=%s"
                    % (row.setup_id, existing[0], row.classification)
                )
            self._duplicates_ignored += 1
            return False

    def _execute_insert(self, sql: str, params: tuple) -> bool:
        attempt = 0
        while True:
            try:
                cur = self._conn.execute(sql, params)
                self._conn.commit()
                return cur.rowcount == 1
            except sqlite3.Error as exc:
                if (
                    isinstance(exc, sqlite3.OperationalError)
                    and is_retryable_sqlite_error(exc)
                    and attempt < self._max_write_retries
                ):
                    self._write_retries += 1
                    time.sleep(self._retry_base_delay_seconds * (2**attempt))
                    attempt += 1
                    continue
                self._write_failures += 1
                raise RuntimeError("vwap qualifier writer failed: %s" % exc) from exc

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("vwap qualifier writer is closed")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closing = True
        if self._worker is not None:
            try:
                self._queue.put(
                    (PRIORITY_STOP, next(self._seq), _SENTINEL),
                    timeout=2.0,
                )
            except queue.Full:
                logger.error("vwap writer close could not enqueue stop token")
            self._worker.join(timeout=5.0)
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._owns_conn:
                self._conn.close()
