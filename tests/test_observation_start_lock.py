"""Tests for observation start lease covering the startup heartbeat gap."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from api.services.observation_start_lock import (
    ObservationStartBusy,
    acquire_start_lock,
    is_start_lease_active,
    reconcile_start_lock_with_heartbeat,
    release_start_lock,
    update_start_lock_pid,
)
from api.services.observation_runner import start_observation_runner


class ObservationStartLockTests(unittest.TestCase):
    def test_second_acquire_blocked_while_live_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = acquire_start_lock("2026-08-03", local_data_dir=root)
            update_start_lock_pid(path, pid=os.getpid(), session_date="2026-08-03")
            with self.assertRaises(ObservationStartBusy):
                acquire_start_lock("2026-08-03", local_data_dir=root)
            release_start_lock(path, local_data_dir=root)

    def test_dead_pid_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = acquire_start_lock("2026-08-03", local_data_dir=root)
            update_start_lock_pid(path, pid=999_999_999, session_date="2026-08-03")
            # Dead PID → reclaim succeeds.
            path2 = acquire_start_lock("2026-08-03", local_data_dir=root)
            self.assertTrue(path2.exists())
            release_start_lock(path2, local_data_dir=root)

    def test_lease_active_until_heartbeat_reconcile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = acquire_start_lock("2026-08-03", local_data_dir=root)
            update_start_lock_pid(path, pid=os.getpid(), session_date="2026-08-03")
            self.assertTrue(is_start_lease_active("2026-08-03", local_data_dir=root))
            reconcile_start_lock_with_heartbeat(
                session_date="2026-08-03",
                heartbeat_fresh=True,
                local_data_dir=root,
            )
            self.assertFalse(is_start_lease_active("2026-08-03", local_data_dir=root))

    def test_start_holds_lease_across_popen_gap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_proc = MagicMock()
            fake_proc.pid = os.getpid()

            with patch(
                "api.services.observation_runner.compute_readiness",
                return_value={
                    "checklist_ok": True,
                    "market_open": True,
                    "runner_running": False,
                    "reason": "",
                    "session_date": "2026-08-03",
                },
            ), patch(
                "api.services.observation_runner.is_status_heartbeat_fresh",
                return_value=False,
            ), patch(
                "api.services.observation_runner.acquire_start_lock",
                side_effect=lambda session_date: acquire_start_lock(
                    session_date, local_data_dir=root
                ),
            ), patch(
                "api.services.observation_runner.update_start_lock_pid",
                side_effect=lambda path, pid, session_date: update_start_lock_pid(
                    path, pid=pid, session_date=session_date
                ),
            ), patch(
                "api.services.observation_runner.subprocess.Popen",
                return_value=fake_proc,
            ), patch(
                "api.services.observation_runner.release_start_lock"
            ) as mock_release:
                ok, message, pid = start_observation_runner("2026-08-03")
                self.assertTrue(ok)
                self.assertEqual(pid, fake_proc.pid)
                self.assertIn("started", message)
                mock_release.assert_not_called()
                self.assertTrue(is_start_lease_active("2026-08-03", local_data_dir=root))

                # Second start while lease held → busy.
                with patch(
                    "api.services.observation_runner.compute_readiness",
                    return_value={
                        "checklist_ok": True,
                        "market_open": True,
                        "runner_running": True,
                        "reason": "Observation runner is already running",
                        "session_date": "2026-08-03",
                    },
                ):
                    ok2, msg2, pid2 = start_observation_runner("2026-08-03")
                self.assertFalse(ok2)
                self.assertIsNone(pid2)
                self.assertIn("already", msg2.lower())


if __name__ == "__main__":
    unittest.main()
