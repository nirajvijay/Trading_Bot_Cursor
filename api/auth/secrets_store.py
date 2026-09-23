"""Atomic flock-backed Kite secrets file (kite.env)."""

from __future__ import annotations

import fcntl
import os
import tempfile
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values

from api.auth import settings


def secrets_path() -> Path:
    return settings.KITE_SECRETS_PATH


def ensure_secrets_dir() -> Path:
    path = secrets_path()
    settings.ensure_parent_dir(path, mode=0o700)
    return path


def read_secrets(path: Path | None = None) -> dict[str, str]:
    target = path or secrets_path()
    if not target.exists():
        return {}
    values = dotenv_values(target)
    return {k: v for k, v in values.items() if k and v is not None}


def write_secrets_atomic(updates: Mapping[str, str], path: Path | None = None) -> None:
    """Merge updates into kite.env using flock + atomic rename. Mode 0600."""
    target = path or ensure_secrets_dir()
    settings.ensure_parent_dir(target, mode=0o700)
    target.parent.mkdir(parents=True, exist_ok=True)

    lock_path = target.with_suffix(target.suffix + ".lock")
    with open(lock_path, "a+", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            current = read_secrets(target)
            current.update({k: str(v) for k, v in updates.items()})
            lines = [f"{key}={current[key]}\n" for key in sorted(current)]
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{target.name}.",
                dir=str(target.parent),
                text=True,
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as tmp_fh:
                    tmp_fh.writelines(lines)
                    tmp_fh.flush()
                    os.fsync(tmp_fh.fileno())
                os.chmod(tmp_name, 0o600)
                os.replace(tmp_name, target)
                try:
                    os.chmod(target, 0o600)
                except OSError:
                    pass
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
