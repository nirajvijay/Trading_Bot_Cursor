"""CSRF synchronizer + Origin/Referer allowlist for state-changing requests."""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import HTTPException, Request

from api.auth import settings
from api.auth.web_auth_store import SessionRecord


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def origin_allowed(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin:
        return origin in settings.WEB_AUTH_ORIGIN_ALLOWLIST
    referer = request.headers.get("referer")
    if not referer:
        return False
    parsed = urlparse(referer)
    if not parsed.scheme or not parsed.netloc:
        return False
    base = f"{parsed.scheme}://{parsed.netloc}"
    return base in settings.WEB_AUTH_ORIGIN_ALLOWLIST


def require_csrf_and_origin(request: Request, session: SessionRecord) -> None:
    if request.method.upper() in SAFE_METHODS:
        return
    if not origin_allowed(request):
        raise HTTPException(status_code=403, detail="Origin not allowed")
    header = request.headers.get(settings.CSRF_HEADER_NAME) or request.headers.get(
        settings.CSRF_HEADER_NAME.lower()
    )
    cookie = request.cookies.get(settings.CSRF_COOKIE_NAME)
    if not header or not cookie:
        raise HTTPException(status_code=403, detail="CSRF token missing")
    if not secrets_equal(header, cookie) or not secrets_equal(header, session.csrf_token):
        raise HTTPException(status_code=403, detail="CSRF token mismatch")


def secrets_equal(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a.encode("utf-8"), b.encode("utf-8")):
        result |= x ^ y
    return result == 0
