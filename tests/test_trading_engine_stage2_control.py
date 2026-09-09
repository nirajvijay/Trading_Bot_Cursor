"""Control-plane contracts using the real cycle, temporary stores and FakeBroker."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone

from api.admin_config.store import AdminConfigStore
from trading_engine_broker import FakeBroker
from trading_engine_cycle import TradingEngineCycle
from trading_engine_handoff import fetch_triggered_since
from trading_engine_preview import preview_trade
from trading_engine_quotes import TouchQuote
from trading_engine_store import TradingEngineStore
from tests.test_trading_engine_cycle import _seed_live

NOW = datetime(2026,8,17,8,30,tzinfo=timezone.utc)


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.live = root/"live.db"
        self.admin_path = root/"admin.db"
        admin = AdminConfigStore(self.admin_path)
        admin.update_config({"round_trip_charge_bps":0,"estimated_slippage_bps":0},actor="test")
        self.version = admin.active_version_id()
        admin.close()
        self.store = TradingEngineStore(root/"engine.db")
        self.run = self.store.start_run(session_date="2026-08-17",live_orders_enabled=False,pid=1)
        self.broker = FakeBroker(last_prices={"AAA":110})
        self.broker.touch_quotes["AAA"] = TouchQuote(110,110,NOW.isoformat())
        self.cycle = TradingEngineCycle(self.store,self.broker,live_db=self.live,session_date="2026-08-17",
            started_at=NOW.isoformat(),run_id=self.run,live_orders_enabled=False,admin_config_db=self.admin_path,
            clock_fn=lambda:NOW,feed_age_seconds_fn=lambda:0)
        self.cycle._arming_readiness = lambda:True
        _seed_live(self.live,"signal",NOW.isoformat())
        db = sqlite3.connect(self.live)
        db.execute("UPDATE live_continuation_decisions SET trigger_exchange_ts=?",(NOW.isoformat(),))
        db.commit()
        db.close()

    def tearDown(self):
        self.cycle.close()
        self.store.close()
        self.tmp.cleanup()

    def command(self,kind,**payload):
        trade_id = payload.pop("trade_id",None)
        cid = self.store.enqueue_command(kind,trade_id=trade_id,payload=payload)
        self.cycle.process_commands()
        return self.store.command_record(cid)

    def arm(self,mode="MANUAL"):
        result = self.command("arm_session",entry_mode=mode,execution_mode="PAPER",
            config_version_id=self.version,run_id=self.run)
        self.assertEqual(result["state"],"succeeded",result)

    def approve(self,**extra):
        return self.command("approve_entry",setup_id="signal",continuation_rule_version="v1",**extra)

    def test_disarmed_blocks_then_manual_never_auto_submits(self):
        self.cycle.tick()
        self.assertEqual(self.broker.market_place_count,0)
        self.arm()
        self.cycle.tick()
        self.assertEqual(self.broker.market_place_count,0)
        result = self.approve()
        self.assertEqual(result["state"],"succeeded",result)
        trade = self.store.get_trade(result["result"]["trade_id"])
        self.assertEqual(trade.filled_qty,trade.protected_qty)
        self.assertEqual(self.broker.orders[trade.entry_order_id].order_type,"LIMIT")

    def test_autopilot_uses_same_sizing_and_protection(self):
        self.arm("AUTOPILOT")
        self.cycle.tick()
        trade = self.store.list_trades(None)[0]
        self.assertEqual(trade.status,"protected_open")
        self.assertEqual(trade.qty,81)
        self.assertEqual(trade.protected_qty,81)
        original = json.loads(trade.original_setup_json)
        self.assertEqual(original["machine_setup"]["setup_id"],"signal")
        self.assertEqual(original["entry_mode"],"AUTOPILOT")

    def test_manual_overrides_reduce_only_preserve_machine_setup(self):
        self.arm()
        result = self.approve(qty_override=5,stop_tighten=105)
        self.assertEqual(result["state"],"succeeded",result)
        trade = self.store.list_trades(None)[0]
        self.assertEqual((trade.qty,trade.initial_stop,trade.current_stop),(5,99,105))
        self.assertEqual(json.loads(trade.original_setup_json)["initial_sizing"]["qty"],81)

    def test_preview_read_only_and_expiry_rechecked_on_approval(self):
        self.arm()
        candidate = fetch_triggered_since(self.live,created_at_gte=NOW.isoformat())[0]
        before = len(self.store.list_trades(None))
        preview = preview_trade(self.cycle,candidate,qty_override=5)
        self.assertTrue(preview["eligible"],preview)
        self.assertEqual(preview["proposed_qty"],5)
        self.assertEqual(self.broker.market_place_count,0)
        self.assertEqual(len(self.store.list_trades(None)),before)
        db = sqlite3.connect(self.live)
        db.execute("UPDATE live_continuation_decisions SET trigger_exchange_ts='bad'")
        db.commit()
        db.close()
        result = self.approve()
        self.assertEqual(result["state"],"failed")
        self.assertEqual(self.broker.market_place_count,0)

    def test_duplicate_command_is_idempotent_conflicting_payload_rejected(self):
        one = self.store.enqueue_command("pause_entries",client_command_id="unique-123")
        two = self.store.enqueue_command("pause_entries",client_command_id="unique-123")
        self.assertEqual(one,two)
        with self.assertRaisesRegex(ValueError,"idempotency_key_conflict"):
            self.store.enqueue_command("close_all",client_command_id="unique-123")

    def test_close_waits_for_actual_fill(self):
        self.arm()
        self.approve(qty_override=5)
        trade = self.store.list_trades(None)[0]
        self.broker.auto_fill_exit = False
        result = self.command("close_position",trade_id=trade.trade_id)
        self.assertEqual(result["state"],"awaiting_broker")
        order_id = self.store.get_trade(trade.trade_id).active_exit_order_id
        self.broker.fill_entry(order_id,110)
        self.cycle.process_commands()
        self.assertEqual(self.store.command_record(result["command_id"])["state"],"succeeded")

    def test_stop_drains_without_flatten(self):
        self.arm()
        self.approve(qty_override=5)
        count = self.broker.market_place_count
        result = self.command("stop_engine")
        self.assertEqual(result["state"],"awaiting_broker")
        self.assertTrue(self.cycle.running)
        self.assertTrue(self.cycle.draining)
        self.assertEqual(self.broker.market_place_count,count)
        self.assertFalse(self.store.session_arm("2026-08-17")["armed"])

    def test_stale_trail_is_not_success_and_not_repeated(self):
        self.arm()
        self.approve(qty_override=5)
        trade = self.store.list_trades(None)[0]
        self.broker.stale_modify_trigger = True
        result = self.command("trail_stop",trade_id=trade.trade_id,new_stop=102)
        self.assertEqual(result["state"],"unknown_needs_reconcile")
        count = self.broker.modify_count
        self.cycle.process_commands()
        self.assertEqual(self.broker.modify_count,count)

    def test_arm_refuses_unknown_ownership_or_unready_checklist(self):
        self.cycle._arming_readiness=lambda:False
        result=self.command("arm_session",execution_mode="PAPER",entry_mode="MANUAL",config_version_id=self.version)
        self.assertEqual(result["state"],"failed")
        self.assertIsNone(self.store.session_arm("2026-08-17"))

    def test_restart_requires_new_arm_even_if_previous_arm_enabled(self):
        self.arm()
        self.cycle.run_id="new-process"
        self.assertEqual(self.cycle._entry_arm_block_reason(),"restart_rearm_required")

    def test_original_setup_cannot_be_overwritten_or_cleared(self):
        self.arm()
        self.approve(qty_override=5)
        trade = self.store.list_trades(None)[0]
        for replacement in (None, '{}'):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "original_setup_is_immutable"):
                self.store.update_trade(trade.trade_id, original_setup_json=replacement)
            self.store._conn.rollback()
            self.assertEqual(self.store.get_trade(trade.trade_id).original_setup_json, trade.original_setup_json)

    def test_autopilot_mode_change_blocked_with_open_manual_position(self):
        self.arm()
        self.approve(qty_override=5)
        result = self.command("arm_session", entry_mode="AUTOPILOT", execution_mode="PAPER",
                              config_version_id=self.version, run_id=self.run)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["result"]["reason"], "mode_switch_requires_flat_reconciled_account")
        self.assertEqual(self.store.session_arm(self.cycle.session_date)["entry_mode"], "MANUAL")
