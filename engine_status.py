"""Heartbeat, the stopping-on-purpose note, and the live P&L mark file.

**The heartbeat is how anything outside knows the engine is alive.** It is
written every tick to a shared file; the API and the Execution Desk only ever
read it, and never talk to the running process directly.

**Crash detection is by absence, because a crashed process cannot report its
own crash.** There is no "it" left to send anything. So the signal is silence:
a heartbeat that stops updating for well beyond the one-second rhythm.

**But the engine is also designed to stop itself on purpose** every day —
breach, 15:15 EOD, or Kill-It-All-Now — and that is success, not a problem.
Without a way to tell the two apart, either every normal end-of-day stop would
false-alarm (training a human to ignore real alarms) or every stop would be
assumed fine and a real crash would be missed. So the very last thing the
engine does before any deliberate exit is write a "stopping on purpose, here
is why" note. Silence *with* the note is healthy. Silence *without* it is a
genuine crash.

**Live P&L is deliberately not a heartbeat field.** It goes to its own file, so
the heartbeat's job stays exactly what it already is. It is never persisted to
the positions table either: it is entirely derived from what Kite reports at an
instant, so a lost or stale value is regenerated correctly by the very next
tick's fetch.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Silence longer than this means the process is gone, not merely slow. Well
# beyond the 1-second tick so ordinary jitter or one slow tick never trips it.
HEARTBEAT_STALE_SECONDS = 8.0


@dataclass
class EngineStatus:
    """One tick's worth of "what is this engine doing right now"."""

    run_id: str
    session_date: str
    state: str = "running"
    pid: int = 0
    updated_at: str = ""
    tick_count: int = 0
    open_positions: int = 0
    unprotected: int = 0
    entries_allowed: bool = True
    entries_stopped: bool = False
    entries_paused: bool = False
    pause_reason: Optional[str] = None
    realised_loss_today: float = 0.0
    daily_loss_cap: float = 0.0
    remaining_daily: float = 0.0
    is_live: bool = False
    last_error: Optional[str] = None
    step_failures: Dict[str, int] = field(default_factory=dict)
    escalations: Dict[str, str] = field(default_factory=dict)
    # Present only on a deliberate stop. Its presence is what distinguishes a
    # healthy shutdown from a crash.
    stopped_on_purpose: bool = False
    stop_reason: Optional[str] = None
    stopped_at: Optional[str] = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    """Write via a temp file and rename, so a reader never sees a half file.

    An os.replace is atomic on the same filesystem, which is what makes a
    once-per-tick write safe to read at any moment without locking.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"), sort_keys=True, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, str(path))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class HeartbeatWriter:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def write(self, status: EngineStatus) -> None:
        status.updated_at = _utc_now_iso()
        if not status.pid:
            status.pid = os.getpid()
        write_json_atomic(self.path, asdict(status))

    def write_stop_note(self, status: EngineStatus, reason: str) -> None:
        """The last thing written before a deliberate exit."""
        status.state = "stopped"
        status.stopped_on_purpose = True
        status.stop_reason = reason
        status.stopped_at = _utc_now_iso()
        status.entries_allowed = False
        self.write(status)


def heartbeat_age_seconds(
    heartbeat: Optional[Dict[str, Any]], *, now: Optional[datetime] = None
) -> Optional[float]:
    if not heartbeat:
        return None
    raw = heartbeat.get("updated_at")
    if not raw:
        return None
    try:
        updated = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return (reference - updated).total_seconds()


def process_alive(pid: Optional[int]) -> bool:
    """Is that process actually running right now?

    A signal-free liveness probe: costs microseconds, touches no disk. Checking
    real liveness rather than trusting a recorded id is what prevents a stale
    record from blocking a legitimate restart forever.
    """
    if not pid or int(pid) <= 0:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists, owned by someone else. Still alive.
        return True
    except OSError:
        return False
    return True


@dataclass(frozen=True)
class LivenessVerdict:
    """What the outside world concludes from the heartbeat."""

    state: str  # "running" | "stopped" | "crashed" | "absent"
    reason: Optional[str] = None
    age_seconds: Optional[float] = None
    pid: Optional[int] = None

    @property
    def crashed(self) -> bool:
        return self.state == "crashed"

    @property
    def running(self) -> bool:
        return self.state == "running"


def assess(
    heartbeat: Optional[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    stale_after: float = HEARTBEAT_STALE_SECONDS,
) -> LivenessVerdict:
    """Alive, stopped on purpose, or crashed — the whole point of the note.

    Order matters: an explicit stop note is believed before anything is
    inferred from silence, because a deliberate stop *is* silence and must not
    be reported as a crash.
    """
    if not heartbeat:
        return LivenessVerdict("absent", reason="no_heartbeat")

    pid = heartbeat.get("pid")
    pid = int(pid) if isinstance(pid, (int, float)) and pid else None
    age = heartbeat_age_seconds(heartbeat, now=now)

    if heartbeat.get("stopped_on_purpose"):
        return LivenessVerdict(
            "stopped",
            reason=str(heartbeat.get("stop_reason") or "stopped_on_purpose"),
            age_seconds=age,
            pid=pid,
        )

    if age is None:
        return LivenessVerdict("absent", reason="no_timestamp", pid=pid)

    if age <= stale_after:
        return LivenessVerdict("running", age_seconds=age, pid=pid)

    # Silence, with no note explaining it. If the process is somehow still
    # there it is wedged rather than ticking, which is equally a problem.
    return LivenessVerdict(
        "crashed",
        reason="heartbeat_silent_without_stop_note",
        age_seconds=age,
        pid=pid,
    )


class LiveMarkWriter:
    """Per-tick unrealised P&L, in its own file.

    Kept out of the heartbeat on purpose, and never written to the positions
    table: it has no lasting value, and the next tick regenerates it.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def write(self, pnl_by_trade: Dict[str, Optional[float]]) -> None:
        known = {k: v for k, v in pnl_by_trade.items() if v is not None}
        write_json_atomic(
            self.path,
            {
                "as_of": _utc_now_iso(),
                "pnl": pnl_by_trade,
                # Free alongside the individual numbers, from the same fetch.
                "total_pnl": sum(known.values()) if known else 0.0,
                "complete": len(known) == len(pnl_by_trade),
            },
        )


def read_live_marks(path: Path) -> Dict[str, Any]:
    data = read_json(path) or {}
    return {
        "as_of": data.get("as_of"),
        "pnl": data.get("pnl") or {},
        "total_pnl": data.get("total_pnl"),
        "complete": bool(data.get("complete")),
    }
