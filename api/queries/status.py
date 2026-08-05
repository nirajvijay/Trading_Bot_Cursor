"""Read optional runner status JSON written by live_observation_runner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from api.schemas.radar import RunnerStatus


def read_runner_status(status_file: Optional[str]) -> RunnerStatus:
    if not status_file:
        return RunnerStatus()
    path = Path(status_file)
    if not path.exists():
        return RunnerStatus()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return RunnerStatus()
    return RunnerStatus(
        session_date=data.get("session_date"),
        subscribed_tokens=data.get("subscribed_tokens"),
        feed_status=data.get("feed_status"),
        last_tick_time=data.get("last_tick_time"),
        updated_at=data.get("updated_at"),
    )
