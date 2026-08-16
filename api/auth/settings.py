"""Website auth and Kite OAuth settings (env-configurable paths + flags).

Production notes:
  - Enabling WEB_AUTH_MFA_REQUIRED (or flipping MFA enforcement) requires an
    API restart. There is no runtime config-reload API; wipe existing sessions
    after enabling MFA enforcement (or rely on MFA confirm / password change
    which already revoke all sessions).
  - CORS and CSRF share WEB_AUTH_ORIGIN_ALLOWLIST as the single canonical origin list.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


DATA_ROOT = Path(
    os.environ.get("NIFTY_RADAR_DATA_ROOT", "/opt/nifty-radar/data")
).expanduser()
SECRETS_ROOT = Path(
    os.environ.get("NIFTY_RADAR_SECRETS_ROOT", "/opt/nifty-radar/secrets")
).expanduser()

WEB_AUTH_DB_PATH = Path(
    os.environ.get("WEB_AUTH_DB_PATH", str(DATA_ROOT / "auth" / "web_auth.db"))
).expanduser()
KITE_OAUTH_STATE_DB_PATH = Path(
    os.environ.get(
        "KITE_OAUTH_STATE_DB_PATH",
        str(DATA_ROOT / "kite-oauth" / "state.db"),
    )
).expanduser()
AUDIT_LOG_PATH = Path(
    os.environ.get("AUDIT_LOG_PATH", str(DATA_ROOT / "auth" / "audit.log"))
).expanduser()
KITE_SECRETS_PATH = Path(
    os.environ.get("KITE_SECRETS_PATH", str(SECRETS_ROOT / "kite.env"))
).expanduser()

APP_ENV = (os.environ.get("APP_ENV") or "development").strip().lower()
WEB_AUTH_ENABLED = _env_bool("WEB_AUTH_ENABLED", True)
WEB_AUTH_MFA_REQUIRED = _env_bool(
    "WEB_AUTH_MFA_REQUIRED",
    APP_ENV == "production",
)
WEB_AUTH_COOKIE_SECURE = _env_bool(
    "WEB_AUTH_COOKIE_SECURE",
    APP_ENV == "production",
)
WEB_AUTH_ORIGIN_ALLOWLIST = tuple(
    origin.strip()
    for origin in (
        os.environ.get(
            "WEB_AUTH_ORIGIN_ALLOWLIST",
            "http://localhost:5173,http://127.0.0.1:5173,"
            "http://localhost:8000,http://127.0.0.1:8000",
        )
    ).split(",")
    if origin.strip()
)

SESSION_TTL_SECONDS = _env_int("WEB_AUTH_SESSION_TTL_SECONDS", 60 * 60 * 12)
STEP_UP_TTL_SECONDS = _env_int("WEB_AUTH_STEP_UP_TTL_SECONDS", 60 * 10)
KITE_OAUTH_TTL_SECONDS = _env_int("KITE_OAUTH_TTL_SECONDS", 60 * 10)

KITE_PASTE_LOGIN_ENABLED = _env_bool(
    "KITE_PASTE_LOGIN_ENABLED",
    APP_ENV != "production",
)
KITE_EXPECTED_USER_ID = (os.environ.get("KITE_EXPECTED_USER_ID") or "").strip() or None
KITE_SUCCESS_REDIRECT_PATH = (
    os.environ.get("KITE_SUCCESS_REDIRECT_PATH") or "/?kite=connected"
).strip()
KITE_FAILURE_REDIRECT_PATH = (
    os.environ.get("KITE_FAILURE_REDIRECT_PATH") or "/?kite=error"
).strip()

SESSION_COOKIE_NAME = "nr_session"
CSRF_COOKIE_NAME = "nr_csrf"
KITE_OAUTH_COOKIE_NAME = "nr_kite_oauth"
CSRF_HEADER_NAME = "X-CSRF-Token"


def _is_localhost_origin(origin: str) -> bool:
    try:
        host = (urlparse(origin).hostname or "").lower()
    except ValueError:
        return True
    return host in {"localhost", "127.0.0.1", "::1"}


def cors_origins() -> list[str]:
    """CORS allow_origins — same source as CSRF Origin allowlist."""
    return list(WEB_AUTH_ORIGIN_ALLOWLIST)


def validate_startup_settings() -> None:
    """Refuse unsafe production configuration."""
    if APP_ENV != "production":
        return
    if not WEB_AUTH_ENABLED:
        raise RuntimeError(
            "WEB_AUTH_ENABLED=false is forbidden when APP_ENV=production"
        )
    if not WEB_AUTH_MFA_REQUIRED:
        raise RuntimeError(
            "WEB_AUTH_MFA_REQUIRED must be true when APP_ENV=production"
        )
    if not KITE_EXPECTED_USER_ID:
        raise RuntimeError(
            "KITE_EXPECTED_USER_ID must be set when APP_ENV=production"
        )
    if not WEB_AUTH_COOKIE_SECURE:
        raise RuntimeError(
            "WEB_AUTH_COOKIE_SECURE must be true when APP_ENV=production"
        )
    origins = cors_origins()
    if not origins:
        raise RuntimeError(
            "WEB_AUTH_ORIGIN_ALLOWLIST must be a non-empty https allowlist "
            "when APP_ENV=production"
        )
    if any(_is_localhost_origin(o) for o in origins):
        raise RuntimeError(
            "localhost/127.0.0.1 origins are forbidden in production "
            "WEB_AUTH_ORIGIN_ALLOWLIST"
        )
    non_https = [o for o in origins if not o.lower().startswith("https://")]
    if non_https:
        raise RuntimeError(
            "production WEB_AUTH_ORIGIN_ALLOWLIST entries must use https:// "
            f"(got: {non_https[0]!r})"
        )


def ensure_parent_dir(path: Path, mode: int = 0o700) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, mode)
    except OSError:
        pass


def reload_from_environ() -> None:
    """Re-read mutable settings from the process environment (tests)."""
    global DATA_ROOT, SECRETS_ROOT
    global WEB_AUTH_DB_PATH, KITE_OAUTH_STATE_DB_PATH, AUDIT_LOG_PATH, KITE_SECRETS_PATH
    global APP_ENV, WEB_AUTH_ENABLED, WEB_AUTH_MFA_REQUIRED, WEB_AUTH_COOKIE_SECURE
    global WEB_AUTH_ORIGIN_ALLOWLIST, SESSION_TTL_SECONDS, STEP_UP_TTL_SECONDS
    global KITE_OAUTH_TTL_SECONDS, KITE_PASTE_LOGIN_ENABLED, KITE_EXPECTED_USER_ID
    global KITE_SUCCESS_REDIRECT_PATH, KITE_FAILURE_REDIRECT_PATH

    DATA_ROOT = Path(
        os.environ.get("NIFTY_RADAR_DATA_ROOT", "/opt/nifty-radar/data")
    ).expanduser()
    SECRETS_ROOT = Path(
        os.environ.get("NIFTY_RADAR_SECRETS_ROOT", "/opt/nifty-radar/secrets")
    ).expanduser()
    WEB_AUTH_DB_PATH = Path(
        os.environ.get("WEB_AUTH_DB_PATH", str(DATA_ROOT / "auth" / "web_auth.db"))
    ).expanduser()
    KITE_OAUTH_STATE_DB_PATH = Path(
        os.environ.get(
            "KITE_OAUTH_STATE_DB_PATH",
            str(DATA_ROOT / "kite-oauth" / "state.db"),
        )
    ).expanduser()
    AUDIT_LOG_PATH = Path(
        os.environ.get("AUDIT_LOG_PATH", str(DATA_ROOT / "auth" / "audit.log"))
    ).expanduser()
    KITE_SECRETS_PATH = Path(
        os.environ.get("KITE_SECRETS_PATH", str(SECRETS_ROOT / "kite.env"))
    ).expanduser()
    APP_ENV = (os.environ.get("APP_ENV") or "development").strip().lower()
    WEB_AUTH_ENABLED = _env_bool("WEB_AUTH_ENABLED", True)
    WEB_AUTH_MFA_REQUIRED = _env_bool(
        "WEB_AUTH_MFA_REQUIRED",
        APP_ENV == "production",
    )
    WEB_AUTH_COOKIE_SECURE = _env_bool(
        "WEB_AUTH_COOKIE_SECURE",
        APP_ENV == "production",
    )
    WEB_AUTH_ORIGIN_ALLOWLIST = tuple(
        origin.strip()
        for origin in (
            os.environ.get(
                "WEB_AUTH_ORIGIN_ALLOWLIST",
                "http://localhost:5173,http://127.0.0.1:5173,"
                "http://localhost:8000,http://127.0.0.1:8000",
            )
        ).split(",")
        if origin.strip()
    )
    SESSION_TTL_SECONDS = _env_int("WEB_AUTH_SESSION_TTL_SECONDS", 60 * 60 * 12)
    STEP_UP_TTL_SECONDS = _env_int("WEB_AUTH_STEP_UP_TTL_SECONDS", 60 * 10)
    KITE_OAUTH_TTL_SECONDS = _env_int("KITE_OAUTH_TTL_SECONDS", 60 * 10)
    KITE_PASTE_LOGIN_ENABLED = _env_bool(
        "KITE_PASTE_LOGIN_ENABLED",
        APP_ENV != "production",
    )
    KITE_EXPECTED_USER_ID = (os.environ.get("KITE_EXPECTED_USER_ID") or "").strip() or None
    KITE_SUCCESS_REDIRECT_PATH = (
        os.environ.get("KITE_SUCCESS_REDIRECT_PATH") or "/?kite=connected"
    ).strip()
    KITE_FAILURE_REDIRECT_PATH = (
        os.environ.get("KITE_FAILURE_REDIRECT_PATH") or "/?kite=error"
    ).strip()
