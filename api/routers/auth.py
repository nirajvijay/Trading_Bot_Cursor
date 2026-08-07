"""Kite Connect auth API (website-session gated; remote callback with state).

Website session auth lives under /api/v1/account/*.
Kite market-data token flows remain under /api/v1/auth/*.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from kiteconnect.exceptions import TokenException

from api.auth import settings
from api.auth.audit import write_audit
from api.auth.deps import (
    WebAuthContext,
    require_step_up,
    require_web_session,
    require_web_session_mutating,
)
from api.auth.kite_oauth_store import get_kite_oauth_store
from api.schemas.auth import (
    AuthStatusResponse,
    CheckTokenResponse,
    KiteStartResponse,
    LoginUrlResponse,
    SessionRequest,
    SessionResponse,
)
from api.services.checklist_cache import invalidate_checklist_cache
from api.services.token_check_cache import write_token_check
from login import (
    build_authorize_url_with_state,
    check_access_token_details,
    generate_session,
    get_login_url,
    mask_token,
    read_auth_status,
)

logger = logging.getLogger(__name__)

LOCALHOST_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
KITE_OAUTH_COOKIE_PATH = "/api/v1/auth/callback"

router = APIRouter(prefix="/auth", tags=["auth"])


def require_localhost(request: Request) -> None:
    """Localhost gate retained for observation start + checklist generate."""
    client = request.client
    host = client.host if client else None
    if host not in LOCALHOST_HOSTS:
        raise HTTPException(status_code=403, detail="This endpoint is for local use only")


def _clear_kite_oauth_cookie(response: Response) -> None:
    """Delete oauth cookie with the same attrs used at create time."""
    response.delete_cookie(
        key=settings.KITE_OAUTH_COOKIE_NAME,
        path=KITE_OAUTH_COOKIE_PATH,
        secure=settings.WEB_AUTH_COOKIE_SECURE,
        httponly=True,
        samesite="lax",
    )


def _set_kite_oauth_cookie(response: Response, cookie_id: str) -> None:
    response.set_cookie(
        key=settings.KITE_OAUTH_COOKIE_NAME,
        value=cookie_id,
        httponly=True,
        secure=settings.WEB_AUTH_COOKIE_SECURE,
        samesite="lax",
        path=KITE_OAUTH_COOKIE_PATH,
        max_age=settings.KITE_OAUTH_TTL_SECONDS,
    )


def _safe_redirect(path: str) -> RedirectResponse:
    # Fixed relative paths only — never bounce to attacker-controlled URLs.
    if not path.startswith("/") or path.startswith("//"):
        path = "/?kite=error"
    return RedirectResponse(url=path, status_code=303)


@router.get(
    "/status",
    response_model=AuthStatusResponse,
    dependencies=[Depends(require_web_session)],
)
def auth_status() -> AuthStatusResponse:
    return AuthStatusResponse(**read_auth_status())


@router.get(
    "/login-url",
    response_model=LoginUrlResponse,
    dependencies=[Depends(require_web_session)],
)
def login_url() -> LoginUrlResponse:
    try:
        return LoginUrlResponse(login_url=get_login_url())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post(
    "/kite/start",
    response_model=KiteStartResponse,
)
def kite_start(
    response: Response,
    ctx: WebAuthContext = Depends(require_step_up),
) -> KiteStartResponse:
    """Start remote Kite OAuth: opaque state + oauth cookie; return authorize_url only."""
    store = get_kite_oauth_store()
    session_id = ctx.session.id if not ctx.auth_disabled else "disabled"
    opaque, cookie_id = store.create_pending(session_id)
    try:
        authorize_url = build_authorize_url_with_state(opaque)
    except ValueError as exc:
        store.clear_pending_for_session(session_id)
        raise HTTPException(status_code=400, detail=str(exc)) from None
    _set_kite_oauth_cookie(response, cookie_id)
    write_audit("kite_oauth_start", username=ctx.session.username)
    return KiteStartResponse(authorize_url=authorize_url)


@router.get("/callback")
def kite_callback(
    request: Request,
    response: Response,
    request_token: Optional[str] = None,
    status: Optional[str] = None,
    state: Optional[str] = None,
    action: Optional[str] = None,
) -> RedirectResponse:
    """Kite OAuth callback. CSRF/Origin EXEMPT. Requires session + oauth cookie + state.

    NEVER log request_token or state.
    """
    # Silence unused query params while keeping signature explicit for FastAPI.
    _ = action

    fail = _safe_redirect(settings.KITE_FAILURE_REDIRECT_PATH)
    oauth_cookie = request.cookies.get(settings.KITE_OAUTH_COOKIE_NAME)
    session_cookie = request.cookies.get(settings.SESSION_COOKIE_NAME)

    def fail_and_clear(reason: str) -> RedirectResponse:
        write_audit("kite_oauth_callback_failed", reason=reason)
        if oauth_cookie:
            try:
                get_kite_oauth_store().delete_by_cookie(oauth_cookie)
            except Exception:
                pass
        _clear_kite_oauth_cookie(fail)
        return fail

    if not settings.WEB_AUTH_ENABLED:
        # Even with auth disabled, callback still needs oauth cookie + state binding.
        session_id = "disabled"
    else:
        if not session_cookie:
            return fail_and_clear("missing_session")
        from api.auth.web_auth_store import get_web_auth_store

        session = get_web_auth_store().get_session(session_cookie)
        if session is None:
            return fail_and_clear("invalid_session")
        session_id = session.id

    if not oauth_cookie:
        return fail_and_clear("missing_oauth_cookie")
    if not state:
        return fail_and_clear("missing_state")
    if status != "success" or not request_token:
        return fail_and_clear("kite_status_or_token")

    consumed = get_kite_oauth_store().consume_atomic(
        opaque_state=state,
        session_id=session_id,
        oauth_cookie_id=oauth_cookie,
    )
    if consumed is None:
        return fail_and_clear("state_mismatch_or_replay")

    try:
        generate_session(
            request_token,
            expected_user_id=settings.KITE_EXPECTED_USER_ID,
            persist=True,
        )
    except ValueError as exc:
        logger.error("Kite callback exchange rejected: %s", type(exc).__name__)
        return fail_and_clear("user_mismatch_or_exchange")
    except TokenException:
        logger.error("Kite callback token exchange failed: TokenException")
        return fail_and_clear("token_exception")
    except Exception as exc:
        logger.error("Kite callback failed: %s", type(exc).__name__)
        return fail_and_clear("exchange_error")

    invalidate_checklist_cache()
    write_audit("kite_oauth_callback_ok")
    success = _safe_redirect(settings.KITE_SUCCESS_REDIRECT_PATH)
    _clear_kite_oauth_cookie(success)
    return success


@router.post(
    "/session",
    response_model=SessionResponse,
)
def create_session(
    body: SessionRequest,
    ctx: WebAuthContext = Depends(require_step_up),
) -> SessionResponse:
    """Legacy paste login. Gated by KITE_PASTE_LOGIN_ENABLED + session + step-up + CSRF."""
    if not settings.KITE_PASTE_LOGIN_ENABLED:
        raise HTTPException(status_code=403, detail="Paste login is disabled")
    try:
        session = generate_session(
            body.request_token,
            expected_user_id=settings.KITE_EXPECTED_USER_ID,
            persist=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except TokenException:
        raise HTTPException(
            status_code=400,
            detail="Request token is invalid or expired. Generate a new login URL and try again.",
        ) from None
    except Exception as exc:
        logger.error("Session generation failed: %s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to generate session") from None

    refresh = session.get("refresh_token")
    user_id = session.get("user_id")
    invalidate_checklist_cache()
    write_audit("kite_paste_login_ok", username=ctx.session.username)
    return SessionResponse(
        success=True,
        user_id=str(user_id) if user_id is not None else None,
        masked_access_token=mask_token(session["access_token"]),
        masked_refresh_token=mask_token(refresh) if refresh else None,
        message="Tokens saved to Kite secrets store",
    )


@router.post(
    "/check-token",
    response_model=CheckTokenResponse,
    dependencies=[Depends(require_web_session_mutating)],
)
def check_token() -> CheckTokenResponse:
    valid, message, user_id = check_access_token_details()
    write_token_check(valid=valid, user_id=user_id)
    invalidate_checklist_cache()
    return CheckTokenResponse(valid=valid, message=message, user_id=user_id)
