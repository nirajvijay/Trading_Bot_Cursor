"""Tests for headless Kite auto-login service."""

from __future__ import annotations

import unittest
from unittest.mock import ANY, MagicMock, patch

from api.services.kite_auto_login import attempt_kite_auto_login, read_auto_login_credentials


class KiteAutoLoginServiceTests(unittest.TestCase):
    @patch("api.services.kite_auto_login._read_env_merged")
    def test_missing_credentials(self, read_env) -> None:
        read_env.return_value = {"KITE_API_KEY": "key", "KITE_API_SECRET": "secret"}
        result = attempt_kite_auto_login()
        self.assertFalse(result.success)
        self.assertEqual(result.failure_reason, "missing_credentials")

    @patch("api.services.kite_auto_login.generate_session")
    @patch("api.services.kite_auto_login.requests.Session")
    @patch("api.services.kite_auto_login._require_env")
    @patch("api.services.kite_auto_login.read_auto_login_credentials")
    def test_happy_path(
        self,
        read_creds,
        require_env,
        session_cls,
        generate_session,
    ) -> None:
        read_creds.return_value = {
            "user_id": "AB1234",
            "password": "pw",
            "totp_secret": "JBSWY3DPEHPK3PXP",
        }
        require_env.return_value = {
            "KITE_API_KEY": "api_key",
            "KITE_API_SECRET": "api_secret",
        }
        session = session_cls.return_value
        connect_resp = MagicMock(status_code=200, url="https://kite.zerodha.com/connect/login?sess_id=x")
        login_resp = MagicMock(
            status_code=200,
            json=lambda: {
                "status": "success",
                "data": {"request_id": "req-1"},
            },
        )
        twofa_resp = MagicMock(
            status_code=200,
            json=lambda: {
                "status": "success",
                "data": {"request_token": "reqtok123"},
            },
        )
        session.get.return_value = connect_resp
        session.post.side_effect = [login_resp, twofa_resp]
        generate_session.return_value = {
            "access_token": "access_token_abcdefghijklmnopqrstuvwxyz",
            "user_id": "AB1234",
        }

        result = attempt_kite_auto_login()
        self.assertTrue(result.success)
        self.assertEqual(result.user_id, "AB1234")
        generate_session.assert_called_once_with(
            "reqtok123",
            expected_user_id=ANY,
            persist=True,
        )

    @patch("api.services.kite_auto_login.requests.Session")
    @patch("api.services.kite_auto_login._require_env")
    @patch("api.services.kite_auto_login.read_auto_login_credentials")
    def test_captcha_required(self, read_creds, require_env, session_cls) -> None:
        read_creds.return_value = {
            "user_id": "AB1234",
            "password": "pw",
            "totp_secret": "JBSWY3DPEHPK3PXP",
        }
        require_env.return_value = {
            "KITE_API_KEY": "api_key",
            "KITE_API_SECRET": "api_secret",
        }
        session = session_cls.return_value
        session.get.return_value = MagicMock(status_code=200, url="https://kite.zerodha.com/connect/login")
        session.post.return_value = MagicMock(
            status_code=400,
            json=lambda: {
                "status": "error",
                "message": "Invalid CAPTCHA values.",
                "error_type": "InputException",
            },
        )

        result = attempt_kite_auto_login()
        self.assertFalse(result.success)
        self.assertEqual(result.failure_reason, "captcha_required")

    @patch("api.services.kite_auto_login._read_env_merged")
    def test_read_credentials_falls_back_to_expected_user_id(self, read_env) -> None:
        read_env.return_value = {
            "KITE_EXPECTED_USER_ID": "LS4795",
            "KITE_PASSWORD": "pw",
            "KITE_TOTP_SECRET": "SECRET",
        }
        creds = read_auto_login_credentials()
        self.assertEqual(creds["user_id"], "LS4795")
        self.assertTrue(all(creds.values()))


if __name__ == "__main__":
    unittest.main()
