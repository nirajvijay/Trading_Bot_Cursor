"""Tests for login.py token helpers."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from login import (
    _load_env_file,
    _read_env_context,
    _read_env_merged,
    _require_env,
    check_access_token,
    extract_request_token,
    mask_token,
)


_KITE_ENV_KEYS = (
    "KITE_API_KEY",
    "KITE_API_SECRET",
    "KITE_ACCESS_TOKEN",
    "KITE_REFRESH_TOKEN",
    "KITE_EXPECTED_USER_ID",
)


class MaskTokenTests(unittest.TestCase):
    def test_short_token(self) -> None:
        self.assertEqual(mask_token("short"), "****")

    def test_normal_token(self) -> None:
        self.assertEqual(mask_token("abcdefghijklmnop"), "abcd...mnop")

    def test_eight_char_token(self) -> None:
        self.assertEqual(mask_token("12345678"), "****")


class ExtractRequestTokenTests(unittest.TestCase):
    def test_raw_token(self) -> None:
        self.assertEqual(extract_request_token("abc123token"), "abc123token")

    def test_full_redirect_url(self) -> None:
        url = "https://127.0.0.1/?request_token=mytoken&action=login&status=success"
        self.assertEqual(extract_request_token(url), "mytoken")

    def test_url_with_extra_params(self) -> None:
        url = "https://example.com/callback?status=success&request_token=xyz789&foo=bar"
        self.assertEqual(extract_request_token(url), "xyz789")

    def test_strips_whitespace(self) -> None:
        self.assertEqual(extract_request_token("  rawtoken  "), "rawtoken")


class SecretsPathPermissionTests(unittest.TestCase):
    def test_exists_true_but_open_permission_error_refuses_legacy(self) -> None:
        """Metadata-readable secrets that fail on open must not use legacy credentials."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secrets = root / "kite.env"
            secrets.write_text("KITE_API_KEY=secret-store-key\n", encoding="utf-8")
            legacy = root / ".env"
            legacy.write_text(
                "KITE_API_KEY=stale-legacy-key\nKITE_ACCESS_TOKEN=stale-legacy-token\n",
                encoding="utf-8",
            )
            real_open = Path.open

            def open_blocked(self, *args, **kwargs):
                if self == secrets:
                    raise PermissionError(13, "Permission denied", str(self))
                return real_open(self, *args, **kwargs)

            with patch.dict(os.environ):
                for key in _KITE_ENV_KEYS:
                    os.environ.pop(key, None)
                with patch("login._secrets_path", return_value=secrets), \
                     patch("login.LEGACY_ENV_PATH", legacy), \
                     patch.object(Path, "open", open_blocked):
                    self.assertTrue(secrets.exists())
                    state, values = _load_env_file(secrets)
                    self.assertEqual(state, "unreadable")
                    self.assertEqual(values, {})
                    merged, unreadable = _read_env_context()
                    self.assertEqual(unreadable, str(secrets))
                    self.assertNotIn("KITE_API_KEY", merged)
                    self.assertNotIn("KITE_ACCESS_TOKEN", merged)
                    with self.assertRaises(ValueError) as ctx:
                        _require_env("KITE_API_KEY")
                    message = str(ctx.exception)
                    self.assertIn("unreadable", message)
                    self.assertIn("Refusing legacy", message)
                    valid, check_message = check_access_token()
                    self.assertFalse(valid)
                    self.assertIn("unreadable", check_message)
                    self.assertNotIn("stale-legacy", check_message)

    def test_unreadable_secrets_discard_legacy_even_if_legacy_loaded_first(self) -> None:
        """If secrets become unreadable after a legacy underlay attempt, keep process-env only."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secrets = root / "missing-secrets.env"
            legacy = root / ".env"
            legacy.write_text("KITE_API_KEY=stale-legacy-key\n", encoding="utf-8")

            def load_side_effect(path: Path):
                if path == secrets:
                    return "unreadable", {}
                if path == legacy:
                    return "loaded", {"KITE_API_KEY": "stale-legacy-key"}
                return "missing", {}

            with patch.dict(os.environ):
                for key in _KITE_ENV_KEYS:
                    os.environ.pop(key, None)
                with patch("login._secrets_path", return_value=secrets), \
                     patch("login.LEGACY_ENV_PATH", legacy), \
                     patch("login._load_env_file", side_effect=load_side_effect):
                    merged, unreadable = _read_env_context()
                    self.assertEqual(unreadable, str(secrets))
                    self.assertEqual(merged, {})
                    self.assertEqual(_read_env_merged(), {})

    def test_process_env_still_overrides_when_secrets_unreadable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            secrets = Path(tmp) / "kite.env"
            secrets.write_text("KITE_API_KEY=file-key\n", encoding="utf-8")
            real_open = Path.open

            def open_blocked(self, *args, **kwargs):
                if self == secrets:
                    raise PermissionError(13, "Permission denied", str(self))
                return real_open(self, *args, **kwargs)

            with patch.dict(os.environ, {"KITE_API_KEY": "explicit-test-key"}, clear=False), \
                 patch("login._secrets_path", return_value=secrets), \
                 patch.object(Path, "open", open_blocked):
                self.assertEqual(_require_env("KITE_API_KEY")["KITE_API_KEY"], "explicit-test-key")
                _values, unreadable = _read_env_context()
                self.assertEqual(unreadable, str(secrets))


if __name__ == "__main__":
    unittest.main()
