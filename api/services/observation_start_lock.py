"""Atomic observation-start lease covering the post-Popen heartbeat gap."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from api import config

IST = ZoneInfo("Asia/Kolkata")
LOCK_FILENAME = "observation_start.lock.json"
_MAX_ACQUIRE_ATTEMPTS = 3
# How long a lease may survive without a live PID / heartbeat before reclaim.
LEASE_STALE_SECONDS = 120


@dataclass(frozen=True)
class StartLockInfo:
    pid: int
    session_date: str
    started_at: str


class ObservationStartBusy(Exception):
    def __init__(self, info: StartLockInfo):
        self.info = info
        super().__init__(
            f"Observation runner is already starting "
            f"(pid={info.pid}, session_date={info.session_date}, started_at={info.started_at})"
        )


def lock_path(*, local_data_dir: Optional[Path] = None) -> Path:
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


def _read_lock(path: Path) -> Optional[StartLockInfo]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return StartLockInfo(
            pid=int(data["pid"]),
            session_date=str(data["session_date"]),
            started_at=str(data["started_at"]),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _clear_lock(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _lease_age_seconds(info: StartLockInfo) -> Optional[float]:
    try:
        started = datetime.fromisoformat(info.started_at)
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=IST)
    return (datetime.now(IST) - started.astimezone(IST)).total_seconds()


def _write_lock_payload(path: Path, info: StartLockInfo) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": info.pid,
        "session_date": info.session_date,
        "started_at": info.started_at,
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


def _try_create_exclusive(path: Path, info: StartLockInfo) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(path), flags, 0o600)
    except FileExistsError:
        return False

    payload = {
        "pid": info.pid,
        "session_date": info.session_date,
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
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return True


def _reclaim_if_stale(path: Path) -> Optional[StartLockInfo]:
    """
    Return holder info if lock must block (live PID or in-flight write).
    Clear and return None when reclaim is allowed (dead PID / stale lease).
    """
    existing = _read_lock(path)
    if existing is not None:
        if _pid_alive(existing.pid):
            return existing
        age = _lease_age_seconds(existing)
        # Dead PID → reclaim. Also reclaim unreadable/zero pid if lease is old.
        if existing.pid <= 0 and age is not None and age < LEASE_STALE_SECONDS:
            return existing
        _clear_lock(path)
        return None

    if path.exists():
        return StartLockInfo(pid=-1, session_date="", started_at="")
    return None


def acquire_start_lock(
    session_date: str,
    *,
    local_data_dir: Optional[Path] = None,
) -> Path:
    """
    Acquire exclusive start lease via O_CREAT|O_EXCL.

    Initially records the API process PID; caller must update_start_lock_pid
    with the spawned runner PID after Popen. Do not release immediately after
    Popen — hold until a fresh matching heartbeat or dead-PID reclaim.
    """
    path = lock_path(local_data_dir=local_data_dir)
    info = StartLockInfo(
        pid=os.getpid(),
        session_date=session_date,
        started_at=datetime.now(IST).isoformat(timespec="seconds"),
    )

    for _ in range(_MAX_ACQUIRE_ATTEMPTS):
        if _try_create_exclusive(path, info):
            return path
        holder = _reclaim_if_stale(path)
        if holder is not None:
            raise ObservationStartBusy(holder)

    holder = _read_lock(path) or StartLockInfo(
        pid=-1,
        session_date=session_date,
        started_at=info.started_at,
    )
    raise ObservationStartBusy(holder)


def update_start_lock_pid(
    path: Path,
    *,
    pid: int,
    session_date: str,
) -> None:
    """Rewrite lease with the spawned runner PID (still held)."""
    info = StartLockInfo(
        pid=pid,
        session_date=session_date,
        started_at=datetime.now(IST).isoformat(timespec="seconds"),
    )
    _write_lock_payload(path, info)


def release_start_lock(
    path: Optional[Path] = None,
    *,
    local_data_dir: Optional[Path] = None,
) -> None:
    target = path or lock_path(local_data_dir=local_data_dir)
    _clear_lock(target)


def read_start_lock(*, local_data_dir: Optional[Path] = None) -> Optional[StartLockInfo]:
    return _read_lock(lock_path(local_data_dir=local_data_dir))


def is_start_lease_active(
    session_date: Optional[str] = None,
    *,
    local_data_dir: Optional[Path] = None,
) -> bool:
    """True while a live-PID start lease covers the startup gap."""
    path = lock_path(local_data_dir=local_data_dir)
    info = _read_lock(path)
    if info is None:
        if path.exists():
            # In-flight write — treat as active to be safe.
            return True
        return False
    if session_date and info.session_date and info.session_date != session_date:
        # Different session lease: still blocks concurrent starts on this host.
        pass
    if _pid_alive(info.pid):
        return True
    # Dead PID → reclaim opportunistically.
    _clear_lock(path)
    return False


def reconcile_start_lock_with_heartbeat(
    *,
    session_date: str,
    heartbeat_fresh: bool,
    local_data_dir: Optional[Path] = None,
) -> None:
    """
    Once the runner writes a fresh matching status heartbeat, the lease is
    redundant and may be cleared. Dead-PID leases are reclaimed always.
    """
    path = lock_path(local_data_dir=local_data_dir)
    info = _read_lock(path)
    if info is None:
        if path.exists() is False:
            return
        # Unreadable — leave unless we have a confirming heartbeat for session.
        if heartbeat_fresh:
            _clear_lock(path)
        return

    if not _pid_alive(info.pid):
        _clear_lock(path)
        return

    if heartbeat_fresh and (not info.session_date or info.session_date == session_date):
        _clear_lock(path)
