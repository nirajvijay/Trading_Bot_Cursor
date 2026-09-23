"""Rate limit failed Kite auto-login attempts per website session."""

from __future__ import annotations

import threading
import time
import json
import os

from fastapi import HTTPException

from api.auth.audit import write_audit


def _account_attempts() -> list[float]:
    from api import config
    path = config.runtime_cache_dir() / "kite-login-attempts.json"
    try:
        values = json.loads(path.read_text())
        if not isinstance(values, list):
            raise ValueError("invalid attempts")
        return [float(x) for x in values if float(x) > time.time() - WINDOW_SECONDS]
    except FileNotFoundError:
        return []
    except (OSError, ValueError, TypeError):
        raise ValueError("Kite attempt history unavailable; inspect server state.") from None


def check_account_budget() -> None:
    # Callers hold the cross-process checklist workflow lock.
    if len(_account_attempts()) >= MAX_FAILURES:
        raise ValueError("Kite login attempt limit reached; wait ten minutes before retrying.")


def record_account_attempt() -> None:
    from api import config
    path = config.runtime_cache_dir() / "kite-login-attempts.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump([*_account_attempts(), time.time()], handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)

WINDOW_SECONDS = 10 * 60
MAX_FAILURES = 3


class _SessionRateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def _prune(self, key: str, now: float) -> None:
        window_start = now - WINDOW_SECONDS
        hits = self._hits.get(key)
        if not hits:
            return
        kept = [t for t in hits if t >= window_start]
        if kept:
            self._hits[key] = kept
        else:
            self._hits.pop(key, None)

    def is_limited(self, session_id: str) -> bool:
        key = f"kite_auto:session:{session_id}"
        now = time.monotonic()
        with self._lock:
            self._prune(key, now)
            return len(self._hits.get(key, ())) >= MAX_FAILURES

    def record_failure(self, session_id: str) -> None:
        key = f"kite_auto:session:{session_id}"
        now = time.monotonic()
        with self._lock:
            self._prune(key, now)
            self._hits.setdefault(key, []).append(now)

    def clear(self, session_id: str) -> None:
        key = f"kite_auto:session:{session_id}"
        with self._lock:
            self._hits.pop(key, None)


_limiter = _SessionRateLimiter()


def reset_kite_auto_login_rate_limiter() -> None:
    _limiter.reset()


def check_kite_auto_login_rate_limit(session_id: str) -> None:
    if _limiter.is_limited(session_id):
        write_audit("kite_auto_login_rate_limited", reason="limit_exceeded")
        raise HTTPException(
            status_code=429,
            detail="Too many failed Kite auto-login attempts. Try again later.",
        )


def record_kite_auto_login_failure(session_id: str) -> None:
    _limiter.record_failure(session_id)


def clear_kite_auto_login_failures(session_id: str) -> None:
    _limiter.clear(session_id)
