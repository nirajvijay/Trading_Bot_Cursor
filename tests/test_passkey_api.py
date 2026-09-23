"""Tests for the /api/v1/account/passkey* endpoints.

Everything here is reachable without a real authenticator: the guards that run
*before* any signature is checked (password, CSRF, enrolment state) and the
management endpoints that let a dead Mac be removed.
"""

from __future__ import annotations

import dataclasses
import unittest
from unittest import mock

import pyotp

from api.auth.web_auth_store import get_web_auth_store
from tests.auth_test_helpers import AuthTestHarness
from webauthn.helpers import bytes_to_base64url


def _without_mfa(user):
    """Same owner record with the TOTP authenticator not enrolled."""
    return dataclasses.replace(user, mfa_enabled=False)


def _enrol_mfa(store) -> str:
    """Put the owner in the state production enforces (WEB_AUTH_MFA_REQUIRED).

    Must run before login: confirming MFA invalidates existing sessions.
    """
    secret = pyotp.random_base32()
    store.set_mfa_pending(secret)
    store.confirm_mfa(secret)
    return secret


class PasskeyLoginOptionsTests(unittest.TestCase):
    def test_wrong_password_never_issues_a_challenge(self) -> None:
        with AuthTestHarness() as h:
            result = h.client.post(
                "/api/v1/account/passkey/login/options",
                json={"username": h.username, "password": "wrong"},
            )
            self.assertEqual(result.status_code, 401)
            self.assertNotIn("challenge_id", result.json())

    def test_correct_password_without_enrolment_is_refused(self) -> None:
        """Nothing is enrolled yet, so there is no passkey to authenticate."""
        with AuthTestHarness() as h:
            result = h.client.post(
                "/api/v1/account/passkey/login/options",
                json={"username": h.username, "password": h.password},
            )
            self.assertEqual(result.status_code, 400)
            self.assertIn("passkey", result.json()["detail"].lower())

    def test_options_are_issued_once_a_passkey_exists(self) -> None:
        with AuthTestHarness() as h:
            get_web_auth_store().save_passkey(1, b"cred-a", b"pub-a", 0)
            result = h.client.post(
                "/api/v1/account/passkey/login/options",
                json={"username": h.username, "password": h.password},
            )
            self.assertEqual(result.status_code, 200, result.text)
            body = result.json()
            self.assertTrue(body["challenge_id"])
            self.assertIn("challenge", body["options"])
            # The allowlist must name the enrolled credential, or the Mac has
            # no way to know which key to offer.
            allowed = [c["id"] for c in body["options"]["allowCredentials"]]
            self.assertEqual(allowed, [bytes_to_base64url(b"cred-a")])


class PasswordlessPasskeyLoginTests(unittest.TestCase):
    """Touch ID alone is the normal sign-in route: no password typed first."""

    def test_options_are_issued_without_any_credentials(self) -> None:
        with AuthTestHarness() as h:
            get_web_auth_store().save_passkey(1, b"cred-a", b"pub-a", 0)
            result = h.client.post("/api/v1/account/passkey/login/options", json={})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertTrue(result.json()["challenge_id"])

    def test_anonymous_options_do_not_name_the_enrolled_devices(self) -> None:
        """An empty allowlist keeps the owner's credential ids private; the
        authenticator resolves the discoverable credential by itself."""
        with AuthTestHarness() as h:
            get_web_auth_store().save_passkey(1, b"cred-a", b"pub-a", 0)
            options = h.client.post(
                "/api/v1/account/passkey/login/options", json={}
            ).json()["options"]
            self.assertEqual(options.get("allowCredentials", []), [])

    def test_passwordless_options_still_need_an_enrolled_passkey(self) -> None:
        with AuthTestHarness() as h:
            result = h.client.post("/api/v1/account/passkey/login/options", json={})
            self.assertEqual(result.status_code, 400)

    def test_a_supplied_password_is_still_checked(self) -> None:
        """Dropping the requirement must not turn into ignoring it."""
        with AuthTestHarness() as h:
            get_web_auth_store().save_passkey(1, b"cred-a", b"pub-a", 0)
            result = h.client.post(
                "/api/v1/account/passkey/login/options",
                json={"username": h.username, "password": "wrong"},
            )
            self.assertEqual(result.status_code, 401)

    def test_verify_rejects_an_assertion_for_another_account(self) -> None:
        """A user handle that is not this owner's must never open a session."""
        with AuthTestHarness() as h:
            store = get_web_auth_store()
            store.save_passkey(1, b"cred-a", b"pub-a", 0)
            challenge_id = h.client.post(
                "/api/v1/account/passkey/login/options", json={}
            ).json()["challenge_id"]
            result = h.client.post(
                "/api/v1/account/passkey/login/verify",
                json={
                    "challenge_id": challenge_id,
                    "credential": {
                        "rawId": bytes_to_base64url(b"cred-a"),
                        "response": {
                            "userHandle": bytes_to_base64url((99).to_bytes(8, "big"))
                        },
                    },
                },
            )
            self.assertEqual(result.status_code, 401)


class PasskeyVerifyGuardTests(unittest.TestCase):
    def test_unknown_challenge_is_rejected(self) -> None:
        with AuthTestHarness() as h:
            get_web_auth_store().save_passkey(1, b"cred-a", b"pub-a", 0)
            result = h.client.post(
                "/api/v1/account/passkey/login/verify",
                json={
                    "username": h.username,
                    "challenge_id": "not-a-real-challenge",
                    "credential": {"rawId": bytes_to_base64url(b"cred-a")},
                },
            )
            self.assertEqual(result.status_code, 401)

    def test_register_options_require_a_session(self) -> None:
        """Enrolment must never be reachable anonymously; whether the refusal
        comes from the CSRF guard or the session guard does not matter."""
        with AuthTestHarness() as h:
            result = h.client.post("/api/v1/account/passkey/register/options")
            self.assertIn(result.status_code, (401, 403))


class PasskeyManagementTests(unittest.TestCase):
    def test_listing_requires_a_session(self) -> None:
        with AuthTestHarness() as h:
            self.assertEqual(h.client.get("/api/v1/account/passkeys").status_code, 401)

    def test_list_reports_enrolled_keys(self) -> None:
        with AuthTestHarness() as h:
            h.login()
            get_web_auth_store().save_passkey(1, b"cred-a", b"pub-a", 0, name="Work Mac")
            body = h.client.get("/api/v1/account/passkeys").json()
            self.assertEqual(len(body["passkeys"]), 1)
            entry = body["passkeys"][0]
            self.assertEqual(entry["name"], "Work Mac")
            self.assertEqual(entry["credential_id"], bytes_to_base64url(b"cred-a"))
            self.assertIsNone(entry["last_used_at"])

    def test_me_reports_passkey_count(self) -> None:
        with AuthTestHarness() as h:
            h.login()
            self.assertEqual(h.client.get("/api/v1/account/me").json()["passkey_count"], 0)
            get_web_auth_store().save_passkey(1, b"cred-a", b"pub-a", 0)
            self.assertEqual(h.client.get("/api/v1/account/me").json()["passkey_count"], 1)

    def test_delete_requires_csrf(self) -> None:
        with AuthTestHarness() as h:
            h.login()
            get_web_auth_store().save_passkey(1, b"cred-a", b"pub-a", 0)
            result = h.client.post(
                "/api/v1/account/passkeys/delete",
                json={
                    "credential_id": bytes_to_base64url(b"cred-a"),
                    "password": h.password,
                },
            )
            self.assertEqual(result.status_code, 403)
            self.assertEqual(len(get_web_auth_store().list_passkeys(1)), 1)

    def test_delete_requires_the_password(self) -> None:
        """A hijacked session must not be able to strip a sign-in factor."""
        with AuthTestHarness() as h:
            h.login()
            get_web_auth_store().save_passkey(1, b"cred-a", b"pub-a", 0)
            result = h.client.post(
                "/api/v1/account/passkeys/delete",
                json={"credential_id": bytes_to_base64url(b"cred-a"), "password": "wrong"},
                headers=h.csrf_headers(),
            )
            self.assertEqual(result.status_code, 401)
            self.assertEqual(len(get_web_auth_store().list_passkeys(1)), 1)

    def test_delete_succeeds_and_frees_re_enrolment(self) -> None:
        """The dead-Mac case: with TOTP enrolled, the last passkey can go, which
        is what lets a replaced machine register again."""
        with AuthTestHarness() as h:
            store = get_web_auth_store()
            secret = _enrol_mfa(store)
            h.login(totp=pyotp.TOTP(secret).now())
            store.save_passkey(1, b"cred-a", b"pub-a", 0)
            result = h.client.post(
                "/api/v1/account/passkeys/delete",
                json={
                    "credential_id": bytes_to_base64url(b"cred-a"),
                    "password": h.password,
                },
                headers=h.csrf_headers(),
            )
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(store.list_passkeys(1), [])
            self.assertEqual(h.client.get("/api/v1/account/me").json()["passkey_count"], 0)

    def test_last_passkey_is_kept_when_no_authenticator_is_enrolled(self) -> None:
        """Deleting it would leave password-only, which is weaker than the
        state the account started in."""
        with AuthTestHarness() as h:
            h.login()
            store = get_web_auth_store()
            store.save_passkey(1, b"cred-a", b"pub-a", 0)
            with mock.patch.object(
                store, "get_user", return_value=_without_mfa(store.get_user())
            ):
                result = h.client.post(
                    "/api/v1/account/passkeys/delete",
                    json={
                        "credential_id": bytes_to_base64url(b"cred-a"),
                        "password": h.password,
                    },
                    headers=h.csrf_headers(),
                )
            self.assertEqual(result.status_code, 400)
            self.assertIn("authenticator", result.json()["detail"].lower())
            self.assertEqual(len(store.list_passkeys(1)), 1)

    def test_a_second_passkey_can_always_be_removed(self) -> None:
        with AuthTestHarness() as h:
            h.login()
            store = get_web_auth_store()
            store.save_passkey(1, b"cred-a", b"pub-a", 0)
            store.save_passkey(1, b"cred-b", b"pub-b", 0)
            result = h.client.post(
                "/api/v1/account/passkeys/delete",
                json={
                    "credential_id": bytes_to_base64url(b"cred-b"),
                    "password": h.password,
                },
                headers=h.csrf_headers(),
            )
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(
                [k.credential_id for k in store.list_passkeys(1)], [b"cred-a"]
            )

    def test_delete_of_unknown_credential_is_404(self) -> None:
        with AuthTestHarness() as h:
            h.login()
            get_web_auth_store().save_passkey(1, b"cred-a", b"pub-a", 0)
            result = h.client.post(
                "/api/v1/account/passkeys/delete",
                json={
                    "credential_id": bytes_to_base64url(b"cred-does-not-exist"),
                    "password": h.password,
                },
                headers=h.csrf_headers(),
            )
            self.assertEqual(result.status_code, 404)


if __name__ == "__main__":
    unittest.main()
