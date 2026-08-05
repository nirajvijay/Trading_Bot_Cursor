"""File-based single-generation-job lock under backend/data/local/."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from api import config

LOCK_FILENAME = "generation.lock.json"
_MAX_ACQUIRE_ATTEMPTS = 3


@dataclass(frozen=True)
class GenerationLockInfo:
    active_task: str
    pid: int
    started_at: str


class GenerationLockBusy(Exception):
    def __init__(self, info: GenerationLockInfo):
        self.info = info
        super().__init__(
            f"another generation task is running: {info.active_task} "
            f"(pid={info.pid}, started_at={info.started_at})"
        )


def lock_path(local_data_dir: Optional[Path] = None) -> Path:
    root = local_data_dir or config.LOCAL_DATA_DIR
    return root / LOCK_FILENAME


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_lock(path: Path) -> Optional[GenerationLockInfo]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return GenerationLockInfo(
            active_task=str(data["active_task"]),
            pid=int(data["pid"]),
            started_at=str(data["started_at"]),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _clear_lock(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _try_create_exclusive(path: Path, info: GenerationLockInfo) -> bool:
    """
    Atomically create the lock file with O_CREAT|O_EXCL.
    Only the creator may write the payload. Returns True on success.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(path), flags, 0o644)
    except FileExistsError:
        return False

    payload = {
        "active_task": info.active_task,
        "pid": info.pid,
        "started_at": info.started_at,
    }
    body = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    try:
        os.write(fd, body)
        os.fsync(fd)
    except OSError:
        try:
            os.close(fd)
        except OSError:
            pass
        _clear_lock(path)
        raise
    else:
        os.close(fd)
    return True


def _reclaim_if_stale(path: Path) -> Optional[GenerationLockInfo]:
    """
    If lock is held by a live PID, return that info (caller raises busy).
    If stale (dead PID), remove and return None so caller can retry.
    If the file exists but payload is not yet readable (creator still writing),
    treat as busy with a placeholder rather than reclaiming under a race.
    """
    existing = _read_lock(path)
    if existing is not None:
        if _pid_alive(existing.pid):
            return existing
        # Dead PID → reclaim then retry exclusive create.
        _clear_lock(path)
        return None

    if path.exists():
        # Exists but unreadable — likely exclusive create won and payload
        # write is in flight. Do not unlink; report busy.
        return GenerationLockInfo(
            active_task="unknown",
            pid=-1,
            started_at="",
        )

    return None


def acquire_generation_lock(task: str, *, local_data_dir: Optional[Path] = None) -> Path:
    """
    Acquire exclusive generation lock via O_CREAT|O_EXCL.

    Raises GenerationLockBusy if held by a live PID.
    Stale locks (dead PID) are removed, then exclusive acquisition is retried.
    """
    path = lock_path(local_data_dir)
    info = GenerationLockInfo(
        active_task=task,
        pid=os.getpid(),
        started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )

    for _ in range(_MAX_ACQUIRE_ATTEMPTS):
        if _try_create_exclusive(path, info):
            return path

        holder = _reclaim_if_stale(path)
        if holder is not None:
            raise GenerationLockBusy(holder)
        # Stale lock cleared — loop and retry exclusive create.

    # Another contender won the retry race.
    holder = _read_lock(path) or GenerationLockInfo(
        active_task="unknown",
        pid=-1,
        started_at=info.started_at,
    )
    raise GenerationLockBusy(holder)


def release_generation_lock(
    path: Optional[Path] = None, *, local_data_dir: Optional[Path] = None
) -> None:
    target = path or lock_path(local_data_dir)
    owned = _read_lock(target)
    if owned is not None and owned.pid != os.getpid():
        # Do not clear another process's lock
        return
    _clear_lock(target)
