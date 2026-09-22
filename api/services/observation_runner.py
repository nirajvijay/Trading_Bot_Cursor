"""Start and monitor the live observation runner (localhost use only)."""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from api import config
from config.nifty100_sector_map import sector_map_payload
from nse_trading_calendar import is_nse_trading_day, is_special_session_day
from api.services.checklist_cache import read_checklist_cache
from api.services.observation_start_lock import (
    ObservationStartBusy,
    acquire_start_lock,
    is_start_lease_active,
    read_start_lock,
    reconcile_start_lock_with_heartbeat,
    release_start_lock,
    update_start_lock_pid,
)

IST = ZoneInfo("Asia/Kolkata")
ROOT = config.ROOT
DEFAULT_STATUS_FILE = config.runtime_cache_dir() / "runner_status.json"
RUNNER_STALE_SECONDS = 30
SESSION_OPEN_MINUTE = 9 * 60 + 15
SESSION_CLOSE_MINUTE = 15 * 60 + 30
EXIT_STATUS_FILENAME = "observation_runner_exit.json"

logger = logging.getLogger(__name__)


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


def _exit_status_file() -> Path:
    return config.runtime_cache_dir() / EXIT_STATUS_FILENAME


def _observation_log_path(session_date: str) -> Path:
    log_dir = Path(
        os.environ.get("OBSERVATION_LOG_DIR", "/opt/nifty-radar/data/logs")
    )
    return log_dir / f"observation-{session_date}.log"


def _read_last_exit_status() -> dict:
    path = _exit_status_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _record_runner_exit(
    *,
    pid: int,
    session_date: str,
    exit_code: int,
    log_path: Path,
) -> None:
    path = _exit_status_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": pid,
        "session_date": session_date,
        "exit_code": exit_code,
        "exited_at": datetime.now(IST).isoformat(timespec="seconds"),
        "log_file": str(log_path),
    }
    tmp_path = path.with_name(f"{path.name}.{pid}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def _reap_runner(
    proc: subprocess.Popen,
    *,
    lock_file: Path,
    session_date: str,
    log_path: Path,
    log_handle,
) -> None:
    """Wait for the child so it cannot remain as a zombie after exiting."""
    try:
        exit_code = proc.wait()
        _record_runner_exit(
            pid=proc.pid,
            session_date=session_date,
            exit_code=exit_code,
            log_path=log_path,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to reap observation runner pid=%s", proc.pid)
    finally:
        try:
            log_handle.close()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to close observation log pid=%s", proc.pid)

        # Do not clear a newer runner's lease if a replacement started while
        # this child was exiting.
        lease = read_start_lock()
        if (
            lease is not None
            and lease.pid == proc.pid
            and lease.session_date == session_date
        ):
            release_start_lock(lock_file)


def _today_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def is_market_open(now: Optional[datetime] = None) -> bool:
    """Return True during NSE cash session hours (weekdays 09:15–15:30 IST)."""
    dt = _normalize_ist(now or datetime.now(IST))
    if not is_nse_trading_day(dt.date()) or is_special_session_day(dt.date()):
        return False
    minutes = dt.hour * 60 + dt.minute
    return SESSION_OPEN_MINUTE <= minutes < SESSION_CLOSE_MINUTE


def observation_start_allowed(session_date: str, now: Optional[datetime] = None) -> bool:
    """Connect from 09:00 on the current regular session; never imply live ticks."""
    dt = _normalize_ist(now or datetime.now(IST))
    return (
        session_date == dt.date().isoformat()
        and is_nse_trading_day(dt.date())
        and not is_special_session_day(dt.date())
        and 9 * 60 <= dt.hour * 60 + dt.minute < SESSION_CLOSE_MINUTE
    )


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


def _current_runner_pid(status_file: Optional[Path] = None) -> Optional[int]:
    """
    Best-effort PID of the running observation process, from whichever source
    is authoritative right now: the status heartbeat once the runner is up,
    or the start lease during the startup gap before the first heartbeat.
    """
    path = status_file or _status_file()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            pid = data.get("pid")
            if isinstance(pid, int) and pid > 0:
                return pid
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    lease = read_start_lock()
    if lease is not None and lease.pid > 0:
        return lease.pid
    return None


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
    sector_map = sector_map_payload()
    if not sector_map["valid"]:
        return {"session_date": date, "overall_status": "failed", "reason_summary": sector_map["reason"]}
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


def compute_readiness(session_date: Optional[str] = None, *, now: Optional[datetime] = None) -> dict:
    instant = _normalize_ist(now or datetime.now(IST))
    date = session_date or instant.date().isoformat()
    checklist = fetch_checklist_summary(date)
    checklist_ok = checklist["overall_status"] == "ok"
    market_open = is_market_open(instant)
    start_window = observation_start_allowed(date, instant)
    runner_running = is_runner_running(session_date=date)

    if runner_running:
        reason = "Observation runner is already running"
        can_start = False
    elif not checklist_ok:
        reason = str(checklist.get("reason_summary") or "Complete Pre-Market Checklist first")
        can_start = False
    elif not start_window:
        reason = "Market closed — observation starts 09:00–15:30 IST on the current regular trading day"
        can_start = False
    else:
        reason = "" if market_open else "Ready to connect; waiting for regular market data at 09:15 IST"
        can_start = True

    last_exit = _read_last_exit_status()
    return {
        "checklist_ok": checklist_ok,
        "checklist_status": checklist["overall_status"],
        "market_open": market_open,
        "observation_start_window": start_window,
        "runner_running": runner_running,
        "can_start": can_start,
        "reason": reason,
        "session_date": checklist["session_date"],
        "expected_stop_at": expected_stop_at_iso(instant) if start_window else None,
        "last_exit_code": last_exit.get("exit_code"),
        "last_exit_session_date": last_exit.get("session_date"),
        "last_exit_at": last_exit.get("exited_at"),
        "last_exit_log_file": last_exit.get("log_file"),
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
    if not readiness["can_start"]:
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

    log_path = _observation_log_path(date)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = None

    try:
        log_handle = log_path.open("ab", buffering=0)
        proc = subprocess.Popen(
            command,
            cwd=str(ROOT),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        if log_handle is not None:
            log_handle.close()
        release_start_lock(lock_file)
        return False, f"Failed to start observation runner: {exc}", None

    # Hold lease with spawned PID — do NOT release after Popen.
    try:
        update_start_lock_pid(lock_file, pid=proc.pid, session_date=date)
    except OSError:
        # Lease file still exists from acquire; best-effort update failed.
        # Keep original lease (API pid) so the gap remains covered.
        pass

    threading.Thread(
        target=_reap_runner,
        kwargs={
            "proc": proc,
            "lock_file": lock_file,
            "session_date": date,
            "log_path": log_path,
            "log_handle": log_handle,
        },
        name=f"reap-observation-{proc.pid}",
        daemon=True,
    ).start()

    return True, f"Observation runner started (pid {proc.pid})", proc.pid


def stop_observation_runner(session_date: Optional[str] = None) -> Tuple[bool, str, Optional[int]]:
    """
    Signal the running observation process to stop (graceful SIGTERM — the
    runner handles this by closing the feed and exiting on its own).

    The runner's status heartbeat naturally goes stale within
    RUNNER_STALE_SECONDS once the process exits, at which point
    is_runner_running() reports it as stopped.
    """
    date = session_date or _today_ist()
    if not is_runner_running(session_date=date):
        return False, "Observation runner is not running", None

    pid = _current_runner_pid()
    if pid is None:
        return False, "Observation runner is running but its PID is unknown; cannot stop cleanly", None

    try:
        # start_new_session=True made the runner its own process-group leader,
        # so signalling the group also reaches anything it spawned.
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        release_start_lock()
        return False, "Observation runner already stopped", None
    except PermissionError as exc:
        return False, f"Failed to stop observation runner: {exc}", None

    release_start_lock()
    return True, f"Stop signal sent to observation runner (pid {pid})", pid
