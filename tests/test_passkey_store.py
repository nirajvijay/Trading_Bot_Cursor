"""Tests for passkey storage: challenge lifecycle and enrolment removal.

These cover the parts of the WebAuthn flow that need no real authenticator --
the single-use/expiry contract on challenges, and the delete path that keeps a
dead Mac from permanently blocking re-enrolment.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from api.auth.web_auth_store import WebAuthStore, _utc_now


class PasskeyChallengeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = WebAuthStore(Path(self._tmp.name) / "web_auth.db")
        self.store.init_db()
        self.store.create_owner("owner", "correct horse battery staple")

    def test_challenge_round_trips(self) -> None:
        challenge = self.store.create_passkey_challenge(1, "authentication", b"\x01\x02\x03")
        self.assertEqual(
            self.store.consume_passkey_challenge(challenge.id, 1, "authentication"),
            b"\x01\x02\x03",
        )

    def test_challenge_is_single_use(self) -> None:
        """A replayed assertion must not be able to reuse a spent challenge."""
        challenge = self.store.create_passkey_challenge(1, "authentication", b"once")
        self.store.consume_passkey_challenge(challenge.id, 1, "authentication")
        self.assertIsNone(
            self.store.consume_passkey_challenge(challenge.id, 1, "authentication")
        )

    def test_expired_challenge_is_rejected(self) -> None:
        challenge = self.store.create_passkey_challenge(
            1, "authentication", b"stale", ttl_seconds=300
        )
        later = _utc_now() + timedelta(seconds=301)
        with mock.patch("api.auth.web_auth_store._utc_now", return_value=later):
            self.assertIsNone(
                self.store.consume_passkey_challenge(challenge.id, 1, "authentication")
            )

    def test_ceremony_is_not_interchangeable(self) -> None:
        """A registration challenge must not satisfy a login, or vice versa."""
        challenge = self.store.create_passkey_challenge(1, "registration", b"reg")
        self.assertIsNone(
            self.store.consume_passkey_challenge(challenge.id, 1, "authentication")
        )

    def test_new_challenge_supersedes_the_previous_one(self) -> None:
        first = self.store.create_passkey_challenge(1, "authentication", b"first")
        self.store.create_passkey_challenge(1, "authentication", b"second")
        self.assertIsNone(
            self.store.consume_passkey_challenge(first.id, 1, "authentication")
        )


class PasskeyRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = WebAuthStore(Path(self._tmp.name) / "web_auth.db")
        self.store.init_db()
        self.store.create_owner("owner", "correct horse battery staple")

    def test_save_and_list(self) -> None:
        self.store.save_passkey(1, b"cred-a", b"pub-a", 0)
        keys = self.store.list_passkeys(1)
        self.assertEqual(len(keys), 1)
        self.assertEqual(keys[0].credential_id, b"cred-a")
        self.assertTrue(keys[0].created_at)
        self.assertIsNone(keys[0].last_used_at)

    def test_sign_count_update_records_last_used(self) -> None:
        self.store.save_passkey(1, b"cred-a", b"pub-a", 0)
        self.store.update_passkey_sign_count(b"cred-a", 7)
        key = self.store.get_passkey(b"cred-a", 1)
        assert key is not None
        self.assertEqual(key.sign_count, 7)
        self.assertIsNotNone(key.last_used_at)

    def test_delete_removes_the_enrolment(self) -> None:
        """The dead-Mac case: the row must be clearable, or passkey_count stays
        above zero forever and the machine can never re-enrol."""
        self.store.save_passkey(1, b"cred-a", b"pub-a", 0)
        self.assertTrue(self.store.delete_passkey(b"cred-a", 1))
        self.assertEqual(self.store.list_passkeys(1), [])
        self.assertIsNone(self.store.get_passkey(b"cred-a", 1))

    def test_delete_is_idempotent_and_reports_miss(self) -> None:
        self.assertFalse(self.store.delete_passkey(b"never-existed", 1))

    def test_delete_leaves_other_passkeys_alone(self) -> None:
        self.store.save_passkey(1, b"cred-a", b"pub-a", 0)
        self.store.save_passkey(1, b"cred-b", b"pub-b", 0)
        self.store.delete_passkey(b"cred-a", 1)
        self.assertEqual([k.credential_id for k in self.store.list_passkeys(1)], [b"cred-b"])


if __name__ == "__main__":
    unittest.main()
