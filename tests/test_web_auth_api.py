"""Tests for website auth, MFA/step-up, and remote Kite OAuth callback."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch
import pyotp
from starlette.responses import Response as StarletteResponse

from api.auth import settings
from api.auth.rate_limit import LOGIN_MAX_FAILURES, MFA_VERIFY_MAX_FAILURES, reset_rate_limiter
from api.auth.secrets_store import read_secrets
from api.auth.settings import validate_startup_settings
from api.auth.web_auth_store import get_web_auth_store
from api.main import app
from api.routers import auth as auth_router
from api.routers.account import _clear_session_cookies
from tests.auth_test_helpers import (
    AuthTestHarness,
    clear_auth_overrides,
    disable_web_auth_overrides,
    make_test_client,
)


def _set_prod_env(**overrides: str) -> dict[str, object]:
    """Set production-like env vars; return previous values for restore."""
    defaults = {
        "APP_ENV": "production",
        "WEB_AUTH_ENABLED": "true",
        "WEB_AUTH_MFA_REQUIRED": "true",
        "WEB_AUTH_COOKIE_SECURE": "true",
        "KITE_EXPECTED_USER_ID": "AB1234",
        "WEB_AUTH_ORIGIN_ALLOWLIST": "https://radar.example.com",
    }
    defaults.update(overrides)
    prev: dict[str, object] = {}
    for key, value in defaults.items():
        prev[key] = os.environ.get(key)
        os.environ[key] = value
    settings.reload_from_environ()
    return prev


def _restore_env(prev: dict[str, object]) -> None:
    for key, value in prev.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = str(value)
    settings.reload_from_environ()


class ProductionGuardTests(unittest.TestCase):
    def test_production_forbids_disabled_web_auth(self) -> None:
        prev = _set_prod_env(WEB_AUTH_ENABLED="false")
        try:
            with self.assertRaises(RuntimeError) as ctx:
                validate_startup_settings()
            self.assertIn("WEB_AUTH_ENABLED", str(ctx.exception))
        finally:
            _restore_env(prev)

    def test_production_requires_mfa(self) -> None:
        prev = _set_prod_env(WEB_AUTH_MFA_REQUIRED="false")
        try:
            with self.assertRaises(RuntimeError) as ctx:
                validate_startup_settings()
            self.assertIn("WEB_AUTH_MFA_REQUIRED", str(ctx.exception))
        finally:
            _restore_env(prev)

    def test_production_requires_expected_user_id(self) -> None:
        prev = _set_prod_env()
        try:
            os.environ["KITE_EXPECTED_USER_ID"] = ""
            settings.reload_from_environ()
            with self.assertRaises(RuntimeError) as ctx:
                validate_startup_settings()
            self.assertIn("KITE_EXPECTED_USER_ID", str(ctx.exception))
        finally:
            _restore_env(prev)

    def test_production_requires_secure_cookies(self) -> None:
        prev = _set_prod_env(WEB_AUTH_COOKIE_SECURE="false")
        try:
            with self.assertRaises(RuntimeError) as ctx:
                validate_startup_settings()
            self.assertIn("WEB_AUTH_COOKIE_SECURE", str(ctx.exception))
        finally:
            _restore_env(prev)

    def test_production_forbids_localhost_cors(self) -> None:
        prev = _set_prod_env(
            WEB_AUTH_ORIGIN_ALLOWLIST="http://localhost:5173,https://radar.example.com"
        )
        try:
            with self.assertRaises(RuntimeError) as ctx:
                validate_startup_settings()
            self.assertIn("localhost", str(ctx.exception).lower())
        finally:
            _restore_env(prev)

    def test_production_requires_https_origins(self) -> None:
        prev = _set_prod_env(WEB_AUTH_ORIGIN_ALLOWLIST="http://radar.example.com")
        try:
            with self.assertRaises(RuntimeError) as ctx:
                validate_startup_settings()
            self.assertIn("https", str(ctx.exception).lower())
        finally:
            _restore_env(prev)

    def test_production_ok_with_https_allowlist(self) -> None:
        prev = _set_prod_env()
        try:
            validate_startup_settings()  # must not raise
            self.assertEqual(settings.cors_origins(), ["https://radar.example.com"])
        finally:
            _restore_env(prev)


class HealthPublicTests(unittest.TestCase):
    def test_health_public_without_session(self) -> None:
        with AuthTestHarness() as h:
            res = h.client.get("/api/v1/health")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()["status"], "ok")


class WebsiteAuthTests(unittest.TestCase):
    def test_unauthenticated_private_api_401(self) -> None:
        with AuthTestHarness() as h:
            res = h.client.get("/api/v1/sessions")
            self.assertEqual(res.status_code, 401)

    def test_login_logout_session(self) -> None:
        with AuthTestHarness() as h:
            h.login()
            me = h.client.get("/api/v1/account/me")
            self.assertEqual(me.status_code, 200)
            self.assertEqual(me.json()["username"], "owner")
            logout = h.client.post(
                "/api/v1/account/logout",
                headers=h.csrf_headers(),
            )
            self.assertEqual(logout.status_code, 200)
            me2 = h.client.get("/api/v1/account/me")
            self.assertEqual(me2.status_code, 401)

    def test_invalid_password(self) -> None:
        with AuthTestHarness() as h:
            res = h.client.post(
                "/api/v1/account/login",
                json={"username": "owner", "password": "wrong-password"},
            )
            self.assertEqual(res.status_code, 401)

    def test_login_rate_limit_429(self) -> None:
        with AuthTestHarness() as h:
            reset_rate_limiter()
            for _ in range(LOGIN_MAX_FAILURES):
                res = h.client.post(
                    "/api/v1/account/login",
                    json={"username": "owner", "password": "wrong-password"},
                )
                self.assertEqual(res.status_code, 401)
            limited = h.client.post(
                "/api/v1/account/login",
                json={"username": "owner", "password": "wrong-password"},
            )
            self.assertEqual(limited.status_code, 429)
            self.assertIn("Too many", limited.json()["detail"])
            # Successful login after reset still works for a different bucket after wipe.
            reset_rate_limiter()
            ok = h.client.post(
                "/api/v1/account/login",
                json={"username": h.username, "password": h.password},
            )
            self.assertEqual(ok.status_code, 200)

    def test_mfa_setup_and_confirm_require_csrf(self) -> None:
        with AuthTestHarness() as h:
            h.login()
            no_csrf = h.client.post("/api/v1/account/mfa/setup", json={})
            self.assertEqual(no_csrf.status_code, 403)
            setup = h.client.post(
                "/api/v1/account/mfa/setup",
                json={},
                headers=h.csrf_headers(),
            )
            self.assertEqual(setup.status_code, 200)
            body = setup.json()
            self.assertIn("otpauth_uri", body)
            self.assertTrue(body["secret"])
            self.assertTrue(body["otpauth_uri"].startswith("otpauth://"))

            no_csrf_confirm = h.client.post(
                "/api/v1/account/mfa/confirm",
                json={"totp": "000000"},
            )
            self.assertEqual(no_csrf_confirm.status_code, 403)

            bad_origin = h.client.post(
                "/api/v1/account/mfa/setup",
                json={},
                headers={
                    **h.csrf_headers(),
                    "Origin": "https://evil.example",
                },
            )
            self.assertEqual(bad_origin.status_code, 403)

    def test_mfa_confirm_rate_limit_429(self) -> None:
        with AuthTestHarness() as h:
            h.login()
            setup = h.client.post(
                "/api/v1/account/mfa/setup",
                headers=h.csrf_headers(),
            )
            self.assertEqual(setup.status_code, 200)
            reset_rate_limiter()
            for _ in range(MFA_VERIFY_MAX_FAILURES):
                bad = h.client.post(
                    "/api/v1/account/mfa/confirm",
                    json={"totp": "000000"},
                    headers=h.csrf_headers(),
                )
                self.assertEqual(bad.status_code, 401)
            limited = h.client.post(
                "/api/v1/account/mfa/confirm",
                json={"totp": "000000"},
                headers=h.csrf_headers(),
            )
            self.assertEqual(limited.status_code, 429)

    def test_password_change_revokes_other_sessions(self) -> None:
        with AuthTestHarness() as h:
            h.login()
            other = make_test_client()
            other_login = other.post(
                "/api/v1/account/login",
                json={"username": h.username, "password": h.password},
            )
            self.assertEqual(other_login.status_code, 200)
            self.assertEqual(other.get("/api/v1/account/me").status_code, 200)

            changed = h.client.post(
                "/api/v1/account/change-password",
                json={
                    "current_password": h.password,
                    "new_password": "new-password-456",
                },
                headers=h.csrf_headers(),
            )
            self.assertEqual(changed.status_code, 200)
            # Other session must die.
            self.assertEqual(other.get("/api/v1/account/me").status_code, 401)
            # Current client cookies cleared → also unauthenticated.
            self.assertEqual(h.client.get("/api/v1/account/me").status_code, 401)
            # New password works.
            h.password = "new-password-456"
            h.login()
            self.assertEqual(h.client.get("/api/v1/account/me").status_code, 200)

    def test_mfa_confirm_revokes_sessions_and_requires_relogin(self) -> None:
        with AuthTestHarness() as h:
            h.login()
            other = make_test_client()
            self.assertEqual(
                other.post(
                    "/api/v1/account/login",
                    json={"username": h.username, "password": h.password},
                ).status_code,
                200,
            )
            setup = h.client.post(
                "/api/v1/account/mfa/setup",
                headers=h.csrf_headers(),
            )
            secret = setup.json()["secret"]
            confirm = h.client.post(
                "/api/v1/account/mfa/confirm",
                json={"totp": pyotp.TOTP(secret).now()},
                headers=h.csrf_headers(),
            )
            self.assertEqual(confirm.status_code, 200)
            self.assertIn("sign in again", confirm.json()["message"].lower())
            self.assertEqual(h.client.get("/api/v1/account/me").status_code, 401)
            self.assertEqual(other.get("/api/v1/account/me").status_code, 401)
            # Re-login requires TOTP.
            no_totp = h.client.post(
                "/api/v1/account/login",
                json={"username": h.username, "password": h.password},
            )
            self.assertEqual(no_totp.status_code, 401)
            ok = h.client.post(
                "/api/v1/account/login",
                json={
                    "username": h.username,
                    "password": h.password,
                    "totp": pyotp.TOTP(secret).now(),
                },
            )
            self.assertEqual(ok.status_code, 200)
            self.assertTrue(ok.json()["mfa_enabled"])

    def test_clear_mfa_deletes_all_sessions(self) -> None:
        with AuthTestHarness() as h:
            h.login()
            store = get_web_auth_store()
            store.create_session(1)
            store.clear_mfa()
            with store.connect() as conn:
                count = conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"]
            self.assertEqual(count, 0)

    def test_mfa_login_when_enabled(self) -> None:
        with AuthTestHarness() as h:
            h.login()
            setup = h.client.post(
                "/api/v1/account/mfa/setup",
                headers=h.csrf_headers(),
            )
            self.assertEqual(setup.status_code, 200)
            secret = setup.json()["secret"]
            code = pyotp.TOTP(secret).now()
            confirm = h.client.post(
                "/api/v1/account/mfa/confirm",
                json={"totp": code},
                headers=h.csrf_headers(),
            )
            self.assertEqual(confirm.status_code, 200)
            # Sessions revoked + cookies cleared on confirm — no explicit logout.

            no_totp = h.client.post(
                "/api/v1/account/login",
                json={"username": h.username, "password": h.password},
            )
            self.assertEqual(no_totp.status_code, 401)

            ok = h.client.post(
                "/api/v1/account/login",
                json={
                    "username": h.username,
                    "password": h.password,
                    "totp": pyotp.TOTP(secret).now(),
                },
            )
            self.assertEqual(ok.status_code, 200)
            self.assertTrue(ok.json()["mfa_enabled"])

    def test_logout_cookie_clear_attrs_match_set(self) -> None:
        """delete_cookie uses same path/secure/samesite/httponly as set_cookie."""
        secure = settings.WEB_AUTH_COOKIE_SECURE
        clear_resp = StarletteResponse()
        _clear_session_cookies(clear_resp)

        headers = clear_resp.headers.getlist("set-cookie")
        by_name = {raw.split("=", 1)[0]: raw.lower() for raw in headers}
        for name in (settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME):
            self.assertIn(name, by_name)
            raw = by_name[name]
            self.assertIn("path=/", raw)
            self.assertIn("samesite=lax", raw)
            if name == settings.SESSION_COOKIE_NAME:
                self.assertIn("httponly", raw)
            if secure:
                self.assertIn("secure", raw)

        with AuthTestHarness() as h:
            h.login()
            logout = h.client.post("/api/v1/account/logout", headers=h.csrf_headers())
            self.assertEqual(logout.status_code, 200)
            set_cookies = logout.headers.get_list("set-cookie")
            joined = " ".join(set_cookies).lower()
            self.assertIn(settings.SESSION_COOKIE_NAME.lower(), joined)
            self.assertIn(settings.CSRF_COOKIE_NAME.lower(), joined)
            self.assertIn("path=/", joined)


class CheckTokenCsrfTests(unittest.TestCase):
    @patch("api.routers.auth.check_access_token_details")
    def test_check_token_requires_csrf(self, mock_check) -> None:
        mock_check.return_value = (True, "ok", "AB1234")
        with AuthTestHarness() as h:
            h.login()
            missing = h.client.post("/api/v1/auth/check-token")
            self.assertEqual(missing.status_code, 403)
            ok = h.client.post("/api/v1/auth/check-token", headers=h.csrf_headers())
            self.assertEqual(ok.status_code, 200)
            self.assertTrue(ok.json()["valid"])


class KiteStartStepUpTests(unittest.TestCase):
    @patch("api.routers.auth.build_authorize_url_with_state")
    def test_kite_start_requires_step_up(self, mock_url) -> None:
        mock_url.side_effect = lambda state: (
            f"https://kite.zerodha.com/connect/login?api_key=x&v=3"
            f"&redirect_params=state%3D{state}"
        )
        with AuthTestHarness() as h:
            h.login()
            res = h.client.post(
                "/api/v1/auth/kite/start",
                headers=h.csrf_headers(),
            )
            self.assertEqual(res.status_code, 403)
            self.assertIn("Step-up", res.json()["detail"])

            h.step_up()
            ok = h.client.post(
                "/api/v1/auth/kite/start",
                headers=h.csrf_headers(),
            )
            self.assertEqual(ok.status_code, 200)
            url = ok.json()["authorize_url"]
            self.assertIn("redirect_params", url)
            state = AuthTestHarness.extract_state_from_authorize_url(url)
            self.assertTrue(len(state) > 10)


class KiteCallbackTests(unittest.TestCase):
    def _start_oauth(self, h: AuthTestHarness) -> str:
        h.login()
        h.step_up()

        def fake_url(state: str) -> str:
            return (
                "https://kite.zerodha.com/connect/login?api_key=x&v=3"
                f"&redirect_params=state%3D{state}"
            )

        with patch("api.routers.auth.build_authorize_url_with_state", side_effect=fake_url):
            res = h.client.post(
                "/api/v1/auth/kite/start",
                headers=h.csrf_headers(),
            )
        self.assertEqual(res.status_code, 200)
        return AuthTestHarness.extract_state_from_authorize_url(res.json()["authorize_url"])

    @patch("api.routers.auth.generate_session")
    def test_callback_valid_state_success(self, mock_gen) -> None:
        mock_gen.return_value = {
            "access_token": "access_token_abcdefghijklmnopqrstuvwxyz",
            "user_id": "AB1234",
        }
        with AuthTestHarness() as h:
            state = self._start_oauth(h)
            res = h.client.get(
                "/api/v1/auth/callback",
                params={
                    "status": "success",
                    "request_token": "req_token_value",
                    "state": state,
                },
                follow_redirects=False,
            )
            self.assertEqual(res.status_code, 303)
            self.assertIn("kite=connected", res.headers["location"])
            mock_gen.assert_called_once()

    def test_callback_missing_state(self) -> None:
        with AuthTestHarness() as h:
            self._start_oauth(h)
            res = h.client.get(
                "/api/v1/auth/callback",
                params={"status": "success", "request_token": "req"},
                follow_redirects=False,
            )
            self.assertEqual(res.status_code, 303)
            self.assertIn("kite=error", res.headers["location"])

    def test_callback_forged_state(self) -> None:
        with AuthTestHarness() as h:
            self._start_oauth(h)
            res = h.client.get(
                "/api/v1/auth/callback",
                params={
                    "status": "success",
                    "request_token": "req",
                    "state": "forged-state-value-not-real",
                },
                follow_redirects=False,
            )
            self.assertEqual(res.status_code, 303)
            self.assertIn("kite=error", res.headers["location"])

    @patch("api.routers.auth.generate_session")
    def test_callback_replayed_state(self, mock_gen) -> None:
        mock_gen.return_value = {
            "access_token": "access_token_abcdefghijklmnopqrstuvwxyz",
            "user_id": "AB1234",
        }
        with AuthTestHarness() as h:
            state = self._start_oauth(h)
            first = h.client.get(
                "/api/v1/auth/callback",
                params={
                    "status": "success",
                    "request_token": "req1",
                    "state": state,
                },
                follow_redirects=False,
            )
            self.assertIn("kite=connected", first.headers["location"])
            # Re-issue oauth cookie by starting again would replace state;
            # replay the same state without a fresh cookie binding.
            second = h.client.get(
                "/api/v1/auth/callback",
                params={
                    "status": "success",
                    "request_token": "req2",
                    "state": state,
                },
                follow_redirects=False,
            )
            self.assertIn("kite=error", second.headers["location"])
            self.assertEqual(mock_gen.call_count, 1)

    @patch("api.routers.auth.generate_session")
    def test_callback_cross_session_state(self, mock_gen) -> None:
        mock_gen.return_value = {
            "access_token": "access_token_abcdefghijklmnopqrstuvwxyz",
            "user_id": "AB1234",
        }
        with AuthTestHarness() as h:
            state = self._start_oauth(h)
            oauth_cookie = h.client.cookies.get(settings.KITE_OAUTH_COOKIE_NAME)
            # New website session (different owner session id).
            h.client.cookies.clear()
            h.login()
            # Restore only the oauth cookie from the other session.
            if oauth_cookie:
                h.client.cookies.set(
                    settings.KITE_OAUTH_COOKIE_NAME,
                    oauth_cookie,
                    path="/api/v1/auth/callback",
                )
            res = h.client.get(
                "/api/v1/auth/callback",
                params={
                    "status": "success",
                    "request_token": "req",
                    "state": state,
                },
                follow_redirects=False,
            )
            self.assertIn("kite=error", res.headers["location"])
            mock_gen.assert_not_called()

    @patch("api.routers.auth.generate_session")
    def test_callback_expected_user_id_mismatch(self, mock_gen) -> None:
        mock_gen.side_effect = ValueError("Kite user_id mismatch: expected AB1234, got ZZ9999")
        with AuthTestHarness() as h:
            os.environ["KITE_EXPECTED_USER_ID"] = "AB1234"
            settings.reload_from_environ()
            # Seed an existing token that must not be overwritten.
            h.secrets.write_text("KITE_ACCESS_TOKEN=keep_me_token_value\n", encoding="utf-8")
            state = self._start_oauth(h)
            res = h.client.get(
                "/api/v1/auth/callback",
                params={
                    "status": "success",
                    "request_token": "req",
                    "state": state,
                },
                follow_redirects=False,
            )
            self.assertIn("kite=error", res.headers["location"])
            self.assertEqual(read_secrets(h.secrets).get("KITE_ACCESS_TOKEN"), "keep_me_token_value")


class LegacyAuthApiTests(unittest.TestCase):
    """Existing Kite auth API coverage with website-auth overrides."""

    def setUp(self) -> None:
        disable_web_auth_overrides()
        app.dependency_overrides[auth_router.require_localhost] = lambda: None
        self.client = make_test_client()

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        clear_auth_overrides()

    @patch("api.routers.auth.read_auth_status")
    def test_status_masks_tokens_only(self, mock_status) -> None:
        full_access = "access_token_abcdefghijklmnopqrstuvwxyz"
        full_refresh = "refresh_token_abcdefghijklmnopqrstuvwxyz"
        mock_status.return_value = {
            "api_key_configured": True,
            "api_secret_configured": True,
            "access_token_present": True,
            "refresh_token_present": True,
            "masked_api_key": "key1...yz12",
            "masked_access_token": "acce...wxyz",
            "masked_refresh_token": "refr...wxyz",
        }
        res = self.client.get("/api/v1/auth/status")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertNotIn(full_access, str(body))
        self.assertNotIn(full_refresh, str(body))
        self.assertTrue(body["api_secret_configured"])
        self.assertEqual(body["masked_access_token"], "acce...wxyz")

    @patch("api.routers.auth.get_login_url")
    def test_login_url_missing_key_safe_error(self, mock_url) -> None:
        mock_url.side_effect = ValueError("Missing required keys: KITE_API_KEY")
        res = self.client.get("/api/v1/auth/login-url")
        self.assertEqual(res.status_code, 400)
        self.assertIn("KITE_API_KEY", res.json()["detail"])

    @patch("api.routers.auth.generate_session")
    def test_session_returns_masked_tokens_only(self, mock_gen) -> None:
        full_access = "superlongaccesstokenvalue1234567890"
        full_refresh = "superlongrefreshtokenvalue1234567890"
        mock_gen.return_value = {
            "access_token": full_access,
            "refresh_token": full_refresh,
            "user_id": "AB1234",
        }
        res = self.client.post(
            "/api/v1/auth/session",
            json={"request_token": "reqtoken"},
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertNotIn(full_access, str(body))
        self.assertNotIn(full_refresh, str(body))
        self.assertEqual(body["user_id"], "AB1234")
        self.assertIn("...", body["masked_access_token"])

    def test_session_requires_auth_when_enabled(self) -> None:
        # Without overrides, unauthenticated paste login is rejected.
        app.dependency_overrides.clear()
        with AuthTestHarness() as h:
            res = h.client.post(
                "/api/v1/auth/session",
                json={"request_token": "reqtoken"},
            )
            self.assertEqual(res.status_code, 401)

    @patch("api.routers.auth.check_access_token_details")
    def test_check_token_response(self, mock_check) -> None:
        mock_check.return_value = (True, "Access token is valid (user: AB1234)", "AB1234")
        # Overrides use auth_disabled, so mutating CSRF is skipped.
        res = self.client.post("/api/v1/auth/check-token")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["valid"])
        self.assertEqual(body["user_id"], "AB1234")

    def test_health_still_works(self) -> None:
        res = self.client.get("/api/v1/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "ok")


class GenerateSessionUserIdTests(unittest.TestCase):
    @patch("login.KiteConnect")
    def test_mismatch_does_not_persist(self, mock_kite_cls) -> None:
        with AuthTestHarness() as h:
            h.secrets.write_text("KITE_API_KEY=k\nKITE_API_SECRET=s\nKITE_ACCESS_TOKEN=old\n", encoding="utf-8")
            os.environ["KITE_SECRETS_PATH"] = str(h.secrets)
            settings.reload_from_environ()
            kite = mock_kite_cls.return_value
            kite.generate_session.return_value = {
                "access_token": "new_token_should_not_write",
                "user_id": "WRONG",
            }
            from login import generate_session

            with self.assertRaises(ValueError):
                generate_session("req", expected_user_id="RIGHT", persist=True)
            self.assertEqual(read_secrets(h.secrets).get("KITE_ACCESS_TOKEN"), "old")


if __name__ == "__main__":
    unittest.main()
