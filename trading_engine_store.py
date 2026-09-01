"""SQLite persistence for the Version 1 trading engine."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Mapping, Optional

from trading_engine_types import (
    DEFAULT_TOTAL_CAPITAL,
    DEMO_LEVERAGE_FACTOR,
    EngineCommand,
    TradeRecord,
)

CREATE_RUNS_SQL = """
CREATE TABLE IF NOT EXISTS engine_runs (
    run_id TEXT PRIMARY KEY,
    session_date TEXT NOT NULL,
    started_at TEXT NOT NULL,
    stopped_at TEXT,
    status TEXT NOT NULL,
    live_orders_enabled INTEGER NOT NULL DEFAULT 0,
    pid INTEGER,
    last_error TEXT,
    total_capital REAL NOT NULL DEFAULT 300000,
    leverage REAL NOT NULL DEFAULT 5,
    consume_new_triggers INTEGER NOT NULL DEFAULT 1,
    require_vwap_accept INTEGER NOT NULL DEFAULT 1
);
"""

CREATE_TRADES_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    setup_id TEXT NOT NULL,
    continuation_rule_version TEXT NOT NULL,
    broker_tag TEXT NOT NULL UNIQUE,
    session_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    instrument_token INTEGER NOT NULL,
    direction TEXT NOT NULL,
    qty INTEGER NOT NULL DEFAULT 0,
    entry_estimate REAL NOT NULL,
    entry_fill REAL,
    initial_stop REAL,
    current_stop REAL,
    exit_fill REAL,
    tick_size REAL NOT NULL,
    notional REAL NOT NULL DEFAULT 0,
    margin_blocked REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    skip_reason TEXT,
    reject_reason TEXT,
    close_reason TEXT,
    entry_order_id TEXT,
    sl_order_id TEXT,
    realised_pnl REAL NOT NULL DEFAULT 0,
    open_pnl REAL NOT NULL DEFAULT 0,
    closed_loss_contribution REAL NOT NULL DEFAULT 0,
    trigger_time TEXT,
    entry_time TEXT,
    close_time TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    auto_trail_enabled INTEGER NOT NULL DEFAULT 0,
    auto_trail_ticks INTEGER,
    auto_trail_extreme REAL,
    UNIQUE (setup_id, continuation_rule_version)
);
"""

CREATE_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS trade_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    trade_id TEXT NOT NULL,
    action TEXT NOT NULL,
    old_stop REAL,
    new_stop REAL,
    actor TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
"""

CREATE_COMMANDS_SQL = """
CREATE TABLE IF NOT EXISTS engine_commands (
    command_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    trade_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    processed_at TEXT
);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_trade_id() -> str:
    return uuid.uuid4().hex


def make_broker_tag(trade_id: str) -> str:
    """Kite tags are alphanumeric, max 20 chars."""
    return ("te" + trade_id)[:20]


def _row_to_trade(row: sqlite3.Row) -> TradeRecord:
    return TradeRecord(
        trade_id=str(row["trade_id"]),
        setup_id=str(row["setup_id"]),
        continuation_rule_version=str(row["continuation_rule_version"]),
        broker_tag=str(row["broker_tag"]),
        session_date=str(row["session_date"]),
        symbol=str(row["symbol"]),
        instrument_token=int(row["instrument_token"]),
        direction=str(row["direction"]),
        qty=int(row["qty"]),
        entry_estimate=float(row["entry_estimate"]),
        entry_fill=None if row["entry_fill"] is None else float(row["entry_fill"]),
        initial_stop=None if row["initial_stop"] is None else float(row["initial_stop"]),
        current_stop=None if row["current_stop"] is None else float(row["current_stop"]),
        exit_fill=None if row["exit_fill"] is None else float(row["exit_fill"]),
        tick_size=float(row["tick_size"]),
        notional=float(row["notional"]),
        margin_blocked=float(row["margin_blocked"]),
        status=str(row["status"]),
        skip_reason=None if row["skip_reason"] is None else str(row["skip_reason"]),
        reject_reason=None if row["reject_reason"] is None else str(row["reject_reason"]),
        close_reason=None if row["close_reason"] is None else str(row["close_reason"]),
        entry_order_id=(
            None if row["entry_order_id"] is None else str(row["entry_order_id"])
        ),
        sl_order_id=None if row["sl_order_id"] is None else str(row["sl_order_id"]),
        realised_pnl=float(row["realised_pnl"]),
        open_pnl=float(row["open_pnl"]),
        closed_loss_contribution=float(row["closed_loss_contribution"]),
        trigger_time=None if row["trigger_time"] is None else str(row["trigger_time"]),
        entry_time=None if row["entry_time"] is None else str(row["entry_time"]),
        close_time=None if row["close_time"] is None else str(row["close_time"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        auto_trail_enabled=_row_bool(row, "auto_trail_enabled", False),
        auto_trail_ticks=_row_optional_int(row, "auto_trail_ticks"),
        auto_trail_extreme=_row_optional_float(row, "auto_trail_extreme"),
    )


def _row_keys(row: sqlite3.Row) -> set[str]:
    return set(row.keys())


def _row_bool(row: sqlite3.Row, key: str, default: bool) -> bool:
    if key not in _row_keys(row):
        return default
    val = row[key]
    if val is None:
        return default
    return bool(int(val))


def _row_optional_int(row: sqlite3.Row, key: str) -> Optional[int]:
    if key not in _row_keys(row):
        return None
    val = row[key]
    return None if val is None else int(val)


def _row_optional_float(row: sqlite3.Row, key: str) -> Optional[float]:
    if key not in _row_keys(row):
        return None
    val = row[key]
    return None if val is None else float(val)


class TradingEngineStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init()

    def _init(self) -> None:
        self._conn.executescript(
            CREATE_RUNS_SQL + CREATE_TRADES_SQL + CREATE_EVENTS_SQL + CREATE_COMMANDS_SQL
        )
        self._ensure_column("trades", "auto_trail_enabled", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("trades", "auto_trail_ticks", "INTEGER")
        self._ensure_column("trades", "auto_trail_extreme", "REAL")
        self._ensure_column(
            "engine_runs", "require_vwap_accept", "INTEGER NOT NULL DEFAULT 1"
        )
        for col, ddl in (
            ("admin_config_version_id", "TEXT"),
            ("risk_cap_used_inr", "REAL"),
            ("daily_loss_cap_inr", "REAL"),
            ("vwap_accept_gap_exclusive_max", "REAL"),
            ("vwap_limited_gap_inclusive_max", "REAL"),
            ("admin_config_read_at", "TEXT"),
        ):
            self._ensure_column("trades", col, ddl)
        self._conn.commit()

    def _ensure_column(self, table: str, name: str, ddl: str) -> None:
        rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {str(r[1]) for r in rows}
        if name not in existing:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    def close(self) -> None:
        self._conn.close()

    def start_run(
        self,
        *,
        session_date: str,
        live_orders_enabled: bool,
        pid: Optional[int],
        total_capital: float = DEFAULT_TOTAL_CAPITAL,
        leverage: float = DEMO_LEVERAGE_FACTOR,
        status: str = "running",
        require_vwap_accept: bool = True,
    ) -> str:
        run_id = uuid.uuid4().hex
        now = _utc_now()
        self._conn.execute(
            """
            INSERT INTO engine_runs (
                run_id, session_date, started_at, status, live_orders_enabled,
                pid, total_capital, leverage, consume_new_triggers, require_vwap_accept
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                run_id,
                session_date,
                now,
                status,
                1 if live_orders_enabled else 0,
                pid,
                float(total_capital),
                float(leverage),
                1 if require_vwap_accept else 0,
            ),
        )
        self._conn.commit()
        return run_id

    def latest_run(self) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM engine_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()

    def set_run_status(
        self,
        run_id: str,
        status: str,
        *,
        last_error: Optional[str] = None,
        stopped: bool = False,
    ) -> None:
        now = _utc_now()
        self._conn.execute(
            """
            UPDATE engine_runs
            SET status = ?, last_error = ?, stopped_at = CASE WHEN ? THEN ? ELSE stopped_at END
            WHERE run_id = ?
            """,
            (status, last_error, 1 if stopped else 0, now, run_id),
        )
        self._conn.commit()

    def set_consume_triggers(self, run_id: str, consume: bool) -> None:
        self._conn.execute(
            "UPDATE engine_runs SET consume_new_triggers = ? WHERE run_id = ?",
            (1 if consume else 0, run_id),
        )
        self._conn.commit()

    def set_total_capital(self, run_id: str, total_capital: float) -> None:
        self._conn.execute(
            "UPDATE engine_runs SET total_capital = ? WHERE run_id = ?",
            (float(total_capital), run_id),
        )
        self._conn.commit()

    def get_total_capital(self, run_id: str) -> float:
        row = self._conn.execute(
            "SELECT total_capital FROM engine_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return DEFAULT_TOTAL_CAPITAL
        return float(row["total_capital"])

    def find_trade(self, setup_id: str, rule_version: str) -> Optional[TradeRecord]:
        row = self._conn.execute(
            """
            SELECT * FROM trades
            WHERE setup_id = ? AND continuation_rule_version = ?
            """,
            (setup_id, rule_version),
        ).fetchone()
        return _row_to_trade(row) if row else None

    def get_trade(self, trade_id: str) -> Optional[TradeRecord]:
        row = self._conn.execute(
            "SELECT * FROM trades WHERE trade_id = ?", (trade_id,)
        ).fetchone()
        return _row_to_trade(row) if row else None

    def list_trades(self, session_date: Optional[str] = None) -> List[TradeRecord]:
        if session_date:
            rows = self._conn.execute(
                "SELECT * FROM trades WHERE session_date = ? ORDER BY created_at ASC",
                (session_date,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM trades ORDER BY created_at ASC"
            ).fetchall()
        return [_row_to_trade(r) for r in rows]

    def insert_candidate(
        self,
        *,
        setup_id: str,
        continuation_rule_version: str,
        session_date: str,
        symbol: str,
        instrument_token: int,
        direction: str,
        entry_estimate: float,
        tick_size: float,
        trigger_time: Optional[str],
        qty: int = 0,
        initial_stop: Optional[float] = None,
        notional: float = 0.0,
        margin_blocked: float = 0.0,
        status: str = "candidate",
    ) -> Optional[TradeRecord]:
        trade_id = make_trade_id()
        tag = make_broker_tag(trade_id)
        now = _utc_now()
        try:
            self._conn.execute(
                """
                INSERT INTO trades (
                    trade_id, setup_id, continuation_rule_version, broker_tag,
                    session_date, symbol, instrument_token, direction, qty,
                    entry_estimate, initial_stop, current_stop, tick_size,
                    notional, margin_blocked, status, trigger_time,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_id,
                    setup_id,
                    continuation_rule_version,
                    tag,
                    session_date,
                    symbol,
                    instrument_token,
                    direction,
                    qty,
                    entry_estimate,
                    initial_stop,
                    initial_stop,
                    tick_size,
                    notional,
                    margin_blocked,
                    status,
                    trigger_time,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError:
            self._conn.rollback()
            return None
        self._conn.commit()
        return self.get_trade(trade_id)

    def update_trade(self, trade_id: str, **fields: Any) -> TradeRecord:
        if not fields:
            trade = self.get_trade(trade_id)
            if trade is None:
                raise KeyError(trade_id)
            return trade
        fields = dict(fields)
        fields["updated_at"] = _utc_now()
        assignments = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [trade_id]
        self._conn.execute(
            f"UPDATE trades SET {assignments} WHERE trade_id = ?",
            values,
        )
        self._conn.commit()
        trade = self.get_trade(trade_id)
        if trade is None:
            raise KeyError(trade_id)
        return trade

    def append_event(
        self,
        trade_id: str,
        action: str,
        *,
        actor: str = "engine",
        old_stop: Optional[float] = None,
        new_stop: Optional[float] = None,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO trade_events (
                at, trade_id, action, old_stop, new_stop, actor, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _utc_now(),
                trade_id,
                action,
                old_stop,
                new_stop,
                actor,
                json.dumps(payload or {}),
            ),
        )
        self._conn.commit()

    def list_events(self, trade_id: Optional[str] = None) -> List[sqlite3.Row]:
        if trade_id:
            return self._conn.execute(
                "SELECT * FROM trade_events WHERE trade_id = ? ORDER BY event_id ASC",
                (trade_id,),
            ).fetchall()
        return self._conn.execute(
            "SELECT * FROM trade_events ORDER BY event_id ASC"
        ).fetchall()

    def enqueue_command(
        self,
        kind: str,
        *,
        trade_id: Optional[str] = None,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO engine_commands (kind, trade_id, payload_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (kind, trade_id, json.dumps(payload or {}), _utc_now()),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def pending_commands(self) -> List[EngineCommand]:
        rows = self._conn.execute(
            """
            SELECT * FROM engine_commands
            WHERE processed_at IS NULL
            ORDER BY command_id ASC
            """
        ).fetchall()
        return [
            EngineCommand(
                command_id=int(r["command_id"]),
                kind=str(r["kind"]),
                trade_id=None if r["trade_id"] is None else str(r["trade_id"]),
                payload_json=str(r["payload_json"]),
                created_at=str(r["created_at"]),
            )
            for r in rows
        ]

    def mark_command_processed(self, command_id: int) -> None:
        self._conn.execute(
            "UPDATE engine_commands SET processed_at = ? WHERE command_id = ?",
            (_utc_now(), command_id),
        )
        self._conn.commit()

    def ack_pending_commands(self, kind: str) -> int:
        cur = self._conn.execute(
            """
            UPDATE engine_commands
            SET processed_at = ?
            WHERE processed_at IS NULL AND kind = ?
            """,
            (_utc_now(), kind),
        )
        self._conn.commit()
        return int(cur.rowcount or 0)
