"""Tests for runner status runner_state derivation."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from api.queries.status import read_runner_status

IST = ZoneInfo("Asia/Kolkata")


class RunnerStatusTests(unittest.TestCase):
    def test_running_when_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runner_status.json"
            path.write_text(
                json.dumps(
                    {
                        "session_date": "2026-08-03",
                        "subscribed_tokens": 100,
                        "feed_status": "STABLE",
                        "last_tick_time": datetime.now(IST).isoformat(),
                        "updated_at": datetime.now(IST).isoformat(),
                    }
                ),
                encoding="utf-8",
            )
            status = read_runner_status(str(path), expected_session_date="2026-08-03")
            self.assertEqual(status.runner_state, "running")
            self.assertEqual(status.feed_status, "STABLE")

    def test_stopped_when_expired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runner_status.json"
            old = datetime.now(IST) - timedelta(seconds=120)
            path.write_text(
                json.dumps(
                    {
                        "session_date": "2026-08-03",
                        "feed_status": "STABLE",
                        "updated_at": old.isoformat(),
                    }
                ),
                encoding="utf-8",
            )
            status = read_runner_status(str(path), expected_session_date="2026-08-03")
            self.assertEqual(status.runner_state, "stopped")

    def test_stopped_when_session_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runner_status.json"
            path.write_text(
                json.dumps(
                    {
                        "session_date": "2026-08-02",
                        "feed_status": "STABLE",
                        "updated_at": datetime.now(IST).isoformat(),
                    }
                ),
                encoding="utf-8",
            )
            status = read_runner_status(str(path), expected_session_date="2026-08-03")
            self.assertEqual(status.runner_state, "stopped")

    def test_stopped_when_missing(self) -> None:
        status = read_runner_status("/tmp/does-not-exist-runner-status.json")
        self.assertEqual(status.runner_state, "stopped")

    def test_stale_feed_still_running(self) -> None:
        """Feed STALE must not flip runner_state to stopped."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runner_status.json"
            path.write_text(
                json.dumps(
                    {
                        "session_date": "2026-08-03",
                        "feed_status": "STALE",
                        "updated_at": datetime.now(IST).isoformat(),
                    }
                ),
                encoding="utf-8",
            )
            status = read_runner_status(str(path), expected_session_date="2026-08-03")
            self.assertEqual(status.runner_state, "running")
            self.assertEqual(status.feed_status, "STALE")


if __name__ == "__main__":
    unittest.main()
