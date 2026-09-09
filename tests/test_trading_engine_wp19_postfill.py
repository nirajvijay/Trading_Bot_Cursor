"""Actual fill hard-cap handling through the production cycle and durable exits."""
import json
from dataclasses import replace
from unittest.mock import Mock

from tests import test_trading_engine_wp18_entry as entry_fixture
from trading_engine_risk import post_fill_risk_decision


class PostFillTests(entry_fixture.EntryIntegrationTests):
    def enter(self):
        self.cycle._submit_entry(self.trade)
        return self.store.get_trade(self.trade.trade_id)

    def test_overfill_pauses_cancels_and_serializes_exit(self):
        trade = self.enter()
        order = self.broker.orders[trade.entry_order_id]
        order.quantity = 12
        order.filled_quantity = 12
        self.cycle._apply_entry_order(trade, order)
        saved = self.store.get_trade(trade.trade_id)
        self.assertTrue(self.cycle._entries_paused())
        self.assertEqual(self.broker.net_position_qty("AAA"), 0)
        self.assertEqual(saved.remaining_position_qty, 0)
        count = self.broker.market_place_count
        self.cycle.enforce_postfill_risk()
        self.cycle.resume_pending_market_exits()
        self.assertEqual(self.broker.market_place_count, count)

    def test_unknown_price_pauses_without_inventing_breach(self):
        trade = self.enter()
        order = self.broker.orders[trade.entry_order_id]
        order.average_price = None
        before = self.broker.market_place_count
        self.cycle._apply_entry_order(trade, order)
        self.assertTrue(self.cycle._entries_paused())
        actions = [r["action"] for r in self.store.list_events(trade.trade_id)]
        self.assertIn("post_fill_risk_unknown", actions)
        self.assertNotIn("post_fill_risk_breach", actions)
        self.assertEqual(self.broker.market_place_count, before)

    def test_admin_reduction_does_not_retroactively_force_exit(self):
        trade = self.enter()
        from api.admin_config.store import AdminConfigStore
        admin = AdminConfigStore(self.admin_path)
        admin.update_config({"per_trade_risk_cap_inr":50, "limited_per_trade_risk_cap_inr":25}, actor="test")
        admin.close()
        before = self.broker.market_place_count
        self.cycle.enforce_postfill_risk()
        self.assertEqual(self.broker.market_place_count, before)
        self.assertEqual(self.broker.net_position_qty("AAA"), 10)

    def test_known_price_per_trade_breach_and_notional_breach(self):
        trade = self.enter()
        limits = json.loads(trade.risk_limits_json)
        limits["per_trade_risk_cap_inr"] = 50
        decision = post_fill_risk_decision(trade, [trade], limits)
        self.assertEqual(decision.state, "breach")
        self.assertIn("per_trade_risk_limit", decision.reasons)
        limits["per_trade_risk_cap_inr"] = 900
        limits["allocated_capital_inr"] = 1000
        self.assertIn("notional_limit", post_fill_risk_decision(trade,[trade],limits).reasons)

    def test_concurrency_symbol_and_setup_caps(self):
        trade = self.enter()
        limits = json.loads(trade.risk_limits_json)
        peer = replace(trade, trade_id="peer", setup_id="peer")
        limits["max_concurrent_positions"] = 1
        limits["max_filled_setups_per_day"] = 1
        decision = post_fill_risk_decision(trade,[trade,peer],limits)
        self.assertTrue({"symbol_limit","concurrency_limit","setup_limit"}.issubset(decision.reasons))

    def test_pause_store_failure_still_blocks_entries(self):
        trade = self.enter()
        self.cycle._pause_entries_for = Mock(return_value=False)
        order = self.broker.orders[trade.entry_order_id]
        order.quantity = order.filled_quantity = 12
        self.cycle._apply_entry_order(trade, order)
        self.assertTrue(self.cycle._local_entries_lock)
        self.assertTrue(self.cycle._entries_paused())

    def test_unknown_stop_blocks_risk_flatten_then_resumes_after_reveal(self):
        trade = self.enter()
        self.broker.hide_order(trade.sl_order_id)
        order = self.broker.orders[trade.entry_order_id]
        order.quantity = order.filled_quantity = 12
        before = self.broker.market_place_count
        self.cycle._apply_entry_order(trade, order)
        self.assertEqual(self.broker.market_place_count, before)
        self.cycle.close()
        self.cycle = self.make_cycle()
        self.cycle.enforce_postfill_risk()
        self.assertEqual(self.broker.market_place_count, before)
        self.broker.reveal_order(trade.sl_order_id)
        self.cycle.enforce_postfill_risk()
        self.assertEqual(self.broker.net_position_qty("AAA"), 0)
        self.assertEqual(self.broker.market_place_count, before+1)

    def test_partial_risk_exit_remains_owned_across_restart_and_competing_close(self):
        trade = self.enter()
        self.broker.auto_fill_exit = False
        entry = self.broker.orders[trade.entry_order_id]
        entry.quantity = entry.filled_quantity = 12
        self.cycle._apply_entry_order(trade, entry)
        saved = self.store.get_trade(trade.trade_id)
        self.assertIsNotNone(saved.active_exit_order_id)
        count = self.broker.market_place_count
        self.cycle.close()
        self.cycle = self.make_cycle()
        self.cycle.close_position(trade.trade_id)
        self.cycle.enforce_postfill_risk()
        self.assertEqual(self.broker.market_place_count, count)
        self.assertEqual(self.store.get_trade(trade.trade_id).active_exit_order_id, saved.active_exit_order_id)
