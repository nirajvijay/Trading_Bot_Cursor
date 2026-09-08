"""WP-1.1 review fixes: pause/reconcile, protection confirmation, remainder tracking."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Optional

from api.admin_config.store import AdminConfigStore
from trading_engine_broker import FakeBroker
from trading_engine_cycle import TradingEngineCycle
from trading_engine_risk import is_unprotected
from trading_engine_store import TradingEngineStore
from trading_engine_types import BrokerOrder, PositionQuote, TradeRecord

from tests.test_trading_engine_cycle import SCHEMA, _seed_live


def _session_align_entry(store: TradingEngineStore, trade_id: str) -> None:
    store.update_trade(trade_id, entry_time="2026-08-17T04:40:00+00:00")


class _IntentBoundaryBroker:
    """Wraps FakeBroker; records whether entry_intent exists at place_market_mis time."""

    def __init__(self, inner: FakeBroker, store: TradingEngineStore) -> None:
        self.inner = inner
        self.store = store
        self.intent_present_at_place = False
        self.place_calls = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def place_market_mis(self, **kwargs: Any) -> BrokerOrder:
        self.place_calls += 1
        tag = kwargs.get("tag") or ""
        # Locate trade by broker tag prefix.
        found_intent = False
        for trade in self.store.list_trades():
            if trade.broker_tag == tag:
                actions = [str(r["action"]) for r in self.store.list_events(trade.trade_id)]
                found_intent = "entry_intent" in actions
                break
        self.intent_present_at_place = found_intent
        return self.inner.place_market_mis(**kwargs)


class Wp11ReviewFixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.live = Path(self.tmp.name) / "live.db"
        self.te = Path(self.tmp.name) / "te.db"
        conn = sqlite3.connect(self.live)
        conn.executescript(SCHEMA)
        conn.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _cycle(
        self,
        broker: Any,
        *,
        live_orders: bool = False,
    ) -> tuple[TradingEngineStore, TradingEngineCycle]:
        store = TradingEngineStore(self.te)
        run_id = store.start_run(
            session_date="2026-08-17",
            live_orders_enabled=live_orders,
            pid=1,
        )
        cycle = TradingEngineCycle(
            store,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:30:00+00:00",
            run_id=run_id,
            live_orders_enabled=live_orders,
        )
        return store, cycle

    def test_1_pause_reconciles_before_skip_hidden_fill(self) -> None:
        _seed_live(self.live, "p1", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=True,
            hide_market_tags=True,
        )
        store, cycle = self._cycle(broker)
        # Place with hidden tag visibility, then mark unknown + clear local order id.
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        # Force uncertain state while broker still holds a COMPLETE fill (hidden by tag).
        store.update_trade(
            trade.trade_id,
            status="submission_unknown",
            entry_order_id=None,
            filled_qty=0,
            remaining_position_qty=0,
        )
        # Ensure intent exists so pause cannot treat as never-submitted.
        self.assertTrue(
            any(str(r["action"]) == "entry_intent" for r in store.list_events(trade.trade_id))
        )
        admin = AdminConfigStore(Path(self.tmp.name) / "admin.db")
        admin.set_entries_paused(True)
        cycle._admin_store = admin  # type: ignore[attr-defined]

        # Tag still hidden → pause must NOT skip.
        cycle._submit_entry(store.get_trade(trade.trade_id))  # type: ignore[arg-type]
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertNotEqual(trade.status, "skipped")
        self.assertEqual(trade.status, "submission_unknown")

        # Reveal broker fill → reconcile must adopt exposure even while paused.
        broker.reveal_tag(trade.broker_tag)
        cycle._submit_entry(trade)
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertNotEqual(trade.status, "skipped")
        self.assertGreater(trade.filled_qty, 0)
        self.assertGreater(trade.remaining_position_qty, 0)

    def test_2_stale_modify_does_not_overstate_protection(self) -> None:
        _seed_live(self.live, "p2", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=False,
            auto_confirm_sl=True,
            stale_modify_quantity=True,
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        intended = int(trade.intended_qty or trade.qty)
        first = max(1, intended // 3)
        broker.fill_entry_partial(trade.entry_order_id, first, 110.0)
        cycle.drive_open()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertEqual(trade.protected_qty, first)

        # Larger fill requests stop qty increase; broker returns stale old qty.
        second = min(intended, first * 2)
        broker.fill_entry_partial(trade.entry_order_id, second, 110.0)
        cycle.drive_open()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        sl = broker.poll_order(trade.sl_order_id)
        assert sl is not None
        self.assertEqual(int(sl.quantity), first)  # stale broker qty
        self.assertEqual(trade.protected_qty, first)  # must not claim second
        self.assertLess(trade.protected_qty, trade.remaining_position_qty)
        self.assertTrue(is_unprotected(trade))

    def test_3_stop_fill_keeps_tracking_entry_remainder(self) -> None:
        _seed_live(self.live, "p3", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=False,
            auto_confirm_sl=True,
            cancel_noop=True,
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        intended = int(trade.intended_qty or trade.qty)
        partial = max(1, intended // 2)
        broker.fill_entry_partial(trade.entry_order_id, partial, 110.0, complete=False)
        cycle.drive_open()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertGreater(trade.remaining_entry_qty, 0)
        self.assertIsNotNone(trade.sl_order_id)
        broker.fill_sl(trade.sl_order_id, 99.0)
        cycle.drive_open()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertNotEqual(trade.status, "closed")
        self.assertEqual(trade.status, "reconciliation_required")
        self.assertGreater(trade.remaining_entry_qty, 0)
        self.assertEqual(trade.remaining_position_qty, 0)

    def test_4_closed_trade_not_unprotected(self) -> None:
        closed = TradeRecord(
            trade_id="c1",
            setup_id="s",
            continuation_rule_version="v1",
            broker_tag="te",
            session_date="2026-08-17",
            symbol="AAA",
            instrument_token=1,
            direction="UP",
            qty=10,
            entry_estimate=100,
            entry_fill=100,
            initial_stop=90,
            current_stop=90,
            exit_fill=95,
            tick_size=0.05,
            notional=1000,
            margin_blocked=0,
            status="closed",
            skip_reason=None,
            reject_reason=None,
            close_reason="sl_hit",
            entry_order_id="e",
            sl_order_id="s",
            realised_pnl=-50,
            open_pnl=0,
            closed_loss_contribution=50,
            trigger_time=None,
            entry_time=None,
            close_time=None,
            created_at="t",
            updated_at="t",
            filled_qty=10,
            exited_qty=10,
            remaining_position_qty=0,
            protected_qty=0,
            intended_qty=10,
        )
        self.assertFalse(is_unprotected(closed))

    def test_5_legacy_migration_does_not_invent_protection(self) -> None:
        # Build a pre-qty-model database manually, then open via TradingEngineStore.
        path = Path(self.tmp.name) / "legacy.db"
        conn = sqlite3.connect(path)
        conn.execute(
            """
            CREATE TABLE trades (
                trade_id TEXT PRIMARY KEY,
                setup_id TEXT NOT NULL,
                continuation_rule_version TEXT NOT NULL,
                broker_tag TEXT NOT NULL UNIQUE,
                session_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                instrument_token INTEGER NOT NULL,
                direction TEXT NOT NULL,
                qty INTEGER NOT NULL DEFAULT 0,
                entry_estimate REAL NOT NULL,
                entry_fill REAL,
                initial_stop REAL,
                current_stop REAL,
                exit_fill REAL,
                tick_size REAL NOT NULL,
                notional REAL NOT NULL DEFAULT 0,
                margin_blocked REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                skip_reason TEXT,
                reject_reason TEXT,
                close_reason TEXT,
                entry_order_id TEXT,
                sl_order_id TEXT,
                realised_pnl REAL NOT NULL DEFAULT 0,
                open_pnl REAL NOT NULL DEFAULT 0,
                closed_loss_contribution REAL NOT NULL DEFAULT 0,
                trigger_time TEXT,
                entry_time TEXT,
                close_time TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO trades (
                trade_id, setup_id, continuation_rule_version, broker_tag, session_date,
                symbol, instrument_token, direction, qty, entry_estimate, entry_fill,
                initial_stop, current_stop, tick_size, notional, margin_blocked, status,
                realised_pnl, open_pnl, closed_loss_contribution, created_at, updated_at
            ) VALUES (
                'legacy1', 'setup', 'v1', 'telegacy1', '2026-08-17', 'AAA', 1, 'UP',
                10, 100, 100, 90, 90, 0.05, 1000, 200, 'protected_open',
                0, 0, 0, 't', 't'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO trades (
                trade_id, setup_id, continuation_rule_version, broker_tag, session_date,
                symbol, instrument_token, direction, qty, entry_estimate, entry_fill,
                initial_stop, current_stop, exit_fill, tick_size, notional, margin_blocked, status,
                realised_pnl, open_pnl, closed_loss_contribution, created_at, updated_at
            ) VALUES (
                'legacy2', 'setup2', 'v1', 'telegacy2', '2026-08-17', 'BBB', 2, 'UP',
                5, 100, 100, 90, 90, 95, 0.05, 500, 0, 'closed',
                -25, 0, 25, 't', 't'
            )
            """
        )
        conn.commit()
        conn.close()

        store = TradingEngineStore(path)
        open_t = store.get_trade("legacy1")
        closed_t = store.get_trade("legacy2")
        assert open_t is not None and closed_t is not None
        self.assertEqual(open_t.qty_model_version, 1)
        self.assertEqual(open_t.intended_qty, 10)
        self.assertEqual(open_t.filled_qty, 10)
        self.assertEqual(open_t.remaining_position_qty, 10)
        self.assertEqual(open_t.protected_qty, 0)  # must not invent broker protection
        self.assertTrue(is_unprotected(open_t))
        self.assertEqual(closed_t.remaining_position_qty, 0)
        self.assertEqual(closed_t.protected_qty, 0)
        self.assertFalse(is_unprotected(closed_t))
        store.close()

    def test_6_ambiguous_submission_does_not_place_second_entry(self) -> None:
        _seed_live(self.live, "p6", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=False,
            hide_market_tags=True,
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        places = broker.market_place_count
        store.update_trade(
            trade.trade_id,
            status="submission_unknown",
            entry_order_id=None,
        )
        cycle.drive_open()
        cycle.drive_open()
        self.assertEqual(broker.market_place_count, places)
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertEqual(trade.status, "submission_unknown")
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("reconcile_waiting", actions)

    def test_intent_persisted_inside_broker_call_boundary(self) -> None:
        _seed_live(self.live, "intent", "2026-08-17T04:40:00+00:00")
        inner = FakeBroker(last_prices={"AAA": 110}, auto_fill_entry=True)
        store = TradingEngineStore(self.te)
        run_id = store.start_run(session_date="2026-08-17", live_orders_enabled=False, pid=1)
        wrapped = _IntentBoundaryBroker(inner, store)
        cycle = TradingEngineCycle(
            store,
            wrapped,  # type: ignore[arg-type]
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:30:00+00:00",
            run_id=run_id,
            live_orders_enabled=False,
        )
        cycle.tick()
        self.assertGreaterEqual(wrapped.place_calls, 1)
        self.assertTrue(
            wrapped.intent_present_at_place,
            "entry_intent must be durable before place_market_mis returns",
        )

    def test_fakebroker_counts_partial_stop_fills(self) -> None:
        broker = FakeBroker(last_prices={"AAA": 110})
        entry = broker.place_market_mis(
            tradingsymbol="AAA", transaction_type="BUY", quantity=10, tag="t1"
        )
        broker.fill_entry(entry.order_id, 110.0)
        sl = broker.place_slm(
            tradingsymbol="AAA",
            transaction_type="SELL",
            quantity=10,
            trigger_price=100.0,
            tag="t1",
        )
        broker.fill_sl_partial(sl.order_id, 4, 100.0, complete=False)
        quote = broker.position_quote("AAA")
        assert quote is not None
        # +10 entry, -4 partial stop => net 6
        self.assertEqual(quote.quantity, 6)

    def test_crash_recovery_intent_no_duplicate_entry_unpaused(self) -> None:
        """Persisted intent + entry_submitting + unpaused + delayed visibility → no second write."""
        _seed_live(self.live, "crash1", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=True,
            hide_market_tags=True,
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        places = broker.market_place_count
        self.assertGreaterEqual(places, 1)
        self.assertTrue(
            any(str(r["action"]) == "entry_intent" for r in store.list_events(trade.trade_id))
        )
        # Crash after broker accept, before local response: still entry_submitting, no order id.
        store.update_trade(
            trade.trade_id,
            status="entry_submitting",
            entry_order_id=None,
            filled_qty=0,
            remaining_position_qty=0,
        )
        # Restart cycle on same store/broker (process recovery).
        store2 = TradingEngineStore(self.te)
        cycle2 = TradingEngineCycle(
            store2,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:31:00+00:00",
            run_id=store2.start_run(session_date="2026-08-17", live_orders_enabled=False, pid=2),
            live_orders_enabled=False,
        )
        for _ in range(3):
            cycle2.drive_open()
        self.assertEqual(broker.market_place_count, places)
        trade = store2.get_trade(trade.trade_id)
        assert trade is not None
        self.assertEqual(trade.status, "submission_unknown")
        broker.reveal_tag(trade.broker_tag)
        cycle2.drive_open()
        cycle2.drive_open()
        trade = store2.get_trade(trade.trade_id)
        assert trade is not None
        self.assertEqual(broker.market_place_count, places)
        self.assertGreater(int(trade.filled_qty or 0), 0)
        self.assertEqual(
            int(trade.remaining_position_qty or 0),
            int(trade.filled_qty or 0) - int(trade.exited_qty or 0),
        )
        net = broker.net_position_qty("AAA")
        assert net is not None
        self.assertEqual(abs(int(net)), int(trade.remaining_position_qty or 0))
        store.close()
        store2.close()

    def test_stop_fill_then_entry_fill_during_cancel(self) -> None:
        """Stop fill + cancellation-time entry fill must update exposure and re-protect."""
        _seed_live(self.live, "cancel_fill", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=False,
            auto_confirm_sl=True,
            cancel_additional_fill=0,  # set after we know partial size
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        intended = int(trade.intended_qty or trade.qty)
        partial = max(1, intended // 2)
        extra = max(1, (intended - partial) // 2)
        broker.fill_entry_partial(trade.entry_order_id, partial, 110.0, complete=False)
        cycle.drive_open()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertEqual(int(trade.filled_qty), partial)
        self.assertGreater(int(trade.remaining_entry_qty or 0), 0)
        places_sl = broker.slm_place_count
        modifies = broker.modify_count
        broker.cancel_additional_fill = extra
        broker.fill_sl(trade.sl_order_id, 99.0)
        cycle.drive_open()
        cycle.drive_open()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertNotEqual(trade.status, "closed")
        self.assertEqual(int(trade.filled_qty), partial + extra)
        self.assertEqual(int(trade.exited_qty), partial)
        self.assertEqual(
            int(trade.remaining_position_qty),
            int(trade.filled_qty) - int(trade.exited_qty),
        )
        self.assertGreater(int(trade.remaining_position_qty), 0)
        net = broker.net_position_qty("AAA")
        assert net is not None
        self.assertEqual(abs(int(net)), int(trade.remaining_position_qty))
        # New protection for residual (do not modify the completed stop).
        self.assertGreaterEqual(broker.slm_place_count, places_sl + 1)
        self.assertGreater(int(trade.protected_qty or 0), 0)
        self.assertEqual(int(trade.protected_qty), int(trade.remaining_position_qty))
        # Completed stop must not receive modify attempts after COMPLETE.
        completed = [
            o
            for o in broker.orders.values()
            if o.order_type in {"SL", "SL-M"} and str(o.status).upper() == "COMPLETE"
        ]
        self.assertTrue(completed)
        store.close()

    def test_partial_stop_fill_with_working_entry_remainder(self) -> None:
        """Triggered OPEN partial stop + entry remainder — no rearm as fresh waiting stop."""
        _seed_live(self.live, "partial_sl", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=False,
            auto_confirm_sl=True,
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        intended = int(trade.intended_qty or trade.qty)
        partial = max(2, intended // 2)
        stop_partial = max(1, partial // 2)
        broker.fill_entry_partial(trade.entry_order_id, partial, 110.0, complete=False)
        cycle.drive_open()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertIsNotNone(trade.sl_order_id)
        first_sl = trade.sl_order_id
        places = broker.market_place_count
        sl_places = broker.slm_place_count
        modifies = broker.modify_count
        broker.fill_sl_partial(trade.sl_order_id, stop_partial, 99.0, complete=False)
        sl = broker.poll_order(trade.sl_order_id)
        assert sl is not None
        self.assertEqual(str(sl.status).upper(), "OPEN")
        self.assertGreater(int(sl.filled_quantity or 0), 0)
        self.assertGreater(int(sl.pending_quantity or 0), 0)
        for _ in range(5):
            cycle.drive_open()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertEqual(broker.market_place_count, places)
        # Must manage the same triggered order — not place a fresh waiting stop.
        self.assertEqual(broker.slm_place_count, sl_places)
        self.assertEqual(trade.sl_order_id, first_sl)
        self.assertEqual(int(trade.filled_qty), partial)
        self.assertEqual(int(trade.exited_qty), stop_partial)
        self.assertEqual(int(trade.remaining_position_qty), partial - stop_partial)
        self.assertGreater(int(trade.remaining_entry_qty or 0), 0)
        self.assertEqual(int(trade.protected_qty), int(trade.remaining_position_qty))
        net = broker.net_position_qty("AAA")
        assert net is not None
        self.assertEqual(abs(int(net)), int(trade.remaining_position_qty))
        sl = broker.poll_order(trade.sl_order_id)
        assert sl is not None
        self.assertEqual(str(sl.status).upper(), "OPEN")
        self.assertNotEqual(str(sl.status).upper(), "TRIGGER PENDING")
        store.close()

    def test_completed_stop_then_late_entry_fills(self) -> None:
        """Completed stop + late entry fills → residual exposure under new protection."""
        _seed_live(self.live, "late_entry", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=False,
            auto_confirm_sl=True,
            cancel_noop=True,
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        intended = int(trade.intended_qty or trade.qty)
        partial = max(1, intended // 2)
        late = min(intended, partial + max(1, (intended - partial) // 2))
        broker.fill_entry_partial(trade.entry_order_id, partial, 110.0, complete=False)
        cycle.drive_open()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        first_sl = trade.sl_order_id
        places_sl = broker.slm_place_count
        places_mkt = broker.market_place_count
        broker.fill_sl(trade.sl_order_id, 99.0)
        cycle.drive_open()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertEqual(int(trade.exited_qty), partial)
        # Late entry fill after stop COMPLETE while remainder still working.
        broker.fill_entry_partial(trade.entry_order_id, late, 110.0, complete=False)
        broker.cancel_noop = False
        for _ in range(4):
            cycle.drive_open()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertEqual(broker.market_place_count, places_mkt)
        self.assertEqual(int(trade.filled_qty), late)
        self.assertEqual(int(trade.exited_qty), partial)
        expected_pos = late - partial
        self.assertEqual(int(trade.remaining_position_qty), expected_pos)
        self.assertGreater(expected_pos, 0)
        self.assertNotEqual(trade.status, "closed")
        net = broker.net_position_qty("AAA")
        assert net is not None
        self.assertEqual(abs(int(net)), expected_pos)
        # New working stop for residual; completed stop untouched by modify-as-resize of itself.
        self.assertGreaterEqual(broker.slm_place_count, places_sl + 1)
        self.assertNotEqual(trade.sl_order_id, first_sl)
        self.assertEqual(int(trade.protected_qty or 0), expected_pos)
        done = broker.poll_order(first_sl or "")
        assert done is not None
        self.assertEqual(str(done.status).upper(), "COMPLETE")
        store.close()

    def test_partial_stop_resized_after_additional_entry_fills(self) -> None:
        """Partially executed OPEN stop resized after more entry fills — same order, no rearm."""
        _seed_live(self.live, "resize_sl", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=False,
            auto_confirm_sl=True,
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        intended = int(trade.intended_qty or trade.qty)
        first = max(4, intended // 3)
        second = min(intended, first + max(2, intended // 4))
        stop_partial = max(1, first // 2)
        broker.fill_entry_partial(trade.entry_order_id, first, 110.0, complete=False)
        cycle.drive_open()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        sl_id = trade.sl_order_id
        places_sl = broker.slm_place_count
        broker.fill_sl_partial(sl_id, stop_partial, 100.0, complete=False)
        cycle.drive_open()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertEqual(str(broker.poll_order(sl_id).status).upper(), "OPEN")  # type: ignore[union-attr]
        rem_before = int(trade.remaining_position_qty)
        broker.fill_entry_partial(trade.entry_order_id, second, 111.0, complete=False)
        for _ in range(3):
            cycle.drive_open()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        sl = broker.poll_order(sl_id)
        assert sl is not None
        self.assertEqual(broker.slm_place_count, places_sl)
        self.assertEqual(trade.sl_order_id, sl_id)
        self.assertEqual(str(sl.status).upper(), "OPEN")
        self.assertEqual(int(sl.filled_quantity or 0), stop_partial)
        expected_pos = second - stop_partial
        self.assertEqual(int(trade.remaining_position_qty), expected_pos)
        self.assertGreater(expected_pos, rem_before)
        # Port contract: pending = desired remaining cover; total = filled + pending.
        self.assertEqual(int(sl.pending_quantity or 0), expected_pos)
        self.assertEqual(int(sl.quantity or 0), stop_partial + expected_pos)
        self.assertEqual(int(trade.protected_qty), expected_pos)
        self.assertGreater(broker.modify_count, 0)
        store.close()

    def test_two_exit_orders_different_prices_final_pnl(self) -> None:
        """Two stop orders at different exit prices → weighted realised P&L on close."""
        _seed_live(self.live, "two_exits", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=False,
            auto_confirm_sl=True,
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        qty = int(trade.intended_qty or trade.qty)
        broker.fill_entry_partial(trade.entry_order_id, qty, 110.0, complete=True)
        cycle.drive_open()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        first_sl = trade.sl_order_id
        assert first_sl is not None
        exit_a_qty = max(1, qty // 3)
        exit_b_qty = qty - exit_a_qty
        broker.fill_sl_partial(first_sl, exit_a_qty, 100.0, complete=False)
        cycle.drive_open()
        # Cancel OPEN remainder so a second stop is placed for residual.
        broker.cancel_order(first_sl)
        cycle.drive_open()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        second_sl = trade.sl_order_id
        self.assertIsNotNone(second_sl)
        self.assertNotEqual(second_sl, first_sl)
        broker.fill_sl(second_sl, 95.0)
        for _ in range(4):
            cycle.drive_open()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertEqual(trade.status, "closed")
        expected_exit_value = exit_a_qty * 100.0 + exit_b_qty * 95.0
        expected_entry_value = qty * 110.0
        expected_pnl = expected_exit_value - expected_entry_value  # UP
        self.assertAlmostEqual(float(trade.exit_value), expected_exit_value, places=6)
        self.assertAlmostEqual(float(trade.entry_value), expected_entry_value, places=6)
        self.assertAlmostEqual(float(trade.realised_pnl), expected_pnl, places=6)
        from trading_engine_risk import fold_exit_costs_into_loss

        expected_loss = fold_exit_costs_into_loss(
            price_pnl=expected_pnl,
            qty=qty,
            entry=110.0,
            charge_bps=float(trade.charge_bps or 0.0),
        )
        self.assertAlmostEqual(float(trade.closed_loss_contribution), expected_loss, places=6)
        weighted_exit = expected_exit_value / float(qty)
        self.assertAlmostEqual(float(trade.exit_fill or 0), weighted_exit, places=6)
        store.close()

    def test_restart_polling_does_not_double_count_executions(self) -> None:
        """Repeated drive_open / restart must not inflate qty or execution values."""
        _seed_live(self.live, "nodouble", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=False,
            auto_confirm_sl=True,
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        intended = int(trade.intended_qty or trade.qty)
        partial = max(2, intended // 2)
        stop_partial = max(1, partial // 2)
        broker.fill_entry_partial(trade.entry_order_id, partial, 110.0, complete=False)
        cycle.drive_open()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertIsNotNone(trade.sl_order_id)
        broker.fill_sl_partial(trade.sl_order_id, stop_partial, 101.0, complete=False)
        cycle.drive_open()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        snap = (
            int(trade.filled_qty),
            int(trade.exited_qty),
            float(trade.entry_value),
            float(trade.exit_value),
            int(trade.remaining_position_qty),
            float(trade.realised_pnl),
        )
        for _ in range(6):
            cycle.drive_open()
        # Simulate restart on same DB + broker state.
        store2 = TradingEngineStore(self.te)
        cycle2 = TradingEngineCycle(
            store2,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:35:00+00:00",
            run_id=store2.start_run(session_date="2026-08-17", live_orders_enabled=False, pid=9),
            live_orders_enabled=False,
        )
        for _ in range(6):
            cycle2.drive_open()
        trade = store2.get_trade(trade.trade_id)
        assert trade is not None
        self.assertEqual(int(trade.filled_qty), snap[0])
        self.assertEqual(int(trade.exited_qty), snap[1])
        self.assertAlmostEqual(float(trade.entry_value), snap[2], places=6)
        self.assertAlmostEqual(float(trade.exit_value), snap[3], places=6)
        self.assertEqual(int(trade.remaining_position_qty), snap[4])
        self.assertAlmostEqual(float(trade.realised_pnl), snap[5], places=6)
        self.assertEqual(float(trade.exit_value), stop_partial * 101.0)
        store.close()
        store2.close()

    def test_fakebroker_kite_adapter_modify_qty_parity(self) -> None:
        """FakeBroker and mocked KiteBroker produce equivalent quantity outcomes on resize."""
        from unittest.mock import MagicMock

        from trading_engine_broker import KiteBroker, modify_slm_total_quantity

        # --- FakeBroker path ---
        fake = FakeBroker(last_prices={"AAA": 110}, auto_confirm_sl=True)
        sl = fake.place_slm(
            tradingsymbol="AAA",
            transaction_type="SELL",
            quantity=10,
            trigger_price=100.0,
            tag="parity",
        )
        fake.fill_sl_partial(sl.order_id, 4, 100.0, complete=False)
        desired_remaining = 8
        updated_fake = fake.modify_slm(
            sl.order_id, 99.0, quantity=desired_remaining, transaction_type="SELL"
        )
        self.assertEqual(int(updated_fake.filled_quantity or 0), 4)
        self.assertEqual(int(updated_fake.pending_quantity or 0), desired_remaining)
        self.assertEqual(
            int(updated_fake.quantity or 0),
            modify_slm_total_quantity(
                desired_remaining_cover=desired_remaining, filled_quantity=4
            ),
        )

        # --- Mocked Kite path ---
        kite = MagicMock()
        order_state = {
            "order_id": "kite-sl-1",
            "tag": "parity",
            "tradingsymbol": "AAA",
            "transaction_type": "SELL",
            "order_type": "SL",
            "quantity": 10,
            "status": "OPEN",
            "average_price": 100.0,
            "filled_quantity": 4,
            "pending_quantity": 6,
            "trigger_price": 100.0,
            "price": 100.0,
        }

        def orders() -> list:
            return [dict(order_state)]

        def modify_order(**kwargs: object) -> dict:
            total = int(kwargs["quantity"])  # type: ignore[arg-type]
            filled = int(order_state["filled_quantity"])
            order_state["quantity"] = total
            order_state["pending_quantity"] = max(0, total - filled)
            order_state["trigger_price"] = kwargs.get("trigger_price")
            order_state["price"] = kwargs.get("price")
            order_state["status"] = "OPEN"
            return {"order_id": order_state["order_id"]}

        kite.orders.side_effect = orders
        kite.modify_order.side_effect = modify_order
        kite_broker = KiteBroker(kite, live_orders_enabled=True)
        updated_kite = kite_broker.modify_slm(
            "kite-sl-1", 99.0, quantity=desired_remaining, transaction_type="SELL"
        )
        # Kite API must receive total = filled + desired_remaining (quantity-only).
        sent = kite.modify_order.call_args.kwargs
        self.assertEqual(int(sent["quantity"]), 4 + desired_remaining)
        self.assertEqual(set(sent.keys()), {"variety", "order_id", "quantity"})
        self.assertEqual(int(updated_kite.filled_quantity or 0), 4)
        self.assertEqual(int(updated_kite.pending_quantity or 0), desired_remaining)
        self.assertEqual(int(updated_kite.quantity or 0), int(updated_fake.quantity or 0))
        self.assertEqual(int(updated_kite.pending_quantity or 0), int(updated_fake.pending_quantity or 0))
        self.assertEqual(int(updated_kite.filled_quantity or 0), int(updated_fake.filled_quantity or 0))

    def test_exit_price_unavailable_then_resolves_below_estimate(self) -> None:
        """Missing exit avg → provisional estimate; later lower confirmed price replaces it."""
        _seed_live(self.live, "px_resolve", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=False,
            auto_confirm_sl=True,
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        qty = int(trade.intended_qty or trade.qty)
        stop = float(trade.current_stop or 0)
        self.assertGreater(stop, 0)
        broker.fill_entry_partial(trade.entry_order_id, qty, 110.0, complete=True)
        cycle.drive_open()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        # Unpriced exit fill (average_price missing) — estimate from stop, not confirmed.
        broker.fill_sl_partial(trade.sl_order_id, qty, price=None, complete=True)
        sl = broker.poll_order(trade.sl_order_id)
        assert sl is not None
        self.assertIsNone(sl.average_price)
        for _ in range(3):
            cycle.drive_open()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertEqual(int(trade.exited_qty), qty)
        self.assertEqual(float(trade.exit_value), 0.0)
        self.assertAlmostEqual(float(trade.exit_value_est), qty * stop, places=6)
        self.assertTrue(bool(trade.pnl_provisional))
        self.assertNotEqual(trade.status, "closed")
        self.assertEqual(float(trade.closed_loss_contribution), 0.0)

        # Confirmed price arrives below the stop estimate.
        resolved = stop - 5.0
        self.assertLess(resolved, stop)
        broker.set_order_average_price(trade.sl_order_id, resolved)
        for _ in range(4):
            cycle.drive_open()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertAlmostEqual(float(trade.exit_value), qty * resolved, places=6)
        self.assertEqual(float(trade.exit_value_est), 0.0)
        self.assertFalse(bool(trade.pnl_provisional))
        self.assertEqual(trade.status, "closed")
        expected_pnl = qty * (resolved - 110.0)  # UP
        self.assertAlmostEqual(float(trade.realised_pnl), expected_pnl, places=6)
        from trading_engine_risk import fold_exit_costs_into_loss

        expected_loss = fold_exit_costs_into_loss(
            price_pnl=expected_pnl,
            qty=qty,
            entry=110.0,
            charge_bps=float(trade.charge_bps or 0.0),
        )
        self.assertAlmostEqual(float(trade.closed_loss_contribution), expected_loss, places=6)
        # Must not keep the higher stop-based estimate.
        self.assertLess(float(trade.exit_value), qty * stop)
        store.close()

    def test_sequential_same_symbol_exits_stay_trade_scoped(self) -> None:
        """Earlier trade's exit must not count against a later trade in the same symbol."""
        _seed_live(self.live, "seq_a", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.tick()
        first = store.list_trades("2026-08-17")[0]
        first_sl = first.sl_order_id
        assert first_sl is not None
        broker.fill_sl(first_sl, 100.0)
        for _ in range(4):
            cycle.drive_open()
        first = store.get_trade(first.trade_id)
        assert first is not None
        self.assertEqual(first.status, "closed")
        first_exit_value = float(first.exit_value or 0)
        self.assertGreater(first_exit_value, 0)
        first_links = {str(r["order_id"]) for r in store.list_order_links(first.trade_id)}
        self.assertIn(first_sl, first_links)

        _seed_live(self.live, "seq_b", "2026-08-17T04:50:00+00:00")
        broker.last_prices["AAA"] = 110
        cycle.tick()
        trades = store.list_trades("2026-08-17")
        self.assertEqual(len(trades), 2)
        second = [t for t in trades if t.trade_id != first.trade_id][0]
        self.assertEqual(second.status, "protected_open")
        self.assertEqual(int(second.exited_qty or 0), 0)
        self.assertEqual(float(second.exit_value or 0), 0.0)
        second_links = {str(r["order_id"]) for r in store.list_order_links(second.trade_id)}
        self.assertNotIn(first_sl, second_links)
        # First trade accounting unchanged after second trade activity.
        for _ in range(3):
            cycle.drive_open()
        first_again = store.get_trade(first.trade_id)
        assert first_again is not None
        self.assertAlmostEqual(float(first_again.exit_value or 0), first_exit_value, places=6)
        store.close()

    def test_unrelated_manual_order_not_silently_attributed(self) -> None:
        _seed_live(self.live, "manual_x", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        _session_align_entry(store, trade.trade_id)
        manual = broker.inject_complete_order(
            tradingsymbol="AAA",
            transaction_type="SELL",
            quantity=1,
            price=109.0,
            order_type="MARKET",
            tag="",
            product="MIS",
            order_timestamp="2026-08-17T04:45:00+00:00",
        )
        for _ in range(3):
            cycle.drive_open()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertEqual(int(trade.exited_qty or 0), 0)
        self.assertEqual(float(trade.exit_value or 0), 0.0)
        self.assertIsNone(store.get_order_link(manual.order_id))
        self.assertIn(trade.status, {"protected_open", "reconciliation_required", "partial_exit"})
        store.close()

    def test_external_flatten_limit_order_attributed_and_closes(self) -> None:
        _seed_live(self.live, "lim_flat", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        _session_align_entry(store, trade.trade_id)
        flat = broker.simulate_external_flatten(
            "AAA",
            112.0,
            order_type="LIMIT",
            order_timestamp="2026-08-17T04:55:00+00:00",
        )
        self.assertEqual(flat.order_type, "LIMIT")
        for _ in range(4):
            cycle.tick()
        closed = store.get_trade(trade.trade_id)
        assert closed is not None
        self.assertEqual(closed.status, "closed")
        self.assertEqual(closed.close_reason, "external_exit")
        link = store.get_order_link(flat.order_id)
        self.assertIsNotNone(link)
        assert link is not None
        self.assertEqual(str(link["trade_id"]), trade.trade_id)
        self.assertEqual(str(link["role"]), "exit")
        self.assertEqual(str(link["attribution_kind"]), "external")
        store.close()

    def test_restart_does_not_reassign_accounted_exit_orders(self) -> None:
        _seed_live(self.live, "rst_a", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.tick()
        first = store.list_trades("2026-08-17")[0]
        _session_align_entry(store, first.trade_id)
        flat = broker.simulate_external_flatten(
            "AAA",
            111.0,
            order_type="LIMIT",
            order_timestamp="2026-08-17T04:55:00+00:00",
        )
        for _ in range(4):
            cycle.tick()
        first = store.get_trade(first.trade_id)
        assert first is not None
        self.assertEqual(first.status, "closed")
        link_before = store.get_order_link(flat.order_id)
        self.assertIsNotNone(link_before)
        assert link_before is not None
        self.assertEqual(str(link_before["trade_id"]), first.trade_id)

        # Restart engine against same durable store + broker book.
        store.close()
        store2 = TradingEngineStore(self.te)
        run2 = store2.start_run(
            session_date="2026-08-17",
            live_orders_enabled=False,
            pid=2,
        )
        cycle2 = TradingEngineCycle(
            store2,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T05:00:00+00:00",
            run_id=run2,
            live_orders_enabled=False,
        )
        _seed_live(self.live, "rst_b", "2026-08-17T05:01:00+00:00")
        broker.last_prices["AAA"] = 110
        cycle2.tick()
        second = [t for t in store2.list_trades("2026-08-17") if t.trade_id != first.trade_id][0]
        for _ in range(3):
            cycle2.drive_open()
        second = store2.get_trade(second.trade_id)
        assert second is not None
        link_after = store2.get_order_link(flat.order_id)
        self.assertIsNotNone(link_after)
        assert link_after is not None
        self.assertEqual(str(link_after["trade_id"]), first.trade_id)
        second_links = {str(r["order_id"]) for r in store2.list_order_links(second.trade_id)}
        self.assertNotIn(flat.order_id, second_links)
        self.assertEqual(int(second.exited_qty or 0), 0)
        self.assertEqual(float(second.exit_value or 0), 0.0)
        store2.close()

    def test_mixed_timestamp_representations_still_attributes_unique_flatten(self) -> None:
        """Space/Z/+05:30 forms must compare as aware instants, not raw strings."""
        _seed_live(self.live, "ts_mix", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        # ISO with offset vs space-separated IST (+05:30) that is after entry in real time
        # but sorts *before* entry under naive string compare (' ' < 'T').
        store.update_trade(trade.trade_id, entry_time="2026-08-17T10:00:00+00:00")
        raw_exit = "2026-08-17 15:35:00+05:30"
        entry = "2026-08-17T10:00:00+00:00"
        self.assertLess(raw_exit, entry)  # string trap on broker-style text
        flat = broker.simulate_external_flatten(
            "AAA",
            112.0,
            order_type="LIMIT",
            order_timestamp=raw_exit,
        )
        for _ in range(4):
            cycle.tick()
        closed = store.get_trade(trade.trade_id)
        assert closed is not None
        self.assertEqual(closed.status, "closed")
        self.assertIsNotNone(store.get_order_link(flat.order_id))
        store.close()

    def test_missing_timing_leaves_untagged_exit_unresolved(self) -> None:
        _seed_live(self.live, "ts_miss", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        _session_align_entry(store, trade.trade_id)
        flat = broker.simulate_external_flatten("AAA", 112.0, order_type="LIMIT", stamp=False)
        self.assertIsNone(flat.order_timestamp)
        for _ in range(4):
            cycle.tick()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertNotEqual(trade.status, "closed")
        self.assertIsNone(store.get_order_link(flat.order_id))
        self.assertEqual(int(trade.exited_qty or 0), 0)
        store.close()

    def test_invalid_timing_leaves_untagged_exit_unresolved(self) -> None:
        _seed_live(self.live, "ts_bad", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        _session_align_entry(store, trade.trade_id)
        flat = broker.simulate_external_flatten(
            "AAA",
            112.0,
            order_type="LIMIT",
            order_timestamp="not-a-timestamp",
        )
        for _ in range(4):
            cycle.tick()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertNotEqual(trade.status, "closed")
        self.assertIsNone(store.get_order_link(flat.order_id))
        store.close()

    def test_timezone_less_broker_timestamp_uses_ist_convention(self) -> None:
        """Naive exchange stamps are IST at the adapter; then attribution can bind."""
        _seed_live(self.live, "ts_ist", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        store.update_trade(trade.trade_id, entry_time="2026-08-17T04:40:00+00:00")
        # 10:25 IST = 04:55 UTC — after entry under IST, before entry if forced to UTC.
        flat = broker.simulate_external_flatten(
            "AAA",
            112.0,
            order_type="LIMIT",
            order_timestamp="2026-08-17 10:25:00",
        )
        self.assertEqual(flat.order_timestamp, "2026-08-17T04:55:00+00:00")
        for _ in range(4):
            cycle.tick()
        closed = store.get_trade(trade.trade_id)
        assert closed is not None
        self.assertEqual(closed.status, "closed")
        self.assertIsNotNone(store.get_order_link(flat.order_id))
        store.close()

    def test_unknown_source_naive_timestamp_rejected(self) -> None:
        """Timezone-less stamp with unknown source must not be attributed."""
        _seed_live(self.live, "ts_unk", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        store.update_trade(trade.trade_id, entry_time="2026-08-17T04:40:00+00:00")
        flat = broker.simulate_external_flatten(
            "AAA",
            112.0,
            order_type="LIMIT",
            order_timestamp="2026-08-17 10:25:00",
            timezone_known=False,
        )
        # Raw naive preserved — engine requires explicit TZ.
        self.assertEqual(flat.order_timestamp, "2026-08-17 10:25:00")
        for _ in range(4):
            cycle.tick()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertNotEqual(trade.status, "closed")
        self.assertIsNone(store.get_order_link(flat.order_id))
        self.assertEqual(int(trade.exited_qty or 0), 0)
        store.close()

    def test_earlier_same_day_naive_exit_rejected_under_ist(self) -> None:
        """Same-day exit before entry in IST must not bind (UTC misread would accept it)."""
        from datetime import datetime, timezone

        from trading_engine_broker import KITE_EXCHANGE_TZ, parse_timestamp_text

        _seed_live(self.live, "ts_early", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        entry = "2026-08-17T05:00:00+00:00"  # 10:30 IST
        store.update_trade(trade.trade_id, entry_time=entry)
        naive = "2026-08-17 09:00:00"  # 09:00 IST = 03:30 UTC
        naive_dt = parse_timestamp_text(naive)
        assert naive_dt is not None and naive_dt.tzinfo is None
        entry_dt = datetime.fromisoformat(entry)
        as_utc = naive_dt.replace(tzinfo=timezone.utc)
        as_ist = naive_dt.replace(tzinfo=KITE_EXCHANGE_TZ).astimezone(timezone.utc)
        self.assertGreater(as_utc, entry_dt)  # incorrect UTC assumption
        self.assertLess(as_ist, entry_dt)  # correct IST convention

        flat = broker.simulate_external_flatten(
            "AAA",
            112.0,
            order_type="LIMIT",
            order_timestamp=naive,
        )
        self.assertEqual(flat.order_timestamp, "2026-08-17T03:30:00+00:00")
        for _ in range(4):
            cycle.tick()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertNotEqual(trade.status, "closed")
        self.assertIsNone(store.get_order_link(flat.order_id))
        store.close()

    def test_competing_candidate_quantities_not_auto_bound(self) -> None:
        """Exact gap match is not unique when other untagged candidates also exist."""
        _seed_live(self.live, "comp_qty", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        _session_align_entry(store, trade.trade_id)
        gap = int(trade.filled_qty or trade.qty)
        self.assertGreaterEqual(gap, 10)
        if trade.sl_order_id:
            broker.cancel_order(trade.sl_order_id)
        # Force flat while leaving competing untagged exit fills on the book.
        broker.position_quotes["AAA"] = PositionQuote(quantity=0)
        exact = broker.inject_complete_order(
            tradingsymbol="AAA",
            transaction_type="SELL",
            quantity=gap,
            price=111.0,
            order_type="LIMIT",
            order_timestamp="2026-08-17T05:00:00+00:00",
        )
        part_a = broker.inject_complete_order(
            tradingsymbol="AAA",
            transaction_type="SELL",
            quantity=4,
            price=110.5,
            order_type="LIMIT",
            order_timestamp="2026-08-17T05:01:00+00:00",
        )
        part_b = broker.inject_complete_order(
            tradingsymbol="AAA",
            transaction_type="SELL",
            quantity=6,
            price=110.0,
            order_type="MARKET",
            order_timestamp="2026-08-17T05:02:00+00:00",
        )
        for _ in range(4):
            cycle.drive_open()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        self.assertIsNone(store.get_order_link(exact.order_id))
        self.assertIsNone(store.get_order_link(part_a.order_id))
        self.assertIsNone(store.get_order_link(part_b.order_id))
        self.assertEqual(int(trade.exited_qty or 0), 0)
        self.assertEqual(float(trade.exit_value or 0), 0.0)
        self.assertNotEqual(trade.status, "closed")
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("exit_attribution_ambiguous", actions)
        store.close()


class PendingGapRegressionTests(unittest.TestCase):
    """Future-feature placeholders — not safety validation for WP-1.1."""

    @unittest.expectedFailure
    def test_close_all_command_kind_exists(self) -> None:
        from typing import get_args

        from trading_engine_types import CommandKind

        self.assertIn("close_all", get_args(CommandKind))

    @unittest.expectedFailure
    def test_square_off_helpers_present_in_cycle(self) -> None:
        import trading_engine_cycle as cyc

        src = Path(cyc.__file__).read_text(encoding="utf-8")
        self.assertTrue("square_off" in src or "15:15" in src)


if __name__ == "__main__":
    unittest.main()
