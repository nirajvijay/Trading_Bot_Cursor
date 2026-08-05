"""Tests for login.py token helpers."""

from __future__ import annotations

import unittest

from login import extract_request_token, mask_token


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


if __name__ == "__main__":
    unittest.main()
