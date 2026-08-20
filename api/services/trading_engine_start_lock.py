"""Start lease for the trading engine (separate from observation)."""

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
LOCK_FILENAME = "trading_engine_start.lock.json"
LEASE_STALE_SECONDS = 120


@dataclass(frozen=True)
class StartLockInfo:
    pid: int
    session_date: str
    started_at: str


class TradingEngineStartBusy(Exception):
    def __init__(self, info: StartLockInfo):
        self.info = info
        super().__init__(
            f"Trading engine is already starting "
            f"(pid={info.pid}, session_date={info.session_date})"
        )


def lock_path(*, local_data_dir: Optional[Path] = None) -> Path:
    root = local_data_dir or config.local_data_dir()
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


def acquire_start_lock(session_date: str, *, local_data_dir: Optional[Path] = None) -> Path:
    path = lock_path(local_data_dir=local_data_dir)
    existing = _read_lock(path)
    if existing is not None and _pid_alive(existing.pid):
        raise TradingEngineStartBusy(existing)
    if existing is not None:
        _clear_lock(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "session_date": session_date,
        "started_at": datetime.now(IST).isoformat(timespec="seconds"),
    }
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        holder = _read_lock(path) or StartLockInfo(-1, session_date, "")
        raise TradingEngineStartBusy(holder) from exc
    body = (json.dumps(payload) + "\n").encode("utf-8")
    try:
        os.write(fd, body)
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


def update_start_lock_pid(path: Path, *, pid: int, session_date: str) -> None:
    payload = {
        "pid": pid,
        "session_date": session_date,
        "started_at": datetime.now(IST).isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def release_start_lock(path: Optional[Path] = None) -> None:
    _clear_lock(path or lock_path())


def is_start_lease_active(session_date: Optional[str] = None) -> bool:
    info = _read_lock(lock_path())
    if info is None:
        return False
    if _pid_alive(info.pid):
        return True
    _clear_lock(lock_path())
    return False


def reconcile_start_lock_with_heartbeat(*, heartbeat_fresh: bool) -> None:
    path = lock_path()
    info = _read_lock(path)
    if info is None:
        return
    if not _pid_alive(info.pid) or heartbeat_fresh:
        _clear_lock(path)
