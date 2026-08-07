"""Tests for website auth, MFA/step-up, and remote Kite OAuth callback."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pyotp
from fastapi.testclient import TestClient

from api.auth import settings
from api.auth.kite_oauth_store import get_kite_oauth_store
from api.auth.secrets_store import read_secrets
from api.auth.settings import validate_startup_settings
from api.auth.web_auth_store import get_web_auth_store
from api.main import app
from api.routers import auth as auth_router
from tests.auth_test_helpers import (
    AuthTestHarness,
    clear_auth_overrides,
    disable_web_auth_overrides,
)


class ProductionGuardTests(unittest.TestCase):
    def test_production_forbids_disabled_web_auth(self) -> None:
        prev_env = os.environ.get("APP_ENV")
        prev_enabled = os.environ.get("WEB_AUTH_ENABLED")
        try:
            os.environ["APP_ENV"] = "production"
            os.environ["WEB_AUTH_ENABLED"] = "false"
            settings.reload_from_environ()
            with self.assertRaises(RuntimeError):
                validate_startup_settings()
        finally:
            if prev_env is None:
                os.environ.pop("APP_ENV", None)
            else:
                os.environ["APP_ENV"] = prev_env
            if prev_enabled is None:
                os.environ.pop("WEB_AUTH_ENABLED", None)
            else:
                os.environ["WEB_AUTH_ENABLED"] = prev_enabled
            settings.reload_from_environ()


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
            h.client.post("/api/v1/account/logout", headers=h.csrf_headers())

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
        self.client = TestClient(app)

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
