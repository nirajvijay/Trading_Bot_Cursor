"""How a human tells an already-running engine to do something.

Delivery is a shared ``engine_commands`` table in the same SQLite file as
``positions`` and ``position_events``: written by the API when someone clicks,
read once per tick by the engine. The same cheap once-a-second polling shape
already used for reading triggers — no sockets, no signals, no new
architecture.

**START is not a command here.** Starting has a chicken-and-egg problem: there
is no running loop to read a "please start" row. It is a process-launch concern
and belongs to the runnable process, not to command handling. Everything in
this module assumes the engine is already alive and ticking.

**The command set is deliberately minimal, and there is no PAUSE.**

* ``STOP`` / ``START`` toggle *new entries only*. The engine keeps running,
  watching, reconciling and listening either way. This supersedes the earlier
  design where STOP had protection-aware graceful-termination behaviour: STOP
  never terminates the process at all. Both are re-clickable freely before
  14:00; after 14:00 START is refused, while the loop keeps running normally
  with entries simply already off. PAUSE was considered and dropped as
  redundant with this.
* ``CLOSE_POSITION`` closes one position and nothing else changes — no
  implication that trading is done for the day.
* ``MOVE_STOP`` (payload ``{"ticks": +1 | -1}``, in price terms) and
  ``SET_TRAIL`` (payload ``{"enabled": bool}``) act on one position's stop;
  the rules live in engine_trailing.
* ``KILL_ALL`` is the third trigger for the shared close-everything sequence,
  after which the session is permanently over.

**Explicit non-trigger:** manually closing every position one at a time does
*not* cause auto-termination. Being flat is also just the normal state at
09:15. Auto-stop is tied to the three named triggers, never to "happens to be
flat right now".
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class CommandKind(str, Enum):
    STOP = "stop"                      # block new entries; keep running
    START = "start"                    # re-enable new entries
    CLOSE_POSITION = "close_position"  # one position, via the active exit path
    KILL_ALL = "kill_all"              # close everything, then terminate
    MOVE_STOP = "move_stop"            # nudge one position's stop by one tick
    SET_TRAIL = "set_trail"            # switch one position's auto-trail on/off


class CommandStatus(str, Enum):
    PENDING = "pending"
    APPLIED = "applied"
    REJECTED = "rejected"


CREATE_COMMANDS_SQL = """
CREATE TABLE IF NOT EXISTS engine_commands (
    command_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    at           TEXT    NOT NULL,
    kind         TEXT    NOT NULL,
    trade_id     TEXT,
    payload_json TEXT    NOT NULL DEFAULT '{}',
    status       TEXT    NOT NULL DEFAULT 'pending',
    applied_at   TEXT,
    result_json  TEXT,
    actor        TEXT
);
"""

CREATE_COMMANDS_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_commands_status "
    "ON engine_commands (status, command_id);"
)

# Commands that need a specific position to act on.
POSITION_SCOPED = (
    CommandKind.CLOSE_POSITION,
    CommandKind.MOVE_STOP,
    CommandKind.SET_TRAIL,
)


@dataclass(frozen=True)
class Command:
    command_id: int
    kind: CommandKind
    trade_id: Optional[str]
    payload: Dict[str, Any]
    at: str
    actor: Optional[str] = None


@dataclass
class PendingCommand:
    """A command handed to the engine, with its own way to report the outcome.

    The engine never touches the queue directly: it applies a command and says
    what happened, and this records it. That keeps the engine's command handler
    testable with a plain stub, and guarantees every command is resolved
    exactly once — a command left neither applied nor rejected would be
    re-applied on the next tick, which for CLOSE_POSITION or KILL_ALL would
    mean acting twice.
    """

    command: Command
    _queue: "CommandQueue"
    resolved: bool = False

    @property
    def kind(self) -> CommandKind:
        return self.command.kind

    @property
    def trade_id(self) -> Optional[str]:
        return self.command.trade_id

    @property
    def payload(self) -> Dict[str, Any]:
        return self.command.payload

    @property
    def command_id(self) -> int:
        return self.command.command_id

    def applied(self, result: Optional[Dict[str, Any]] = None) -> None:
        if self.resolved:
            return
        self.resolved = True
        self._queue.mark_applied(self.command.command_id, result)

    def rejected(self, reason: str) -> None:
        if self.resolved:
            return
        self.resolved = True
        self._queue.reject(self.command.command_id, reason)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class CommandQueue:
    """Read/write access to engine_commands.

    Shares the store's database file. Opened separately so the API can enqueue
    without holding the engine's connection.
    """

    def __init__(self, db_path, *, read_only: bool = False) -> None:
        self.db_path = str(db_path)
        self.read_only = read_only
        if read_only:
            self._conn = sqlite3.connect(
                f"file:{self.db_path}?mode=ro", uri=True, isolation_level=""
            )
        else:
            self._conn = sqlite3.connect(self.db_path, isolation_level="")
        self._conn.row_factory = sqlite3.Row
        if not read_only:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            with self._conn:
                self._conn.execute(CREATE_COMMANDS_SQL)
                self._conn.execute(CREATE_COMMANDS_INDEX_SQL)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "CommandQueue":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Write side (the API)
    # ------------------------------------------------------------------

    def enqueue(
        self,
        kind: CommandKind | str,
        *,
        trade_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        actor: Optional[str] = None,
    ) -> int:
        # NB: on Python 3.9 str(CommandKind.STOP) is "CommandKind.STOP", not
        # "stop" -- str-mixin enums only stringify to their value from 3.11.
        # So never round-trip an enum through str() here.
        kind = kind if isinstance(kind, CommandKind) else CommandKind(str(kind))
        if kind in POSITION_SCOPED and not trade_id:
            raise ValueError(f"{kind.value} requires a trade_id")
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO engine_commands (at, kind, trade_id, payload_json, status, actor) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    _utc_now_iso(),
                    kind.value,
                    trade_id,
                    json.dumps(payload or {}, separators=(",", ":"), sort_keys=True, default=str),
                    CommandStatus.PENDING.value,
                    actor,
                ),
            )
        return int(cursor.lastrowid or 0)

    # ------------------------------------------------------------------
    # Read side (the engine)
    # ------------------------------------------------------------------

    def pending(self) -> List[Command]:
        """Unapplied commands, oldest first."""
        try:
            rows = self._conn.execute(
                "SELECT * FROM engine_commands WHERE status = ? ORDER BY command_id ASC",
                (CommandStatus.PENDING.value,),
            ).fetchall()
        except sqlite3.OperationalError:
            # A read-only handle on a store the engine has created but never
            # enqueued into: no table yet is "no commands", not an error.
            return []
        out: List[Command] = []
        for row in rows:
            try:
                kind = CommandKind(str(row["kind"]))
            except ValueError:
                # An unknown kind must not wedge the queue behind it.
                self.reject(int(row["command_id"]), "unknown_command_kind")
                continue
            out.append(
                Command(
                    command_id=int(row["command_id"]),
                    kind=kind,
                    trade_id=(None if row["trade_id"] is None else str(row["trade_id"])),
                    payload=json.loads(str(row["payload_json"] or "{}")),
                    at=str(row["at"]),
                    actor=(None if row["actor"] is None else str(row["actor"])),
                )
            )
        return out

    def mark_applied(
        self, command_id: int, result: Optional[Dict[str, Any]] = None
    ) -> None:
        self._finish(command_id, CommandStatus.APPLIED, result or {})

    def reject(self, command_id: int, reason: str) -> None:
        self._finish(command_id, CommandStatus.REJECTED, {"reason": reason})

    def _finish(
        self, command_id: int, status: CommandStatus, result: Dict[str, Any]
    ) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE engine_commands SET status = ?, applied_at = ?, result_json = ? "
                "WHERE command_id = ?",
                (
                    status.value,
                    _utc_now_iso(),
                    json.dumps(result, separators=(",", ":"), sort_keys=True, default=str),
                    int(command_id),
                ),
            )

    def record(self, command_id: int) -> Optional[Dict[str, Any]]:
        try:
            row = self._conn.execute(
                "SELECT * FROM engine_commands WHERE command_id = ?", (int(command_id),)
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        if row is None:
            return None
        data = dict(row)
        data["payload"] = json.loads(str(data.pop("payload_json") or "{}"))
        raw_result = data.pop("result_json", None)
        data["result"] = json.loads(str(raw_result)) if raw_result else None
        return data

    def take_pending(self) -> List[PendingCommand]:
        """What the engine calls once per tick: pending commands, each able to
        report its own outcome."""
        return [PendingCommand(command=c, _queue=self) for c in self.pending()]

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            rows = self._conn.execute(
                "SELECT * FROM engine_commands ORDER BY command_id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(row) for row in rows]
