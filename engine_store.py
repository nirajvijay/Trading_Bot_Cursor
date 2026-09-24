"""SqlitePositionStore: the engine's persistent memory.

Two tables, per the finalized schema decision:

* ``positions`` answers "what is true right now" — one row per trade, updated
  in place.
* ``position_events`` is an append-only diary — one row every time something
  meaningful happens, never rewritten — so the *path* a trade took stays
  recoverable, not just where it ended up.

Deliberately leaner than the old engine's ~50-column ``trades`` table: no
partial-fill/partial-exit bookkeeping columns until the exit sequence proves it
needs them, and no ``live_pnl`` column at all (unrealised P&L is derived from
Kite every tick and never persisted — see engine_live_pnl).

Durability is not a tuning knob here. ``save`` must not return until the bytes
are genuinely on disk, because the "write intent before calling the broker"
crash-safety mechanism is worthless otherwise: a crash in the gap between a
fast buffered "done" and the real write would erase the exact record that
mechanism exists to protect. SQLite's committing behaviour already does this
correctly and costs milliseconds at our scale, so ``synchronous=OFF``,
``journal_mode=MEMORY`` and every other "faster" setting are forbidden.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import fields as dataclass_fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from engine_types import ExecutionState, Position, TriggerCandidate

# A position in one of these states can never become open again, so it is
# excluded from the working set and never reconciled against the broker.
TERMINAL_STATES = (
    ExecutionState.CLOSED,
    ExecutionState.REJECTED,
    ExecutionState.CANCELLED,
)

CREATE_POSITIONS_SQL = """
CREATE TABLE IF NOT EXISTS positions (
    trade_id            TEXT    PRIMARY KEY,
    setup_id            TEXT    NOT NULL,
    session_date        TEXT    NOT NULL,
    tradingsymbol       TEXT    NOT NULL,
    direction           TEXT    NOT NULL,
    vwap_classification TEXT,
    state               TEXT    NOT NULL,
    qty                 INTEGER NOT NULL DEFAULT 0,
    entry_price         REAL,
    stop_price          REAL,
    risk_taken_rupees   REAL,
    entry_order_id      TEXT,
    stop_order_id       TEXT,
    exit_order_id       TEXT,
    realised_pnl        REAL,
    is_live             INTEGER NOT NULL DEFAULT 0,
    run_id              TEXT,
    candidate_json      TEXT    NOT NULL,
    extra_json          TEXT    NOT NULL DEFAULT '{}',
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL
);
"""

CREATE_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS position_events (
    event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id     TEXT    NOT NULL,
    at           TEXT    NOT NULL,
    event_type   TEXT    NOT NULL,
    payload_json TEXT    NOT NULL DEFAULT '{}'
);
"""

CREATE_INDEXES_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_positions_setup ON positions (setup_id);",
    "CREATE INDEX IF NOT EXISTS idx_positions_state ON positions (state);",
    "CREATE INDEX IF NOT EXISTS idx_positions_session ON positions (session_date);",
    "CREATE INDEX IF NOT EXISTS idx_events_trade ON position_events (trade_id, event_id);",
)

UPSERT_POSITION_SQL = """
INSERT INTO positions (
    trade_id, setup_id, session_date, tradingsymbol, direction,
    vwap_classification, state, qty, entry_price, stop_price,
    risk_taken_rupees, entry_order_id, stop_order_id, exit_order_id,
    realised_pnl, is_live, run_id, candidate_json, extra_json,
    created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(trade_id) DO UPDATE SET
    state               = excluded.state,
    qty                 = excluded.qty,
    entry_price         = excluded.entry_price,
    stop_price          = excluded.stop_price,
    risk_taken_rupees   = excluded.risk_taken_rupees,
    entry_order_id      = excluded.entry_order_id,
    stop_order_id       = excluded.stop_order_id,
    exit_order_id       = excluded.exit_order_id,
    realised_pnl        = excluded.realised_pnl,
    vwap_classification = excluded.vwap_classification,
    is_live             = excluded.is_live,
    run_id              = excluded.run_id,
    candidate_json      = excluded.candidate_json,
    extra_json          = excluded.extra_json,
    updated_at          = excluded.updated_at
"""

INSERT_EVENT_SQL = """
INSERT INTO position_events (trade_id, at, event_type, payload_json)
VALUES (?, ?, ?, ?)
"""

_CANDIDATE_FIELD_NAMES = frozenset(f.name for f in dataclass_fields(TriggerCandidate))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def candidate_to_json(candidate: TriggerCandidate) -> str:
    """Serialize a candidate as immutable reference data.

    Field-by-field rather than dataclasses.asdict so a future nested type
    cannot silently change the stored shape.
    """
    return json.dumps(
        {name: getattr(candidate, name) for name in sorted(_CANDIDATE_FIELD_NAMES)},
        separators=(",", ":"),
        sort_keys=True,
    )


def candidate_from_json(raw: str) -> TriggerCandidate:
    """Rehydrate a candidate, tolerating schema drift in both directions.

    Unknown keys (written by a newer build) are dropped, and missing keys fall
    back to the dataclass defaults — so adding a field to TriggerCandidate
    never makes yesterday's rows unreadable.
    """
    data = json.loads(raw)
    known = {k: v for k, v in data.items() if k in _CANDIDATE_FIELD_NAMES}
    return TriggerCandidate(**known)


class SqlitePositionStore:
    """Satisfies engine_core.PositionStore, plus the event diary and reads."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), isolation_level="")
        self._conn.row_factory = sqlite3.Row
        # WAL keeps a read-only API reader out of the engine's way while still
        # being fully durable on commit. synchronous=FULL is required for that
        # durability in WAL mode (NORMAL skips the fsync); see module docstring
        # for why this is not negotiable.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._create_schema()

    def _create_schema(self) -> None:
        with self._conn:
            self._conn.execute(CREATE_POSITIONS_SQL)
            self._conn.execute(CREATE_EVENTS_SQL)
            for statement in CREATE_INDEXES_SQL:
                self._conn.execute(statement)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SqlitePositionStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # PositionStore protocol
    # ------------------------------------------------------------------

    def open_positions(self) -> List[Position]:
        """Every position that is not in a terminal state, oldest first."""
        placeholders = ",".join("?" for _ in TERMINAL_STATES)
        rows = self._conn.execute(
            f"SELECT * FROM positions WHERE state NOT IN ({placeholders}) "
            "ORDER BY created_at ASC",
            tuple(state.value for state in TERMINAL_STATES),
        ).fetchall()
        return [self._row_to_position(row) for row in rows]

    def save(self, position: Position) -> None:
        with self._conn:
            self._upsert(position)

    def exists(self, setup_id: str) -> bool:
        """Has this setup already been routed?

        No cross-day scoping needed: the system is intraday-only, every
        position is squared off by 15:15, so each trading day starts genuinely
        fresh.
        """
        row = self._conn.execute(
            "SELECT 1 FROM positions WHERE setup_id = ? LIMIT 1", (setup_id,)
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # Event diary
    # ------------------------------------------------------------------

    def append_event(
        self,
        trade_id: str,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> int:
        with self._conn:
            return self._insert_event(trade_id, event_type, payload)

    def save_with_event(
        self,
        position: Position,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Write the state row and its diary entry in one transaction.

        The load-bearing durability primitive: the crash-safety design is
        meaningless if a crash can leave a state row without the event that
        explains it, or an event describing a state that was never persisted.
        """
        with self._conn:
            self._upsert(position)
            self._insert_event(position.trade_id, event_type, payload)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, trade_id: str) -> Optional[Position]:
        row = self._conn.execute(
            "SELECT * FROM positions WHERE trade_id = ?", (trade_id,)
        ).fetchone()
        return None if row is None else self._row_to_position(row)

    def closed_today(self, session_date: str) -> List[Position]:
        """Closed positions for one session — the daily-loss input."""
        rows = self._conn.execute(
            "SELECT * FROM positions WHERE session_date = ? AND state = ? "
            "ORDER BY updated_at ASC",
            (session_date, ExecutionState.CLOSED.value),
        ).fetchall()
        return [self._row_to_position(row) for row in rows]

    def list_positions(self, session_date: Optional[str] = None) -> List[sqlite3.Row]:
        """Raw rows for the API, including created_at/updated_at."""
        if session_date is None:
            return list(
                self._conn.execute("SELECT * FROM positions ORDER BY created_at ASC")
            )
        return list(
            self._conn.execute(
                "SELECT * FROM positions WHERE session_date = ? ORDER BY created_at ASC",
                (session_date,),
            )
        )

    def list_events(self, trade_id: str) -> List[sqlite3.Row]:
        """The diary for one trade, in the order it happened."""
        return list(
            self._conn.execute(
                "SELECT * FROM position_events WHERE trade_id = ? ORDER BY event_id ASC",
                (trade_id,),
            )
        )

    def latest_event_payloads(
        self, trade_ids: Sequence[str], event_types: Sequence[str]
    ) -> Dict[Tuple[str, str], dict]:
        """The latest payload of each given event type, per trade, in one read."""
        if not trade_ids or not event_types:
            return {}
        id_marks = ",".join("?" for _ in trade_ids)
        type_marks = ",".join("?" for _ in event_types)
        rows = self._conn.execute(
            f"SELECT trade_id, event_type, payload_json FROM position_events "
            f"WHERE trade_id IN ({id_marks}) AND event_type IN ({type_marks}) "
            f"ORDER BY event_id ASC",
            (*trade_ids, *event_types),
        )
        out: Dict[Tuple[str, str], dict] = {}
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except (ValueError, TypeError):
                payload = {}
            out[(str(row["trade_id"]), str(row["event_type"]))] = payload
        return out

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _upsert(self, position: Position) -> None:
        now = _utc_now_iso()
        candidate = position.candidate
        self._conn.execute(
            UPSERT_POSITION_SQL,
            (
                position.trade_id,
                candidate.setup_id,
                candidate.session_date,
                candidate.tradingsymbol,
                candidate.direction,
                candidate.vwap_classification,
                _state_value(position.state),
                int(position.qty or 0),
                position.entry_price,
                position.stop_price,
                position.risk_taken_rupees,
                position.entry_order_id,
                position.stop_order_id,
                position.exit_order_id,
                position.realised_pnl,
                1 if position.is_live else 0,
                position.run_id,
                candidate_to_json(candidate),
                json.dumps(position.extra or {}, separators=(",", ":"), sort_keys=True),
                now,  # created_at, ignored by the ON CONFLICT branch
                now,  # updated_at
            ),
        )

    def _insert_event(
        self,
        trade_id: str,
        event_type: str,
        payload: Optional[Dict[str, Any]],
    ) -> int:
        cursor = self._conn.execute(
            INSERT_EVENT_SQL,
            (
                trade_id,
                _utc_now_iso(),
                event_type,
                json.dumps(payload or {}, separators=(",", ":"), sort_keys=True, default=str),
            ),
        )
        return int(cursor.lastrowid or 0)

    def _row_to_position(self, row: sqlite3.Row) -> Position:
        return Position(
            trade_id=str(row["trade_id"]),
            candidate=candidate_from_json(str(row["candidate_json"])),
            state=ExecutionState(str(row["state"])),
            qty=int(row["qty"] or 0),
            entry_price=_opt_float(row["entry_price"]),
            stop_price=_opt_float(row["stop_price"]),
            entry_order_id=_opt_str(row["entry_order_id"]),
            stop_order_id=_opt_str(row["stop_order_id"]),
            exit_order_id=_opt_str(row["exit_order_id"]),
            realised_pnl=_opt_float(row["realised_pnl"]),
            risk_taken_rupees=_opt_float(row["risk_taken_rupees"]),
            is_live=bool(row["is_live"]),
            run_id=_opt_str(row["run_id"]),
            extra=json.loads(str(row["extra_json"] or "{}")),
        )


def _state_value(state: ExecutionState | str) -> str:
    return state.value if isinstance(state, ExecutionState) else str(state)


def _opt_float(value: object) -> Optional[float]:
    return None if value is None else float(value)


def _opt_str(value: object) -> Optional[str]:
    return None if value is None else str(value)


def realised_loss_rupees(positions: Sequence[Position]) -> float:
    """Total realised loss as a positive number. Profits do not offset.

    One rule, shared with the daily-loss cap (engine_risk.trade_loss_rupees).
    """
    from engine_risk import realised_loss_rupees as _rule

    return _rule(positions)
