"""
Bounded synchronous persistence for VWAP v2 qualifications.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from api.admin_config.snapshot import AdminConfigSnapshot

SQLITE_BUSY = 5
SQLITE_LOCKED = 6
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "live.db"


def is_retryable_sqlite_error(exc: BaseException) -> bool:
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg or getattr(exc, "sqlite_errorcode", None) in (
        SQLITE_BUSY,
        SQLITE_LOCKED,
    )


from vwap_qualifier_v2_types import VWAP_CLASSIFICATIONS, VwapQualificationV2

logger = logging.getLogger(__name__)

TABLE_NAME = "live_vwap_qualifications"

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


class VwapQualifierMigrationError(Exception):
    """Raised when the live VWAP table PK cannot be migrated."""


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def qualification_pk_columns(conn: sqlite3.Connection) -> tuple[str, ...]:
    rows = conn.execute("PRAGMA table_info(%s)" % TABLE_NAME).fetchall()
    if not rows:
        return ()
    pk = sorted((int(row[5]), str(row[1])) for row in rows if int(row[5]) > 0)
    return tuple(name for _order, name in pk)

NEW_PK_COLUMNS = (
    "session_date",
    "setup_id",
    "continuation_rule_version",
    "vwap_rule_version",
)


@dataclass(frozen=True)
class VwapV2WriterMetrics:
    inserted: int
    duplicates_ignored: int
    persist_failures: int
    write_retries: int


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dt_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat(timespec="seconds")


def _build_payload(row: VwapQualificationV2, provenance: Optional["AdminConfigSnapshot"] = None) -> str:
    payload: dict[str, Any] = {
        "rule_version": row.vwap_rule_version,
        "classification": row.classification,
        "risk_profile": row.risk_profile,
        "risk_cap_inr": row.risk_cap_inr,
        "vwap": row.vwap,
        "gap": row.gap,
        "requested_cutoff_minute": _dt_iso(row.requested_cutoff_minute),
        "actual_snapshot_cutoff_minute": _dt_iso(row.actual_snapshot_cutoff_minute),
        "cache_age_minutes": row.cache_age_minutes,
        "quality_reason": row.quality_reason,
        "historical_minute_count": row.historical_minute_count,
        "live_minute_count": row.live_minute_count,
    }
    if provenance is not None:
        payload["admin_provenance"] = provenance.provenance_fields()
        payload["admin_provenance"]["per_trade_risk_cap_inr"] = provenance.per_trade_risk_cap_inr
        payload["admin_provenance"]["limited_per_trade_risk_cap_inr"] = (
            provenance.limited_per_trade_risk_cap_inr
        )
    return json.dumps(payload, sort_keys=True, default=str)


class VwapQualifierV2Writer:
    def __init__(
        self,
        db_path: Path = DEFAULT_DB_PATH,
        *,
        busy_timeout_ms: int = 5000,
        max_write_retries: int = 5,
        retry_base_delay_seconds: float = 0.01,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        self._db_path = db_path
        self._max_write_retries = max_write_retries
        self._retry_base_delay = retry_base_delay_seconds
        self._owns_conn = conn is None
        self._inserted = 0
        self._duplicates_ignored = 0
        self._persist_failures = 0
        self._write_retries = 0
        if conn is None:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        else:
            self._conn = conn
        self._prepare_schema()

    def _prepare_schema(self) -> None:
        if not _table_exists(self._conn, TABLE_NAME):
            self._conn.execute(CREATE_SQL)
            self._conn.commit()
        else:
            pk = qualification_pk_columns(self._conn)
            if pk != NEW_PK_COLUMNS and pk != (
                "setup_id",
                "continuation_rule_version",
                "vwap_rule_version",
            ):
                raise VwapQualifierMigrationError(
                    "unexpected live_vwap_qualifications primary key: %s" % (pk,)
                )
        for col, ddl in (
            ("admin_config_version_id", "TEXT"),
            ("per_trade_risk_cap_inr", "REAL"),
            ("limited_per_trade_risk_cap_inr", "REAL"),
            ("daily_loss_cap_inr", "REAL"),
            ("vwap_accept_gap_exclusive_max", "REAL"),
            ("vwap_limited_gap_inclusive_max", "REAL"),
            ("admin_config_read_at", "TEXT"),
        ):
            self._ensure_column(col, ddl)
        self._conn.commit()

    def _ensure_column(self, name: str, ddl: str) -> None:
        rows = self._conn.execute(f"PRAGMA table_info({TABLE_NAME})").fetchall()
        existing = {str(r[1]) for r in rows}
        if name not in existing:
            self._conn.execute(f"ALTER TABLE {TABLE_NAME} ADD COLUMN {name} {ddl}")

    @property
    def metrics(self) -> VwapV2WriterMetrics:
        return VwapV2WriterMetrics(
            inserted=self._inserted,
            duplicates_ignored=self._duplicates_ignored,
            persist_failures=self._persist_failures,
            write_retries=self._write_retries,
        )

    def insert_sync(
        self,
        row: VwapQualificationV2,
        *,
        provenance: Optional["AdminConfigSnapshot"] = None,
    ) -> bool:
        if row.classification not in VWAP_CLASSIFICATIONS:
            raise ValueError("invalid classification: %s" % row.classification)
        payload = _build_payload(row, provenance)
        prov_cols = (
            provenance.admin_config_version_id if provenance else None,
            provenance.per_trade_risk_cap_inr if provenance else None,
            provenance.limited_per_trade_risk_cap_inr if provenance else None,
            provenance.daily_loss_cap_inr if provenance else None,
            provenance.vwap_accept_gap_exclusive_max if provenance else None,
            provenance.vwap_limited_gap_inclusive_max if provenance else None,
            provenance.admin_config_read_at if provenance else None,
        )
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
            "live",
            _dt_iso(row.actual_snapshot_cutoff_minute),
            row.historical_minute_count + row.live_minute_count,
            None,
            0,
            payload,
            _utc_now_iso(),
            *prov_cols,
        )
        sql = """
        INSERT OR IGNORE INTO live_vwap_qualifications (
            setup_id, continuation_rule_version, vwap_rule_version,
            instrument_token, tradingsymbol, session_date, direction,
            trigger_price, last_price, trigger_tick_sequence, trigger_exchange_ts,
            vwap, gap, classification, quality_ok, quality_reason, vwap_provenance,
            bootstrap_cutoff_exchange_ts, completed_5m_count, in_progress_bucket_start,
            in_progress_volume, payload_json, created_at,
            admin_config_version_id, per_trade_risk_cap_inr, limited_per_trade_risk_cap_inr,
            daily_loss_cap_inr, vwap_accept_gap_exclusive_max, vwap_limited_gap_inclusive_max,
            admin_config_read_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        attempt = 0
        while True:
            try:
                cur = self._conn.execute(sql, params)
                self._conn.commit()
                if cur.rowcount == 1:
                    self._inserted += 1
                    return True
                self._duplicates_ignored += 1
                return False
            except sqlite3.Error as exc:
                if (
                    isinstance(exc, sqlite3.OperationalError)
                    and is_retryable_sqlite_error(exc)
                    and attempt < self._max_write_retries
                ):
                    self._write_retries += 1
                    time.sleep(self._retry_base_delay * (2**attempt))
                    attempt += 1
                    continue
                self._persist_failures += 1
                logger.error(
                    "VWAP_PERSIST_FAILED setup=%s classification=%s err=%s",
                    row.setup_id,
                    row.classification,
                    exc,
                )
                return False

    def close(self) -> None:
        if self._owns_conn:
            self._conn.close()
