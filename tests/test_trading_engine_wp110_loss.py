"""Daily-loss halt uses complete liquidation P&L, never admission reservations."""
import unittest
from dataclasses import replace
from datetime import timedelta

from api.admin_config.store import AdminConfigStore
from tests import test_trading_engine_wp18_entry as fixture
from trading_engine_cycle import RECOVERY_EVENTS_TRADE_ID
from trading_engine_loss import trade_loss_slice
from trading_engine_quotes import TouchQuote


class LossHaltTests(unittest.TestCase):
    setUp = fixture.EntryIntegrationTests.setUp
    tearDown = fixture.EntryIntegrationTests.tearDown
    make_cycle = fixture.EntryIntegrationTests.make_cycle

    def enter(self):
        self.cycle._submit_entry(self.trade)
        return self.store.get_trade(self.trade.trade_id)

    def cap(self, value):
        admin = AdminConfigStore(self.admin_path)
        admin.update_config({"daily_loss_cap_inr":value}, actor="test")
        admin.close()

    def test_full_admission_reserve_does_not_force_liquidation(self):
        trade = self.enter()
        self.cap(101)
        count = self.broker.market_place_count
        self.cycle.enforce_daily_loss()
        self.assertFalse(self.cycle.loss_halt_snapshot["halted"])
        self.assertEqual(self.broker.market_place_count, count)
        self.assertEqual(self.broker.net_position_qty("AAA"), 10)

    def test_long_liquidation_uses_bid_short_uses_ask(self):
        trade = self.enter()
        quote = TouchQuote(105,115,fixture.NOW.isoformat())
        self.assertEqual(trade_loss_slice(trade,quote,fixture.NOW).unrealised, -50)
        short = replace(trade, direction="DOWN")
        self.assertEqual(trade_loss_slice(short,quote,fixture.NOW).unrealised, -50)

    def test_published_trade_mark_uses_same_slice_and_rejects_stale_or_changed_quantity(self):
        from api.queries.trading import live_trade_mark
        trade = self.enter()
        self.broker.touch_quotes["AAA"] = TouchQuote(109,111,fixture.NOW.isoformat())
        self.cycle.enforce_daily_loss()
        heartbeat = {"session_date":trade.session_date,"broker_sync_at":fixture.NOW.isoformat(),
                     "loss_halt":self.cycle.loss_halt_snapshot}
        mark = live_trade_mark(trade,heartbeat,fixture.NOW,0)
        self.assertEqual(mark["price"],109)
        self.assertEqual(mark["open_pnl"],-10)
        self.assertFalse(mark["stale"])
        self.assertTrue(live_trade_mark(trade,heartbeat,fixture.NOW+timedelta(seconds=3),0)["stale"])
        self.assertTrue(live_trade_mark(replace(trade,remaining_position_qty=9),heartbeat,fixture.NOW,0)["stale"])
        self.assertIsNone(live_trade_mark(trade,heartbeat,fixture.NOW,None)["open_pnl"])

    def test_partial_exit_losses_and_charges_count_once(self):
        trade = self.enter()
        trade = replace(trade, exited_qty=4, remaining_position_qty=6,
            exit_confirmed_qty=4, exit_value=400, exit_value_est=0, charge_bps=10)
        value = trade_loss_slice(trade, TouchQuote(110,110,fixture.NOW.isoformat()), fixture.NOW)
        self.assertAlmostEqual(value.realised_net, -40.44)
        self.assertEqual(value.unrealised, 0)
        self.assertTrue(value.complete)

    def test_missing_mark_preserves_confirmed_partial_loss(self):
        trade = self.enter()
        trade = replace(trade, exited_qty=4, remaining_position_qty=6,
            exit_confirmed_qty=4, exit_value=400, exit_value_est=0, charge_bps=0)
        value = trade_loss_slice(trade, None, fixture.NOW)
        self.assertEqual(value.realised_net, -40)
        self.assertIsNone(value.unrealised)
        self.assertFalse(value.complete)
        self.assertTrue(value.realised_complete)

    def test_unpriced_execution_blocks_entries_without_false_healthy_total(self):
        trade = self.enter()
        self.store.update_trade(trade.trade_id, entry_value_est=100, halt_realised_net=-20)
        count = self.broker.market_place_count
        self.cycle.enforce_daily_loss()
        self.assertFalse(self.cycle.loss_halt_snapshot["complete"])
        self.assertIsNone(self.cycle.loss_halt_snapshot["net_session_pnl"])
        self.assertTrue(self.cycle._entries_paused())
        self.assertEqual(self.broker.market_place_count, count)

    def test_unknown_realised_preserves_last_confirmed_amount(self):
        trade = self.enter()
        trade = replace(trade, exited_qty=4, remaining_position_qty=6, entry_value_est=100,
            exit_confirmed_qty=4, exit_value=400, halt_realised_net=-40)
        value = trade_loss_slice(trade,None,fixture.NOW)
        self.assertEqual(value.realised_net, -40)
        self.assertFalse(value.realised_complete)

    def test_mtm_halt_latches_across_restart_and_admin_resume(self):
        trade = self.enter()
        self.cap(50)
        self.broker.touch_quotes["AAA"] = TouchQuote(100,100,fixture.NOW.isoformat())
        self.cycle.enforce_daily_loss()
        self.assertTrue(self.cycle.loss_halt_snapshot["halted"])
        self.assertEqual(self.broker.net_position_qty("AAA"), 0)
        count = self.broker.market_place_count
        admin = AdminConfigStore(self.admin_path)
        admin.set_entries_paused(False)
        admin.close()
        self.cycle.close()
        self.cycle = self.make_cycle()
        self.cycle.enforce_daily_loss()
        self.assertTrue(self.cycle._entries_paused())
        self.assertTrue(self.cycle.loss_halt_snapshot["halted"])
        self.assertEqual(self.broker.market_place_count,count)
        events = self.store.list_events(RECOVERY_EVENTS_TRADE_ID)
        self.assertEqual(sum(r["action"] == "daily_loss_halt" for r in events), 1)

    def test_realised_only_halt_with_missing_open_mark(self):
        trade = self.enter()
        self.cap(30)
        self.store.update_trade(trade.trade_id, exited_qty=4, remaining_position_qty=6,
            exit_confirmed_qty=4, exit_value=400, exit_value_est=0, charge_bps=0)
        self.broker.touch_quotes.clear()
        # Isolate dispatch here; synthetic accounting does not manufacture broker executions.
        from unittest.mock import Mock
        self.cycle._request_market_exit = Mock()
        self.cycle.enforce_daily_loss()
        self.assertTrue(self.cycle.loss_halt_snapshot["halted"])
        self.assertFalse(self.cycle.loss_halt_snapshot["complete"])
        self.cycle._request_market_exit.assert_called_once()

    def test_future_stale_quotes_are_incomplete_not_zero(self):
        trade = self.enter()
        for age in (-1,3):
            quote = TouchQuote(100,100,(fixture.NOW-timedelta(seconds=age)).isoformat())
            value = trade_loss_slice(trade,quote,fixture.NOW)
            self.assertFalse(value.complete)
            self.assertIsNone(value.unrealised)

    def test_hidden_stop_blocks_halt_exit_until_reconciled(self):
        trade = self.enter()
        self.cap(50)
        self.broker.touch_quotes["AAA"] = TouchQuote(100,100,fixture.NOW.isoformat())
        self.broker.hide_order(trade.sl_order_id)
        count = self.broker.market_place_count
        self.cycle.enforce_daily_loss()
        self.assertEqual(self.broker.market_place_count,count)
        self.broker.reveal_order(trade.sl_order_id)
        self.cycle.enforce_daily_loss()
        self.assertEqual(self.broker.net_position_qty("AAA"),0)
        self.assertEqual(self.broker.market_place_count,count+1)

    def test_realised_profit_offsets_halt_not_admission_loss(self):
        trade = self.enter()
        self.cap(50)
        self.store.update_trade(trade.trade_id, status="closed", remaining_position_qty=0,
            exited_qty=10, exit_confirmed_qty=10, exit_value=1000, charge_bps=0,
            closed_loss_contribution=100)
        other = self.store.insert_candidate(setup_id="profit", continuation_rule_version="v1",
            session_date="2026-08-17", symbol="BBB", instrument_token=2, direction="UP",
            entry_estimate=110, tick_size=.05, trigger_time=fixture.NOW.isoformat(), qty=10,
            initial_stop=100, status="closed")
        self.store.update_trade(other.trade_id, filled_qty=10, exited_qty=10,
            remaining_position_qty=0, entry_value=1100, entry_fill=110,
            exit_value=1300, exit_confirmed_qty=10, charge_bps=0,
            run_id=self.run, entry_live_orders_enabled=False)
        self.cycle.enforce_daily_loss()
        self.assertEqual(self.cycle.loss_halt_snapshot["realised_net"],100)
        self.assertFalse(self.cycle.loss_halt_snapshot["halted"])
        from trading_engine_risk import closed_loss_today
        self.assertEqual(closed_loss_today(self.store.list_trades("2026-08-17")),100)
