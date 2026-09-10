import unittest
from unittest.mock import patch
from api import config
from api.admin_config.store import AdminConfigStore
from trading_engine_store import TradingEngineStore
from tests import test_trading_engine_api as fixture


class ControlApiTests(unittest.TestCase):
    setUp = fixture.TradingEngineApiTests.setUp
    tearDown = fixture.TradingEngineApiTests.tearDown

    def test_trade_audit_returns_persisted_plan_and_only_owned_events(self):
        store = TradingEngineStore(config.trading_engine_db_path())
        trade = store.insert_candidate(setup_id="audit-s", continuation_rule_version="v1",
            session_date="2026-09-10", symbol="AAA", instrument_token=1, direction="UP",
            entry_estimate=100, tick_size=0.05, trigger_time="2026-09-10T10:00:00+05:30")
        store.update_trade(trade.trade_id, original_setup_json='{"machine_stop":95}',
                           qty_model_version=1, filled_qty=10, exited_qty=4, remaining_position_qty=6, pnl_provisional=True)
        store.append_event(trade.trade_id, "trail_requested", new_stop=97, actor="owner")
        store.append_event("unrelated", "must_not_leak")
        store.close()
        response = self.client.get(f"/api/v1/trading-engine/trades/{trade.trade_id}/audit")
        self.assertEqual(response.status_code,200,response.text)
        data = response.json()
        self.assertEqual(data["original_setup"], {"machine_stop":95})
        self.assertEqual(data["trade"]["remaining_position_qty"],6)
        self.assertTrue(data["trade"]["pnl_provisional"])
        self.assertEqual([e["action"] for e in data["events"]], ["trail_requested"])
        self.assertEqual(self.client.get("/api/v1/trading-engine/trades/missing/audit").status_code,404)

    def test_saved_arm_does_not_report_permission_when_stopped_or_unsynced(self):
        from api.routers.trading import _session_date
        from datetime import datetime, timezone
        day = _session_date(None)
        store = TradingEngineStore(config.trading_engine_db_path())
        run_id = store.start_run(session_date=day, live_orders_enabled=False, pid=None)
        store.save_session_arm(session_date=day, run_id=run_id, execution_mode="PAPER",
                               entry_mode="MANUAL", config_version_id="test", actor="test")
        store.close()
        admin = AdminConfigStore(config.admin_config_db_path())
        admin.set_entries_paused(False)
        admin.close()
        heartbeat = {"session_date": day, "broker_sync_at": datetime.now(timezone.utc).isoformat()}
        with patch("api.routers.trading.is_engine_running", return_value=False), \
             patch("api.services.trading_engine_runner.read_heartbeat", return_value=heartbeat):
            self.assertEqual(self.client.get("/api/v1/trading-engine/control").json()["strip"]["entry_permission"], "disarmed")
        with patch("api.routers.trading.is_engine_running", return_value=True), \
             patch("api.services.trading_engine_runner.read_heartbeat", return_value={"session_date": day}):
            self.assertEqual(self.client.get("/api/v1/trading-engine/control").json()["strip"]["entry_permission"], "data_not_ready")
        with patch("api.routers.trading.is_engine_running", return_value=True), \
             patch("api.services.trading_engine_runner.read_heartbeat", return_value={**heartbeat, "recovery_unresolved": True}):
            self.assertEqual(self.client.get("/api/v1/trading-engine/control").json()["strip"]["entry_permission"], "recovery_required")

    def test_command_returns_acceptance_and_deduplicates(self):
        body={"kind":"pause_entries","client_command_id":"one-operation"}
        first=self.client.post("/api/v1/trading-engine/commands",json=body)
        second=self.client.post("/api/v1/trading-engine/commands",json=body)
        self.assertEqual(first.status_code,202,first.text)
        self.assertEqual(first.json()["state"],"queued")
        self.assertEqual(first.json()["command_id"],second.json()["command_id"])
        read=self.client.get(f'/api/v1/trading-engine/commands/{first.json()["command_id"]}')
        self.assertEqual(read.json()["state"],"queued")
        conflict=self.client.post("/api/v1/trading-engine/commands",json={**body,"kind":"disarm"})
        self.assertEqual(conflict.status_code,409)

    def test_live_arm_rejected_without_authorization(self):
        response=self.client.post("/api/v1/trading-engine/commands",json={
            "kind":"arm_session","client_command_id":"live-attempt","execution_mode":"LIVE",
            "live_confirmation":True,"config_version_id":"v1"})
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.json()["detail"],"live_execution_not_authorized")

    def test_approval_requires_running_engine_and_whole_quantity(self):
        body={"kind":"approve_entry","client_command_id":"approve-1","setup_id":"s","continuation_rule_version":"v1"}
        response=self.client.post("/api/v1/trading-engine/commands",json=body)
        self.assertEqual(response.status_code,409)
        response=self.client.post("/api/v1/trading-engine/commands",json={**body,"qty_override":1.5})
        self.assertEqual(response.status_code,422)

    def test_control_without_fresh_heartbeat_never_reports_healthy_pnl(self):
        response=self.client.get("/api/v1/trading-engine/control")
        self.assertEqual(response.status_code,200,response.text)
        strip=response.json()["strip"]
        self.assertIsNone(strip["open_pnl"])
        self.assertIsNone(strip["sync_age_seconds"])
        self.assertEqual(strip["entry_permission"],"disarmed")

    def test_admin_exposes_effective_and_preserves_omitted_keys(self):
        admin=AdminConfigStore(config.admin_config_db_path())
        admin.update_config({"daily_loss_cap_inr":2995,"allocated_capital_inr":200000},actor="test")
        admin.arm_effective_config(actor="test")
        admin.close()
        old=self.client.get("/api/v1/admin/config").json()
        request={"values":{k:old["values"][k] for k in (
            "per_trade_risk_cap_inr","limited_per_trade_risk_cap_inr","daily_loss_cap_inr",
            "vwap_accept_gap_exclusive_max","vwap_limited_gap_inclusive_max")},
            "expected_version_id":old["version_id"]}
        response=self.client.patch("/api/v1/admin/config",json=request)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["values"]["allocated_capital_inr"],200000)
        self.assertEqual(response.json()["effective_values"]["daily_loss_cap_inr"],2995)
        request["values"]["allocated_capital_inr"]=300000
        request["expected_version_id"]=response.json()["version_id"]
        changed=self.client.patch("/api/v1/admin/config",json=request)
        self.assertEqual(changed.status_code,200,changed.text)
        self.assertEqual(changed.json()["effective_values"]["allocated_capital_inr"],200000)
        self.assertIn("allocated_capital_inr",changed.json()["pending_next_arm"])
