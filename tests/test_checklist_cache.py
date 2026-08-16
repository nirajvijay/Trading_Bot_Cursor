"""Tests for checklist cache identity, atomic write, and invalidation."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from api.services.checklist_cache import (
    SCHEMA_VERSION,
    current_universe_manifest_id,
    invalidate_checklist_cache,
    read_checklist_cache,
    write_checklist_cache,
)
from universe_manifest import write_universe_manifest_atomic


class ChecklistCacheTests(unittest.TestCase):
    def test_write_and_read_ok_and_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_universe_manifest_atomic(root / "universe_manifest.json")
            for status, next_step in (
                ("ok", "Start live observation"),
                ("warning", "Historical candles: incomplete"),
                ("failed", "Invalid token"),
            ):
                write_checklist_cache(
                    {
                        "session_date": "2026-08-03",
                        "overall_status": status,
                        "checked_at": "2026-08-03T08:00:00+05:30",
                        "next_step": next_step,
                        "blockers": [next_step] if status != "ok" else [],
                    },
                    local_data_dir=root,
                )
                cached = read_checklist_cache("2026-08-03", local_data_dir=root)
                self.assertIsNotNone(cached)
                assert cached is not None
                self.assertEqual(cached["overall_status"], status)
                if status != "ok":
                    self.assertEqual(cached["reason_summary"], next_step)
                path = root / "checklist_cache.json"
                self.assertEqual(stat_mode(path) & 0o777, 0o600)

    def test_corrupt_json_is_miss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_universe_manifest_atomic(root / "universe_manifest.json")
            path = root / "checklist_cache.json"
            path.write_text("{not-json", encoding="utf-8")
            self.assertIsNone(read_checklist_cache("2026-08-03", local_data_dir=root))

    def test_schema_version_mismatch_is_miss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_universe_manifest_atomic(root / "universe_manifest.json")
            write_checklist_cache(
                {
                    "session_date": "2026-08-03",
                    "overall_status": "ok",
                    "checked_at": "2026-08-03T08:00:00+05:30",
                    "next_step": "",
                    "blockers": [],
                },
                local_data_dir=root,
            )
            path = root / "checklist_cache.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["schema_version"] = SCHEMA_VERSION + 1
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertIsNone(read_checklist_cache("2026-08-03", local_data_dir=root))

    def test_manifest_identity_mismatch_is_miss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_universe_manifest_atomic(root / "universe_manifest.json")
            write_checklist_cache(
                {
                    "session_date": "2026-08-03",
                    "overall_status": "ok",
                    "checked_at": "2026-08-03T08:00:00+05:30",
                    "next_step": "",
                    "blockers": [],
                },
                local_data_dir=root,
            )
            path = root / "checklist_cache.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["universe_manifest_id"] = "tampered"
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertIsNone(read_checklist_cache("2026-08-03", local_data_dir=root))

    def test_session_date_mismatch_is_miss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_universe_manifest_atomic(root / "universe_manifest.json")
            write_checklist_cache(
                {
                    "session_date": "2026-08-03",
                    "overall_status": "warning",
                    "checked_at": "2026-08-03T08:00:00+05:30",
                    "next_step": "Need historical",
                    "blockers": ["Need historical"],
                },
                local_data_dir=root,
            )
            self.assertIsNone(read_checklist_cache("2026-08-04", local_data_dir=root))

    def test_invalidate_removes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_universe_manifest_atomic(root / "universe_manifest.json")
            write_checklist_cache(
                {
                    "session_date": "2026-08-03",
                    "overall_status": "ok",
                    "checked_at": "2026-08-03T08:00:00+05:30",
                    "next_step": "",
                    "blockers": [],
                },
                local_data_dir=root,
            )
            invalidate_checklist_cache(local_data_dir=root)
            self.assertFalse((root / "checklist_cache.json").exists())

    def test_manifest_write_invalidates_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_universe_manifest_atomic(root / "universe_manifest.json")
            write_checklist_cache(
                {
                    "session_date": "2026-08-03",
                    "overall_status": "ok",
                    "checked_at": "2026-08-03T08:00:00+05:30",
                    "next_step": "",
                    "blockers": [],
                },
                local_data_dir=root,
            )
            self.assertTrue((root / "checklist_cache.json").exists())
            write_universe_manifest_atomic(root / "universe_manifest.json")
            self.assertFalse((root / "checklist_cache.json").exists())

    def test_current_universe_manifest_id_changes_with_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = current_universe_manifest_id(local_data_dir=root)
            self.assertTrue(before.startswith("missing:"))
            write_universe_manifest_atomic(root / "universe_manifest.json")
            after = current_universe_manifest_id(local_data_dir=root)
            self.assertNotEqual(before, after)


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode


if __name__ == "__main__":
    unittest.main()
