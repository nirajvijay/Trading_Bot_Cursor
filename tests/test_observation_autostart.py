from __future__ import annotations

import os
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from api.services import checklist_activity as activity
from api.services import observation_autostart as autostart
from nse_trading_calendar import IST

DAY = "2026-09-24"  # regular Thursday session


def at(hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{DAY}T{hhmm}:00").replace(tzinfo=IST)


class ObservationAutostartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"NIFTY_RADAR_RUNTIME_CACHE_DIR": str(Path(self.tmp.name) / "cache")})
        self.env.start()
        self.today = patch.object(activity, "today", return_value=DAY)
        self.today.start()
        self.running = patch("api.services.observation_runner.is_runner_running", return_value=False)
        self.running.start()
        self.start = patch("api.services.observation_runner.start_observation_runner",
                           return_value=(True, "started", 4242))
        self.start_mock = self.start.start()

    def tearDown(self):
        for p in (self.start, self.running, self.today, self.env):
            p.stop()
        self.tmp.cleanup()

    def completed(self, **extra):
        activity.save_activity({"session_date": DAY, "source": "automatic", "status": "completed",
                                "stage": "validation", "dirty": [], **extra})

    def log(self) -> str:
        # run_log names the file by the real clock date.
        return "".join(p.read_text() for p in (Path(self.tmp.name) / "logs").glob("morning-checklist-*.log"))

    def test_waits_for_0900_then_starts_once(self):
        self.completed()
        self.assertIsNone(autostart.autostart_tick(at("08:55")))
        self.assertEqual(autostart.autostart_tick(at("09:00")), "observation started (pid 4242)")
        self.assertIsNone(autostart.autostart_tick(at("09:01")))
        self.start_mock.assert_called_once_with(DAY)
        self.assertIn("observation autostart: observation started (pid 4242)", self.log())
        self.assertTrue(autostart.autostarted_today(DAY))

    def test_waits_while_checklist_lock_is_held(self):
        self.completed()
        with activity.workflow_lock():
            self.assertIsNone(autostart.autostart_tick(at("09:00")))
        self.assertIsNotNone(autostart.autostart_tick(at("09:00")))
        self.start_mock.assert_called_once_with(DAY)

    def test_never_starts_after_0915(self):
        self.completed()
        self.assertIsNone(autostart.autostart_tick(at("09:15")))
        self.start_mock.assert_not_called()

    def test_requires_completed_automatic_run(self):
        for record in ({"source": "automatic", "status": "blocked"},
                       {"source": "automatic", "status": "running"},
                       {"source": "manual", "status": "completed"},
                       {"source": "automatic", "status": "completed", "dirty": ["baselines"]}):
            activity.save_activity({"session_date": DAY, "stage": "kite", "dirty": [], **record})
            self.assertIsNone(autostart.autostart_tick(at("09:05")))
        self.start_mock.assert_not_called()
        # Completing later, before 09:15, still starts.
        self.completed()
        self.assertIsNotNone(autostart.autostart_tick(at("09:10")))

    def test_skips_holidays(self):
        self.completed()
        holiday = datetime(2026, 10, 2, 9, 5, tzinfo=IST)
        with patch.object(activity, "today", return_value="2026-10-02"):
            self.assertIsNone(autostart.autostart_tick(holiday))
        self.start_mock.assert_not_called()

    def test_already_running_is_not_restarted(self):
        self.completed()
        with patch("api.services.observation_runner.is_runner_running", return_value=True):
            self.assertIn("already running", autostart.autostart_tick(at("09:00")))
        self.start_mock.assert_not_called()
        self.assertFalse(autostart.autostarted_today(DAY))

    def test_refused_start_is_logged_and_not_retried(self):
        self.completed()
        self.start_mock.return_value = (False, "Complete Pre-Market Checklist first", None)
        self.assertIn("not started", autostart.autostart_tick(at("09:00")))
        self.assertIsNone(autostart.autostart_tick(at("09:01")))
        self.assertEqual(self.start_mock.call_count, 1)
        self.assertIn("observation not started: Complete Pre-Market Checklist first", self.log())
        self.assertFalse(autostart.autostarted_today(DAY))

    def test_background_thread_only_in_production(self):
        stop = threading.Event()
        with patch.dict(os.environ, {"APP_ENV": "development"}):
            self.assertIsNone(autostart.start_background(stop))
        with patch.dict(os.environ, {"APP_ENV": "production"}), patch.object(autostart, "POLL_SECONDS", 0.01), \
             patch.object(autostart, "autostart_tick") as tick:
            thread = autostart.start_background(stop)
            self.assertIsNotNone(thread)
            import time
            time.sleep(0.1)
            stop.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertGreater(tick.call_count, 0)


if __name__ == "__main__":
    unittest.main()
