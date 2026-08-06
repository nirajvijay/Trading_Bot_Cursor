"""Start and monitor the live observation runner (localhost use only)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from api import config
from api.services.checklist_cache import read_checklist_cache
from api.services.observation_start_lock import (
    ObservationStartBusy,
    acquire_start_lock,
    is_start_lease_active,
    reconcile_start_lock_with_heartbeat,
    release_start_lock,
    update_start_lock_pid,
)

IST = ZoneInfo("Asia/Kolkata")
ROOT = config.ROOT
DEFAULT_STATUS_FILE = Path("/tmp/runner_status.json")
RUNNER_STALE_SECONDS = 30
SESSION_OPEN_MINUTE = 9 * 60 + 15
SESSION_CLOSE_MINUTE = 15 * 60 + 30


def _normalize_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def session_close_datetime(now: Optional[datetime] = None) -> datetime:
    """Return today's NSE cash session close instant (15:30 IST)."""
    dt = _normalize_ist(now or datetime.now(IST))
    return dt.replace(hour=15, minute=30, second=0, microsecond=0)


def seconds_until_session_close(now: Optional[datetime] = None) -> float:
    """Seconds until 15:30 IST today; minimum 1.0."""
    dt = _normalize_ist(now or datetime.now(IST))
    remaining = (session_close_datetime(dt) - dt).total_seconds()
    return max(1.0, remaining)


def expected_stop_at_iso(now: Optional[datetime] = None) -> str:
    return session_close_datetime(now).isoformat()


def _status_file() -> Path:
    raw = config.RUNNER_STATUS_FILE or str(DEFAULT_STATUS_FILE)
    return Path(raw)


def _today_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def is_market_open(now: Optional[datetime] = None) -> bool:
    """Return True during NSE cash session hours (weekdays 09:15–15:30 IST)."""
    dt = _normalize_ist(now or datetime.now(IST))
    if dt.weekday() >= 5:
        return False
    minutes = dt.hour * 60 + dt.minute
    return SESSION_OPEN_MINUTE <= minutes < SESSION_CLOSE_MINUTE


def is_status_heartbeat_fresh(
    status_file: Optional[Path] = None,
    *,
    expected_session_date: Optional[str] = None,
) -> bool:
    """True when runner_status.json was updated recently for the expected session."""
    path = status_file or _status_file()
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if expected_session_date:
            file_session = data.get("session_date")
            if file_session and str(file_session) != expected_session_date:
                return False
        updated_at = data.get("updated_at")
        if not updated_at:
            return False
        updated = datetime.fromisoformat(str(updated_at))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=IST)
        age = (datetime.now(IST) - updated.astimezone(IST)).total_seconds()
        return age < RUNNER_STALE_SECONDS
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def is_runner_running(
    status_file: Optional[Path] = None,
    *,
    session_date: Optional[str] = None,
) -> bool:
    """
    Runner is running if status heartbeat is fresh, or a start lease is still
    active for the startup gap before the first heartbeat.
    """
    date = session_date or _today_ist()
    heartbeat = is_status_heartbeat_fresh(
        status_file, expected_session_date=date
    )
    reconcile_start_lock_with_heartbeat(session_date=date, heartbeat_fresh=heartbeat)
    if heartbeat:
        return True
    return is_start_lease_active(date)


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _build_runner_command() -> list[str]:
    status_path = _status_file()
    return [
        sys.executable,
        "live_observation_runner.py",
        "--status-file",
        str(status_path),
        "--instruments-db",
        str(config.LOCAL_INSTRUMENTS_DB_PATH),
        "--historical-db",
        str(config.LOCAL_HISTORICAL_DB_PATH),
        "--baselines-db",
        str(config.LOCAL_BASELINES_DB_PATH),
        "--until-session-close",
    ]


def fetch_checklist_summary(session_date: Optional[str] = None) -> dict:
    """
    Read cached checklist summary only (no deep DB scan).

    Cache miss → not_checked with a run-checklist reason.
    """
    date = session_date or _today_ist()
    cached = read_checklist_cache(date)
    if cached is None:
        return {
            "session_date": date,
            "overall_status": "not_checked",
            "reason_summary": "Run Pre-Market Checklist",
        }
    return {
        "session_date": cached["session_date"],
        "overall_status": cached["overall_status"],
        "reason_summary": cached.get("reason_summary")
        or cached.get("next_step")
        or (
            ""
            if cached["overall_status"] == "ok"
            else "Complete Pre-Market Checklist first"
        ),
    }


def compute_readiness(session_date: Optional[str] = None) -> dict:
    date = session_date or _today_ist()
    checklist = fetch_checklist_summary(date)
    checklist_ok = checklist["overall_status"] == "ok"
    market_open = is_market_open()
    runner_running = is_runner_running(session_date=date)

    if runner_running:
        reason = "Observation runner is already running"
        can_start = False
    elif not checklist_ok:
        reason = str(checklist.get("reason_summary") or "Complete Pre-Market Checklist first")
        can_start = False
    elif not market_open:
        reason = "Market closed — available 09:15–15:30 IST on weekdays"
        can_start = False
    else:
        reason = ""
        can_start = True

    return {
        "checklist_ok": checklist_ok,
        "checklist_status": checklist["overall_status"],
        "market_open": market_open,
        "runner_running": runner_running,
        "can_start": can_start,
        "reason": reason,
        "session_date": checklist["session_date"],
        "expected_stop_at": expected_stop_at_iso() if market_open else None,
    }


def start_observation_runner(session_date: Optional[str] = None) -> Tuple[bool, str, Optional[int]]:
    """
    Start the observation runner under an atomic start lease.

    The lease is held through the startup gap until a fresh matching status
    heartbeat arrives (or the spawned PID dies and the lease is reclaimed).
    """
    date = session_date or _today_ist()
    readiness = compute_readiness(date)
    if readiness["runner_running"]:
        return False, readiness["reason"], None
    if not readiness["checklist_ok"]:
        return False, readiness["reason"], None
    if not readiness["market_open"]:
        return False, readiness["reason"], None

    try:
        lock_file = acquire_start_lock(date)
    except ObservationStartBusy as exc:
        return False, str(exc), None

    # Re-check under the lock (another tab may have started between checks).
    if is_status_heartbeat_fresh(expected_session_date=date):
        release_start_lock(lock_file)
        return False, "Observation runner is already running", None

    command = _build_runner_command()
    env = os.environ.copy()
    env["RUNNER_STATUS_FILE"] = str(_status_file())

    try:
        proc = subprocess.Popen(
            command,
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        release_start_lock(lock_file)
        return False, f"Failed to start observation runner: {exc}", None

    # Hold lease with spawned PID — do NOT release after Popen.
    try:
        update_start_lock_pid(lock_file, pid=proc.pid, session_date=date)
    except OSError:
        # Lease file still exists from acquire; best-effort update failed.
        # Keep original lease (API pid) so the gap remains covered.
        pass

    return True, f"Observation runner started (pid {proc.pid})", proc.pid
