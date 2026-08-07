"""Website account authentication API (/api/v1/account/*)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from api.auth import settings
from api.auth.audit import write_audit
from api.auth.csrf import require_csrf_and_origin
from api.auth.deps import WebAuthContext, require_web_session, require_web_session_mutating
from api.auth.totp import generate_totp_secret, provisioning_uri, verify_totp
from api.auth.web_auth_store import get_web_auth_store

router = APIRouter(prefix="/account", tags=["account"])


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    totp: Optional[str] = None


class MeResponse(BaseModel):
    username: str
    mfa_enabled: bool
    mfa_required: bool
    step_up_active: bool
    auth_enabled: bool


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8)


class StepUpRequest(BaseModel):
    password: str = Field(..., min_length=1)
    totp: Optional[str] = None


class MfaConfirmRequest(BaseModel):
    totp: str = Field(..., min_length=6, max_length=8)


class MfaSetupResponse(BaseModel):
    otpauth_uri: str
    secret: str
    message: str


class MessageResponse(BaseModel):
    success: bool
    message: str


def _set_session_cookies(response: Response, session_id: str, csrf_token: str) -> None:
    secure = settings.WEB_AUTH_COOKIE_SECURE
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="lax",
        path="/",
        secure=secure,
        max_age=settings.SESSION_TTL_SECONDS,
    )
    response.set_cookie(
        key=settings.CSRF_COOKIE_NAME,
        value=csrf_token,
        httponly=False,
        samesite="lax",
        path="/",
        secure=secure,
        max_age=settings.SESSION_TTL_SECONDS,
    )


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(settings.SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(settings.CSRF_COOKIE_NAME, path="/")


def _mfa_needed(user_mfa_enabled: bool) -> bool:
    return bool(user_mfa_enabled or settings.WEB_AUTH_MFA_REQUIRED)


@router.post("/login", response_model=MeResponse)
def login(body: LoginRequest, response: Response) -> MeResponse:
    if not settings.WEB_AUTH_ENABLED:
        raise HTTPException(status_code=400, detail="Website auth is disabled")
    store = get_web_auth_store()
    user = store.verify_login(body.username, body.password)
    if user is None:
        write_audit("web_login_failed", reason="invalid_credentials")
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if _mfa_needed(user.mfa_enabled):
        if not user.mfa_enabled or not user.mfa_secret:
            write_audit("web_login_failed", reason="mfa_required_not_enrolled")
            raise HTTPException(
                status_code=403,
                detail="MFA is required but not enrolled. Complete MFA setup first.",
            )
        if not body.totp or not verify_totp(user.mfa_secret, body.totp):
            write_audit("web_login_failed", reason="invalid_totp")
            raise HTTPException(status_code=401, detail="Invalid MFA code")

    session = store.create_session(user.id)
    _set_session_cookies(response, session.id, session.csrf_token)
    write_audit("web_login_ok", username=user.username)
    return MeResponse(
        username=user.username,
        mfa_enabled=user.mfa_enabled,
        mfa_required=settings.WEB_AUTH_MFA_REQUIRED,
        step_up_active=False,
        auth_enabled=True,
    )


@router.post("/logout", response_model=MessageResponse)
def logout(
    request: Request,
    response: Response,
    ctx: WebAuthContext = Depends(require_web_session),
) -> MessageResponse:
    if not ctx.auth_disabled:
        # Logout is state-changing: require CSRF when auth is on.
        require_csrf_and_origin(request, ctx.session)
        get_web_auth_store().delete_session(ctx.session.id)
        write_audit("web_logout", username=ctx.session.username)
    _clear_session_cookies(response)
    return MessageResponse(success=True, message="Logged out")


@router.get("/me", response_model=MeResponse)
def me(ctx: WebAuthContext = Depends(require_web_session)) -> MeResponse:
    if ctx.auth_disabled:
        return MeResponse(
            username="disabled",
            mfa_enabled=False,
            mfa_required=False,
            step_up_active=True,
            auth_enabled=False,
        )
    store = get_web_auth_store()
    session = store.get_session(ctx.session.id)
    if session is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return MeResponse(
        username=session.username,
        mfa_enabled=session.mfa_enabled,
        mfa_required=settings.WEB_AUTH_MFA_REQUIRED,
        step_up_active=store.has_valid_step_up(session),
        auth_enabled=True,
    )


@router.post("/change-password", response_model=MessageResponse)
def change_password(
    body: ChangePasswordRequest,
    ctx: WebAuthContext = Depends(require_web_session_mutating),
) -> MessageResponse:
    if ctx.auth_disabled:
        raise HTTPException(status_code=400, detail="Website auth is disabled")
    store = get_web_auth_store()
    user = store.get_user()
    if user is None:
        raise HTTPException(status_code=400, detail="No owner configured")
    if store.verify_login(user.username, body.current_password) is None:
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    store.change_password(user.id, body.new_password)
    write_audit("web_password_changed", username=user.username)
    return MessageResponse(success=True, message="Password updated")


@router.post("/step-up", response_model=MessageResponse)
def step_up(
    body: StepUpRequest,
    ctx: WebAuthContext = Depends(require_web_session_mutating),
) -> MessageResponse:
    if ctx.auth_disabled:
        return MessageResponse(success=True, message="Step-up not required (auth disabled)")
    store = get_web_auth_store()
    user = store.get_user()
    if user is None:
        raise HTTPException(status_code=400, detail="No owner configured")
    if store.verify_login(user.username, body.password) is None:
        write_audit("web_step_up_failed", reason="bad_password")
        raise HTTPException(status_code=401, detail="Invalid password")
    if _mfa_needed(user.mfa_enabled):
        if not user.mfa_secret or not body.totp or not verify_totp(user.mfa_secret, body.totp):
            write_audit("web_step_up_failed", reason="bad_totp")
            raise HTTPException(status_code=401, detail="Invalid MFA code")
    expires = store.grant_step_up(ctx.session.id)
    write_audit("web_step_up_ok", username=user.username)
    return MessageResponse(
        success=True,
        message=f"Step-up granted until {expires.astimezone(timezone.utc).isoformat()}",
    )


@router.post("/mfa/setup", response_model=MfaSetupResponse)
def mfa_setup(ctx: WebAuthContext = Depends(require_web_session_mutating)) -> MfaSetupResponse:
    if ctx.auth_disabled:
        raise HTTPException(status_code=400, detail="Website auth is disabled")
    store = get_web_auth_store()
    user = store.get_user()
    if user is None:
        raise HTTPException(status_code=400, detail="No owner configured")
    secret = generate_totp_secret()
    store.set_mfa_pending(secret)
    write_audit("web_mfa_setup_started", username=user.username)
    return MfaSetupResponse(
        otpauth_uri=provisioning_uri(secret, user.username),
        secret=secret,
        message="Scan the otpauth URI / enter the secret in your TOTP app, then confirm.",
    )


@router.post("/mfa/confirm", response_model=MessageResponse)
def mfa_confirm(
    body: MfaConfirmRequest,
    ctx: WebAuthContext = Depends(require_web_session_mutating),
) -> MessageResponse:
    if ctx.auth_disabled:
        raise HTTPException(status_code=400, detail="Website auth is disabled")
    store = get_web_auth_store()
    user = store.get_user()
    if user is None or not user.mfa_pending_secret:
        raise HTTPException(status_code=400, detail="No MFA setup in progress")
    if not verify_totp(user.mfa_pending_secret, body.totp):
        raise HTTPException(status_code=401, detail="Invalid MFA code")
    store.confirm_mfa(user.mfa_pending_secret)
    write_audit("web_mfa_enabled", username=user.username)
    return MessageResponse(success=True, message="MFA enabled")
