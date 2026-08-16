"""FastAPI dependencies for website session and step-up."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Request

from api.auth import settings
from api.auth.csrf import require_csrf_and_origin
from api.auth.web_auth_store import SessionRecord, get_web_auth_store


@dataclass
class WebAuthContext:
    session: SessionRecord
    auth_disabled: bool = False


def _session_from_cookie(
    nr_session: Optional[str],
) -> Optional[SessionRecord]:
    if not nr_session:
        return None
    store = get_web_auth_store()
    session = store.get_session(nr_session)
    if session is None:
        return None
    store.touch_session(session.id)
    return session


def require_web_session(
    request: Request,
    nr_session: Optional[str] = Cookie(default=None, alias=settings.SESSION_COOKIE_NAME),
) -> WebAuthContext:
    """Require a valid website session on private APIs.

    When WEB_AUTH_ENABLED is false (non-production only), skip enforcement.
    """
    if not settings.WEB_AUTH_ENABLED:
        # Synthetic context for disabled auth (local/dev/tests).
        fake = SessionRecord(
            id="disabled",
            user_id=0,
            csrf_token="disabled",
            created_at="",
            expires_at="",
            last_seen_at="",
            step_up_expires_at=None,
            username="disabled",
            mfa_enabled=False,
        )
        request.state.web_auth = WebAuthContext(session=fake, auth_disabled=True)
        return request.state.web_auth

    session = _session_from_cookie(nr_session)
    if session is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    ctx = WebAuthContext(session=session, auth_disabled=False)
    request.state.web_auth = ctx
    return ctx


def require_web_session_mutating(
    request: Request,
    ctx: WebAuthContext = Depends(require_web_session),
) -> WebAuthContext:
    """Session + CSRF/Origin for state-changing website-authenticated routes."""
    if ctx.auth_disabled:
        return ctx
    require_csrf_and_origin(request, ctx.session)
    return ctx


def require_step_up(
    request: Request,
    ctx: WebAuthContext = Depends(require_web_session_mutating),
) -> WebAuthContext:
    """Require recent step-up (~10 min) for Kite token-changing actions."""
    if ctx.auth_disabled:
        return ctx
    store = get_web_auth_store()
    # Refresh session from store in case step-up was just granted.
    session = store.get_session(ctx.session.id)
    if session is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not store.has_valid_step_up(session):
        raise HTTPException(status_code=403, detail="Step-up authentication required")
    ctx.session = session
    request.state.web_auth = ctx
    return ctx


def optional_web_session(
    request: Request,
    nr_session: Optional[str] = Cookie(default=None, alias=settings.SESSION_COOKIE_NAME),
) -> Optional[WebAuthContext]:
    if not settings.WEB_AUTH_ENABLED:
        return WebAuthContext(
            session=SessionRecord(
                id="disabled",
                user_id=0,
                csrf_token="disabled",
                created_at="",
                expires_at="",
                last_seen_at="",
                step_up_expires_at=None,
                username="disabled",
                mfa_enabled=False,
            ),
            auth_disabled=True,
        )
    session = _session_from_cookie(nr_session)
    if session is None:
        return None
    ctx = WebAuthContext(session=session)
    request.state.web_auth = ctx
    return ctx
