"""In-memory failure rate limiter for website auth endpoints.

Single-owner private app limits (documented constants):
  - LOGIN: 5 failures / 15 minutes (per IP and per IP+username)
  - MFA verify (login TOTP / mfa/confirm): 5 failures / 15 minutes
  - Step-up (password/TOTP): 5 failures / 15 minutes

Sliding window of failure timestamps. Never log passwords/TOTP/tokens.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from fastapi import HTTPException, Request

from api.auth.audit import write_audit

# --- Tunables (single-owner private app) ---
WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 5
MFA_VERIFY_MAX_FAILURES = 5
STEP_UP_MAX_FAILURES = 5

ACTION_LOGIN = "login"
ACTION_MFA_VERIFY = "mfa_verify"
ACTION_STEP_UP = "step_up"

_LIMITS = {
    ACTION_LOGIN: LOGIN_MAX_FAILURES,
    ACTION_MFA_VERIFY: MFA_VERIFY_MAX_FAILURES,
    ACTION_STEP_UP: STEP_UP_MAX_FAILURES,
}


class RateLimiter:
    """Per-key sliding-window failure counter."""

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def reset(self) -> None:
        """Clear all counters (tests)."""
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

    def is_limited(self, key: str, max_failures: int) -> bool:
        now = time.monotonic()
        with self._lock:
            self._prune(key, now)
            return len(self._hits.get(key, ())) >= max_failures

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._prune(key, now)
            self._hits.setdefault(key, []).append(now)

    def clear(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


_limiter = RateLimiter()


def reset_rate_limiter() -> None:
    """Test helper: wipe all rate-limit state."""
    _limiter.reset()


def client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _keys_for(action: str, ip: str, username: Optional[str] = None) -> list[str]:
    keys = [f"{action}:ip:{ip}"]
    if username is not None:
        # Normalize for consistent bucketing; do not reveal existence.
        keys.append(f"{action}:ip_user:{ip}:{username.strip().lower()}")
    return keys


def check_rate_limit(
    action: str,
    request: Request,
    *,
    username: Optional[str] = None,
) -> None:
    """Raise HTTP 429 if any key for this action is over limit."""
    max_failures = _LIMITS.get(action, LOGIN_MAX_FAILURES)
    ip = client_ip(request)
    for key in _keys_for(action, ip, username):
        if _limiter.is_limited(key, max_failures):
            write_audit(
                "auth_rate_limited",
                action=action,
                reason="limit_exceeded",
            )
            raise HTTPException(
                status_code=429,
                detail="Too many attempts. Try again later.",
            )


def record_auth_failure(
    action: str,
    request: Request,
    *,
    username: Optional[str] = None,
) -> None:
    ip = client_ip(request)
    for key in _keys_for(action, ip, username):
        _limiter.record_failure(key)


def clear_auth_failures(
    action: str,
    request: Request,
    *,
    username: Optional[str] = None,
) -> None:
    ip = client_ip(request)
    for key in _keys_for(action, ip, username):
        _limiter.clear(key)


def clear_login_related_failures(request: Request, username: str) -> None:
    """Reset login + MFA verify counters after successful authentication."""
    clear_auth_failures(ACTION_LOGIN, request, username=username)
    clear_auth_failures(ACTION_MFA_VERIFY, request, username=username)
    clear_auth_failures(ACTION_STEP_UP, request, username=username)
