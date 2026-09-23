from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from api.services import checklist_activity as activity
from api.services import execution_autostart as autostart
from api.services import observation_autostart
from engine_config import SessionRiskConfig
from nse_trading_calendar import IST

DAY = "2026-09-24"  # regular Thursday session


def at(hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{DAY}T{hhmm}:00").replace(tzinfo=IST)


class ExecutionAutostartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"NIFTY_RADAR_RUNTIME_CACHE_DIR": str(Path(self.tmp.name) / "cache")})
        self.env.start()
        self.start = patch("api.services.execution_engine_runner.start_engine",
                           return_value=(True, "execution engine started (pid 777)", 777))
        self.start_mock = self.start.start()

    def tearDown(self):
        for p in (self.start, self.env):
            p.stop()
        self.tmp.cleanup()

    def observation_autostarted(self):
        from api import config
        config.runtime_cache_dir().mkdir(parents=True, exist_ok=True)
        observation_autostart._record_started(DAY, 4242)

    def log(self) -> str:
        return "".join(p.read_text() for p in (Path(self.tmp.name) / "logs").glob("morning-checklist-*.log"))

    def test_waits_for_open_then_starts_live_once_with_default_caps(self):
        self.observation_autostarted()
        self.assertIsNone(autostart.autostart_tick(at("09:14")))
        self.assertEqual(autostart.autostart_tick(at("09:15")),
                         "execution engine started with live Kite orders (pid 777)")
        self.assertIsNone(autostart.autostart_tick(at("09:16")))
        self.start_mock.assert_called_once_with(session_config=SessionRiskConfig(), session_date=DAY,
                                                live_orders=True, now=at("09:15"))
        caps = self.start_mock.call_args.kwargs["session_config"]
        self.assertEqual((caps.per_trade_cap_rupees, caps.per_trade_cap_vwap_limited_rupees,
                          caps.daily_loss_cap_rupees, caps.total_capital_rupees), (50.0, 25.0, 150.0, 300_000.0))
        self.assertIn("execution autostart: execution engine started with live Kite orders (pid 777)", self.log())

    def test_never_starts_without_observation_autostart(self):
        for hhmm in ("09:15", "09:20", "09:29"):
            self.assertIsNone(autostart.autostart_tick(at(hhmm)))
        self.start_mock.assert_not_called()

    def test_never_starts_after_0930(self):
        self.observation_autostarted()
        self.assertIsNone(autostart.autostart_tick(at("09:30")))
        self.assertIsNone(autostart.autostart_tick(at("11:00")))
        self.start_mock.assert_not_called()

    def test_waits_while_checklist_lock_is_held(self):
        self.observation_autostarted()
        with activity.workflow_lock():
            self.assertIsNone(autostart.autostart_tick(at("09:15")))
        self.assertIsNotNone(autostart.autostart_tick(at("09:16")))
        self.start_mock.assert_called_once()

    def test_refused_start_is_logged_and_not_retried(self):
        self.observation_autostarted()
        self.start_mock.return_value = (False, "observation_runner: observation_runner_not_running", None)
        self.assertIn("not started", autostart.autostart_tick(at("09:15")))
        self.assertIsNone(autostart.autostart_tick(at("09:16")))
        self.assertEqual(self.start_mock.call_count, 1)
        self.assertIn("execution engine not started: observation_runner: observation_runner_not_running",
                      self.log())

    def test_exception_is_logged_and_not_retried(self):
        self.observation_autostarted()
        self.start_mock.side_effect = RuntimeError("boom")
        self.assertEqual(autostart.autostart_tick(at("09:15")), "execution engine not started: RuntimeError")
        self.assertIsNone(autostart.autostart_tick(at("09:16")))
        self.assertEqual(self.start_mock.call_count, 1)

    def test_background_thread_only_in_production(self):
        stop = threading.Event()
        with patch.dict(os.environ, {"APP_ENV": "development"}):
            self.assertIsNone(autostart.start_background(stop))
        with patch.dict(os.environ, {"APP_ENV": "production"}), patch.object(autostart, "POLL_SECONDS", 0.01), \
             patch.object(autostart, "autostart_tick") as tick:
            thread = autostart.start_background(stop)
            self.assertIsNotNone(thread)
            time.sleep(0.1)
            stop.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertGreater(tick.call_count, 0)


if __name__ == "__main__":
    unittest.main()
