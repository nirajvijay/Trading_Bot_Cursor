"""Atomic private cache of completed pre-market checklist results."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from api import config
from universe_manifest import MANIFEST_FILENAME, default_manifest_path, symbol_list_checksum

IST = ZoneInfo("Asia/Kolkata")
CACHE_FILENAME = "checklist_cache.json"
SCHEMA_VERSION = 3


def _cache_path(*, local_data_dir: Optional[Path] = None) -> Path:
    root = local_data_dir or config.LOCAL_DATA_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root / CACHE_FILENAME


def _today_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def current_universe_manifest_id(*, local_data_dir: Optional[Path] = None) -> str:
    """Cheap identity for the universe/manifest used by cache validation."""
    root = local_data_dir or config.LOCAL_DATA_DIR
    path = default_manifest_path(root)
    expected = symbol_list_checksum()
    if not path.exists():
        return f"missing:{expected}"
    try:
        raw = path.read_bytes()
    except OSError:
        return f"unreadable:{expected}"
    file_hash = hashlib.sha256(raw).hexdigest()
    try:
        data = json.loads(raw.decode("utf-8"))
        checksum = str(data.get("symbol_list_checksum") or "")
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, AttributeError):
        checksum = ""
    if checksum:
        return f"{checksum}:{file_hash}"
    return f"{expected}:{file_hash}"


def _reason_summary(payload: dict[str, Any]) -> str:
    next_step = str(payload.get("next_step") or "").strip()
    if next_step:
        return next_step
    blockers = payload.get("blockers") or []
    if isinstance(blockers, list) and blockers:
        return str(blockers[0])
    overall = str(payload.get("overall_status") or "not_checked")
    if overall == "ok":
        return ""
    return "Complete Pre-Market Checklist first"


def write_checklist_cache(
    checklist: dict[str, Any],
    *,
    local_data_dir: Optional[Path] = None,
) -> None:
    """Persist any completed checklist result (ok / warning / failed / needs_update)."""
    root = local_data_dir or config.LOCAL_DATA_DIR
    path = _cache_path(local_data_dir=root)
    session_date = str(checklist.get("session_date") or _today_ist())
    overall_status = str(checklist.get("overall_status") or "not_checked")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "session_date": session_date,
        "universe_manifest_id": current_universe_manifest_id(local_data_dir=root),
        "overall_status": overall_status,
        "computed_at": str(checklist.get("checked_at") or datetime.now(IST).isoformat()),
        "reason_summary": _reason_summary(checklist),
        "blockers": list(checklist.get("blockers") or [])[:5],
        "next_step": str(checklist.get("next_step") or ""),
    }
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


def invalidate_checklist_cache(*, local_data_dir: Optional[Path] = None) -> None:
    path = _cache_path(local_data_dir=local_data_dir)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def read_checklist_cache(
    session_date: Optional[str] = None,
    *,
    local_data_dir: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """
    Return cached checklist summary when identity matches.

    Corrupt / partial / mismatched identity → None (treat as not ready).
    """
    root = local_data_dir or config.LOCAL_DATA_DIR
    path = _cache_path(local_data_dir=root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    try:
        schema_version = int(data.get("schema_version"))
    except (TypeError, ValueError):
        return None
    if schema_version != SCHEMA_VERSION:
        return None

    expected_date = session_date or _today_ist()
    if str(data.get("session_date") or "") != expected_date:
        return None

    cached_manifest_id = str(data.get("universe_manifest_id") or "")
    if not cached_manifest_id:
        return None
    if cached_manifest_id != current_universe_manifest_id(local_data_dir=root):
        return None

    overall = str(data.get("overall_status") or "")
    if not overall:
        return None

    return {
        "session_date": expected_date,
        "overall_status": overall,
        "computed_at": data.get("computed_at"),
        "reason_summary": str(data.get("reason_summary") or ""),
        "blockers": list(data.get("blockers") or []),
        "next_step": str(data.get("next_step") or ""),
        "universe_manifest_id": cached_manifest_id,
        "schema_version": schema_version,
    }


# Re-export for callers that need the filename constant
__all__ = [
    "CACHE_FILENAME",
    "MANIFEST_FILENAME",
    "SCHEMA_VERSION",
    "current_universe_manifest_id",
    "invalidate_checklist_cache",
    "read_checklist_cache",
    "write_checklist_cache",
]
