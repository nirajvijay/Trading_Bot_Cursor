"""Authenticated HTTP -> durable commands -> persistent PAPER -> audit/report.

Only temporary accounts/databases and injected market inputs. This is a synthetic
integration session, not real broker execution or deployed browser evidence.
"""
from contextlib import ExitStack
from datetime import datetime, timezone
import sqlite3
import unittest
from unittest.mock import patch

from api import config
from api.admin_config.store import AdminConfigStore
from api.services.checklist_cache import write_checklist_cache
from tests.auth_test_helpers import AuthTestHarness
from tests.test_trading_engine_cycle import _seed_live
from trading_engine_cycle import TradingEngineCycle
from trading_engine_paper import PaperBroker
from trading_engine_quotes import TouchQuote
from trading_engine_store import TradingEngineStore


NOW = datetime(2026, 8, 17, 8, 30, tzinfo=timezone.utc)
DAY = "2026-08-17"
BASE = "/api/v1/trading-engine"


class OwnerWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.scope = ExitStack()
        self.addCleanup(self.scope.close)
        self.h = self.scope.enter_context(AuthTestHarness())
        root = self.h.root
        self.scope.enter_context(patch.dict("os.environ", {
            "TRADING_ENGINE_DB_PATH": str(root / "engine.db"),
            "ADMIN_CONFIG_DB_PATH": str(root / "admin.db"),
            "LIVE_DB_PATH": str(root / "signals.db"),
            "TRADING_ENGINE_LIVE_ORDERS": "false",
            "NIFTY_RADAR_LIVE_WRITES_AUTHORIZED": "0",
        }))
        self.scope.enter_context(patch.object(config, "LOCAL_DATA_DIR", root))
        self.scope.enter_context(patch("api.routers.trading._session_date", return_value=DAY))
        self.scope.enter_context(patch("api.routers.trading.is_engine_running", return_value=True))
        # Tripwires: none of these workflows may reach any real order write.
        for method in ("place_order", "modify_order", "cancel_order"):
            self.scope.enter_context(patch("kiteconnect.KiteConnect." + method,
                side_effect=AssertionError("real broker write forbidden")))
        self.admin = AdminConfigStore(config.admin_config_db_path())
        self.scope.callback(self.admin.close)
        self.admin.update_config({"daily_loss_cap_inr": 2995}, actor="isolated-owner")
        self.store = TradingEngineStore(config.trading_engine_db_path())
        self.scope.callback(self.store.close)
        self.quote = TouchQuote(110, 110, NOW.isoformat())
        self.paper_path = root / "paper.db"
        self.broker = self.open_broker()
        self.scope.callback(lambda: self.broker.close())
        _seed_live(config.live_db_path(), "owner-signal", NOW.isoformat())
        with sqlite3.connect(config.live_db_path()) as db:
            db.execute("UPDATE live_continuation_decisions SET trigger_exchange_ts=?", (NOW.isoformat(),))
        self.cycle = self.open_cycle()
        self.scope.callback(lambda: self.cycle.close())
        self.h.login()
        self.serial = 0

    def open_broker(self):
        return PaperBroker(self.paper_path, quote_provider=lambda symbol: self.quote,
                           clock_fn=lambda: NOW)

    def open_cycle(self):
        run = self.store.start_run(session_date=DAY, live_orders_enabled=False, pid=1)
        return TradingEngineCycle(self.store, self.broker, live_db=config.live_db_path(),
            session_date=DAY, started_at=NOW.isoformat(), run_id=run,
            live_orders_enabled=False, admin_config_db=config.admin_config_db_path(),
            clock_fn=lambda: NOW, feed_age_seconds_fn=lambda: 0)

    def send(self, kind, **payload):
        self.serial += 1
        body = {"kind": kind, "client_command_id": f"owner-workflow-{self.serial}", **payload}
        response = self.h.client.post(BASE + "/commands", json=body, headers=self.h.csrf_headers())
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["state"], "queued")
        duplicate = self.h.client.post(BASE + "/commands", json=body, headers=self.h.csrf_headers())
        self.assertEqual(response.json()["command_id"], duplicate.json()["command_id"])
        self.cycle.process_commands()
        return self.h.client.get(BASE + f'/commands/{response.json()["command_id"]}').json()

    def arm(self, mode):
        return self.send("arm_session", execution_mode="PAPER", entry_mode=mode,
                         config_version_id=self.admin.active_version_id())

    def exercise(self, mode):
        # Login is insufficient for privileged execution commands.
        denied = self.h.client.post(BASE + "/commands", json={"kind": "arm_session",
            "client_command_id": "not-confirmed", "config_version_id": self.admin.active_version_id()},
            headers=self.h.csrf_headers())
        self.assertEqual(denied.status_code, 403)
        self.h.step_up()
        self.assertEqual(self.arm(mode)["state"], "failed")
        self.assertEqual(self.broker.limit_place_count, 0)
        write_checklist_cache({"session_date": DAY, "overall_status": "ok"})
        armed = self.arm(mode)
        self.assertEqual(armed["state"], "succeeded", armed)
        if mode == "MANUAL":
            self.cycle.tick()
            self.assertEqual(self.broker.limit_place_count, 0)
            result = self.send("approve_entry", setup_id="owner-signal",
                continuation_rule_version="v1", qty_override=5)
            self.assertEqual(result["state"], "succeeded", result)
        else:
            self.cycle.tick()
        trade = self.store.list_trades(DAY)[0]
        self.assertGreater(trade.filled_qty, 0)
        self.assertEqual(trade.protected_qty, trade.remaining_position_qty)
        plan = trade.original_setup_json
        result = self.send("trail_stop", trade_id=trade.trade_id, new_stop=105)
        self.assertEqual(result["state"], "succeeded", result)
        audit = self.h.client.get(BASE + f"/trades/{trade.trade_id}/audit")
        self.assertEqual(audit.status_code, 200, audit.text)
        self.assertTrue(audit.json()["original_setup_available"])
        self.assertTrue(audit.json()["orders"])
        self.assertEqual(self.store.get_trade(trade.trade_id).original_setup_json, plan)
        self.assertEqual(self.send("set_auto_trail", trade_id=trade.trade_id, enabled=False)["state"], "succeeded")
        self.cycle.close()
        self.broker.close()
        self.broker = self.open_broker()
        self.cycle = self.open_cycle()
        self.cycle.tick()
        self.assertIsNotNone(self.cycle._entry_arm_block_reason())
        self.assertEqual(self.broker.limit_place_count, 1)
        armed = self.arm(mode)
        self.assertEqual(armed["state"], "succeeded", armed)
        # Drain is not flatten; closure must be a separate durable command.
        self.assertEqual(self.send("stop_engine")["state"], "awaiting_broker")
        self.assertNotEqual(self.broker.net_position_qty("AAA"), 0)
        result = self.send("close_all")
        self.assertEqual(result["state"], "succeeded", result)
        self.assertEqual(self.broker.net_position_qty("AAA"), 0)
        self.assertFalse(any(o.pending_quantity for o in self.broker.list_orders()))
        report = self.h.client.get(BASE + f"/report?session_date={DAY}&download=true")
        self.assertEqual(report.status_code, 200, report.text)
        self.assertIn("attachment", report.headers["content-disposition"])
        self.report = report.json()
        paper = report.json()["modes"]["PAPER"]
        self.assertEqual(paper["strategy_outcomes"]["closed_with_complete_prices"], 1)
        self.assertEqual(paper["engineering_quality"]["unresolved_exposure_trade_ids"], [])
        self.assertEqual(report.json()["modes"]["LIVE"]["strategy_outcomes"]["observed_trade_records"], 0)
        self.assertEqual(self.admin.load_effective_payload()["daily_loss_cap_inr"], 2995)

    def test_authenticated_manual_paper_lifecycle(self):
        self.exercise("MANUAL")

    def test_authenticated_autopilot_paper_lifecycle(self):
        self.exercise("AUTOPILOT")
