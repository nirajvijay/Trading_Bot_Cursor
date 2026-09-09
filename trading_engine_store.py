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

CREATE_ORDER_LINKS_SQL = """
CREATE TABLE IF NOT EXISTS trade_order_links (
    order_id TEXT PRIMARY KEY,
    trade_id TEXT NOT NULL,
    role TEXT NOT NULL,
    attribution_kind TEXT NOT NULL,
    attributed_at TEXT NOT NULL,
    session_date TEXT
);
CREATE INDEX IF NOT EXISTS idx_trade_order_links_trade
    ON trade_order_links(trade_id);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_trade_id() -> str:
    return uuid.uuid4().hex


def make_broker_tag(trade_id: str) -> str:
    """Kite tags are alphanumeric, max 20 chars."""
    return ("te" + trade_id)[:20]


def _row_to_trade(row: sqlite3.Row) -> TradeRecord:
    qty = int(row["qty"])
    keys = set(row.keys())
    intended = (
        int(row["intended_qty"])
        if "intended_qty" in keys and row["intended_qty"] is not None
        else qty
    )
    filled = int(row["filled_qty"]) if "filled_qty" in keys and row["filled_qty"] is not None else 0
    exited = int(row["exited_qty"]) if "exited_qty" in keys and row["exited_qty"] is not None else 0
    entry_value = (
        float(row["entry_value"])
        if "entry_value" in keys and row["entry_value"] is not None
        else 0.0
    )
    exit_value = (
        float(row["exit_value"])
        if "exit_value" in keys and row["exit_value"] is not None
        else 0.0
    )
    entry_value_est = (
        float(row["entry_value_est"])
        if "entry_value_est" in keys and row["entry_value_est"] is not None
        else 0.0
    )
    exit_value_est = (
        float(row["exit_value_est"])
        if "exit_value_est" in keys and row["exit_value_est"] is not None
        else 0.0
    )
    pnl_provisional = False
    if "pnl_provisional" in keys and row["pnl_provisional"] is not None:
        pnl_provisional = bool(int(row["pnl_provisional"]))
    remaining = (
        int(row["remaining_entry_qty"])
        if "remaining_entry_qty" in keys and row["remaining_entry_qty"] is not None
        else 0
    )
    rem_pos = (
        int(row["remaining_position_qty"])
        if "remaining_position_qty" in keys and row["remaining_position_qty"] is not None
        else 0
    )
    protected = (
        int(row["protected_qty"])
        if "protected_qty" in keys and row["protected_qty"] is not None
        else 0
    )
    qty_model_version = (
        int(row["qty_model_version"])
        if "qty_model_version" in keys and row["qty_model_version"] is not None
        else 0
    )
    status = str(row["status"])
    run_id = None
    if "run_id" in keys and row["run_id"] is not None:
        run_id = str(row["run_id"])
    entry_live = None
    if "entry_live_orders_enabled" in keys and row["entry_live_orders_enabled"] is not None:
        entry_live = bool(int(row["entry_live_orders_enabled"]))
    return TradeRecord(
        trade_id=str(row["trade_id"]),
        setup_id=str(row["setup_id"]),
        continuation_rule_version=str(row["continuation_rule_version"]),
        broker_tag=str(row["broker_tag"]),
        session_date=str(row["session_date"]),
        symbol=str(row["symbol"]),
        instrument_token=int(row["instrument_token"]),
        direction=str(row["direction"]),
        qty=qty,
        entry_estimate=float(row["entry_estimate"]),
        entry_fill=None if row["entry_fill"] is None else float(row["entry_fill"]),
        initial_stop=None if row["initial_stop"] is None else float(row["initial_stop"]),
        current_stop=None if row["current_stop"] is None else float(row["current_stop"]),
        exit_fill=None if row["exit_fill"] is None else float(row["exit_fill"]),
        tick_size=float(row["tick_size"]),
        notional=float(row["notional"]),
        margin_blocked=float(row["margin_blocked"]),
        status=status,
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
        intended_qty=intended,
        filled_qty=filled,
        exited_qty=exited,
        entry_value=entry_value,
        exit_value=exit_value,
        entry_value_est=entry_value_est,
        exit_value_est=exit_value_est,
        pnl_provisional=pnl_provisional,
        remaining_entry_qty=remaining,
        remaining_position_qty=rem_pos,
        protected_qty=protected,
        qty_model_version=qty_model_version,
        run_id=run_id,
        entry_live_orders_enabled=entry_live,
        charge_bps=_row_optional_float(row, "charge_bps"),
        entry_limit_price=_row_optional_float(row, "entry_limit_price"),
        risk_cap_used_inr=_row_optional_float(row, "risk_cap_used_inr"),
        admin_config_version_id=row["admin_config_version_id"] if "admin_config_version_id" in keys else None,
        risk_limits_json=row["risk_limits_json"] if "risk_limits_json" in keys else None,
        slippage_bps=_row_optional_float(row, "slippage_bps"),
        exit_confirmed_qty=_row_optional_int(row, "exit_confirmed_qty"),
        exit_est_qty=_row_optional_int(row, "exit_est_qty"),
        protection_deadline_at=(
            None
            if "protection_deadline_at" not in keys or row["protection_deadline_at"] is None
            else str(row["protection_deadline_at"])
        ),
        r_value=_row_optional_float(row, "r_value"),
        last_trail_modify_at=(
            None
            if "last_trail_modify_at" not in keys or row["last_trail_modify_at"] is None
            else str(row["last_trail_modify_at"])
        ),
        entry_submitted_at=(
            None
            if "entry_submitted_at" not in keys or row["entry_submitted_at"] is None
            else str(row["entry_submitted_at"])
        ),
        auto_trail_owner_disabled=_row_bool(row, "auto_trail_owner_disabled", False),
        active_exit_order_id=(
            None
            if "active_exit_order_id" not in keys or row["active_exit_order_id"] is None
            else str(row["active_exit_order_id"])
        ),
        active_exit_kind=(
            None
            if "active_exit_kind" not in keys or row["active_exit_kind"] is None
            else str(row["active_exit_kind"])
        ),
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
            CREATE_RUNS_SQL
            + CREATE_TRADES_SQL
            + CREATE_EVENTS_SQL
            + CREATE_COMMANDS_SQL
            + CREATE_ORDER_LINKS_SQL
        )
        self._ensure_column("trades", "auto_trail_enabled", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("trades", "auto_trail_ticks", "INTEGER")
        self._ensure_column("trades", "auto_trail_extreme", "REAL")
        self._ensure_column(
            "engine_runs", "require_vwap_accept", "INTEGER NOT NULL DEFAULT 1"
        )
        for col, ddl in (
            ("entry_limit_price", "REAL"),
            ("risk_limits_json", "TEXT"),
            ("admin_config_version_id", "TEXT"),
            ("risk_cap_used_inr", "REAL"),
            ("daily_loss_cap_inr", "REAL"),
            ("vwap_accept_gap_exclusive_max", "REAL"),
            ("vwap_limited_gap_inclusive_max", "REAL"),
            ("admin_config_read_at", "TEXT"),
            ("intended_qty", "INTEGER NOT NULL DEFAULT 0"),
            ("filled_qty", "INTEGER NOT NULL DEFAULT 0"),
            ("exited_qty", "INTEGER NOT NULL DEFAULT 0"),
            ("entry_value", "REAL NOT NULL DEFAULT 0"),
            ("exit_value", "REAL NOT NULL DEFAULT 0"),
            ("entry_value_est", "REAL NOT NULL DEFAULT 0"),
            ("exit_value_est", "REAL NOT NULL DEFAULT 0"),
            ("pnl_provisional", "INTEGER NOT NULL DEFAULT 0"),
            ("remaining_entry_qty", "INTEGER NOT NULL DEFAULT 0"),
            ("remaining_position_qty", "INTEGER NOT NULL DEFAULT 0"),
            ("protected_qty", "INTEGER NOT NULL DEFAULT 0"),
            ("qty_model_version", "INTEGER NOT NULL DEFAULT 0"),
            ("run_id", "TEXT"),
            ("entry_live_orders_enabled", "INTEGER"),
            ("charge_bps", "REAL"),
            ("slippage_bps", "REAL"),
            ("exit_confirmed_qty", "INTEGER"),
            ("exit_est_qty", "INTEGER"),
            ("protection_deadline_at", "TEXT"),
            ("r_value", "REAL"),
            ("last_trail_modify_at", "TEXT"),
            ("entry_submitted_at", "TEXT"),
            ("auto_trail_owner_disabled", "INTEGER NOT NULL DEFAULT 0"),
            ("active_exit_order_id", "TEXT"),
            ("active_exit_kind", "TEXT"),
        ):
            self._ensure_column("trades", col, ddl)
        for col, ddl in (
            ("filled_qty", "INTEGER NOT NULL DEFAULT 0"),
            ("confirmed_filled_qty", "INTEGER NOT NULL DEFAULT 0"),
            ("average_price", "REAL"),
            ("execution_value", "REAL NOT NULL DEFAULT 0"),
            ("execution_value_est", "REAL NOT NULL DEFAULT 0"),
            ("reconciled_at", "TEXT"),
        ):
            self._ensure_column("trade_order_links", col, ddl)
        self._migrate_qty_model_v1()
        # Closed rows: align exited_qty with cumulative entry fills (flat). Do not invent for open.
        self._conn.execute(
            """
            UPDATE trades
            SET exited_qty = filled_qty
            WHERE status = 'closed'
              AND COALESCE(exited_qty, 0) = 0
              AND COALESCE(filled_qty, 0) > 0
            """
        )
        # Backfill confirmed execution values only from stored fill prices (never invent).
        self._conn.execute(
            """
            UPDATE trades
            SET entry_value = COALESCE(entry_fill, 0) * COALESCE(filled_qty, 0)
            WHERE COALESCE(entry_value, 0) = 0
              AND COALESCE(filled_qty, 0) > 0
              AND entry_fill IS NOT NULL
            """
        )
        self._conn.execute(
            """
            UPDATE trades
            SET exit_value = COALESCE(exit_fill, 0) * COALESCE(exited_qty, 0)
            WHERE COALESCE(exit_value, 0) = 0
              AND COALESCE(exited_qty, 0) > 0
              AND exit_fill IS NOT NULL
              AND COALESCE(pnl_provisional, 0) = 0
            """
        )
        self._conn.commit()

    def _migrate_qty_model_v1(self) -> None:
        """Explicit qty-model migration. Never invent broker-confirmed protection.

        qty_model_version 0 → 1:
        - Terminal rows: position flat; filled_qty set for closed only as historical size.
        - Open exposure rows: intended/filled/remaining_position from local qty as
          *unconfirmed estimates*; protected_qty stays 0 until broker confirmation.
        """
        rows = self._conn.execute(
            "SELECT trade_id, status, qty FROM trades WHERE qty_model_version = 0"
        ).fetchall()
        for row in rows:
            trade_id = str(row["trade_id"])
            status = str(row["status"])
            qty = int(row["qty"] or 0)
            if status in {"closed"}:
                fields = (
                    qty,  # intended
                    qty,  # filled cumulative historical
                    0,  # remaining_entry
                    0,  # remaining_position
                    0,  # protected — not inventing
                    1,  # version
                    trade_id,
                )
            elif status in {"skipped", "rejected", "candidate"}:
                fields = (qty, 0, 0, 0, 0, 1, trade_id)
            elif status in {
                "entry_submitting",
                "submission_unknown",
            }:
                fields = (qty, 0, qty, 0, 0, 1, trade_id)
            else:
                # Open / in-flight: local size estimate only; protection unconfirmed.
                fields = (qty, qty, 0, qty, 0, 1, trade_id)
            self._conn.execute(
                """
                UPDATE trades SET
                    intended_qty = ?,
                    filled_qty = ?,
                    remaining_entry_qty = ?,
                    remaining_position_qty = ?,
                    protected_qty = ?,
                    qty_model_version = ?
                WHERE trade_id = ?
                """,
                fields,
            )

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

    def get_order_link(self, order_id: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM trade_order_links WHERE order_id = ?",
            (str(order_id),),
        ).fetchone()

    def list_order_links(self, trade_id: str) -> List[sqlite3.Row]:
        return self._conn.execute(
            """
            SELECT * FROM trade_order_links
            WHERE trade_id = ?
            ORDER BY attributed_at ASC, order_id ASC
            """,
            (str(trade_id),),
        ).fetchall()

    def attribute_order(
        self,
        trade_id: str,
        order_id: str,
        *,
        role: str,
        attribution_kind: str,
        session_date: Optional[str] = None,
    ) -> str:
        """Persist order→trade ownership.

        Returns:
          linked   — newly inserted
          exists   — already linked to this trade
          conflict — already linked to a different trade (unchanged)
        """
        existing = self.get_order_link(order_id)
        if existing is not None:
            if str(existing["trade_id"]) == str(trade_id):
                return "exists"
            return "conflict"
        self._conn.execute(
            """
            INSERT INTO trade_order_links (
                order_id, trade_id, role, attribution_kind, attributed_at, session_date
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(order_id),
                str(trade_id),
                str(role),
                str(attribution_kind),
                _utc_now(),
                session_date,
            ),
        )
        self._conn.commit()
        return "linked"

    def record_order_execution(
        self,
        order_id: str,
        *,
        filled_qty: int,
        average_price: Optional[float],
        estimated_px: float = 0.0,
    ) -> None:
        """Merge a visible broker observation into the durable per-order snapshot.

        - Priced observations are authoritative for the observed fill qty.
        - Unpriced observations must not erase previously confirmed avg/value; extra
          unpriced shares are tracked separately via confirmed_filled_qty.
        - Callers must not invoke this when the order is temporarily missing.
        """
        existing = self.get_order_link(order_id)
        if existing is None:
            return
        keys = set(existing.keys())

        def _int(col: str, default: int = 0) -> int:
            if col not in keys or existing[col] is None:
                return default
            return int(existing[col])

        def _float(col: str, default: float = 0.0) -> float:
            if col not in keys or existing[col] is None:
                return default
            return float(existing[col])

        prev_filled = _int("filled_qty")
        prev_confirmed = _int("confirmed_filled_qty")
        prev_avg = (
            None
            if "average_price" not in keys or existing["average_price"] is None
            else float(existing["average_price"])
        )
        # Legacy rows: priced snapshot without confirmed_filled_qty → all filled confirmed.
        if prev_avg is not None and prev_confirmed <= 0 and prev_filled > 0:
            prev_confirmed = prev_filled
        prev_value = _float("execution_value")
        observed = max(0, int(filled_qty))

        if average_price is not None:
            avg: Optional[float] = float(average_price)
            filled_out = observed
            confirmed_out = observed
            value = float(filled_out) * float(avg) if filled_out > 0 else 0.0
            value_est = 0.0
        elif prev_avg is not None and prev_confirmed > 0:
            # Keep confirmed slice; allow total filled to grow as unpriced remainder.
            avg = float(prev_avg)
            confirmed_out = prev_confirmed
            filled_out = max(observed, prev_filled, prev_confirmed)
            value = (
                float(prev_value)
                if prev_value > 0
                else float(avg) * float(confirmed_out)
            )
            extra = max(0, filled_out - confirmed_out)
            value_est = float(extra) * float(estimated_px or 0.0)
        else:
            avg = None
            filled_out = observed
            confirmed_out = 0
            value = 0.0
            value_est = float(filled_out) * float(estimated_px or 0.0)

        self._conn.execute(
            """
            UPDATE trade_order_links
            SET filled_qty = ?,
                confirmed_filled_qty = ?,
                average_price = ?,
                execution_value = ?,
                execution_value_est = ?,
                reconciled_at = ?
            WHERE order_id = ?
            """,
            (
                filled_out,
                confirmed_out,
                avg,
                value,
                value_est,
                _utc_now(),
                str(order_id),
            ),
        )
        self._conn.commit()

    def exit_execution_totals(
        self,
        trade_id: str,
        *,
        estimated_px: float = 0.0,
    ) -> tuple[float, float, int, int]:
        """Durable (confirmed_value, estimated_value, confirmed_qty, est_qty).

        Qty and ₹ come from the same per-order reconciliation snapshot. Missing
        broker visibility does not drop previously recorded rows.
        """
        rows = self._conn.execute(
            """
            SELECT filled_qty, confirmed_filled_qty, average_price,
                   execution_value, execution_value_est
            FROM trade_order_links
            WHERE trade_id = ? AND role IN ('exit', 'stop')
            """,
            (str(trade_id),),
        ).fetchall()
        confirmed_value = 0.0
        estimated_value = 0.0
        confirmed_qty = 0
        est_qty = 0
        for row in rows:
            keys = set(row.keys())
            filled = int(row["filled_qty"] or 0) if "filled_qty" in keys else 0
            if filled <= 0:
                continue
            avg = row["average_price"] if "average_price" in keys else None
            confirmed_filled = (
                int(row["confirmed_filled_qty"] or 0)
                if "confirmed_filled_qty" in keys
                else 0
            )
            if avg is not None and confirmed_filled <= 0:
                confirmed_filled = filled
            confirmed_filled = max(0, min(filled, confirmed_filled))
            unpriced = max(0, filled - confirmed_filled)
            if confirmed_filled > 0:
                confirmed_qty += confirmed_filled
                stored = (
                    float(row["execution_value"] or 0)
                    if "execution_value" in keys
                    else 0.0
                )
                if stored > 0:
                    confirmed_value += stored
                elif avg is not None:
                    confirmed_value += float(confirmed_filled) * float(avg)
            if unpriced > 0:
                est_qty += unpriced
                stored_est = (
                    float(row["execution_value_est"] or 0)
                    if "execution_value_est" in keys
                    else 0.0
                )
                estimated_value += (
                    stored_est
                    if stored_est > 0
                    else float(unpriced) * float(estimated_px or 0.0)
                )
        return (
            float(confirmed_value),
            float(estimated_value),
            int(confirmed_qty),
            int(est_qty),
        )

    def exit_execution_qty_totals(self, trade_id: str) -> tuple[int, int]:
        """Exact (confirmed_qty, est_qty) from durable exit/stop order link records."""
        _cv, _ev, confirmed, estimated = self.exit_execution_totals(trade_id)
        return int(confirmed), int(estimated)
