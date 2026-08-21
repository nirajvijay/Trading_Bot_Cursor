"""HTTP API for the trading engine."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api.main import app
from trading_engine_store import TradingEngineStore
from trading_engine_types import DEFAULT_TOTAL_CAPITAL


class TradingEngineApiTests(unittest.TestCase):
    def setUp(self) -> None:
        from tests.auth_test_helpers import disable_web_auth_overrides, make_test_client

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "trading_engine.db"
        self.prev = {}
        for key, value in {
            "TRADING_ENGINE_DB_PATH": str(self.db),
            "TRADING_ENGINE_STATUS_FILE": str(self.root / "te_status.json"),
            "TRADING_ENGINE_STOP_FILE": str(self.root / "te.stop"),
            "TRADING_ENGINE_LIVE_ORDERS": "false",
            "NIFTY_RADAR_DATA_ROOT": str(self.root / "data"),
            "NIFTY_RADAR_RUNTIME_CACHE_DIR": str(self.root / "cache"),
            "LOCAL_DATA_DIR": str(self.root / "data" / "local"),
        }.items():
            self.prev[key] = os.environ.get(key)
            os.environ[key] = value
        disable_web_auth_overrides()
        self.client = make_test_client()

    def tearDown(self) -> None:
        from tests.auth_test_helpers import clear_auth_overrides

        app.dependency_overrides.clear()
        clear_auth_overrides()
        for key, value in self.prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def test_status_stopped_when_empty(self) -> None:
        res = self.client.get("/api/v1/trading-engine/status?session_date=2026-08-17")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["state"], "stopped")
        self.assertEqual(body["total_capital"], DEFAULT_TOTAL_CAPITAL)
        self.assertFalse(body["engine_running"])
        self.assertTrue(body["can_confirm_live"])
        self.assertFalse(body["accepting_triggers"])
        self.assertTrue(body["require_vwap_accept"])

    def test_snapshot_empty_buckets(self) -> None:
        res = self.client.get("/api/v1/trading-engine/snapshot?session_date=2026-08-17")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["active"], [])
        self.assertEqual(body["closed"], [])
        self.assertEqual(body["skipped"], [])

    def test_start_mocked_popen(self) -> None:
        with patch("api.services.trading_engine_runner.subprocess.Popen") as popen:
            popen.return_value.pid = 4242
            res = self.client.post(
                "/api/v1/trading-engine/start",
                json={"confirm_live_orders": False, "session_date": "2026-08-17"},
            )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["success"])
        self.assertEqual(res.json()["pid"], 4242)

    def test_start_acks_leftover_stop_engine(self) -> None:
        store = TradingEngineStore(self.db)
        store.enqueue_command("stop_engine")
        self.assertEqual(len(store.pending_commands()), 1)
        store.close()
        with patch("api.services.trading_engine_runner.subprocess.Popen") as popen:
            popen.return_value.pid = 4244
            res = self.client.post(
                "/api/v1/trading-engine/start",
                json={"confirm_live_orders": False, "session_date": "2026-08-17"},
            )
        self.assertEqual(res.status_code, 200)
        store = TradingEngineStore(self.db)
        self.assertEqual(store.pending_commands(), [])
        store.close()

    def test_unchecked_start_omits_live_flag_even_if_env_true(self) -> None:
        os.environ["TRADING_ENGINE_LIVE_ORDERS"] = "true"
        with patch("api.services.trading_engine_runner.subprocess.Popen") as popen:
            popen.return_value.pid = 4245
            res = self.client.post(
                "/api/v1/trading-engine/start",
                json={"confirm_live_orders": False, "session_date": "2026-08-17"},
            )
        self.assertEqual(res.status_code, 200)
        cmd = popen.call_args[0][0]
        self.assertNotIn("--live-orders", cmd)

    def test_live_start_follows_ui_checkbox_not_env(self) -> None:
        os.environ["TRADING_ENGINE_LIVE_ORDERS"] = "false"
        with patch("api.services.trading_engine_runner.subprocess.Popen") as popen:
            popen.return_value.pid = 4243
            res = self.client.post(
                "/api/v1/trading-engine/start",
                json={"confirm_live_orders": True, "session_date": "2026-08-17"},
            )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["success"])
        cmd = popen.call_args[0][0]
        self.assertIn("--live-orders", cmd)

    def test_capital_and_trail(self) -> None:
        cap = self.client.post("/api/v1/trading-engine/capital", json={"total_capital": 250000})
        self.assertEqual(cap.status_code, 200)
        self.assertEqual(cap.json()["total_capital"], 250000)

        store = TradingEngineStore(self.db)
        trade = store.insert_candidate(
            setup_id="s",
            continuation_rule_version="v1",
            session_date="2026-08-17",
            symbol="AAA",
            instrument_token=1,
            direction="UP",
            entry_estimate=110,
            tick_size=1,
            trigger_time="t",
            qty=10,
            initial_stop=99,
            status="protected_open",
        )
        assert trade is not None
        store.update_trade(trade.trade_id, sl_order_id="sl1", current_stop=99)
        store.close()

        trail = self.client.post(
            f"/api/v1/trading-engine/trades/{trade.trade_id}/trail-stop",
            json={"new_stop": 105},
        )
        self.assertEqual(trail.status_code, 200)
        store = TradingEngineStore(self.db)
        cmds = store.pending_commands()
        self.assertEqual(cmds[0].kind, "trail_stop")
        store.close()

        snap = self.client.get("/api/v1/trading-engine/snapshot?session_date=2026-08-17")
        body = snap.json()
        self.assertEqual(len(body["active"]), 1)
        self.assertEqual(body["closed"], [])
        self.assertEqual(body["skipped"], [])
        self.assertIn("tick_size", body["active"][0])

        auto = self.client.post(
            f"/api/v1/trading-engine/trades/{trade.trade_id}/auto-trail",
            json={"enabled": True},
        )
        self.assertEqual(auto.status_code, 200)
        store = TradingEngineStore(self.db)
        kinds = [c.kind for c in store.pending_commands()]
        self.assertIn("set_auto_trail", kinds)
        store.close()

    def test_stop_writes_file(self) -> None:
        res = self.client.post("/api/v1/trading-engine/stop")
        self.assertEqual(res.status_code, 200)
        self.assertTrue((self.root / "te.stop").exists())
        store = TradingEngineStore(self.db)
        self.assertEqual([c.kind for c in store.pending_commands()], [])
        store.close()

    def test_heartbeat_running_false_is_not_engine_running(self) -> None:
        import json
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from api.services.trading_engine_runner import is_engine_running

        now = datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(timespec="seconds")
        Path(os.environ["TRADING_ENGINE_STATUS_FILE"]).write_text(
            json.dumps(
                {
                    "updated_at": now,
                    "running": False,
                    "state": "stopped",
                    "session_date": "2026-08-17",
                }
            ),
            encoding="utf-8",
        )
        self.assertFalse(is_engine_running(session_date="2026-08-17"))


if __name__ == "__main__":
    unittest.main()
