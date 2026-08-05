"""Local-only Kite auth API. Intended for 127.0.0.1 use only.

Start API with:
    uvicorn api.main:app --host 127.0.0.1 --port 8000

Do not use --host 0.0.0.0.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from kiteconnect.exceptions import TokenException

from api.schemas.auth import (
    AuthStatusResponse,
    CheckTokenResponse,
    LoginUrlResponse,
    SessionRequest,
    SessionResponse,
)
from api.services.token_check_cache import write_token_check
from login import (
    check_access_token_details,
    generate_session,
    get_login_url,
    mask_token,
    read_auth_status,
)

logger = logging.getLogger(__name__)

LOCALHOST_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
LOCAL_AUTH_HEADER = "x-nifty-radar-local-auth"
LOCAL_AUTH_VALUE = "true"

router = APIRouter(prefix="/auth", tags=["auth"])


def require_localhost(request: Request) -> None:
    client = request.client
    host = client.host if client else None
    if host not in LOCALHOST_HOSTS:
        raise HTTPException(status_code=403, detail="Auth endpoints are for local use only")


def require_local_session_header(
    x_nifty_radar_local_auth: Optional[str] = Header(default=None),
) -> None:
    if x_nifty_radar_local_auth != LOCAL_AUTH_VALUE:
        raise HTTPException(status_code=403, detail="Local auth header required for session generation")


@router.get("/status", response_model=AuthStatusResponse, dependencies=[Depends(require_localhost)])
def auth_status() -> AuthStatusResponse:
    return AuthStatusResponse(**read_auth_status())


@router.get("/login-url", response_model=LoginUrlResponse, dependencies=[Depends(require_localhost)])
def login_url() -> LoginUrlResponse:
    try:
        return LoginUrlResponse(login_url=get_login_url())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post(
    "/session",
    response_model=SessionResponse,
    dependencies=[Depends(require_localhost), Depends(require_local_session_header)],
)
def create_session(body: SessionRequest) -> SessionResponse:
    try:
        session = generate_session(body.request_token)
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
    return SessionResponse(
        success=True,
        user_id=str(user_id) if user_id is not None else None,
        masked_access_token=mask_token(session["access_token"]),
        masked_refresh_token=mask_token(refresh) if refresh else None,
        message="Tokens saved to backend/.env",
    )


@router.post("/check-token", response_model=CheckTokenResponse, dependencies=[Depends(require_localhost)])
def check_token() -> CheckTokenResponse:
    valid, message, user_id = check_access_token_details()
    write_token_check(valid=valid, user_id=user_id)
    return CheckTokenResponse(valid=valid, message=message, user_id=user_id)
