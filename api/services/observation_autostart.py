"""Start observation once after the automatic morning checklist completes.

Runs inside the long-lived API process so the runner is spawned, tracked and
stopped exactly as the website's Start button does. Tries at most once per
day, between 09:00 and the 09:15 open, and only when today's automatic
checklist run completed with every stage valid. A successful start is recorded
so the execution autostart knows observation came up on its own today.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from typing import Optional

from api import config
from nse_trading_calendar import IST

logger = logging.getLogger(__name__)

POLL_SECONDS = 30
LATEST_START_MINUTE = 9 * 60 + 15


def _claim_today(day: str) -> bool:
    """Atomically record today's single attempt; False if already claimed."""
    root = config.runtime_cache_dir()
    root.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(root / f"observation-autostart-{day}.marker",
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    os.write(fd, datetime.now(IST).isoformat().encode())
    os.close(fd)
    return True


def _started_marker(day: str):
    return config.runtime_cache_dir() / f"observation-autostarted-{day}.marker"


def _record_started(day: str, pid: Optional[int]) -> None:
    _started_marker(day).write_text(f"{datetime.now(IST).isoformat()} pid={pid}\n")


def autostarted_today(day: str) -> bool:
    """True only when this autostart itself launched observation on ``day``."""
    return _started_marker(day).exists()


def autostart_tick(now: Optional[datetime] = None) -> Optional[str]:
    """One check. Returns the outcome when an attempt was made, else None."""
    from api.services.checklist_activity import read_activity, workflow_busy
    from api.services.observation_runner import (is_runner_running, observation_start_allowed,
                                                 start_observation_runner)
    from morning_checklist import run_log

    now = (now or datetime.now(IST)).astimezone(IST)
    day = now.date().isoformat()
    if not observation_start_allowed(day, now) or now.hour * 60 + now.minute >= LATEST_START_MINUTE:
        return None
    activity = read_activity(day) or {}
    if not (activity.get("source") == "automatic" and activity.get("status") == "completed"
            and not activity.get("dirty")):
        # Not ready (or blocked/manual); keep waiting until 09:15, never force it.
        return None
    if workflow_busy():
        # The morning job saves "completed" just before releasing its lock.
        return None
    if not _claim_today(day):
        return None
    try:
        if is_runner_running(session_date=day):
            outcome = "observation already running; nothing to start"
        else:
            ok, message, pid = start_observation_runner(day)
            outcome = f"observation started (pid {pid})" if ok else f"observation not started: {message}"
            if ok:
                try:
                    _record_started(day, pid)
                except OSError as exc:
                    outcome += f"; could not record it, execution will not autostart ({type(exc).__name__})"
    except Exception as exc:
        outcome = f"observation not started: {type(exc).__name__}"
    run_log(f"observation autostart: {outcome}")
    return outcome


def _loop(stop: threading.Event) -> None:
    while not stop.wait(POLL_SECONDS):
        try:
            autostart_tick()
        except Exception as exc:  # never let the API thread die
            logger.error("Observation autostart check failed: %s", type(exc).__name__)


def start_background(stop: threading.Event) -> Optional[threading.Thread]:
    """Only the production API auto-starts; dev machines and tests never do."""
    if os.environ.get("APP_ENV", "").strip().lower() != "production":
        return None
    thread = threading.Thread(target=_loop, args=(stop,), name="observation-autostart", daemon=True)
    thread.start()
    return thread
