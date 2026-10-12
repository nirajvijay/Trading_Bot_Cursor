"""Persistence for the ORB detector, in the observation (live) database.

Five tables, all owned by this module and never touched by market-data,
spike, pullback or continuation code:

* live_orb_baselines  each stock's ATR14 and average opening volume today
* live_orb_ranges     every stock's opening range at 09:20, ranked
* live_orb_arms       the stocks armed at 09:20 (at most three)
* live_orb_decisions  the one terminal outcome of each arm
* live_orb_status     what the detector is doing, for the Strategy tab

The ranking is written once per day in a single transaction, before anything
is armed in memory. Each arm gets exactly one decision row; a TRIGGERED row is
what the execution engine reads.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from orb_types import DECISION_TYPES, OrbArm, OrbBaseline, OrbDecision, RankedRange

_IST = ZoneInfo("Asia/Kolkata")

CREATE_BASELINES_SQL = """
CREATE TABLE IF NOT EXISTS live_orb_baselines (
    session_date     TEXT    NOT NULL,
    orb_rule_version TEXT    NOT NULL,
    instrument_token INTEGER NOT NULL,
    tradingsymbol    TEXT    NOT NULL,
    ready            INTEGER NOT NULL CHECK (ready IN (0, 1)),
    reason           TEXT,
    detail           TEXT,
    atr14            REAL,
    avg_or_volume    REAL,
    history_as_of    TEXT,
    min_or_bars      INTEGER,
    sessions_json    TEXT    NOT NULL,
    computed_at      TEXT    NOT NULL,
    PRIMARY KEY (session_date, orb_rule_version, instrument_token)
);
"""

CREATE_RANGES_SQL = """
CREATE TABLE IF NOT EXISTS live_orb_ranges (
    session_date     TEXT    NOT NULL,
    orb_rule_version TEXT    NOT NULL,
    instrument_token INTEGER NOT NULL,
    tradingsymbol    TEXT    NOT NULL,
    valid            INTEGER NOT NULL CHECK (valid IN (0, 1)),
    selected         INTEGER NOT NULL CHECK (selected IN (0, 1)),
    rank             INTEGER,
    reason           TEXT,
    source           TEXT    NOT NULL,
    direction        TEXT,
    level            REAL,
    or_open          REAL,
    or_high          REAL,
    or_low           REAL,
    or_close         REAL,
    or_volume        INTEGER,
    preopen_volume   INTEGER,
    atr14            REAL,
    avg_or_volume    REAL,
    rvol             REAL,
    snapshot_at      TEXT,
    preopen_at       TEXT,
    ranked_at        TEXT    NOT NULL,
    created_at       TEXT    NOT NULL,
    PRIMARY KEY (session_date, orb_rule_version, instrument_token)
);
"""

CREATE_ARMS_SQL = """
CREATE TABLE IF NOT EXISTS live_orb_arms (
    setup_id         TEXT    NOT NULL,
    orb_rule_version TEXT    NOT NULL,
    session_date     TEXT    NOT NULL,
    instrument_token INTEGER NOT NULL,
    tradingsymbol    TEXT    NOT NULL,
    direction        TEXT    NOT NULL CHECK (direction IN ('UP', 'DOWN')),
    level            REAL    NOT NULL,
    level_ticks      INTEGER NOT NULL,
    tick_size        REAL    NOT NULL,
    atr14            REAL    NOT NULL,
    stop_distance    REAL    NOT NULL,
    rvol             REAL    NOT NULL,
    rank             INTEGER NOT NULL,
    or_open          REAL    NOT NULL,
    or_high          REAL    NOT NULL,
    or_low           REAL    NOT NULL,
    or_close         REAL    NOT NULL,
    or_volume        INTEGER NOT NULL,
    avg_or_volume    REAL    NOT NULL,
    armed_at         TEXT    NOT NULL,
    created_at       TEXT    NOT NULL,
    PRIMARY KEY (setup_id, orb_rule_version)
);
"""

CREATE_DECISIONS_SQL = """
CREATE TABLE IF NOT EXISTS live_orb_decisions (
    setup_id              TEXT    NOT NULL,
    orb_rule_version      TEXT    NOT NULL,
    decision_type         TEXT    NOT NULL
        CHECK (decision_type IN ('TRIGGERED', 'MISSED', 'EXPIRED')),
    reason                TEXT,
    trigger_tick_sequence INTEGER,
    trigger_exchange_ts   TEXT,
    trigger_price         REAL,
    planned_stop          REAL,
    overshoot             REAL,
    overshoot_atr         REAL,
    payload_json          TEXT    NOT NULL,
    created_at            TEXT    NOT NULL,
    PRIMARY KEY (setup_id, orb_rule_version)
);
"""

CREATE_STATUS_SQL = """
CREATE TABLE IF NOT EXISTS live_orb_status (
    session_date     TEXT NOT NULL,
    orb_rule_version TEXT NOT NULL,
    state            TEXT NOT NULL,
    reason           TEXT,
    detail_json      TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (session_date, orb_rule_version)
);
"""

ALL_TABLES_SQL = (
    CREATE_BASELINES_SQL,
    CREATE_RANGES_SQL,
    CREATE_ARMS_SQL,
    CREATE_DECISIONS_SQL,
    CREATE_STATUS_SQL,
)

_ARM_COLUMNS = (
    "setup_id, orb_rule_version, session_date, instrument_token, tradingsymbol, "
    "direction, level, level_ticks, tick_size, atr14, stop_distance, rvol, rank, "
    "or_open, or_high, or_low, or_close, or_volume, avg_or_volume"
)

_SQLITE_BUSY, _SQLITE_LOCKED = 5, 6


def _retryable(exc: sqlite3.Error) -> bool:
    """Busy or locked. Works on Pythons without ``sqlite_errorcode`` too."""
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None:
        return (int(code) & 0xFF) in (_SQLITE_BUSY, _SQLITE_LOCKED)
    message = str(exc).lower()
    return "locked" in message or "busy" in message


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ist_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=_IST)
    return value.astimezone(_IST).isoformat(timespec="seconds")


def _dumps(payload: Optional[Mapping[str, Any]]) -> str:
    return json.dumps(dict(payload or {}), sort_keys=True, default=str)


@dataclass(frozen=True)
class OrbDayRecord:
    """What is already on disk for one session: used to resume after a restart."""

    ranked: bool
    arms: List[OrbArm] = field(default_factory=list)
    # setup_id -> decision_type, for arms that already have their outcome.
    decisions: Dict[str, str] = field(default_factory=dict)


class OrbWriterError(RuntimeError):
    """A write that did not happen. The caller decides what that means."""


class OrbWriter:
    def __init__(
        self,
        db_path: Optional[Path] = None,
        *,
        busy_timeout_ms: int = 5000,
        max_write_retries: int = 5,
        retry_base_delay_seconds: float = 0.05,
        conn: Optional[sqlite3.Connection] = None,
        now_fn: Callable[[], datetime] = _utc_now,
    ) -> None:
        if conn is None and db_path is None:
            raise ValueError("OrbWriter needs a db_path or a connection")
        self._now_fn = now_fn
        self._max_write_retries = max_write_retries
        self._retry_base_delay_seconds = retry_base_delay_seconds
        self._owns_conn = conn is None
        if conn is None:
            assert db_path is not None
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        else:
            self._conn = conn
        for statement in ALL_TABLES_SQL:
            self._conn.execute(statement)
        self._conn.commit()
        self._lock = threading.RLock()
        self._closed = False
        self.write_retries = 0
        self.write_failures = 0

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def save_baselines(
        self, session_date: str, rule_version: str, baselines: Iterable[OrbBaseline]
    ) -> int:
        """Replace today's baselines. Returns how many rows were written."""
        now = self._now_iso()
        rows = [
            (
                session_date,
                rule_version,
                int(b.instrument_token),
                b.tradingsymbol,
                1 if b.ready else 0,
                b.reason,
                b.detail,
                b.atr14,
                b.avg_or_volume,
                b.history_as_of,
                b.min_or_bars,
                json.dumps(list(b.sessions)),
                now,
            )
            for b in baselines
        ]

        def work(conn: sqlite3.Connection) -> int:
            conn.executemany(
                """
                INSERT OR REPLACE INTO live_orb_baselines (
                    session_date, orb_rule_version, instrument_token, tradingsymbol,
                    ready, reason, detail, atr14, avg_or_volume, history_as_of,
                    min_or_bars, sessions_json, computed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            return len(rows)

        return self._transaction(work)

    def save_ranking(
        self,
        session_date: str,
        rule_version: str,
        ranked: Sequence[RankedRange],
        arms: Sequence[OrbArm],
        *,
        ranked_at: datetime,
    ) -> bool:
        """Write the day's ranking and its arms, all or nothing.

        Returns False, writing nothing, when this session is already ranked:
        the ranking happens once a day and is never revised.
        """
        now = self._now_iso()
        ranked_iso = _ist_iso(ranked_at)
        range_rows = []
        for entry in ranked:
            item = entry.range
            range_rows.append(
                (
                    session_date,
                    rule_version,
                    int(item.instrument_token),
                    item.tradingsymbol,
                    1 if item.valid else 0,
                    1 if entry.selected else 0,
                    entry.rank,
                    entry.reason,
                    item.source,
                    item.direction,
                    item.level,
                    item.or_open,
                    item.or_high,
                    item.or_low,
                    item.or_close,
                    item.or_volume,
                    item.preopen_volume,
                    item.atr14,
                    item.avg_or_volume,
                    item.rvol,
                    _ist_iso(item.snapshot_at),
                    _ist_iso(item.preopen_at),
                    ranked_iso,
                    now,
                )
            )
        arm_rows = [
            (
                arm.setup_id,
                arm.rule_version,
                arm.session_date,
                int(arm.instrument_token),
                arm.tradingsymbol,
                arm.direction,
                arm.level,
                int(arm.level_ticks),
                arm.tick_size,
                arm.atr14,
                arm.stop_distance,
                arm.rvol,
                int(arm.rank),
                arm.or_open,
                arm.or_high,
                arm.or_low,
                arm.or_close,
                int(arm.or_volume),
                arm.avg_or_volume,
                ranked_iso,
                now,
            )
            for arm in arms
        ]

        def work(conn: sqlite3.Connection) -> bool:
            if self._is_ranked(conn, session_date, rule_version):
                return False
            # Plain INSERT: a row that appeared meanwhile fails the whole
            # transaction instead of being silently merged with ours.
            conn.executemany(
                """
                INSERT INTO live_orb_ranges (
                    session_date, orb_rule_version, instrument_token, tradingsymbol,
                    valid, selected, rank, reason, source, direction, level,
                    or_open, or_high, or_low, or_close, or_volume, preopen_volume,
                    atr14, avg_or_volume, rvol, snapshot_at, preopen_at,
                    ranked_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                range_rows,
            )
            conn.executemany(
                f"""
                INSERT INTO live_orb_arms ({_ARM_COLUMNS}, armed_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                arm_rows,
            )
            return True

        try:
            return bool(self._transaction(work))
        except sqlite3.IntegrityError:
            return False

    def insert_decision(
        self, decision: OrbDecision, *, payload: Optional[Mapping[str, Any]] = None
    ) -> bool:
        """Record an arm's outcome. True when this call wrote it."""
        if decision.decision_type not in DECISION_TYPES:
            raise ValueError(f"invalid decision_type: {decision.decision_type}")
        params = (
            decision.setup_id,
            decision.rule_version,
            decision.decision_type,
            decision.reason,
            decision.tick_sequence,
            _ist_iso(decision.exchange_ts),
            decision.trigger_price,
            decision.planned_stop,
            decision.overshoot,
            decision.overshoot_atr,
            _dumps(payload),
            self._now_iso(),
        )

        def work(conn: sqlite3.Connection) -> bool:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO live_orb_decisions (
                    setup_id, orb_rule_version, decision_type, reason,
                    trigger_tick_sequence, trigger_exchange_ts, trigger_price,
                    planned_stop, overshoot, overshoot_atr, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                params,
            )
            return cur.rowcount == 1

        return bool(self._transaction(work))

    def save_status(
        self,
        session_date: str,
        rule_version: str,
        state: str,
        *,
        reason: Optional[str] = None,
        detail: Optional[Mapping[str, Any]] = None,
    ) -> None:
        params = (session_date, rule_version, state, reason, _dumps(detail), self._now_iso())

        def work(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT OR REPLACE INTO live_orb_status (
                    session_date, orb_rule_version, state, reason, detail_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                params,
            )

        self._transaction(work)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def load_day(self, session_date: str, rule_version: str) -> OrbDayRecord:
        with self._lock:
            self._ensure_open()
            ranked = self._is_ranked(self._conn, session_date, rule_version)
            arm_rows = self._conn.execute(
                f"""
                SELECT {_ARM_COLUMNS}
                FROM live_orb_arms
                WHERE session_date = ? AND orb_rule_version = ?
                ORDER BY rank ASC
                """,
                (session_date, rule_version),
            ).fetchall()
            arms = [
                OrbArm(
                    setup_id=str(row[0]),
                    rule_version=str(row[1]),
                    session_date=str(row[2]),
                    instrument_token=int(row[3]),
                    tradingsymbol=str(row[4]),
                    direction=str(row[5]),
                    level=float(row[6]),
                    level_ticks=int(row[7]),
                    tick_size=float(row[8]),
                    atr14=float(row[9]),
                    stop_distance=float(row[10]),
                    rvol=float(row[11]),
                    rank=int(row[12]),
                    or_open=float(row[13]),
                    or_high=float(row[14]),
                    or_low=float(row[15]),
                    or_close=float(row[16]),
                    or_volume=int(row[17]),
                    avg_or_volume=float(row[18]),
                )
                for row in arm_rows
            ]
            decisions: Dict[str, str] = {}
            for arm in arms:
                row = self._conn.execute(
                    """
                    SELECT decision_type FROM live_orb_decisions
                    WHERE setup_id = ? AND orb_rule_version = ?
                    """,
                    (arm.setup_id, arm.rule_version),
                ).fetchone()
                if row is not None:
                    decisions[arm.setup_id] = str(row[0])
            return OrbDayRecord(ranked=ranked or bool(arms), arms=arms, decisions=decisions)

    @staticmethod
    def _is_ranked(conn: sqlite3.Connection, session_date: str, rule_version: str) -> bool:
        row = conn.execute(
            """
            SELECT 1 FROM live_orb_ranges
            WHERE session_date = ? AND orb_rule_version = ?
            LIMIT 1
            """,
            (session_date, rule_version),
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------

    def _now_iso(self) -> str:
        """Wall-clock UTC, to the second: the same form as the continuation
        writer's created_at, which the engine's trigger floor compares with."""
        now = self._now_fn()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc).isoformat(timespec="seconds")

    def _transaction(self, work):
        """Run ``work(conn)`` and commit; roll back and retry while locked."""
        with self._lock:
            self._ensure_open()
            attempt = 0
            while True:
                try:
                    result = work(self._conn)
                    self._conn.commit()
                    return result
                except sqlite3.IntegrityError:
                    self._conn.rollback()
                    raise
                except sqlite3.Error as exc:
                    self._conn.rollback()
                    if _retryable(exc) and attempt < self._max_write_retries:
                        self.write_retries += 1
                        time.sleep(self._retry_base_delay_seconds * (2**attempt))
                        attempt += 1
                        continue
                    self.write_failures += 1
                    raise OrbWriterError(f"orb writer failed: {exc}") from exc

    def _ensure_open(self) -> None:
        if self._closed:
            raise OrbWriterError("orb writer is closed")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._owns_conn:
                self._conn.close()
