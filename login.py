"""
Kite Connect login helper.

Flow:
1. Run `python login.py` and open the printed login URL in a browser.
2. After login, paste the request_token (or full redirect URL) when prompted.
3. Access token (and refresh token, if provided by Kite) are saved via the secrets store
   (KITE_SECRETS_PATH, default /opt/nifty-radar/secrets/kite.env) with legacy .env fallback.

Other commands:
  python login.py --check-token       Validate access token via kite.profile()
  python login.py --request-token TOKEN
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import dotenv_values
from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException

ROOT = Path(__file__).resolve().parent
LEGACY_ENV_PATH = ROOT / ".env"


def _secrets_path() -> Path:
    try:
        from api.auth import settings

        return settings.KITE_SECRETS_PATH
    except Exception:
        return Path(
            os.environ.get(
                "KITE_SECRETS_PATH",
                "/opt/nifty-radar/secrets/kite.env",
            )
        )


_KITE_CREDENTIAL_KEYS = (
    "KITE_API_KEY",
    "KITE_API_SECRET",
    "KITE_ACCESS_TOKEN",
    "KITE_REFRESH_TOKEN",
    "KITE_EXPECTED_USER_ID",
)


def _apply_process_env(merged: dict[str, str]) -> None:
    for key in _KITE_CREDENTIAL_KEYS:
        if os.environ.get(key):
            merged[key] = os.environ[key]


def _load_env_file(path: Path) -> tuple[str, dict[str, str]]:
    """Load dotenv values by actually reading file contents.

    Returns (state, values) where state is 'missing' | 'loaded' | 'unreadable'.
    Metadata-only checks are insufficient: exists() can succeed while open() fails.
    """
    try:
        exists = path.exists()
    except PermissionError:
        return "unreadable", {}
    if not exists:
        return "missing", {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = handle.read()
    except PermissionError:
        return "unreadable", {}
    except OSError:
        return "unreadable", {}
    from io import StringIO

    values: dict[str, str] = {}
    for key, value in dotenv_values(stream=StringIO(raw)).items():
        if key and value is not None:
            values[key] = value
    return "loaded", values


def _read_env_context() -> tuple[dict[str, str], str | None]:
    """Return (merged values, unreadable_secrets_path_or_None).

    Unreadable configured secrets: discard any legacy credentials and keep only
    explicit process-env overrides. No module-global error latch between calls.
    """
    secrets = _secrets_path()
    secrets_state, secrets_values = _load_env_file(secrets)
    if secrets_state == "unreadable":
        merged: dict[str, str] = {}
        _apply_process_env(merged)
        return merged, str(secrets)

    merged = {}
    if secrets_state == "missing":
        legacy_state, legacy_values = _load_env_file(LEGACY_ENV_PATH)
        if legacy_state == "loaded":
            merged.update(legacy_values)
    else:
        legacy_state, legacy_values = _load_env_file(LEGACY_ENV_PATH)
        if legacy_state == "loaded":
            merged.update(legacy_values)
        merged.update(secrets_values)
    _apply_process_env(merged)
    return merged, None


def _read_env_merged() -> dict[str, str]:
    """Merge credential sources with retained precedence.

    Precedence when the configured secrets file loads successfully:
    legacy project ``.env`` (underlay) → secrets store → process-env overrides.

    When the secrets file is missing: legacy ``.env`` (if present) → process-env.
    When the secrets file is unreadable: process-env only (no legacy fallback).
    """
    values, _unreadable = _read_env_context()
    return values


def _require_env(*keys: str) -> dict[str, str]:
    values, unreadable = _read_env_context()
    missing = [key for key in keys if not values.get(key)]
    if unreadable and missing:
        raise ValueError(
            f"Kite secrets store unreadable at {unreadable}; "
            f"missing {', '.join(missing)}. "
            "Refusing legacy .env credential fallback. "
            "Run as the service account that can read the secrets store, "
            "or set the required keys explicitly in the process environment."
        )
    if missing:
        raise ValueError(
            f"Missing required keys: {', '.join(missing)}. "
            f"Set them in {_secrets_path()} (or legacy {LEGACY_ENV_PATH})"
        )
    return {key: values[key] for key in keys}


def _save_env_vars(updates: dict[str, str]) -> None:
    try:
        from api.auth.secrets_store import write_secrets_atomic

        write_secrets_atomic(updates)
        return
    except Exception:
        # Fallback for CLI use before api package paths are ready.
        pass

    from dotenv import set_key

    path = _secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()
        try:
            path.chmod(0o600)
        except OSError:
            pass
    for key, value in updates.items():
        set_key(str(path), key, value)


def _get_kite(api_key: str | None = None, access_token: str | None = None) -> KiteConnect:
    env = _read_env_merged()
    key = api_key or env.get("KITE_API_KEY")
    if not key:
        raise ValueError("KITE_API_KEY is not set")

    kite = KiteConnect(api_key=key)
    token = access_token or env.get("KITE_ACCESS_TOKEN")
    if token:
        kite.set_access_token(token)
    return kite


def _save_session_tokens(session: dict) -> None:
    """Save access token and refresh token (if returned) via secrets store."""
    updates = {"KITE_ACCESS_TOKEN": session["access_token"]}
    refresh_token = session.get("refresh_token")
    if refresh_token:
        updates["KITE_REFRESH_TOKEN"] = refresh_token
    _save_env_vars(updates)


def get_login_url() -> str:
    env = _require_env("KITE_API_KEY")
    return _get_kite(api_key=env["KITE_API_KEY"]).login_url()


def build_authorize_url_with_state(opaque_state: str) -> str:
    """Kite login URL with redirect_params so callback receives state=<opaque>."""
    from urllib.parse import quote

    base = get_login_url()
    redirect_params = quote(f"state={opaque_state}", safe="")
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}redirect_params={redirect_params}"


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


def generate_session(
    request_token: str,
    *,
    expected_user_id: str | None = None,
    persist: bool = True,
) -> dict:
    """
    Exchange request_token for access_token and refresh_token.

    If expected_user_id is set and the session user_id mismatches, raise ValueError
    without writing tokens. When persist=False, return session without writing.
    """
    env = _require_env("KITE_API_KEY", "KITE_API_SECRET")
    kite = _get_kite(api_key=env["KITE_API_KEY"])
    request_token = extract_request_token(request_token)

    session = kite.generate_session(
        request_token=request_token,
        api_secret=env["KITE_API_SECRET"],
    )
    user_id = session.get("user_id")
    uid = str(user_id) if user_id is not None else None
    expected = expected_user_id
    if expected is None:
        expected = _read_env_merged().get("KITE_EXPECTED_USER_ID") or None
    if expected and uid != expected:
        raise ValueError(
            f"Kite user_id mismatch: expected {expected}, got {uid or 'n/a'}"
        )
    if persist:
        _save_session_tokens(session)
    return session


def is_access_token_valid(access_token: str | None = None) -> bool:
    """Return True if the access token is valid (checked via kite.profile())."""
    env = _read_env_merged()
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
    env, unreadable = _read_env_context()
    token = access_token or env.get("KITE_ACCESS_TOKEN")
    if unreadable and not token:
        return (
            False,
            f"Kite secrets store unreadable at {unreadable}; "
            "KITE_ACCESS_TOKEN is missing. Refusing legacy .env credential fallback.",
        )
    if not token:
        return False, "KITE_ACCESS_TOKEN is missing"

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
    env = _read_env_merged()
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
    env, unreadable = _read_env_context()
    token = access_token or env.get("KITE_ACCESS_TOKEN")
    if unreadable and not token:
        return (
            False,
            f"Kite secrets store unreadable at {unreadable}; "
            "KITE_ACCESS_TOKEN is missing. Refusing legacy .env credential fallback.",
            None,
        )
    if not token:
        return False, "KITE_ACCESS_TOKEN is missing", None

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
        help="Check whether KITE_ACCESS_TOKEN is valid (via kite.profile())",
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

    print(f"Login successful. Tokens saved to {_secrets_path()}")
    print(f"  user_id:       {session.get('user_id', 'n/a')}")
    print(f"  access_token:  {_mask_token(session['access_token'])}")
    if session.get("refresh_token"):
        print(f"  refresh_token: {_mask_token(session['refresh_token'])}")
    else:
        print("  refresh_token: not returned (normal for personal Kite Connect apps)")


if __name__ == "__main__":
    main()
