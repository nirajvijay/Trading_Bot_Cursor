"""Shared helpers for API tests that need website auth fixtures."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

from fastapi.testclient import TestClient

from api.auth import settings
from api.auth.deps import (
    WebAuthContext,
    require_step_up,
    require_web_session,
    require_web_session_mutating,
)
from api.auth.kite_oauth_store import reset_kite_oauth_store_cache
from api.auth.rate_limit import reset_rate_limiter
from api.auth.web_auth_store import (
    SessionRecord,
    reset_web_auth_store_cache,
    get_web_auth_store,
)
from api.main import app


ORIGIN = "http://localhost:5173"
TEST_BASE_URL = "http://127.0.0.1"


def make_test_client() -> TestClient:
    """TestClient with an allowlisted Host (TrustedHostMiddleware rejects testserver)."""
    return TestClient(app, base_url=TEST_BASE_URL)


def disable_web_auth_overrides() -> None:
    """Bypass website auth for legacy endpoint tests."""
    fake = WebAuthContext(
        session=SessionRecord(
            id="test-disabled",
            user_id=0,
            csrf_token="test-csrf",
            created_at="",
            expires_at="",
            last_seen_at="",
            step_up_expires_at=None,
            username="test",
            mfa_enabled=False,
        ),
        auth_disabled=True,
    )
    app.dependency_overrides[require_web_session] = lambda: fake
    app.dependency_overrides[require_web_session_mutating] = lambda: fake
    app.dependency_overrides[require_step_up] = lambda: fake


def clear_auth_overrides() -> None:
    for dep in (require_web_session, require_web_session_mutating, require_step_up):
        app.dependency_overrides.pop(dep, None)


class AuthTestHarness:
    """Temp auth DBs + TestClient with cookie jar."""

    def __init__(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.web_db = self.root / "web_auth.db"
        self.oauth_db = self.root / "kite_oauth.db"
        self.audit = self.root / "audit.log"
        self.secrets = self.root / "kite.env"
        self._prev_env: dict[str, Optional[str]] = {}
        self.client: TestClient
        self.username = "owner"
        self.password = "test-password-123"

    def __enter__(self) -> "AuthTestHarness":
        self._set_env(
            {
                "APP_ENV": "development",
                "WEB_AUTH_ENABLED": "true",
                "WEB_AUTH_MFA_REQUIRED": "false",
                "WEB_AUTH_COOKIE_SECURE": "false",
                "WEB_AUTH_DB_PATH": str(self.web_db),
                "KITE_OAUTH_STATE_DB_PATH": str(self.oauth_db),
                "AUDIT_LOG_PATH": str(self.audit),
                "KITE_SECRETS_PATH": str(self.secrets),
                "KITE_PASTE_LOGIN_ENABLED": "true",
                "WEB_AUTH_ORIGIN_ALLOWLIST": ORIGIN,
                "KITE_API_KEY": "test_api_key",
                "KITE_API_SECRET": "test_api_secret",
            }
        )
        settings.reload_from_environ()
        reset_web_auth_store_cache()
        reset_kite_oauth_store_cache()
        reset_rate_limiter()
        clear_auth_overrides()
        store = get_web_auth_store()
        store.init_db()
        store.create_owner(self.username, self.password)
        self.client = make_test_client()
        return self

    def __exit__(self, *args: object) -> None:
        self.client.close()
        clear_auth_overrides()
        self._restore_env()
        settings.reload_from_environ()
        reset_web_auth_store_cache()
        reset_kite_oauth_store_cache()
        reset_rate_limiter()
        self._tmpdir.cleanup()

    def _set_env(self, values: dict[str, str]) -> None:
        for key, value in values.items():
            self._prev_env[key] = os.environ.get(key)
            os.environ[key] = value

    def _restore_env(self) -> None:
        for key, prev in self._prev_env.items():
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev

    def login(self, totp: Optional[str] = None) -> dict:
        body: dict = {"username": self.username, "password": self.password}
        if totp is not None:
            body["totp"] = totp
        res = self.client.post("/api/v1/account/login", json=body)
        assert res.status_code == 200, res.text
        return res.json()

    def csrf_headers(self) -> dict[str, str]:
        csrf = self.client.cookies.get(settings.CSRF_COOKIE_NAME)
        assert csrf, "CSRF cookie missing after login"
        return {
            settings.CSRF_HEADER_NAME: csrf,
            "Origin": ORIGIN,
        }

    def step_up(self, totp: Optional[str] = None) -> None:
        body: dict = {"password": self.password}
        if totp is not None:
            body["totp"] = totp
        res = self.client.post(
            "/api/v1/account/step-up",
            json=body,
            headers=self.csrf_headers(),
        )
        assert res.status_code == 200, res.text

    @staticmethod
    def extract_state_from_authorize_url(url: str) -> str:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        redirect_params = qs.get("redirect_params", [None])[0]
        assert redirect_params, f"redirect_params missing in {url}"
        decoded = unquote(redirect_params)
        inner = parse_qs(decoded)
        state = inner.get("state", [None])[0]
        assert state, f"state missing in redirect_params={decoded}"
        return state
