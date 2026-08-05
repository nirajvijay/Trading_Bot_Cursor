"""Optional runner status file for the read-only API."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def write_runner_status(
    path: Path,
    *,
    session_date: str,
    subscribed_tokens: int,
    feed_status: str,
    last_tick_time: Optional[str],
) -> None:
    payload = {
        "session_date": session_date,
        "subscribed_tokens": subscribed_tokens,
        "feed_status": feed_status,
        "last_tick_time": last_tick_time,
        "updated_at": datetime.now(IST).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
