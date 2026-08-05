"""Persist Kite token validation results for pre-market checklist."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from api import config

IST = ZoneInfo("Asia/Kolkata")


def _cache_path() -> Path:
    config.LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return config.LOCAL_DATA_DIR / "token_check.json"


def _today_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def write_token_check(*, valid: bool, user_id: Optional[str] = None) -> None:
    """Record the result of a token validation for today's IST session."""
    payload = {
        "valid": valid,
        "checked_at": datetime.now(IST).isoformat(),
        "session_date": _today_ist(),
        "user_id": user_id,
    }
    path = _cache_path()
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_token_check() -> Optional[dict]:
    """Return cached token check if present."""
    path = _cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def token_valid_for_today() -> Optional[bool]:
    """Return True/False if a check exists for today; None if no check today."""
    cached = read_token_check()
    if not cached:
        return None
    if cached.get("session_date") != _today_ist():
        return None
    return bool(cached.get("valid"))
