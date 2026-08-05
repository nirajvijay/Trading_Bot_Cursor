"""
Kite Connect login helper.

Flow:
1. Run `python login.py` and open the printed login URL in a browser.
2. After login, paste the request_token (or full redirect URL) when prompted.
3. Access token (and refresh token, if provided by Kite) are saved to `.env`.

Other commands:
  python login.py --check-token       Validate access token in .env via kite.profile()
  python login.py --request-token TOKEN
"""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import dotenv_values, set_key
from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException

ENV_PATH = Path(__file__).resolve().parent / ".env"


def _require_env(*keys: str) -> dict[str, str]:
    values = dotenv_values(ENV_PATH)
    missing = [key for key in keys if not values.get(key)]
    if missing:
        raise ValueError(
            f"Missing required .env keys: {', '.join(missing)}. "
            f"Add them to {ENV_PATH}"
        )
    return {key: values[key] for key in keys}


def _save_env_vars(updates: dict[str, str]) -> None:
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not ENV_PATH.exists():
        ENV_PATH.touch()

    for key, value in updates.items():
        set_key(str(ENV_PATH), key, value)


def _get_kite(api_key: str | None = None, access_token: str | None = None) -> KiteConnect:
    env = dotenv_values(ENV_PATH)
    key = api_key or env.get("KITE_API_KEY")
    if not key:
        raise ValueError("KITE_API_KEY is not set in .env")

    kite = KiteConnect(api_key=key)
    token = access_token or env.get("KITE_ACCESS_TOKEN")
    if token:
        kite.set_access_token(token)
    return kite


def _save_session_tokens(session: dict) -> None:
    """Save access token and refresh token (if returned) to .env."""
    updates = {"KITE_ACCESS_TOKEN": session["access_token"]}
    refresh_token = session.get("refresh_token")
    if refresh_token:
        updates["KITE_REFRESH_TOKEN"] = refresh_token
    _save_env_vars(updates)


def get_login_url() -> str:
    env = _require_env("KITE_API_KEY")
    return _get_kite(api_key=env["KITE_API_KEY"]).login_url()


def extract_request_token(value: str) -> str:
    """Accept a raw request_token or a full redirect URL."""
    value = value.strip()
    if "request_token=" in value:
        parsed = urlparse(value)
        params = parse_qs(parsed.query)
        token = params.get("request_token", [None])[0]
        if token:
            return token
    return value


def generate_session(request_token: str) -> dict:
    """
    Exchange request_token for access_token and refresh_token.
    Saves tokens to .env (refresh token only if Kite returns one).
    """
    env = _require_env("KITE_API_KEY", "KITE_API_SECRET")
    kite = _get_kite(api_key=env["KITE_API_KEY"])
    request_token = extract_request_token(request_token)

    session = kite.generate_session(
        request_token=request_token,
        api_secret=env["KITE_API_SECRET"],
    )
    _save_session_tokens(session)
    return session


def is_access_token_valid(access_token: str | None = None) -> bool:
    """Return True if the access token is valid (checked via kite.profile())."""
    env = dotenv_values(ENV_PATH)
    token = access_token or env.get("KITE_ACCESS_TOKEN")
    if not token:
        return False

    try:
        _require_env("KITE_API_KEY")
    except ValueError:
        return False

    try:
        kite = _get_kite(access_token=token)
        kite.profile()
        return True
    except TokenException:
        return False


def check_access_token(access_token: str | None = None) -> tuple[bool, str]:
    """Check access token validity via kite.profile() and return (is_valid, message)."""
    env = dotenv_values(ENV_PATH)
    token = access_token or env.get("KITE_ACCESS_TOKEN")
    if not token:
        return False, "KITE_ACCESS_TOKEN is missing from .env"

    try:
        _require_env("KITE_API_KEY")
    except ValueError as exc:
        return False, str(exc)

    try:
        kite = _get_kite(access_token=token)
        profile = kite.profile()
        user_id = profile.get("user_id", "n/a")
        return True, f"Access token is valid (user: {user_id})"
    except TokenException:
        return (
            False,
            "Access token is invalid or expired. Run `python login.py` to get a new token.",
        )


def _mask_token(token: str) -> str:
    if len(token) <= 8:
        return "****"
    return f"{token[:4]}...{token[-4:]}"


def mask_token(token: str) -> str:
    """Public alias for token masking in API responses."""
    return _mask_token(token)


def read_auth_status() -> dict:
    """Read-only auth configuration status with masked token previews only."""
    env = dotenv_values(ENV_PATH)
    api_key = env.get("KITE_API_KEY") or ""
    api_secret = env.get("KITE_API_SECRET") or ""
    access_token = env.get("KITE_ACCESS_TOKEN") or ""
    refresh_token = env.get("KITE_REFRESH_TOKEN") or ""

    return {
        "api_key_configured": bool(api_key),
        "api_secret_configured": bool(api_secret),
        "access_token_present": bool(access_token),
        "refresh_token_present": bool(refresh_token),
        "masked_api_key": mask_token(api_key) if api_key else None,
        "masked_access_token": mask_token(access_token) if access_token else None,
        "masked_refresh_token": mask_token(refresh_token) if refresh_token else None,
    }


def check_access_token_details(
    access_token: str | None = None,
) -> tuple[bool, str, str | None]:
    """Check token validity and return (is_valid, message, user_id)."""
    env = dotenv_values(ENV_PATH)
    token = access_token or env.get("KITE_ACCESS_TOKEN")
    if not token:
        return False, "KITE_ACCESS_TOKEN is missing from .env", None

    try:
        _require_env("KITE_API_KEY")
    except ValueError as exc:
        return False, str(exc), None

    try:
        kite = _get_kite(access_token=token)
        profile = kite.profile()
        user_id = profile.get("user_id")
        uid = str(user_id) if user_id is not None else None
        message = f"Access token is valid (user: {uid or 'n/a'})"
        return True, message, uid
    except TokenException:
        return (
            False,
            "Access token is invalid or expired. Run `python login.py` to get a new token.",
            None,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Kite Connect login and token utilities")
    parser.add_argument(
        "--request-token",
        help="Request token from Kite login redirect (or paste full redirect URL)",
    )
    parser.add_argument(
        "--check-token",
        action="store_true",
        help="Check whether KITE_ACCESS_TOKEN in .env is valid (via kite.profile())",
    )
    args = parser.parse_args()

    if args.check_token:
        valid, message = check_access_token()
        print(message)
        raise SystemExit(0 if valid else 1)

    if args.request_token:
        session = generate_session(args.request_token)
    else:
        login_url = get_login_url()
        print("Open this URL in your browser and complete the Kite login:")
        print(login_url)
        print()
        pasted = input("Paste request_token or full redirect URL: ").strip()
        if not pasted:
            raise SystemExit("No request_token provided.")
        session = generate_session(pasted)

    print("Login successful. Tokens saved to .env")
    print(f"  user_id:       {session.get('user_id', 'n/a')}")
    print(f"  access_token:  {_mask_token(session['access_token'])}")
    if session.get("refresh_token"):
        print(f"  refresh_token: {_mask_token(session['refresh_token'])}")
    else:
        print("  refresh_token: not returned (normal for personal Kite Connect apps)")


if __name__ == "__main__":
    main()
