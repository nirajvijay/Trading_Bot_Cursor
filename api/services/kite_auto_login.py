"""Headless Kite login via password + external TOTP (unofficial Zerodha flow).

Used when KITE_AUTO_LOGIN_ENABLED and credentials are present in kite.env.
Never log passwords, TOTP secrets, request_token, or access_token.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Optional
from urllib.parse import parse_qs, urlparse

import pyotp
import requests
from kiteconnect.exceptions import TokenException

from api.auth import settings
from login import _read_env_merged, _require_env, generate_session, mask_token

logger = logging.getLogger(__name__)

AutoLoginFailureReason = Literal[
    "missing_credentials",
    "missing_api_key",
    "connect_login_failed",
    "api_login_non_json",
    "captcha_required",
    "login_rejected",
    "missing_request_id",
    "api_twofa_non_json",
    "totp_rejected",
    "no_request_token",
    "session_token_error",
    "user_mismatch",
    "token_exception",
    "unexpected_error",
]

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class AutoLoginResult:
    success: bool
    failure_reason: Optional[AutoLoginFailureReason] = None
    message: Optional[str] = None
    user_id: Optional[str] = None
    masked_access_token: Optional[str] = None


def auto_login_credentials_configured() -> bool:
    creds = read_auto_login_credentials()
    return all(creds.values())


def read_auto_login_credentials() -> dict[str, Optional[str]]:
    env = _read_env_merged()
    user_id = (env.get("KITE_USER_ID") or env.get("KITE_EXPECTED_USER_ID") or "").strip()
    password = (env.get("KITE_PASSWORD") or "").strip()
    totp_secret = (env.get("KITE_TOTP_SECRET") or "").strip()
    return {
        "user_id": user_id or None,
        "password": password or None,
        "totp_secret": totp_secret or None,
    }


def _extract_request_token_from_urls(urls: list[str]) -> Optional[str]:
    for url in urls:
        query = parse_qs(urlparse(url).query)
        token = query.get("request_token", [None])[0]
        if token:
            return str(token)
    return None


def _fetch_request_token(
    session: requests.Session,
    *,
    connect_url: str,
    api_key: str,
) -> tuple[Optional[str], Optional[AutoLoginFailureReason]]:
    finish_url = connect_url
    if "skip_session" not in finish_url:
        sep = "&" if "?" in finish_url else "?"
        finish_url = finish_url + sep + "skip_session=true"
    try:
        r3 = session.get(finish_url, allow_redirects=True, timeout=30)
    except requests.RequestException:
        return None, "no_request_token"
    candidates = [r3.url] + [h.url for h in r3.history]
    token = _extract_request_token_from_urls(candidates)
    if token:
        return token, None
    try:
        r4 = session.get(
            f"https://kite.zerodha.com/connect/finish?api_key={api_key}",
            allow_redirects=True,
            timeout=30,
        )
    except requests.RequestException:
        return None, "no_request_token"
    candidates = [r4.url] + [h.url for h in r4.history]
    token = _extract_request_token_from_urls(candidates)
    if token:
        return token, None
    return None, "no_request_token"


def attempt_kite_auto_login() -> AutoLoginResult:
    """Run headless login; persist token via generate_session on success."""
    creds = read_auto_login_credentials()
    if not all(creds.values()):
        return AutoLoginResult(
            success=False,
            failure_reason="missing_credentials",
            message="Kite auto-login credentials are not fully configured",
        )

    try:
        api_env = _require_env("KITE_API_KEY", "KITE_API_SECRET")
    except ValueError:
        return AutoLoginResult(
            success=False,
            failure_reason="missing_api_key",
            message="KITE_API_KEY or KITE_API_SECRET is missing",
        )

    api_key = api_env["KITE_API_KEY"]
    user_id = creds["user_id"]
    password = creds["password"]
    totp_secret = creds["totp_secret"]

    http = requests.Session()
    http.headers.update({"User-Agent": _USER_AGENT, "X-Kite-Version": "3"})

    login_start = f"https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"
    try:
        r0 = http.get(login_start, allow_redirects=True, timeout=30)
    except requests.RequestException as exc:
        logger.warning("Kite auto-login connect failed: %s", type(exc).__name__)
        return AutoLoginResult(
            success=False,
            failure_reason="connect_login_failed",
            message="Could not reach Kite login endpoint",
        )

    if r0.status_code >= 400:
        return AutoLoginResult(
            success=False,
            failure_reason="connect_login_failed",
            message=f"Kite connect login returned HTTP {r0.status_code}",
        )

    try:
        r1 = http.post(
            "https://kite.zerodha.com/api/login",
            data={"user_id": user_id, "password": password},
            timeout=30,
        )
        body1 = r1.json()
    except ValueError:
        return AutoLoginResult(
            success=False,
            failure_reason="api_login_non_json",
            message="Kite login returned a non-JSON response",
        )
    except requests.RequestException as exc:
        logger.warning("Kite auto-login password step failed: %s", type(exc).__name__)
        return AutoLoginResult(
            success=False,
            failure_reason="login_rejected",
            message="Kite login request failed",
        )

    msg1 = str(body1.get("message") or "")
    if body1.get("status") != "success":
        err = f"{msg1} {body1.get('error_type') or ''}"
        if "CAPTCHA" in err.upper():
            return AutoLoginResult(
                success=False,
                failure_reason="captcha_required",
                message=msg1 or "CAPTCHA required",
            )
        return AutoLoginResult(
            success=False,
            failure_reason="login_rejected",
            message=msg1 or "Kite login rejected",
        )

    request_id = (body1.get("data") or {}).get("request_id")
    if not request_id:
        return AutoLoginResult(
            success=False,
            failure_reason="missing_request_id",
            message="Kite login did not return a 2FA request id",
        )

    otp = pyotp.TOTP(totp_secret).now()
    try:
        r2 = http.post(
            "https://kite.zerodha.com/api/twofa",
            data={
                "user_id": user_id,
                "request_id": request_id,
                "twofa_value": otp,
                "twofa_type": "totp",
            },
            timeout=30,
        )
        body2 = r2.json()
    except ValueError:
        return AutoLoginResult(
            success=False,
            failure_reason="api_twofa_non_json",
            message="Kite 2FA returned a non-JSON response",
        )
    except requests.RequestException as exc:
        logger.warning("Kite auto-login TOTP step failed: %s", type(exc).__name__)
        return AutoLoginResult(
            success=False,
            failure_reason="totp_rejected",
            message="Kite 2FA request failed",
        )

    if body2.get("status") != "success":
        return AutoLoginResult(
            success=False,
            failure_reason="totp_rejected",
            message=str(body2.get("message") or "Kite 2FA rejected"),
        )

    request_token: Optional[str] = None
    data2 = body2.get("data") or {}
    if isinstance(data2, dict) and data2.get("request_token"):
        request_token = str(data2["request_token"])
    if not request_token:
        request_token, token_reason = _fetch_request_token(
            http,
            connect_url=r0.url,
            api_key=api_key,
        )
        if not request_token:
            return AutoLoginResult(
                success=False,
                failure_reason=token_reason or "no_request_token",
                message="Kite login succeeded but request_token was not found",
            )

    try:
        session = generate_session(
            request_token,
            expected_user_id=settings.KITE_EXPECTED_USER_ID,
            persist=True,
        )
    except ValueError:
        return AutoLoginResult(
            success=False,
            failure_reason="user_mismatch",
            message="Kite user_id did not match expected account",
        )
    except TokenException:
        return AutoLoginResult(
            success=False,
            failure_reason="token_exception",
            message="Kite request_token exchange failed",
        )
    except Exception as exc:
        logger.error("Kite auto-login exchange failed: %s", type(exc).__name__)
        return AutoLoginResult(
            success=False,
            failure_reason="unexpected_error",
            message="Unexpected error during Kite token exchange",
        )

    uid = session.get("user_id")
    access_token = session.get("access_token") or ""
    return AutoLoginResult(
        success=True,
        message="Kite access token generated automatically",
        user_id=str(uid) if uid is not None else None,
        masked_access_token=mask_token(access_token) if access_token else None,
    )
