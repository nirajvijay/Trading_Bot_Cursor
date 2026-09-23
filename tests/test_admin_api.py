"""Tests for Admin Console V1 HTTP API."""

from __future__ import annotations

import unittest
from copy import deepcopy

from api.admin_config.defaults import DEFAULT_ADMIN_CONFIG_VALUES
from api.admin_config.store import AdminConfigStore
from tests.auth_test_helpers import AuthTestHarness, disable_web_auth_overrides, clear_auth_overrides


def _default_patch_body(version_id: str | None = None) -> dict:
    values = deepcopy(DEFAULT_ADMIN_CONFIG_VALUES)
    body: dict = {"values": values}
    if version_id is not None:
        body["expected_version_id"] = version_id
    return body


class AdminApiTests(unittest.TestCase):
    def test_get_config_requires_session(self) -> None:
        with AuthTestHarness() as h:
            res = h.client.get("/api/v1/admin/config")
            self.assertEqual(res.status_code, 401)

    def test_get_config_authenticated(self) -> None:
        with AuthTestHarness() as h:
            h.login()
            res = h.client.get("/api/v1/admin/config")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertIn("version_id", data)
            self.assertIn("values", data)
            self.assertAlmostEqual(
                data["vwap_accept_gap_percent"],
                data["values"]["vwap_accept_gap_exclusive_max"] * 100,
            )

    def test_patch_requires_step_up(self) -> None:
        with AuthTestHarness() as h:
            h.login()
            res = h.client.patch(
                "/api/v1/admin/config",
                json=_default_patch_body(),
                headers=h.csrf_headers(),
            )
            self.assertEqual(res.status_code, 403)
            self.assertIn("Step-up", res.json()["detail"])

    def test_patch_updates_thresholds(self) -> None:
        with AuthTestHarness() as h:
            h.login()
            h.step_up()
            get_res = h.client.get("/api/v1/admin/config")
            version_id = get_res.json()["version_id"]
            body = _default_patch_body(version_id)
            body["values"]["per_trade_risk_cap_inr"] = 850.0
            body["values"]["vwap_accept_gap_exclusive_max"] = 0.0022
            res = h.client.patch(
                "/api/v1/admin/config",
                json=body,
                headers=h.csrf_headers(),
            )
            self.assertEqual(res.status_code, 200, res.text)
            data = res.json()
            self.assertEqual(data["values"]["per_trade_risk_cap_inr"], 850.0)
            self.assertAlmostEqual(data["vwap_accept_gap_percent"], 0.22)

    def test_patch_version_conflict(self) -> None:
        with AuthTestHarness() as h:
            h.login()
            h.step_up()
            body = _default_patch_body("stale-version-id")
            body["values"]["daily_loss_cap_inr"] = 2800.0
            res = h.client.patch(
                "/api/v1/admin/config",
                json=body,
                headers=h.csrf_headers(),
            )
            self.assertEqual(res.status_code, 409)

    def test_pause_and_resume(self) -> None:
        with AuthTestHarness() as h:
            h.login()
            h.step_up()
            pause = h.client.post("/api/v1/admin/trading/pause", headers=h.csrf_headers())
            self.assertEqual(pause.status_code, 200)
            self.assertTrue(pause.json()["entries_paused"])
            cfg = h.client.get("/api/v1/admin/config").json()
            self.assertTrue(cfg["entries_paused"])

            resume = h.client.post("/api/v1/admin/trading/resume", headers=h.csrf_headers())
            # Resume may 409 if engine not running — acceptable in unit test.
            if resume.status_code == 200:
                self.assertFalse(resume.json()["entries_paused"])
            else:
                self.assertEqual(resume.status_code, 409)

    def test_audit_list(self) -> None:
        with AuthTestHarness() as h:
            h.login()
            res = h.client.get("/api/v1/admin/audit?limit=5")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertIn("entries", data)

    def test_auth_disabled_allows_mutations(self) -> None:
        with AuthTestHarness() as h:
            h.login()
            disable_web_auth_overrides()
            try:
                body = _default_patch_body()
                body["values"]["limited_per_trade_risk_cap_inr"] = 400.0
                res = h.client.patch(
                    "/api/v1/admin/config",
                    json=body,
                    headers=h.csrf_headers(),
                )
                self.assertEqual(res.status_code, 200, res.text)
            finally:
                clear_auth_overrides()

    def test_percent_ratio_guard_via_api(self) -> None:
        """Sending ratio 0.22 (22%) must be rejected — limited gap max is 0.01."""
        with AuthTestHarness() as h:
            h.login()
            disable_web_auth_overrides()
            try:
                body = _default_patch_body()
                body["values"]["vwap_limited_gap_inclusive_max"] = 0.22
                res = h.client.patch(
                    "/api/v1/admin/config",
                    json=body,
                    headers=h.csrf_headers(),
                )
                self.assertEqual(res.status_code, 422)
            finally:
                clear_auth_overrides()

    def test_canonical_pause_survives_store_reopen(self) -> None:
        with AuthTestHarness() as h:
            h.login()
            h.step_up()
            pause = h.client.post("/api/v1/admin/trading/pause", headers=h.csrf_headers())
            self.assertEqual(pause.status_code, 200)
            admin_db = h.root / "data" / "config" / "admin_config.db"
            store = AdminConfigStore(admin_db, read_only=True)
            try:
                self.assertTrue(store.read_entries_paused())
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
