"""Real calendar/time gates and connection telemetry; no broker or subprocess writes."""
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from api.runner_status import write_runner_status
from api.queries.status import read_runner_status
from api.services.observation_runner import compute_readiness, observation_start_allowed

IST = ZoneInfo("Asia/Kolkata")


class ObservationStage2Tests(unittest.TestCase):
    def test_0900_start_is_not_market_open(self):
        with patch("api.services.observation_runner.fetch_checklist_summary", return_value={
            "overall_status": "ok", "session_date": "2026-08-03",
        }), patch("api.services.observation_runner.is_runner_running", return_value=False):
            result = compute_readiness("2026-08-03", now=datetime(2026, 8, 3, 9, tzinfo=IST))
        self.assertTrue(result["can_start"])
        self.assertFalse(result["market_open"])
        self.assertIn("waiting", result["reason"])
        self.assertIn("15:30", result["expected_stop_at"])

    def test_start_window_bounds_and_session_identity(self):
        opening = datetime(2026, 8, 3, 9, tzinfo=IST)
        self.assertFalse(observation_start_allowed("2026-08-03", opening - timedelta(seconds=1)))
        self.assertTrue(observation_start_allowed("2026-08-03", opening))
        self.assertFalse(observation_start_allowed("2026-08-02", opening))
        self.assertFalse(observation_start_allowed("2026-08-03", opening.replace(hour=15, minute=30)))
        for day in (datetime(2026, 8, 2, 10, tzinfo=IST), datetime(2026, 1, 26, 10, tzinfo=IST),
                    datetime(2026, 11, 8, 10, tzinfo=IST)):
            self.assertFalse(observation_start_allowed(day.date().isoformat(), day))

    def test_connection_without_ticks_is_waiting_not_disconnected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            for connected, tick, feed, phase in (
                (True, None, "WAITING", "connected_waiting_market_data"),
                (False, None, "DISCONNECTED", "disconnected"),
                (True, datetime.now(IST).isoformat(), "STABLE", "receiving_market_data"),
                (True, datetime.now(IST).isoformat(), "STALE", "stale_market_data"),
            ):
                write_runner_status(path, session_date="2026-08-03", subscribed_tokens=100,
                                    feed_status=feed, last_tick_time=tick, websocket_connected=connected)
                result = read_runner_status(str(path), expected_session_date="2026-08-03")
                self.assertEqual(result.observation_phase, phase)
                self.assertEqual(result.websocket_connected, connected)
                self.assertEqual(result.runner_state, "running")
