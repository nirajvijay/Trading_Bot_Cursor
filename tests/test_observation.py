"""Tests for observation readiness and start endpoints."""

from __future__ import annotations

import json
import tempfile
import unittest
import unittest.mock
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from api.main import app
from api.services.observation_runner import (
    _build_runner_command,
    compute_readiness,
    expected_stop_at_iso,
    is_market_open,
    is_runner_running,
    seconds_until_session_close,
    session_close_datetime,
)
from live_observation_runner import parse_args

IST = ZoneInfo("Asia/Kolkata")


class ObservationRunnerTests(unittest.TestCase):
    def test_is_market_open_weekday_session(self) -> None:
        dt = datetime(2026, 8, 3, 10, 0, tzinfo=IST)
        self.assertTrue(is_market_open(dt))

    def test_is_market_closed_before_open(self) -> None:
        dt = datetime(2026, 8, 3, 9, 0, tzinfo=IST)
        self.assertFalse(is_market_open(dt))

    def test_is_market_closed_weekend(self) -> None:
        dt = datetime(2026, 8, 2, 10, 0, tzinfo=IST)
        self.assertFalse(is_market_open(dt))

    def test_is_runner_running_when_status_recent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runner_status.json"
            path.write_text(
                json.dumps(
                    {
                        "session_date": "2026-08-03",
                        "subscribed_tokens": 50,
                        "feed_status": "STABLE",
                        "updated_at": datetime.now(IST).isoformat(),
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(is_runner_running(path))

    def test_is_runner_running_false_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.json"
            self.assertFalse(is_runner_running(path))

    @patch("api.services.observation_runner.fetch_checklist_summary")
    @patch("api.services.observation_runner.is_runner_running", return_value=False)
    @patch("api.services.observation_runner.is_market_open", return_value=False)
    def test_compute_readiness_blocks_when_market_closed(
        self, _mock_market, _mock_runner, mock_checklist
    ) -> None:
        mock_checklist.return_value = {
            "overall_status": "ok",
            "session_date": "2026-08-03",
        }
        result = compute_readiness("2026-08-03")
        self.assertTrue(result["checklist_ok"])
        self.assertFalse(result["market_open"])
        self.assertFalse(result["can_start"])
        self.assertIn("Market closed", result["reason"])

    @patch("api.services.observation_runner.fetch_checklist_summary")
    @patch("api.services.observation_runner.is_runner_running", return_value=False)
    @patch("api.services.observation_runner.is_market_open", return_value=True)
    def test_compute_readiness_blocks_when_checklist_not_ok(
        self, _mock_market, _mock_runner, mock_checklist
    ) -> None:
        mock_checklist.return_value = {
            "overall_status": "warning",
            "session_date": "2026-08-03",
        }
        result = compute_readiness("2026-08-03")
        self.assertFalse(result["checklist_ok"])
        self.assertFalse(result["can_start"])
        self.assertIn("Pre-Market Checklist", result["reason"])

    @patch("api.services.observation_runner.fetch_checklist_summary")
    @patch("api.services.observation_runner.is_runner_running", return_value=False)
    @patch("api.services.observation_runner.is_market_open", return_value=True)
    def test_compute_readiness_can_start_when_ready(
        self, _mock_market, _mock_runner, mock_checklist
    ) -> None:
        mock_checklist.return_value = {
            "overall_status": "ok",
            "session_date": "2026-08-03",
        }
        result = compute_readiness("2026-08-03")
        self.assertTrue(result["can_start"])
        self.assertEqual(result["reason"], "")
        self.assertIsNotNone(result["expected_stop_at"])


class SessionCloseScheduleTests(unittest.TestCase):
    def test_seconds_until_session_close_at_10am(self) -> None:
        dt = datetime(2026, 8, 3, 10, 0, tzinfo=IST)
        self.assertAlmostEqual(seconds_until_session_close(dt), 5.5 * 3600, places=0)

    def test_seconds_until_session_close_at_1529(self) -> None:
        dt = datetime(2026, 8, 3, 15, 29, tzinfo=IST)
        self.assertAlmostEqual(seconds_until_session_close(dt), 60.0, places=0)

    def test_seconds_until_session_close_after_close_is_minimum(self) -> None:
        dt = datetime(2026, 8, 3, 15, 31, tzinfo=IST)
        self.assertEqual(seconds_until_session_close(dt), 1.0)

    def test_session_close_datetime(self) -> None:
        dt = datetime(2026, 8, 3, 11, 45, 30, tzinfo=IST)
        close = session_close_datetime(dt)
        self.assertEqual(close.hour, 15)
        self.assertEqual(close.minute, 30)
        self.assertEqual(close.second, 0)

    def test_expected_stop_at_iso(self) -> None:
        dt = datetime(2026, 8, 3, 10, 0, tzinfo=IST)
        self.assertIn("15:30:00", expected_stop_at_iso(dt))

    def test_build_runner_command_includes_until_session_close(self) -> None:
        cmd = _build_runner_command()
        self.assertIn("--until-session-close", cmd)

    def test_parse_args_until_session_close(self) -> None:
        args = parse_args(["--until-session-close"])
        self.assertTrue(args.until_session_close)
        self.assertEqual(args.duration_minutes, 60.0)


class SessionCloseEngineTests(unittest.TestCase):
    def test_on_session_closed_invoked_for_session_close_reason(self) -> None:
        engine = unittest.mock.MagicMock()
        session_date = "2026-08-03"
        stop_reason = "session_close"
        if stop_reason == "session_close":
            engine.on_session_closed(session_date)
        engine.on_session_closed.assert_called_once_with(session_date)



class ObservationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        from api.routers import auth as auth_router

        app.dependency_overrides[auth_router.require_localhost] = lambda: None
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_readiness_endpoint(self) -> None:
        with patch("api.routers.observation.compute_readiness") as mock_ready:
            mock_ready.return_value = {
                "checklist_ok": False,
                "checklist_status": "warning",
                "market_open": False,
                "runner_running": False,
                "can_start": False,
                "reason": "Complete Pre-Market Checklist first",
                "session_date": "2026-08-03",
            }
            client = self.client
            res = client.get("/api/v1/observation/readiness?session_date=2026-08-03")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertFalse(body["can_start"])
        self.assertEqual(body["session_date"], "2026-08-03")

    def test_start_rejects_when_checklist_not_ok(self) -> None:
        with patch("api.routers.observation.compute_readiness") as mock_ready:
            mock_ready.return_value = {
                "checklist_ok": False,
                "checklist_status": "warning",
                "market_open": True,
                "runner_running": False,
                "can_start": False,
                "reason": "Complete Pre-Market Checklist first",
                "session_date": "2026-08-03",
            }
            client = self.client
            res = client.post("/api/v1/observation/start?session_date=2026-08-03")
        self.assertEqual(res.status_code, 400)
        self.assertIn("Pre-Market Checklist", res.json()["detail"])

    def test_start_rejects_when_market_closed(self) -> None:
        with patch("api.routers.observation.compute_readiness") as mock_ready:
            mock_ready.return_value = {
                "checklist_ok": True,
                "checklist_status": "ok",
                "market_open": False,
                "runner_running": False,
                "can_start": False,
                "reason": "Market closed — available 09:15–15:30 IST on weekdays",
                "session_date": "2026-08-03",
            }
            client = self.client
            res = client.post("/api/v1/observation/start?session_date=2026-08-03")
        self.assertEqual(res.status_code, 400)
        self.assertIn("Market closed", res.json()["detail"])

    def test_start_succeeds_when_gated_ok(self) -> None:
        with patch("api.routers.observation.compute_readiness") as mock_ready, patch(
            "api.routers.observation.start_observation_runner",
            return_value=(True, "Observation runner started (pid 12345)", 12345),
        ):
            mock_ready.return_value = {
                "checklist_ok": True,
                "checklist_status": "ok",
                "market_open": True,
                "runner_running": False,
                "can_start": True,
                "reason": "",
                "session_date": "2026-08-03",
            }
            client = self.client
            res = client.post("/api/v1/observation/start?session_date=2026-08-03")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["pid"], 12345)


if __name__ == "__main__":
    unittest.main()
