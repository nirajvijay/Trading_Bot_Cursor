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
        self.assertEqual([int(r[0]) for r in rows], [1, 2])

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

    def test_strict_validate_rejects_incomplete_replacement(self) -> None:
        partial = {
            "per_trade_risk_cap_inr": 900.0,
            "limited_per_trade_risk_cap_inr": 450.0,
            "daily_loss_cap_inr": 2995.0,
            "vwap_accept_gap_exclusive_max": 0.0022,
            "vwap_limited_gap_inclusive_max": 0.004,
        }
        with self.assertRaises(ValueError) as ctx:
            validate_config_values(partial)
        self.assertIn("incomplete_config", str(ctx.exception))

    def test_partial_save_merges_against_saved_preserves_daily_cap(self) -> None:
        store = AdminConfigStore(self.db_path)
        try:
            # Simulate host saved ₹2,995 (and a custom allocated capital).
            base = dict(store.load_active_payload())
            base["daily_loss_cap_inr"] = 2995.0
            base["allocated_capital_inr"] = 250_000.0
            store.update_config(base, actor="tester")
            # Partial legacy patch omits daily + allocated — must not reset to code defaults.
            partial = {
                "per_trade_risk_cap_inr": 850.0,
                "limited_per_trade_risk_cap_inr": 450.0,
                "vwap_accept_gap_exclusive_max": 0.0022,
                "vwap_limited_gap_inclusive_max": 0.004,
            }
            store.update_config(partial, actor="tester")
            payload = store.load_active_payload()
            self.assertEqual(payload["daily_loss_cap_inr"], 2995.0)
            self.assertEqual(payload["allocated_capital_inr"], 250_000.0)
            self.assertEqual(payload["per_trade_risk_cap_inr"], 850.0)
        finally:
            store.close()

    def test_load_merges_new_keys_onto_legacy_saved_payload(self) -> None:
        store = AdminConfigStore(self.db_path)
        try:
            version_id = store.active_version_id()
            legacy = {
                "per_trade_risk_cap_inr": 900.0,
                "limited_per_trade_risk_cap_inr": 450.0,
                "daily_loss_cap_inr": 2995.0,
                "vwap_accept_gap_exclusive_max": 0.0022,
                "vwap_limited_gap_inclusive_max": 0.004,
            }
            store._conn.execute(
                "UPDATE admin_config_versions SET payload_json = ? WHERE version_id = ?",
                (__import__("json").dumps(legacy), version_id),
            )
            store._conn.commit()
            payload = store.load_active_payload()
            self.assertEqual(payload["daily_loss_cap_inr"], 2995.0)
            self.assertEqual(
                payload["allocated_capital_inr"],
                DEFAULT_ADMIN_CONFIG_VALUES["allocated_capital_inr"],
            )
            self.assertEqual(
                payload["max_filled_setups_per_day"],
                DEFAULT_ADMIN_CONFIG_VALUES["max_filled_setups_per_day"],
            )
        finally:
            store.close()

    def test_apply_policy_reductions_immediate_capital_next_arm(self) -> None:
        store = AdminConfigStore(self.db_path)
        try:
            base = dict(store.load_active_payload())
            base["daily_loss_cap_inr"] = 2995.0
            base["allocated_capital_inr"] = 300_000.0
            store.update_config(base, actor="tester")
            store.arm_effective_config(actor="tester")
            self.assertEqual(store.load_effective_payload()["allocated_capital_inr"], 300_000.0)

            tighter = dict(store.load_active_payload())
            tighter["daily_loss_cap_inr"] = 2000.0
            tighter["allocated_capital_inr"] = 50_000.0
            store.update_config(tighter, actor="tester")
            eff = store.load_effective_payload()
            saved = store.load_active_payload()
            self.assertEqual(saved["daily_loss_cap_inr"], 2000.0)
            self.assertEqual(saved["allocated_capital_inr"], 50_000.0)
            # Risk reduction immediate; capital waits for arm.
            self.assertEqual(eff["daily_loss_cap_inr"], 2000.0)
            self.assertEqual(eff["allocated_capital_inr"], 300_000.0)
            # Open-trade profile is stamped from Effective at accept — version id recorded.
            snap = store.capture_snapshot()
            self.assertEqual(snap.daily_loss_cap_inr, 2000.0)
            self.assertEqual(snap.admin_config_version_id, store.effective_version_id())

            store.arm_effective_config(actor="tester")
            self.assertEqual(store.load_effective_payload()["allocated_capital_inr"], 50_000.0)
        finally:
            store.close()

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

    def test_rejects_malformed_session_gate_hhmm(self) -> None:
        """Admin entry_cutoff/square_off use the same strict HHMM validator."""
        cases = (
            ("entry_cutoff_ist", 1865.0),
            ("entry_cutoff_ist", 2400.0),
            ("entry_cutoff_ist", 1445.5),
            ("entry_cutoff_ist", float("nan")),
            ("entry_cutoff_ist", float("inf")),
            ("square_off_ist", 1865.0),
            ("square_off_ist", 2400.0),
            ("square_off_ist", 1515.25),
            ("square_off_ist", float("nan")),
            ("square_off_ist", float("-inf")),
        )
        for key, bad in cases:
            with self.subTest(key=key, bad=bad):
                values = dict(DEFAULT_ADMIN_CONFIG_VALUES)
                values[key] = bad
                with self.assertRaises(ValueError):
                    validate_config_values(values)

    def test_rejects_invalid_session_gate_ordering(self) -> None:
        values = dict(DEFAULT_ADMIN_CONFIG_VALUES)
        values["entry_cutoff_ist"] = 1515.0
        values["square_off_ist"] = 1445.0
        with self.assertRaises(ValueError) as ctx:
            validate_config_values(values)
        self.assertIn("square_off_ist must be after entry_cutoff_ist", str(ctx.exception))
        equal = dict(DEFAULT_ADMIN_CONFIG_VALUES)
        equal["entry_cutoff_ist"] = 1445.0
        equal["square_off_ist"] = 1445.0
        with self.assertRaises(ValueError):
            validate_config_values(equal)


if __name__ == "__main__":
    unittest.main()
