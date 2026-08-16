"""Remote website access for checklist generate and observation start."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.auth import settings
from api.auth.kite_oauth_store import reset_kite_oauth_store_cache
from api.auth.rate_limit import reset_rate_limiter
from api.auth.web_auth_store import get_web_auth_store, reset_web_auth_store_cache
from api.main import app
from tests.auth_test_helpers import clear_auth_overrides

PRODUCTION_ORIGIN = "https://njtrading.website"
PRODUCTION_BASE_URL = "https://njtrading.website"


class RemoteGenerateTests(unittest.TestCase):
  def setUp(self) -> None:
    self._tmpdir = tempfile.TemporaryDirectory()
    root = Path(self._tmpdir.name)
    self._prev_env: dict[str, str | None] = {}
    for key, value in {
      "APP_ENV": "development",
      "WEB_AUTH_ENABLED": "true",
      "WEB_AUTH_MFA_REQUIRED": "false",
      "WEB_AUTH_COOKIE_SECURE": "false",
      "WEB_AUTH_DB_PATH": str(root / "web_auth.db"),
      "KITE_OAUTH_STATE_DB_PATH": str(root / "kite_oauth.db"),
      "AUDIT_LOG_PATH": str(root / "audit.log"),
      "KITE_SECRETS_PATH": str(root / "kite.env"),
      "WEB_AUTH_ORIGIN_ALLOWLIST": PRODUCTION_ORIGIN,
      "KITE_API_KEY": "test_api_key",
      "KITE_API_SECRET": "test_api_secret",
      "NIFTY_RADAR_DATA_ROOT": str(root / "data"),
      "NIFTY_RADAR_RUNTIME_CACHE_DIR": str(root / "data" / "runtime-cache"),
    }.items():
      self._prev_env[key] = os.environ.get(key)
      os.environ[key] = value
    settings.reload_from_environ()
    reset_web_auth_store_cache()
    reset_kite_oauth_store_cache()
    reset_rate_limiter()
    clear_auth_overrides()
    store = get_web_auth_store()
    store.init_db()
    store.create_owner("owner", "test-password-123")
    self.client = TestClient(app, base_url=PRODUCTION_BASE_URL)
    self.password = "test-password-123"

  def tearDown(self) -> None:
    self.client.close()
    clear_auth_overrides()
    for key, prev in self._prev_env.items():
      if prev is None:
        os.environ.pop(key, None)
      else:
        os.environ[key] = prev
    settings.reload_from_environ()
    reset_web_auth_store_cache()
    reset_kite_oauth_store_cache()
    reset_rate_limiter()
    self._tmpdir.cleanup()

  def _login_headers(self) -> dict[str, str]:
    res = self.client.post(
      "/api/v1/account/login",
      json={"username": "owner", "password": self.password},
    )
    self.assertEqual(res.status_code, 200, res.text)
    csrf = self.client.cookies.get(settings.CSRF_COOKIE_NAME)
    self.assertTrue(csrf)
    return {
      settings.CSRF_HEADER_NAME: csrf,
      "Origin": PRODUCTION_ORIGIN,
    }

  def test_generate_baselines_from_production_host(self) -> None:
    headers = self._login_headers()
    with patch(
      "api.routers.checklist.run_local_generation",
      return_value=(True, "baselines complete"),
    ):
      res = self.client.post(
        "/api/v1/premarket-checklist/generate/baselines?session_date=2026-08-08",
        headers=headers,
      )
    self.assertEqual(res.status_code, 200, res.text)
    self.assertTrue(res.json()["success"])

  def test_generate_requires_auth(self) -> None:
    res = self.client.post(
      "/api/v1/premarket-checklist/generate/baselines",
      headers={"Origin": PRODUCTION_ORIGIN},
    )
    self.assertEqual(res.status_code, 401)

  def test_observation_start_from_production_host(self) -> None:
    headers = self._login_headers()
    with patch(
      "api.routers.observation.start_observation_runner",
      return_value=(True, "Observation runner started (pid 12345)", 12345),
    ):
      res = self.client.post(
        "/api/v1/observation/start?session_date=2026-08-08",
        headers=headers,
      )
    self.assertEqual(res.status_code, 200, res.text)
    self.assertTrue(res.json()["success"])


if __name__ == "__main__":
  unittest.main()
