"""Read optional runner status JSON written by live_observation_runner."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from api.schemas.radar import RunnerStatus

IST = ZoneInfo("Asia/Kolkata")
RUNNER_STALE_SECONDS = 30


def _runner_state_from_payload(
    data: dict,
    *,
    expected_session_date: Optional[str] = None,
) -> str:
    if expected_session_date:
        file_session = data.get("session_date")
        if file_session and str(file_session) != expected_session_date:
            return "stopped"
    updated_at = data.get("updated_at")
    if not updated_at:
        return "stopped"
    try:
        updated = datetime.fromisoformat(str(updated_at))
    except ValueError:
        return "stopped"
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=IST)
    age = (datetime.now(IST) - updated.astimezone(IST)).total_seconds()
    if age < RUNNER_STALE_SECONDS:
        return "running"
    return "stopped"


def read_runner_status(
    status_file: Optional[str],
    *,
    expected_session_date: Optional[str] = None,
) -> RunnerStatus:
    if not status_file:
        return RunnerStatus(runner_state="stopped")
    path = Path(status_file)
    if not path.exists():
        return RunnerStatus(runner_state="stopped")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return RunnerStatus(runner_state="stopped")

    if expected_session_date:
        file_session = data.get("session_date")
        if file_session and str(file_session) != expected_session_date:
            return RunnerStatus(runner_state="stopped")

    state = _runner_state_from_payload(data, expected_session_date=expected_session_date)
    return RunnerStatus(
        session_date=data.get("session_date"),
        subscribed_tokens=data.get("subscribed_tokens"),
        feed_status=data.get("feed_status"),
        last_tick_time=data.get("last_tick_time"),
        updated_at=data.get("updated_at"),
        runner_state=state,
    )
