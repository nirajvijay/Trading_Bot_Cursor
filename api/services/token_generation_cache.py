"""Persist non-secret Kite token generation metadata for the checklist.

The record lives under the persistent runtime cache, never in a release tree or
the secrets store. It intentionally contains no token material.
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


def _cache_path(*, create: bool = True) -> Path:
    root = config.runtime_cache_dir()
    if create:
        root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(root, 0o700)
        except OSError:
            pass
    return root / "kite_token_generation.json"


def write_token_generated() -> None:
    """Record a successful Kite token generation without recording any secret."""
    payload = {"generated_at": datetime.now(IST).isoformat()}
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


def read_token_generated_at() -> Optional[str]:
    """Return the most recently recorded token generation timestamp."""
    path = _cache_path(create=False)
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    generated_at = data.get("generated_at")
    return generated_at if isinstance(generated_at, str) else None
