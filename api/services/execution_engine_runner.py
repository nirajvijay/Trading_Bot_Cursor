"""Launching, watching, and refusing to double-launch the execution engine.

The always-on API server is what starts the engine: it is already running
whenever the Execution Desk is usable at all, and the engine cannot run inline
as part of handling one click because it needs to keep ticking for hours.

Status is read, never asked for. The engine writes its own heartbeat every
tick; the API only ever reads that file and never talks to the running process.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import engine_clock
from api import config
from engine_config import InvalidSessionConfig, SessionRiskConfig, validate
from engine_status import (
    LivenessVerdict,
    assess,
    process_alive,
    read_json,
    read_live_marks,
)


@dataclass(frozen=True)
class Precondition:
    key: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class Preflight:
    """Everything checked before a start is allowed, each with its own reason."""

    can_start: bool
    checks: List[Precondition]
    engine: LivenessVerdict

    @property
    def refusals(self) -> List[str]:
        return [c.key for c in self.checks if not c.ok]


def heartbeat() -> Optional[dict]:
    return read_json(config.execution_engine_status_file())


def liveness(*, now: Optional[datetime] = None) -> LivenessVerdict:
    return assess(heartbeat(), now=now)


def live_marks() -> dict:
    return read_live_marks(config.execution_engine_live_mark_file())


def engine_is_running(*, now: Optional[datetime] = None) -> bool:
    """Alive means a fresh heartbeat AND a process that actually exists.

    Both halves matter: a fresh heartbeat from a process that has since been
    killed would block a legitimate restart, and a live process with a silent
    heartbeat is wedged rather than working.
    """
    verdict = liveness(now=now)
    if not verdict.running:
        return False
    if verdict.pid is None:
        return True
    return process_alive(verdict.pid)


def observation_runner_active() -> Tuple[bool, str]:
    """The engine has no market connection of its own.

    Everything it knows about triggers, VWAP and feed health comes from what
    the observation runner writes to the shared database, so "runner down" and
    "feed stale" are the same underlying fact. Refusing start with its own
    clear message is a friendlier front door than starting into a
    permanently-stale-feed state that silently pauses forever — but it does not
    replace the feed-staleness check, which remains the safety net for the
    runner dying *after* a successful start.
    """
    from api.services import observation_runner as obs

    try:
        running = bool(obs.is_runner_running())
    except Exception as exc:  # noqa: BLE001
        return False, f"observation_runner_state_unknown: {exc}"
    return running, "active" if running else "observation_runner_not_running"


def preflight(
    config_values: Optional[SessionRiskConfig] = None,
    *,
    now: Optional[datetime] = None,
) -> Preflight:
    """Evaluate every start precondition. Each refuses outright with a reason;
    none of them queue and wait for a window to open."""
    moment = now or engine_clock.to_ist()
    checks: List[Precondition] = []

    market_open = engine_clock.market_session_open(moment)
    checks.append(
        Precondition(
            "market_hours",
            market_open,
            "09:15-15:30 IST" if market_open else "outside NSE cash session",
        )
    )

    before_cutoff = engine_clock.ist_time(moment) < engine_clock.ENTRY_CUTOFF_IST
    checks.append(
        Precondition(
            "before_entry_cutoff",
            before_cutoff,
            "before 14:00 IST"
            if before_cutoff
            else "after 14:00 IST: a session started now could never take a trade",
        )
    )

    runner_ok, runner_detail = observation_runner_active()
    checks.append(Precondition("observation_runner", runner_ok, runner_detail))

    verdict = liveness(now=now)
    already = engine_is_running(now=now)
    checks.append(
        Precondition(
            "no_engine_running",
            not already,
            "no live engine" if not already else f"engine already running (pid {verdict.pid})",
        )
    )

    if config_values is not None:
        try:
            validate(config_values)
            checks.append(Precondition("risk_config", True, "caps are sane"))
        except InvalidSessionConfig as exc:
            checks.append(Precondition("risk_config", False, str(exc)))

    return Preflight(
        can_start=all(c.ok for c in checks), checks=checks, engine=verdict
    )


def build_command(
    *,
    session_config: SessionRiskConfig,
    session_date: str,
    run_id: str,
    live_orders: bool,
) -> List[str]:
    return [
        sys.executable,
        "run_execution_engine.py",
        "--engine-db",
        str(config.execution_engine_db_path()),
        "--live-db",
        str(config.live_db_path()),
        "--status-file",
        str(config.execution_engine_status_file()),
        "--live-mark-file",
        str(config.execution_engine_live_mark_file()),
        "--runner-status-file",
        str(config.RUNNER_STATUS_FILE),
        "--session-date",
        session_date,
        "--run-id",
        run_id,
        "--per-trade-cap",
        str(session_config.per_trade_cap_rupees),
        "--limited-per-trade-cap",
        str(session_config.per_trade_cap_vwap_limited_rupees),
        "--daily-loss-cap",
        str(session_config.daily_loss_cap_rupees),
        "--total-capital",
        str(session_config.total_capital_rupees),
        "--leverage",
        str(session_config.leverage_factor),
    ] + (["--live-orders"] if live_orders else [])


def start_engine(
    *,
    session_config: SessionRiskConfig,
    session_date: Optional[str] = None,
    live_orders: bool = False,
    run_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Tuple[bool, str, Optional[int]]:
    from api.services.checklist_activity import ChecklistBusy, workflow_lock
    try:
        with workflow_lock(shared=True):
            return _start_engine(session_config=session_config, session_date=session_date,
                                 live_orders=live_orders, run_id=run_id, now=now)
    except ChecklistBusy:
        return False, "Checklist preparation is running", None


def _start_engine(
    *, session_config: SessionRiskConfig, session_date: Optional[str] = None,
    live_orders: bool = False, run_id: Optional[str] = None, now: Optional[datetime] = None,
) -> Tuple[bool, str, Optional[int]]:
    """Spawn the engine, or refuse with the specific reason."""
    from uuid import uuid4

    day = session_date or engine_clock.to_ist(now).strftime("%Y-%m-%d")
    checks = preflight(session_config, now=now)
    if not checks.can_start:
        # Report every failing precondition, not just the first. Fixing them
        # one refusal at a time is a worse experience than being told
        # everything that is wrong up front.
        failed = [c for c in checks.checks if not c.ok]
        return False, "; ".join(f"{c.key}: {c.detail}" for c in failed), None

    if live_orders and os.environ.get("NIFTY_RADAR_LIVE_WRITES_AUTHORIZED") != "1":
        return False, "live_execution_not_authorized", None

    command = build_command(
        session_config=session_config,
        session_date=day,
        run_id=run_id or uuid4().hex[:12],
        live_orders=live_orders,
    )
    status_file = config.execution_engine_status_file()
    status_file.parent.mkdir(parents=True, exist_ok=True)
    config.execution_engine_db_path().parent.mkdir(parents=True, exist_ok=True)

    try:
        proc = subprocess.Popen(
            command,
            cwd=str(config.ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        return False, f"failed_to_start: {exc}", None

    # Record the pid immediately, before the loop has written its first
    # heartbeat, so a start clicked twice in quick succession is still refused.
    _seed_pid(status_file, pid=proc.pid, session_date=day, run_id=run_id or "")
    return True, f"execution engine started (pid {proc.pid})", proc.pid


def _seed_pid(status_file: Path, *, pid: int, session_date: str, run_id: str) -> None:
    from engine_status import write_json_atomic

    existing = read_json(status_file) or {}
    existing.update(
        {
            "pid": pid,
            "session_date": session_date,
            "run_id": run_id,
            "state": "starting",
            "updated_at": datetime.now(engine_clock.IST).astimezone().isoformat(),
            "stopped_on_purpose": False,
            "stop_reason": None,
        }
    )
    try:
        write_json_atomic(status_file, existing)
    except OSError:
        pass
