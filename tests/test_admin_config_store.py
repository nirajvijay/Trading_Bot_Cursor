"""Tests for AdminConfigStore, validation, migrations, and rollback semantics."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from api.admin_config.defaults import DEFAULT_ADMIN_CONFIG_VALUES
from api.admin_config.migrations import run_migrations
from api.admin_config.store import AdminConfigStore, VersionConflictError, validate_config_values


class AdminConfigStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "admin_config.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_bootstrap_defaults(self) -> None:
        store = AdminConfigStore(self.db_path)
        try:
            payload = store.load_active_payload()
            self.assertEqual(payload["per_trade_risk_cap_inr"], DEFAULT_ADMIN_CONFIG_VALUES["per_trade_risk_cap_inr"])
            self.assertFalse(store.read_entries_paused())
            resp = store.get_config_response()
            self.assertAlmostEqual(resp["vwap_accept_gap_percent"], payload["vwap_accept_gap_exclusive_max"] * 100)
        finally:
            store.close()

    def test_migrations_idempotent(self) -> None:
        conn = sqlite3.connect(self.db_path)
        run_migrations(conn)
        run_migrations(conn)
        rows = conn.execute("SELECT version FROM admin_schema_migrations ORDER BY version").fetchall()
        conn.close()
        self.assertEqual([int(r[0]) for r in rows], [1])

    def test_daily_cap_below_per_trade_warns(self) -> None:
        values = dict(DEFAULT_ADMIN_CONFIG_VALUES)
        values["daily_loss_cap_inr"] = 100.0
        values["per_trade_risk_cap_inr"] = 900.0
        _, warnings = validate_config_values(values)
        self.assertIn("daily_cap_below_per_trade_cap", warnings)

    def test_rejects_invalid_vwap_ratio(self) -> None:
        values = dict(DEFAULT_ADMIN_CONFIG_VALUES)
        values["vwap_limited_gap_inclusive_max"] = 0.22
        with self.assertRaises(ValueError):
            validate_config_values(values)

    def test_update_and_version_conflict(self) -> None:
        store = AdminConfigStore(self.db_path)
        try:
            v0 = store.active_version_id()
            new_values = dict(store.load_active_payload())
            new_values["per_trade_risk_cap_inr"] = 800.0
            v1, _ = store.update_config(new_values, actor="tester")
            self.assertNotEqual(v0, v1)
            with self.assertRaises(VersionConflictError) as ctx:
                store.update_config(
                    new_values,
                    actor="tester",
                    expected_version_id=v0,
                )
            self.assertEqual(ctx.exception.current_version_id, v1)
        finally:
            store.close()

    def test_rollback_does_not_change_pause(self) -> None:
        store = AdminConfigStore(self.db_path)
        try:
            v0 = store.active_version_id()
            store.set_entries_paused(True)
            new_values = dict(store.load_active_payload())
            new_values["daily_loss_cap_inr"] = 2500.0
            v1, _ = store.update_config(new_values, actor="tester")
            self.assertNotEqual(v0, v1)
            rolled = store.rollback_config(v0, actor="tester")
            self.assertEqual(rolled, v0)
            self.assertTrue(store.read_entries_paused())
            payload = store.load_active_payload()
            self.assertEqual(payload["daily_loss_cap_inr"], DEFAULT_ADMIN_CONFIG_VALUES["daily_loss_cap_inr"])
        finally:
            store.close()

    def test_capture_snapshot_provenance(self) -> None:
        store = AdminConfigStore(self.db_path)
        try:
            snap = store.capture_snapshot(risk_cap_used_inr=450.0)
            fields = snap.provenance_fields()
            self.assertEqual(fields["admin_config_version_id"], store.active_version_id())
            self.assertEqual(fields["risk_cap_used_inr"], 450.0)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
