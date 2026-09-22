"""Website account authentication API (/api/v1/account/*)."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from api.auth import settings
from api.auth.audit import write_audit
from api.auth.csrf import require_csrf_and_origin
from api.auth.deps import WebAuthContext, require_web_session, require_web_session_mutating
from api.auth.rate_limit import (
    ACTION_LOGIN,
    ACTION_MFA_VERIFY,
    ACTION_STEP_UP,
    check_rate_limit,
    clear_auth_failures,
    clear_login_related_failures,
    record_auth_failure,
)
from api.auth.totp import generate_totp_secret, provisioning_uri, verify_totp
from api.auth.web_auth_store import get_web_auth_store
from webauthn.helpers import bytes_to_base64url
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorAttachment,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/account", tags=["account"])


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    totp: Optional[str] = None


class PasskeyLoginOptionsRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class PasskeyLoginOptionsResponse(BaseModel):
    challenge_id: str
    options: dict[str, object]


class PasskeyLoginVerifyRequest(BaseModel):
    username: str = Field(..., min_length=1)
    challenge_id: str = Field(..., min_length=1)
    credential: dict[str, object]


class PasskeyRegisterOptionsResponse(BaseModel):
    challenge_id: str
    options: dict[str, object]


class PasskeyRegisterVerifyRequest(BaseModel):
    challenge_id: str = Field(..., min_length=1)
    credential: dict[str, object]


class PasskeyInfo(BaseModel):
    credential_id: str
    name: str
    created_at: str
    last_used_at: Optional[str] = None


class PasskeyListResponse(BaseModel):
    passkeys: list[PasskeyInfo]


class PasskeyDeleteRequest(BaseModel):
    credential_id: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class MeResponse(BaseModel):
    username: str
    mfa_enabled: bool
    mfa_required: bool
    step_up_active: bool
    auth_enabled: bool
    passkey_count: int


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
    """Delete session cookies with the same attrs used at create time."""
    secure = settings.WEB_AUTH_COOKIE_SECURE
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        path="/",
        secure=secure,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        key=settings.CSRF_COOKIE_NAME,
        path="/",
        secure=secure,
        httponly=False,
        samesite="lax",
    )


def _mfa_needed(user_mfa_enabled: bool) -> bool:
    return bool(user_mfa_enabled or settings.WEB_AUTH_MFA_REQUIRED)


def _me_response(username: str, mfa_enabled: bool, passkey_count: int) -> MeResponse:
    return MeResponse(
        username=username,
        mfa_enabled=mfa_enabled,
        mfa_required=settings.WEB_AUTH_MFA_REQUIRED,
        step_up_active=False,
        auth_enabled=True,
        passkey_count=passkey_count,
    )


def _complete_login(
    response: Response,
    user_id: int,
    username: str,
    mfa_enabled: bool,
    *,
    audit_event: str,
) -> MeResponse:
    store = get_web_auth_store()
    session = store.create_session(user_id)
    _set_session_cookies(response, session.id, session.csrf_token)
    write_audit(audit_event, username=username)
    return _me_response(username, mfa_enabled, len(store.list_passkeys(user_id)))


@router.post("/login", response_model=MeResponse)
def login(body: LoginRequest, request: Request, response: Response) -> MeResponse:
    if not settings.WEB_AUTH_ENABLED:
        raise HTTPException(status_code=400, detail="Website auth is disabled")

    check_rate_limit(ACTION_LOGIN, request, username=body.username)
    check_rate_limit(ACTION_MFA_VERIFY, request, username=body.username)

    store = get_web_auth_store()
    user = store.verify_login(body.username, body.password)
    if user is None:
        record_auth_failure(ACTION_LOGIN, request, username=body.username)
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
            record_auth_failure(ACTION_MFA_VERIFY, request, username=body.username)
            write_audit("web_login_failed", reason="invalid_totp")
            raise HTTPException(status_code=401, detail="Invalid MFA code")

    clear_login_related_failures(request, body.username)
    return _complete_login(
        response,
        user.id,
        user.username,
        user.mfa_enabled,
        audit_event="web_login_ok",
    )


@router.post("/passkey/login/options", response_model=PasskeyLoginOptionsResponse)
def passkey_login_options(
    body: PasskeyLoginOptionsRequest, request: Request
) -> PasskeyLoginOptionsResponse:
    """Verify the password, then issue a short-lived Touch ID challenge."""
    if not settings.WEB_AUTH_ENABLED:
        raise HTTPException(status_code=400, detail="Website auth is disabled")
    check_rate_limit(ACTION_LOGIN, request, username=body.username)
    store = get_web_auth_store()
    user = store.verify_login(body.username, body.password)
    if user is None:
        record_auth_failure(ACTION_LOGIN, request, username=body.username)
        write_audit("web_passkey_login_failed", reason="invalid_credentials")
        raise HTTPException(status_code=401, detail="Invalid username or password")
    keys = store.list_passkeys(user.id)
    if not keys:
        raise HTTPException(status_code=400, detail="No passkey is enrolled")
    options = generate_authentication_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=key.credential_id) for key in keys
        ],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    challenge = store.create_passkey_challenge(
        user.id, "authentication", options.challenge
    )
    return PasskeyLoginOptionsResponse(
        challenge_id=challenge.id,
        options=json.loads(options_to_json(options)),
    )


@router.post("/passkey/login/verify", response_model=MeResponse)
def passkey_login_verify(
    body: PasskeyLoginVerifyRequest, request: Request, response: Response
) -> MeResponse:
    if not settings.WEB_AUTH_ENABLED:
        raise HTTPException(status_code=400, detail="Website auth is disabled")
    check_rate_limit(ACTION_MFA_VERIFY, request, username=body.username)
    store = get_web_auth_store()
    user = store.get_user()
    if user is None or user.username != body.username:
        raise HTTPException(status_code=401, detail="Passkey authentication failed")
    challenge = store.consume_passkey_challenge(
        body.challenge_id, user.id, "authentication"
    )
    credential_id = body.credential.get("rawId") or body.credential.get("id")
    if challenge is None or not isinstance(credential_id, str):
        record_auth_failure(ACTION_MFA_VERIFY, request, username=body.username)
        raise HTTPException(status_code=401, detail="Passkey authentication failed")
    try:
        key = store.get_passkey(base64url_to_bytes(credential_id), user.id)
    except ValueError:
        key = None
    if key is None:
        record_auth_failure(ACTION_MFA_VERIFY, request, username=body.username)
        raise HTTPException(status_code=401, detail="Passkey authentication failed")
    try:
        verification = verify_authentication_response(
            credential=body.credential,
            expected_challenge=challenge,
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_ORIGIN,
            credential_public_key=key.public_key,
            credential_current_sign_count=key.sign_count,
            require_user_verification=True,
        )
    except Exception as exc:
        # The client only ever sees a generic 401, but a bare `except` with a
        # constant reason makes a real misconfiguration (wrong RP ID, wrong
        # origin, library upgrade) indistinguishable from an ordinary bad
        # signature. Record the exception type so the audit trail can tell
        # them apart, and log the full traceback for the server operator.
        logger.warning("passkey authentication failed", exc_info=True)
        record_auth_failure(ACTION_MFA_VERIFY, request, username=body.username)
        write_audit(
            "web_passkey_login_failed",
            reason="invalid_credential",
            error_type=type(exc).__name__,
        )
        raise HTTPException(status_code=401, detail="Passkey authentication failed")
    clear_login_related_failures(request, body.username)
    store.update_passkey_sign_count(
        verification.credential_id, verification.new_sign_count
    )
    return _complete_login(
        response,
        user.id,
        user.username,
        user.mfa_enabled,
        audit_event="web_passkey_login_ok",
    )


@router.post("/passkey/register/options", response_model=PasskeyRegisterOptionsResponse)
def passkey_register_options(
    ctx: WebAuthContext = Depends(require_web_session_mutating),
) -> PasskeyRegisterOptionsResponse:
    if ctx.auth_disabled:
        raise HTTPException(status_code=400, detail="Website auth is disabled")
    store = get_web_auth_store()
    user = store.get_user()
    if user is None:
        raise HTTPException(status_code=400, detail="No owner configured")
    keys = store.list_passkeys(user.id)
    options = generate_registration_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        rp_name="NIFTY RADAR",
        user_id=user.id.to_bytes(8, "big"),
        user_name=user.username,
        user_display_name=user.username,
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=key.credential_id) for key in keys
        ],
        authenticator_selection=AuthenticatorSelectionCriteria(
            authenticator_attachment=AuthenticatorAttachment.PLATFORM,
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    challenge = store.create_passkey_challenge(
        user.id, "registration", options.challenge
    )
    return PasskeyRegisterOptionsResponse(
        challenge_id=challenge.id,
        options=json.loads(options_to_json(options)),
    )


@router.post("/passkey/register/verify", response_model=MessageResponse)
def passkey_register_verify(
    body: PasskeyRegisterVerifyRequest,
    ctx: WebAuthContext = Depends(require_web_session_mutating),
) -> MessageResponse:
    if ctx.auth_disabled:
        raise HTTPException(status_code=400, detail="Website auth is disabled")
    store = get_web_auth_store()
    user = store.get_user()
    if user is None:
        raise HTTPException(status_code=400, detail="No owner configured")
    challenge = store.consume_passkey_challenge(
        body.challenge_id, user.id, "registration"
    )
    if challenge is None:
        raise HTTPException(status_code=400, detail="Passkey setup expired")
    try:
        verification = verify_registration_response(
            credential=body.credential,
            expected_challenge=challenge,
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_ORIGIN,
            require_user_verification=True,
        )
    except Exception as exc:
        logger.warning("passkey registration failed", exc_info=True)
        write_audit(
            "web_passkey_registration_failed",
            username=user.username,
            error_type=type(exc).__name__,
        )
        raise HTTPException(status_code=400, detail="Passkey registration failed")
    store.save_passkey(
        user.id,
        verification.credential_id,
        verification.credential_public_key,
        verification.sign_count,
    )
    write_audit("web_passkey_registered", username=user.username)
    return MessageResponse(success=True, message="Touch ID passkey registered")


@router.get("/passkeys", response_model=PasskeyListResponse)
def list_passkeys(
    ctx: WebAuthContext = Depends(require_web_session),
) -> PasskeyListResponse:
    """List enrolled passkeys so a stale device can be identified and removed."""
    if ctx.auth_disabled:
        return PasskeyListResponse(passkeys=[])
    store = get_web_auth_store()
    user = store.get_user()
    if user is None:
        raise HTTPException(status_code=400, detail="No owner configured")
    return PasskeyListResponse(
        passkeys=[
            PasskeyInfo(
                credential_id=bytes_to_base64url(key.credential_id),
                name=key.name,
                created_at=key.created_at,
                last_used_at=key.last_used_at,
            )
            for key in store.list_passkeys(user.id)
        ]
    )


@router.post("/passkeys/delete", response_model=MessageResponse)
def delete_passkey(
    body: PasskeyDeleteRequest,
    request: Request,
    ctx: WebAuthContext = Depends(require_web_session_mutating),
) -> MessageResponse:
    """Remove one enrolled passkey, re-authenticating with the password first.

    Without this, a passkey that died outside this system (new Mac, macOS
    reinstall, wiped Secure Enclave) leaves a row that can never be cleared:
    it keeps passkey_count above zero so the setup prompt never reappears, and
    the browser sees it in excludeCredentials so the same Mac cannot re-enrol.
    """
    if ctx.auth_disabled:
        raise HTTPException(status_code=400, detail="Website auth is disabled")
    check_rate_limit(ACTION_STEP_UP, request, username=ctx.session.username)
    store = get_web_auth_store()
    user = store.get_user()
    if user is None:
        raise HTTPException(status_code=400, detail="No owner configured")
    # Re-authenticate, exactly as change-password does: a hijacked session must
    # not be able to strip the owner's sign-in factors.
    if store.verify_login(user.username, body.password) is None:
        record_auth_failure(ACTION_STEP_UP, request, username=user.username)
        write_audit("web_passkey_delete_failed", reason="bad_password")
        raise HTTPException(status_code=401, detail="Password is incorrect")
    clear_auth_failures(ACTION_STEP_UP, request, username=user.username)
    try:
        credential_id = base64url_to_bytes(body.credential_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown passkey")

    enrolled = store.list_passkeys(user.id)
    if not any(key.credential_id == credential_id for key in enrolled):
        raise HTTPException(status_code=404, detail="Unknown passkey")
    is_last_passkey = len(enrolled) == 1

    # Removing the last passkey is allowed only while TOTP is still enrolled.
    #
    # The whole point of this endpoint is the dead-Mac case, where that last
    # row is precisely what blocks re-enrolment -- so a blanket refusal would
    # defeat it. But with no passkey and no authenticator, the account falls
    # back to password-only, which is a worse position than it started in.
    # Gating on mfa_enabled keeps the escape hatch open without ever letting a
    # delete drop the account to a single factor.
    if is_last_passkey and not user.mfa_enabled:
        raise HTTPException(
            status_code=400,
            detail=(
                "This is your only passkey and no authenticator app is enrolled. "
                "Set up the authenticator first, then remove it."
            ),
        )

    if not store.delete_passkey(credential_id, user.id):
        raise HTTPException(status_code=404, detail="Unknown passkey")
    write_audit("web_passkey_deleted", username=user.username)
    return MessageResponse(success=True, message="Passkey removed")


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
            passkey_count=0,
        )
    store = get_web_auth_store()
    session = store.get_session(ctx.session.id)
    if session is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    data = _me_response(
        session.username,
        session.mfa_enabled,
        len(store.list_passkeys(session.user_id)),
    )
    data.step_up_active = store.has_valid_step_up(session)
    return data


@router.post("/change-password", response_model=MessageResponse)
def change_password(
    body: ChangePasswordRequest,
    response: Response,
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
    _clear_session_cookies(response)
    write_audit("web_password_changed", username=user.username)
    return MessageResponse(
        success=True,
        message="Password updated. Please sign in again.",
    )


@router.post("/step-up", response_model=MessageResponse)
def step_up(
    body: StepUpRequest,
    request: Request,
    ctx: WebAuthContext = Depends(require_web_session_mutating),
) -> MessageResponse:
    if ctx.auth_disabled:
        return MessageResponse(success=True, message="Step-up not required (auth disabled)")
    check_rate_limit(ACTION_STEP_UP, request, username=ctx.session.username)
    store = get_web_auth_store()
    user = store.get_user()
    if user is None:
        raise HTTPException(status_code=400, detail="No owner configured")
    if store.verify_login(user.username, body.password) is None:
        record_auth_failure(ACTION_STEP_UP, request, username=user.username)
        write_audit("web_step_up_failed", reason="bad_password")
        raise HTTPException(status_code=401, detail="Invalid password")
    if _mfa_needed(user.mfa_enabled):
        if not user.mfa_secret or not body.totp or not verify_totp(user.mfa_secret, body.totp):
            record_auth_failure(ACTION_STEP_UP, request, username=user.username)
            write_audit("web_step_up_failed", reason="bad_totp")
            raise HTTPException(status_code=401, detail="Invalid MFA code")
    clear_auth_failures(ACTION_STEP_UP, request, username=user.username)
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
    request: Request,
    response: Response,
    ctx: WebAuthContext = Depends(require_web_session_mutating),
) -> MessageResponse:
    if ctx.auth_disabled:
        raise HTTPException(status_code=400, detail="Website auth is disabled")
    check_rate_limit(ACTION_MFA_VERIFY, request, username=ctx.session.username)
    store = get_web_auth_store()
    user = store.get_user()
    if user is None or not user.mfa_pending_secret:
        raise HTTPException(status_code=400, detail="No MFA setup in progress")
    if not verify_totp(user.mfa_pending_secret, body.totp):
        record_auth_failure(ACTION_MFA_VERIFY, request, username=user.username)
        write_audit("web_mfa_confirm_failed", reason="invalid_totp")
        raise HTTPException(status_code=401, detail="Invalid MFA code")
    clear_auth_failures(ACTION_MFA_VERIFY, request, username=user.username)
    store.confirm_mfa(user.mfa_pending_secret)
    _clear_session_cookies(response)
    write_audit("web_mfa_enabled", username=user.username)
    return MessageResponse(
        success=True,
        message="MFA enabled. Please sign in again with MFA.",
    )
