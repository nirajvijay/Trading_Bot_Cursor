"""Persist Kite token validation results for pre-market checklist.

Writes under NIFTY_RADAR_DATA_ROOT/runtime-cache (never release trees).
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from api import config

IST = ZoneInfo("Asia/Kolkata")


def _ensure_cache_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _cache_path(*, create: bool = True) -> Path:
    root = config.runtime_cache_dir()
    if create:
        _ensure_cache_dir(root)
    return root / "token_check.json"


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
    text = json.dumps(payload, indent=2) + "\n"
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, text.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def read_token_check() -> Optional[dict]:
    """Return cached token check if present."""
    path = _cache_path(create=False)
    try:
        if not path.exists():
            return None
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
