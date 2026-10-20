"""The API reaps the engine it starts, so no zombie outlives the session.

2026-10-19: Friday's engine exited at 15:30 but stayed a zombie of the API;
the 08:40 checklist read its pid as alive and refused to prepare Monday.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from api.services import execution_engine_runner as launcher
from engine_config import SessionRiskConfig


class EngineReaperTests(unittest.TestCase):
    def test_the_reaper_collects_the_exit_code(self) -> None:
        proc = subprocess.Popen([sys.executable, "-c", "raise SystemExit(3)"])
        launcher._reap_engine(proc)
        self.assertEqual(proc.returncode, 3)

    def test_a_started_engine_is_reaped_when_it_exits(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        with patch.object(launcher, "preflight", return_value=SimpleNamespace(can_start=True, checks=[])), \
                patch.object(launcher, "build_command", return_value=[sys.executable, "-c", "pass"]), \
                patch.object(launcher.config, "execution_engine_status_file", return_value=tmp / "status.json"), \
                patch.object(launcher.config, "execution_engine_db_path", return_value=tmp / "engine.db"):
            ok, _message, pid = launcher._start_engine(session_config=SessionRiskConfig(), live_orders=False)
        self.assertTrue(ok)
        reapers = [t for t in threading.enumerate() if t.name == f"reap-engine-{pid}"]
        self.assertEqual(len(reapers), 1)
        reapers[0].join(timeout=10)
        self.assertFalse(reapers[0].is_alive())
        # Already collected by the reaper: nothing left for anyone to wait on.
        with self.assertRaises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)


if __name__ == "__main__":
    unittest.main()
