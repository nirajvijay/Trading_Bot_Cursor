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
from api.queries.checklist import fetch_premarket_checklist

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


def is_runner_running(status_file: Optional[Path] = None) -> bool:
    """Runner is running if status file was updated recently."""
    path = status_file or _status_file()
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
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
    date = session_date or _today_ist()
    return fetch_premarket_checklist(
        live_db=config.LIVE_DB_PATH,
        instruments_db=config.INSTRUMENTS_DB_PATH,
        historical_db=config.HISTORICAL_DB_PATH,
        baselines_db=config.BASELINES_DB_PATH,
        session_date=date,
    )


def compute_readiness(session_date: Optional[str] = None) -> dict:
    checklist = fetch_checklist_summary(session_date)
    checklist_ok = checklist["overall_status"] == "ok"
    market_open = is_market_open()
    runner_running = is_runner_running()

    if runner_running:
        reason = "Observation runner is already running"
        can_start = False
    elif not checklist_ok:
        reason = "Complete Pre-Market Checklist first"
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
    readiness = compute_readiness(session_date)
    if readiness["runner_running"]:
        return False, readiness["reason"], None
    if not readiness["checklist_ok"]:
        return False, readiness["reason"], None
    if not readiness["market_open"]:
        return False, readiness["reason"], None

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
        return False, f"Failed to start observation runner: {exc}", None

    return True, f"Observation runner started (pid {proc.pid})", proc.pid
