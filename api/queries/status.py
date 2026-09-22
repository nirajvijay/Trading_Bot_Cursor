"""Read optional runner status JSON written by live_observation_runner."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from api.schemas.radar import RunnerStatus, VwapHealthStatus, VwapQualifierStatus

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
    vwap = None
    raw_vwap = data.get("vwap_qualifier")
    if isinstance(raw_vwap, dict):
        try:
            vwap = VwapQualifierStatus.model_validate(raw_vwap)
        except ValidationError:
            vwap = None
    return RunnerStatus(
        session_date=data.get("session_date"),
        subscribed_tokens=data.get("subscribed_tokens"),
        feed_status=data.get("feed_status"),
        last_tick_time=data.get("last_tick_time"),
        updated_at=data.get("updated_at"),
        runner_state=state,
        websocket_connected=data.get("websocket_connected") if state == "running" else None,
        observation_phase=data.get("observation_phase", "unknown") if state == "running" else "stopped",
        vwap_qualifier=vwap,
    )


def read_vwap_health(status_file: Optional[str]) -> VwapHealthStatus:
    """Read the standalone vwap_health_check.py output, if it has ever run."""
    if not status_file:
        return VwapHealthStatus()
    path = Path(status_file)
    if not path.exists():
        return VwapHealthStatus()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return VwapHealthStatus()
    # Newer files carry an explicit status; older ones only had a bool "ok".
    # Tolerate both so a stale file left over a deploy degrades gracefully
    # instead of showing a spurious alarm until the next timer run.
    raw_status = data.get("status")
    if raw_status not in ("ok", "alarm", "idle"):
        raw_status = "ok" if data.get("ok") else "alarm"

    try:
        return VwapHealthStatus(
            status=raw_status,
            session_live=bool(data.get("session_live", False)),
            session_date=data.get("session_date"),
            triggered_count=int(data.get("triggered_count", 0) or 0),
            qualified_count=int(data.get("qualified_count", 0) or 0),
            stuck_count=int(data.get("stuck_count", 0) or 0),
            callback_failures=int(data.get("callback_failures", 0) or 0),
            persist_failures=int(data.get("persist_failures", 0) or 0),
            reason=data.get("reason"),
            checked_at=data.get("checked_at"),
        )
    except (TypeError, ValueError, ValidationError):
        return VwapHealthStatus()
