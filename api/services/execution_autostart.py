"""Start the execution engine, with live Kite orders, once observation autostarted.

Runs inside the long-lived API process and calls the same start path as the
Execution Desk's Start button, with the default caps and live orders on, so
the desk shows it exactly as if the button had been pressed. Tries at most
once per day, between the 09:15 open and 09:30, and only when the observation
autostart itself launched observation today. A manual observation start, a
missed observation window, or any refused precondition means no engine today.
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
EARLIEST_START_MINUTE = 9 * 60 + 15
LATEST_START_MINUTE = 9 * 60 + 30


def _claim_today(day: str) -> bool:
    """Atomically record today's single attempt; False if already claimed."""
    root = config.runtime_cache_dir()
    root.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(root / f"execution-autostart-{day}.marker",
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    os.write(fd, datetime.now(IST).isoformat().encode())
    os.close(fd)
    return True


def autostart_tick(now: Optional[datetime] = None) -> Optional[str]:
    """One check. Returns the outcome when an attempt was made, else None."""
    from api.services.checklist_activity import workflow_busy
    from api.services.execution_engine_runner import start_engine
    from api.services.observation_autostart import autostarted_today
    from engine_config import SessionRiskConfig
    from morning_checklist import run_log

    now = (now or datetime.now(IST)).astimezone(IST)
    day = now.date().isoformat()
    minute = now.hour * 60 + now.minute
    if not EARLIEST_START_MINUTE <= minute < LATEST_START_MINUTE:
        return None
    if not autostarted_today(day):
        return None
    if workflow_busy():
        # start_engine would refuse while checklist prep holds the lock.
        return None
    if not _claim_today(day):
        return None
    try:
        ok, message, pid = start_engine(session_config=SessionRiskConfig(), session_date=day,
                                        live_orders=True, now=now)
        outcome = (f"execution engine started with live Kite orders (pid {pid})" if ok
                   else f"execution engine not started: {message}")
    except Exception as exc:
        outcome = f"execution engine not started: {type(exc).__name__}"
    run_log(f"execution autostart: {outcome}")
    return outcome


def _loop(stop: threading.Event) -> None:
    while not stop.wait(POLL_SECONDS):
        try:
            autostart_tick()
        except Exception as exc:  # never let the API thread die
            logger.error("Execution autostart check failed: %s", type(exc).__name__)


def start_background(stop: threading.Event) -> Optional[threading.Thread]:
    """Only the production API auto-starts; dev machines and tests never do."""
    if os.environ.get("APP_ENV", "").strip().lower() != "production":
        return None
    thread = threading.Thread(target=_loop, args=(stop,), name="execution-autostart", daemon=True)
    thread.start()
    return thread
