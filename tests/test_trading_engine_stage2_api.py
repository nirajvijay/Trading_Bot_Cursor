import unittest
from unittest.mock import patch
from api import config
from api.admin_config.store import AdminConfigStore
from trading_engine_store import TradingEngineStore
from tests import test_trading_engine_api as fixture


class ControlApiTests(unittest.TestCase):
    setUp = fixture.TradingEngineApiTests.setUp
    tearDown = fixture.TradingEngineApiTests.tearDown

    def test_live_preference_never_authorizes_or_arms_engine(self):
        admin = AdminConfigStore(config.admin_config_db_path())
        admin.update_config({"preferred_execution_mode": "LIVE"}, actor="tester")
        admin.close()
        with patch("api.routers.trading.is_engine_running", return_value=False):
            response = self.client.get("/api/v1/trading-engine/control")
        self.assertEqual(response.status_code, 200, response.text)
        state = response.json()
        self.assertEqual(state["saved"]["preferred_execution_mode"], "LIVE")
        self.assertFalse(state["live_execution_authorized"])
        self.assertEqual(state["strip"]["entry_permission"], "disarmed")
        self.test_live_arm_rejected_without_authorization()

    def test_report_separates_modes_and_excludes_provisional_outcomes(self):
        store = TradingEngineStore(config.trading_engine_db_path())
        for i, (mode, provisional, pnl) in enumerate(((False,False,-10),(False,True,999),(True,False,20),(None,False,30))):
            trade = store.insert_candidate(setup_id=f"report-{i}",continuation_rule_version="v1",
                session_date="2026-09-10",symbol="AAA",instrument_token=1,direction="UP",
                entry_estimate=100,tick_size=.05,trigger_time="2026-09-10T10:00:00+05:30")
            store.update_trade(trade.trade_id,qty_model_version=1,status="closed",filled_qty=1,exited_qty=1,
                entry_live_orders_enabled=mode,pnl_provisional=provisional,realised_pnl=pnl)
        store.close()
        response=self.client.get("/api/v1/trading-engine/report?session_date=2026-09-10&download=true")
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn("attachment",response.headers["content-disposition"])
        modes=response.json()["modes"]
        self.assertEqual(modes["PAPER"]["strategy_outcomes"]["complete_closed_recorded_pnl"],-10)
        self.assertEqual(modes["PAPER"]["strategy_outcomes"]["losses"],1)
        self.assertEqual(len(modes["PAPER"]["engineering_quality"]["provisional_accounting_trade_ids"]),1)
        self.assertEqual(modes["LIVE"]["strategy_outcomes"]["complete_closed_recorded_pnl"],20)
        self.assertEqual(modes["UNKNOWN"]["strategy_outcomes"]["complete_closed_recorded_pnl"],30)
        self.assertEqual(self.client.get("/api/v1/trading-engine/report?session_date=invalid").status_code,400)

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
        heartbeat = {"run_id": run_id, "session_date": day, "broker_sync_at": datetime.now(timezone.utc).isoformat()}
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

    def test_old_run_heartbeat_cannot_report_armed_or_open_pnl(self):
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
        now = datetime.now(timezone.utc).isoformat()
        heartbeat = {"run_id":"previous-run", "session_date":day, "broker_sync_at":now,
                     "loss_halt":{"complete":True, "unrealised":1234, "as_of":now, "mark_age_seconds":0}}
        with patch("api.routers.trading.is_engine_running", return_value=True), \
             patch("api.services.trading_engine_runner.read_heartbeat", return_value=heartbeat), \
             patch("trading_engine_cycle.feed_age_seconds_from_runner_status", return_value=0):
            response = self.client.get("/api/v1/trading-engine/control")
            self.assertEqual(response.status_code, 200, response.text)
            strip = response.json()["strip"]
            self.assertEqual(strip["entry_permission"], "data_not_ready")
            self.assertIsNone(strip["open_pnl"])
            self.assertIsNone(strip["sync_age_seconds"])

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

    def test_control_surfaces_provenance_unknown_open_before_engine_tick(self):
        """Authenticated /control lists unresolved recovery without mutating history."""
        from contextlib import ExitStack
        from datetime import datetime, timezone

        from api.main import app
        from tests.auth_test_helpers import AuthTestHarness, clear_auth_overrides

        clear_auth_overrides()
        app.dependency_overrides.clear()
        with ExitStack() as stack:
            h = stack.enter_context(AuthTestHarness())
            root = h.root
            engine_db = root / "engine.db"
            admin_db = root / "admin.db"
            for key, value in {
                "TRADING_ENGINE_DB_PATH": str(engine_db),
                "ADMIN_CONFIG_DB_PATH": str(admin_db),
                "TRADING_ENGINE_LIVE_ORDERS": "false",
                "NIFTY_RADAR_LIVE_WRITES_AUTHORIZED": "0",
            }.items():
                stack.enter_context(patch.dict("os.environ", {key: value}))
            store = TradingEngineStore(str(engine_db))
            stack.callback(store.close)

            exposed = store.insert_candidate(
                setup_id="__e2e_vwap_live__|open", continuation_rule_version="v1",
                session_date="2026-09-02", symbol="ADANIPORTS", instrument_token=3861249,
                direction="DOWN", entry_estimate=1652.0, tick_size=0.1,
                trigger_time="2026-09-02T12:12:00+05:30",
            )
            store.update_trade(
                exposed.trade_id, qty_model_version=1, status="protected_open",
                qty=145, intended_qty=145, filled_qty=145, remaining_position_qty=145,
                protected_qty=0, entry_fill=1651.5, initial_stop=1655.1, current_stop=1655.1,
                entry_order_id="703e083c2b3b4fe1", sl_order_id="e04efc533b014d21",
                run_id=None, entry_live_orders_enabled=None, daily_loss_cap_inr=2995,
            )
            store.append_event(exposed.trade_id, "provenance_unknown",
                               payload={"session_date": "2026-09-02", "symbol": "ADANIPORTS"})

            whitespace = store.insert_candidate(
                setup_id="blank-run", continuation_rule_version="v1",
                session_date="2026-09-02", symbol="BLANKRUN", instrument_token=1,
                direction="UP", entry_estimate=100.0, tick_size=0.05,
                trigger_time="2026-09-02T12:14:00+05:30",
            )
            store.update_trade(
                whitespace.trade_id, qty_model_version=1, status="protected_open",
                filled_qty=10, remaining_position_qty=10, protected_qty=10,
                run_id="   ", entry_live_orders_enabled=0,
            )

            pending = store.insert_candidate(
                setup_id="pending-unknown", continuation_rule_version="v1",
                session_date="2026-09-02", symbol="PENDING", instrument_token=2,
                direction="UP", entry_estimate=100.0, tick_size=0.05,
                trigger_time="2026-09-02T12:15:00+05:30",
            )
            store.update_trade(
                pending.trade_id, qty_model_version=1, status="entry_submitting",
                intended_qty=5, filled_qty=0, remaining_entry_qty=5, remaining_position_qty=0,
                run_id=None, entry_live_orders_enabled=None,
            )
            unknown_submit = store.insert_candidate(
                setup_id="submission-unknown", continuation_rule_version="v1",
                session_date="2026-09-02", symbol="SUBUNK", instrument_token=3,
                direction="UP", entry_estimate=100.0, tick_size=0.05,
                trigger_time="2026-09-02T12:16:00+05:30",
            )
            store.update_trade(
                unknown_submit.trade_id, qty_model_version=1, status="submission_unknown",
                intended_qty=5, filled_qty=0, remaining_entry_qty=5, remaining_position_qty=0,
                run_id="", entry_live_orders_enabled=None,
            )

            closed = store.insert_candidate(
                setup_id="closed-flat", continuation_rule_version="v1",
                session_date="2026-09-02", symbol="CLOSED", instrument_token=4,
                direction="UP", entry_estimate=100.0, tick_size=0.05,
                trigger_time="2026-09-02T12:17:00+05:30",
            )
            store.update_trade(
                closed.trade_id, qty_model_version=1, status="closed",
                filled_qty=5, exited_qty=5, remaining_position_qty=0, remaining_entry_qty=0,
                run_id=None, entry_live_orders_enabled=None,
            )
            skipped = store.insert_candidate(
                setup_id="skipped-flat", continuation_rule_version="v1",
                session_date="2026-09-02", symbol="SKIP", instrument_token=5,
                direction="UP", entry_estimate=100.0, tick_size=0.05,
                trigger_time="2026-09-02T12:18:00+05:30",
            )
            store.update_trade(skipped.trade_id, status="skipped", qty_model_version=1)

            denied = h.client.get("/api/v1/trading-engine/control")
            self.assertEqual(denied.status_code, 401, denied.text)
            h.login()
            with patch("api.routers.trading.is_engine_running", return_value=False):
                response = h.client.get("/api/v1/trading-engine/control")
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            ids = {row["trade_id"] for row in body["incidents"]}
            self.assertEqual(
                ids,
                {exposed.trade_id, whitespace.trade_id, pending.trade_id, unknown_submit.trade_id},
            )
            self.assertNotIn(closed.trade_id, ids)
            self.assertNotIn(skipped.trade_id, ids)
            self.assertEqual(len(body["incidents"]), 4)
            self.assertTrue(body["strip"]["unresolved_incident"])
            self.assertEqual(body["strip"]["execution_mode"], "PAPER")
            self.assertEqual(body["strip"]["entry_permission"], "disarmed")
            self.assertFalse(body["live_execution_authorized"])
            self.assertIn("provenance_unknown", [row["action"] for row in body["recovery_events"]])

            # GET /control must not mutate durable trade history.
            for trade_id, status, run_id in (
                (exposed.trade_id, "protected_open", None),
                (whitespace.trade_id, "protected_open", "   "),
                (pending.trade_id, "entry_submitting", None),
                (unknown_submit.trade_id, "submission_unknown", ""),
                (closed.trade_id, "closed", None),
                (skipped.trade_id, "skipped", None),
            ):
                row = store.get_trade(trade_id)
                assert row is not None
                self.assertEqual(row.status, status)
                self.assertEqual(row.run_id, run_id)

            from api.routers.trading import _session_date
            day = _session_date(None)
            run_id = store.start_run(session_date=day, live_orders_enabled=False, pid=None)
            store.save_session_arm(
                session_date=day, run_id=run_id, execution_mode="PAPER",
                entry_mode="MANUAL", config_version_id="v1", actor="test",
            )
            admin = AdminConfigStore(str(admin_db))
            admin.set_entries_paused(False)
            admin.close()
            now = datetime.now(timezone.utc).isoformat()
            heartbeat = {"run_id": run_id, "session_date": day, "broker_sync_at": now}
            with patch("api.routers.trading.is_engine_running", return_value=True), \
                 patch("api.services.trading_engine_runner.read_heartbeat", return_value=heartbeat), \
                 patch("trading_engine_cycle.feed_age_seconds_from_runner_status", return_value=0):
                armed_body = h.client.get("/api/v1/trading-engine/control").json()
            self.assertEqual(len(armed_body["incidents"]), 4)
            self.assertEqual(armed_body["strip"]["execution_mode"], "PAPER")
            self.assertEqual(armed_body["strip"]["entry_permission"], "recovery_required")
            self.assertTrue(armed_body["strip"]["unresolved_incident"])
            self.assertEqual(store.get_trade(exposed.trade_id).status, "protected_open")
            self.assertEqual(store.get_trade(pending.trade_id).status, "entry_submitting")
            # LIVE strip may report LIVE run mode, but authorization stays disabled.
            live_run = store.start_run(session_date=day, live_orders_enabled=True, pid=None)
            store.save_session_arm(
                session_date=day, run_id=live_run, execution_mode="LIVE",
                entry_mode="MANUAL", config_version_id="v1", actor="test",
            )
            with patch("api.routers.trading.is_engine_running", return_value=True), \
                 patch("api.services.trading_engine_runner.read_heartbeat",
                       return_value={"run_id": live_run, "session_date": day, "broker_sync_at": now}), \
                 patch("trading_engine_cycle.feed_age_seconds_from_runner_status", return_value=0):
                live_body = h.client.get("/api/v1/trading-engine/control").json()
            self.assertEqual(live_body["strip"]["execution_mode"], "LIVE")
            self.assertFalse(live_body["live_execution_authorized"])
            self.assertEqual(len(live_body["incidents"]), 4)
            self.assertEqual(live_body["strip"]["entry_permission"], "recovery_required")
            self.assertTrue(live_body["strip"]["unresolved_incident"])
            self.assertEqual(store.get_trade(exposed.trade_id).status, "protected_open")
            self.assertEqual(store.get_trade(pending.trade_id).status, "entry_submitting")
            self.assertEqual(store.get_trade(unknown_submit.trade_id).status, "submission_unknown")

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
