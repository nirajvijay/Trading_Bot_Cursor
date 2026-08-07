"""Pydantic schemas for Kite auth API responses."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class AuthStatusResponse(BaseModel):
    api_key_configured: bool
    api_secret_configured: bool
    access_token_present: bool
    refresh_token_present: bool
    masked_api_key: Optional[str] = None
    masked_access_token: Optional[str] = None
    masked_refresh_token: Optional[str] = None


class LoginUrlResponse(BaseModel):
    login_url: str


class KiteStartResponse(BaseModel):
    authorize_url: str


class SessionRequest(BaseModel):
    request_token: str = Field(..., min_length=1, description="Raw request_token or full redirect URL")


class SessionResponse(BaseModel):
    success: bool
    user_id: Optional[str] = None
    masked_access_token: str
    masked_refresh_token: Optional[str] = None
    message: str = Field(
        default="Tokens saved to Kite secrets store",
        description="POST /auth/session writes tokens via the secrets store",
    )


class CheckTokenResponse(BaseModel):
    valid: bool
    message: str
    user_id: Optional[str] = None
