"""Tests for local Kite auth API."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.main import app
from api.routers import auth as auth_router


class AuthApiTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[auth_router.require_localhost] = lambda: None
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

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
        self.assertNotIn("KITE_API_SECRET", json.dumps(body))
        self.assertNotIn(full_access, json.dumps(body))
        self.assertNotIn(full_refresh, json.dumps(body))
        self.assertTrue(body["api_secret_configured"])
        self.assertEqual(body["masked_access_token"], "acce...wxyz")

    @patch("api.routers.auth.get_login_url")
    def test_login_url_missing_key_safe_error(self, mock_url) -> None:
        mock_url.side_effect = ValueError("Missing required .env keys: KITE_API_KEY. Add them to /path/.env")
        res = self.client.get("/api/v1/auth/login-url")
        self.assertEqual(res.status_code, 400)
        self.assertIn("KITE_API_KEY", res.json()["detail"])
        self.assertNotIn("traceback", res.text.lower())

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
            headers={"X-NIFTY-RADAR-LOCAL-AUTH": "true"},
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertNotIn(full_access, json.dumps(body))
        self.assertNotIn(full_refresh, json.dumps(body))
        self.assertEqual(body["user_id"], "AB1234")
        self.assertIn("...", body["masked_access_token"])
        self.assertIn("backend/.env", body["message"])

    def test_session_requires_local_header(self) -> None:
        res = self.client.post(
            "/api/v1/auth/session",
            json={"request_token": "reqtoken"},
        )
        self.assertEqual(res.status_code, 403)

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


if __name__ == "__main__":
    unittest.main()
