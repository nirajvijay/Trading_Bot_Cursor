"""Tests for the /api/v1/vwap/health reader (api.queries.status.read_vwap_health)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from api.queries.status import read_vwap_health


class VwapHealthStatusTests(unittest.TestCase):
    def test_unknown_when_no_file_argument(self) -> None:
        status = read_vwap_health(None)
        self.assertEqual(status.status, "unknown")

    def test_unknown_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vwap_health.json"
            status = read_vwap_health(str(path))
            self.assertEqual(status.status, "unknown")

    def test_ok_maps_through(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vwap_health.json"
            path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "session_date": "2026-09-23",
                        "triggered_count": 3,
                        "qualified_count": 3,
                        "stuck_count": 0,
                        "callback_failures": 0,
                        "persist_failures": 0,
                        "reason": None,
                        "checked_at": "2026-09-23T10:00:00+05:30",
                    }
                ),
                encoding="utf-8",
            )
            status = read_vwap_health(str(path))
            self.assertEqual(status.status, "ok")
            self.assertEqual(status.triggered_count, 3)
            self.assertEqual(status.qualified_count, 3)

    def test_idle_maps_through(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vwap_health.json"
            path.write_text(
                json.dumps(
                    {
                        "status": "idle",
                        "session_live": False,
                        "session_date": "2026-09-23",
                        "triggered_count": 0,
                        "qualified_count": 0,
                        "stuck_count": 0,
                        "callback_failures": 0,
                        "persist_failures": 0,
                        "reason": None,
                        "checked_at": "2026-09-23T09:00:00+05:30",
                    }
                ),
                encoding="utf-8",
            )
            status = read_vwap_health(str(path))
            self.assertEqual(status.status, "idle")
            self.assertFalse(status.session_live)

    def test_legacy_ok_file_without_status_still_reads(self) -> None:
        """A file written before the status field existed must not read as alarm."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vwap_health.json"
            path.write_text(json.dumps({"ok": True, "session_date": "2026-09-23"}), encoding="utf-8")
            self.assertEqual(read_vwap_health(str(path)).status, "ok")

    def test_alarm_maps_through_with_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vwap_health.json"
            path.write_text(
                json.dumps(
                    {
                        "ok": False,
                        "session_date": "2026-09-23",
                        "triggered_count": 4,
                        "qualified_count": 0,
                        "stuck_count": 4,
                        "callback_failures": 4,
                        "persist_failures": 0,
                        "reason": "4 VWAP classify call(s) crashed before persisting a verdict",
                        "checked_at": "2026-09-23T10:00:00+05:30",
                    }
                ),
                encoding="utf-8",
            )
            status = read_vwap_health(str(path))
            self.assertEqual(status.status, "alarm")
            self.assertEqual(status.stuck_count, 4)
            self.assertIn("crashed", status.reason or "")

    def test_corrupt_json_falls_back_to_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vwap_health.json"
            path.write_text("{not valid json", encoding="utf-8")
            status = read_vwap_health(str(path))
            self.assertEqual(status.status, "unknown")


if __name__ == "__main__":
    unittest.main()
