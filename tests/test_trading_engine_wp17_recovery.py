"""WP-1.7: cross-session recovery, orphan detection, unlinked historical stops."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from api.admin_config.defaults import DEFAULT_ADMIN_CONFIG_VALUES
from api.admin_config.store import AdminConfigStore
from trading_engine_broker import BrokerOrder, FakeBroker, PositionQuote
from trading_engine_cycle import RECOVERY_EVENTS_TRADE_ID, TradingEngineCycle
from tests.engine_lifecycle_fixture import TradingEngineCycle
from trading_engine_store import TradingEngineStore

from tests.test_trading_engine_cycle import SCHEMA, _seed_live, _fresh_feed_age

_IST = ZoneInfo("Asia/Kolkata")


class Wp17RecoveryTests(unittest.TestCase):
    def test_foreign_symbol_cannot_hide_behind_owned_tag(self) -> None:
        _, broker, prior = self._seed_prior_protected()
        broker.orders["foreign"] = BrokerOrder(
            order_id="foreign", tradingsymbol="FOREIGN", transaction_type="SELL",
            quantity=10, product="MIS", order_type="SL", status="TRIGGER PENDING",
            tag=prior.broker_tag, trigger_price=90, filled_quantity=0, pending_quantity=10,
        )
        store, cycle = self._cycle(broker)
        state = cycle._scan_recovery_state()
        self.assertIn("foreign", [r["order_id"] for r in state["orphan_stops"]])
        self.assertTrue(state["ownership_unresolved"])
        store.close()

    def test_missing_discovery_snapshot_is_not_an_empty_account(self) -> None:
        for field in ("list_orders", "list_net_positions"):
            with self.subTest(field=field):
                broker = FakeBroker()
                setattr(broker, field, lambda: None)
                store, cycle = self._cycle(broker)
                state = cycle._scan_recovery_state()
                self.assertTrue(state["discovery_error"])
                self.assertTrue(state["ownership_unresolved"])
                store.close()

    def test_malformed_discovery_positions_fail_closed(self) -> None:
        for qty in (None, float("nan"), 1.5, "2", True):
            with self.subTest(qty=qty):
                broker = FakeBroker()
                broker.list_net_positions = lambda: {"UNKNOWN": qty}
                store, cycle = self._cycle(broker)
                state = cycle._scan_recovery_state()
                self.assertTrue(state["discovery_error"])
                self.assertTrue(state["blocks_entries"])
                store.close()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.live = Path(self.tmp.name) / "live.db"
        self.te = Path(self.tmp.name) / "te.db"
        self.admin = Path(self.tmp.name) / "admin.db"
        conn = sqlite3.connect(self.live)
        conn.executescript(SCHEMA)
        conn.close()
        admin = AdminConfigStore(self.admin)
        cfg = dict(DEFAULT_ADMIN_CONFIG_VALUES)
        cfg["daily_loss_cap_inr"] = 2995.0
        cfg["entry_cutoff_ist"] = 1445.0
        cfg["square_off_ist"] = 1515.0
        cfg["round_trip_charge_bps"] = 0.0
        cfg["estimated_slippage_bps"] = 0.0
        admin.update_config(cfg, actor="test")
        admin.arm_effective_config(actor="test")
        admin.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _cycle(
        self,
        broker: FakeBroker,
        *,
        clock: datetime | None = None,
        session_date: str = "2026-08-18",
        live_orders_enabled: bool = False,
    ) -> tuple[TradingEngineStore, TradingEngineCycle]:
        store = TradingEngineStore(self.te)
        run_id = store.start_run(
            session_date=session_date,
            live_orders_enabled=live_orders_enabled,
            pid=1,
            total_capital=300_000.0,
        )

        def _clock() -> datetime:
            assert clock is not None
            return clock

        cycle = TradingEngineCycle(
            store,
            broker,
            live_db=self.live,
            session_date=session_date,
            started_at=f"{session_date}T04:30:00+00:00",
            run_id=run_id,
            live_orders_enabled=live_orders_enabled,
            admin_config_db=self.admin,
            clock_fn=_clock if clock is not None else None,
            feed_age_seconds_fn=_fresh_feed_age,
        )
        return store, cycle

    def _seed_prior_protected(
        self,
        *,
        symbol: str = "PRIOR",
        session_date: str = "2026-08-17",
        live_orders_enabled: bool = False,
        with_provenance: bool = True,
    ) -> tuple[TradingEngineStore, FakeBroker, object]:
        broker = FakeBroker(
            last_prices={symbol: 110},
            auto_fill_entry=True,
            auto_confirm_sl=True,
        )
        _seed_live(self.live, f"prior_{symbol}", f"{session_date}T04:40:00+00:00", symbol=symbol)
        store, cycle = self._cycle(
            broker,
            clock=datetime(2026, 8, 17, 14, 0, tzinfo=_IST),
            session_date=session_date,
            live_orders_enabled=live_orders_enabled,
        )
        cycle.tick()
        trade = store.list_trades(session_date)[0]
        self.assertEqual(trade.status, "protected_open")
        if not with_provenance:
            # ADANIPORTS-class: strip provenance while leaving qty/status intact.
            store._conn.execute(
                "UPDATE trades SET run_id = NULL, entry_live_orders_enabled = NULL WHERE trade_id = ?",
                (trade.trade_id,),
            )
            store._conn.commit()
            trade = store.get_trade(trade.trade_id)
            assert trade is not None
        store.close()
        return store, broker, trade

    def test_prior_session_protected_open_blocks_entries_current_session(self) -> None:
        store, broker, prior = self._seed_prior_protected()
        admin = AdminConfigStore(self.admin)
        admin.set_entries_paused(False)
        admin.close()

        store2 = TradingEngineStore(self.te)
        clock = datetime(2026, 8, 18, 14, 5, tzinfo=_IST)
        cycle = TradingEngineCycle(
            store2,
            broker,
            live_db=self.live,
            session_date="2026-08-18",
            started_at="2026-08-18T04:30:00+00:00",
            run_id=store2.start_run(
                session_date="2026-08-18",
                live_orders_enabled=False,
                pid=2,
                total_capital=300_000.0,
            ),
            live_orders_enabled=False,
            admin_config_db=self.admin,
            clock_fn=lambda: clock,
            feed_age_seconds_fn=_fresh_feed_age,
        )
        places_before = broker.market_place_count
        cycle.tick()
        admin = AdminConfigStore(self.admin)
        self.assertTrue(admin.read_entries_paused())
        details = [str(r.get("detail") or "") for r in admin.list_audit(limit=30)]
        self.assertTrue(
            any(d in {"cross_session_recovery", "restart_recovery"} for d in details)
        )
        admin.close()
        refreshed = store2.get_trade(prior.trade_id)
        assert refreshed is not None
        self.assertIn("cross_session_recovery", [str(r["action"]) for r in store2.list_events(prior.trade_id)])
        _seed_live(self.live, "new1", "2026-08-18T04:50:00+00:00", symbol="NEW1")
        before = len(store2.list_trades("2026-08-18"))
        cycle.tick()
        self.assertEqual(len(store2.list_trades("2026-08-18")), before)
        self.assertEqual(broker.market_place_count, places_before)
        store2.close()

    def test_orphan_broker_position_blocks_no_auto_adopt_no_auto_flatten(self) -> None:
        broker = FakeBroker(last_prices={"ORPH": 100}, auto_fill_entry=True, auto_confirm_sl=True)
        broker.position_quotes["ORPH"] = PositionQuote(
            quantity=50,
            average_price=100.0,
            last_price=100.0,
        )
        clock = datetime(2026, 8, 18, 14, 0, tzinfo=_IST)
        store, cycle = self._cycle(broker, clock=clock)
        places = broker.market_place_count
        sl_places = broker.slm_place_count
        trades_before = len(store.list_trades(None))
        cycle.tick()
        admin = AdminConfigStore(self.admin)
        self.assertTrue(admin.read_entries_paused())
        self.assertIn(
            "orphan_recovery",
            [str(r.get("detail") or "") for r in admin.list_audit(limit=30)],
        )
        admin.close()
        actions = [str(r["action"]) for r in store.list_events(RECOVERY_EVENTS_TRADE_ID)]
        self.assertIn("orphan_broker_position", actions)
        self.assertEqual(len(store.list_trades(None)), trades_before)
        self.assertEqual(broker.market_place_count, places)
        self.assertEqual(broker.slm_place_count, sl_places)
        self.assertEqual(broker.position_quotes["ORPH"].quantity, 50)
        store.close()

    def test_unlinked_historical_stop_discovered(self) -> None:
        broker = FakeBroker(last_prices={"MANUAL": 200}, auto_fill_entry=True, auto_confirm_sl=True)
        orphan_stop = BrokerOrder(
            order_id="manual-sl-1",
            tradingsymbol="MANUAL",
            transaction_type="SELL",
            quantity=10,
            product="MIS",
            order_type="SL-M",
            status="TRIGGER PENDING",
            tag="MANUALTAG",
            trigger_price=190.0,
            pending_quantity=10,
            filled_quantity=0,
        )
        broker.orders[orphan_stop.order_id] = orphan_stop
        clock = datetime(2026, 8, 18, 14, 0, tzinfo=_IST)
        store, cycle = self._cycle(broker, clock=clock)
        places = broker.market_place_count
        cycle.tick()
        admin = AdminConfigStore(self.admin)
        self.assertTrue(admin.read_entries_paused())
        admin.close()
        events = store.list_events(RECOVERY_EVENTS_TRADE_ID)
        actions = [str(r["action"]) for r in events]
        self.assertIn("unlinked_historical_stop", actions)
        payload = next(
            r for r in events if str(r["action"]) == "unlinked_historical_stop"
        )
        import json

        body = json.loads(str(payload["payload_json"]))
        self.assertEqual(body.get("order_id"), "manual-sl-1")
        self.assertEqual(body.get("action_policy"), "pause_only_no_auto_adopt_flatten")
        self.assertEqual(broker.market_place_count, places)
        self.assertEqual(str(broker.orders["manual-sl-1"].status), "TRIGGER PENDING")
        store.close()

    def test_matched_prior_session_engine_trade_continues_protection(self) -> None:
        store, broker, prior = self._seed_prior_protected(symbol="OWNED")
        assert prior.sl_order_id is not None
        broker.reject_order(prior.sl_order_id, status="CANCELLED")
        places = broker.slm_place_count

        admin = AdminConfigStore(self.admin)
        admin.set_entries_paused(False)
        admin.close()

        store2 = TradingEngineStore(self.te)
        clock = datetime(2026, 8, 18, 14, 10, tzinfo=_IST)
        cycle = TradingEngineCycle(
            store2,
            broker,
            live_db=self.live,
            session_date="2026-08-18",
            started_at="2026-08-18T04:30:00+00:00",
            run_id=store2.start_run(
                session_date="2026-08-18",
                live_orders_enabled=False,
                pid=3,
                total_capital=300_000.0,
            ),
            live_orders_enabled=False,
            admin_config_db=self.admin,
            clock_fn=lambda: clock,
            feed_age_seconds_fn=_fresh_feed_age,
        )
        cycle.tick()
        admin = AdminConfigStore(self.admin)
        self.assertTrue(admin.read_entries_paused())
        admin.close()
        # Management continues — replacement protective stop may be placed.
        cycle.tick()
        self.assertGreater(broker.slm_place_count, places)
        again = store2.get_trade(prior.trade_id)
        assert again is not None
        self.assertNotEqual(again.status, "closed")
        self.assertGreater(int(again.remaining_position_qty or 0), 0)
        store2.close()

    def test_provenance_unknown_row_pause_only(self) -> None:
        store, broker, prior = self._seed_prior_protected(
            symbol="ADANIPORTS", with_provenance=False
        )
        places = broker.market_place_count
        sl_places = broker.slm_place_count
        admin = AdminConfigStore(self.admin)
        admin.set_entries_paused(False)
        admin.close()

        store2 = TradingEngineStore(self.te)
        clock = datetime(2026, 8, 18, 14, 0, tzinfo=_IST)
        cycle = TradingEngineCycle(
            store2,
            broker,
            live_db=self.live,
            session_date="2026-08-18",
            started_at="2026-08-18T04:30:00+00:00",
            run_id=store2.start_run(
                session_date="2026-08-18",
                live_orders_enabled=False,
                pid=4,
                total_capital=300_000.0,
            ),
            live_orders_enabled=False,
            admin_config_db=self.admin,
            clock_fn=lambda: clock,
            feed_age_seconds_fn=_fresh_feed_age,
        )
        cycle.tick()
        admin = AdminConfigStore(self.admin)
        self.assertTrue(admin.read_entries_paused())
        details = [str(r.get("detail") or "") for r in admin.list_audit(limit=30)]
        self.assertTrue(
            any(d in {"provenance_recovery", "cross_session_recovery", "orphan_recovery"} for d in details)
        )
        admin.close()
        refreshed = store2.get_trade(prior.trade_id)
        assert refreshed is not None
        self.assertEqual(refreshed.status, "reconciliation_required")
        actions = [str(r["action"]) for r in store2.list_events(prior.trade_id)]
        self.assertIn("provenance_unknown", actions)
        # No auto-delete, no extra flatten/adopt writes.
        self.assertIsNotNone(store2.get_trade(prior.trade_id))
        self.assertEqual(broker.market_place_count, places)
        self.assertEqual(broker.slm_place_count, sl_places)
        store2.close()

    def test_paper_live_mode_mismatch_not_managed(self) -> None:
        """Known LIVE-origin trade must not be managed by a PAPER engine."""
        store, broker, prior = self._seed_prior_protected(
            symbol="LIVEORIG", live_orders_enabled=True
        )
        # Force LIVE provenance on the row while current engine is PAPER.
        store2 = TradingEngineStore(self.te)
        store2.update_trade(
            prior.trade_id,
            run_id="live-run-x",
            entry_live_orders_enabled=1,
        )
        places = broker.market_place_count
        sl_places = broker.slm_place_count
        admin = AdminConfigStore(self.admin)
        admin.set_entries_paused(False)
        admin.close()
        clock = datetime(2026, 8, 18, 14, 0, tzinfo=_IST)
        cycle = TradingEngineCycle(
            store2,
            broker,
            live_db=self.live,
            session_date="2026-08-18",
            started_at="2026-08-18T04:30:00+00:00",
            run_id=store2.start_run(
                session_date="2026-08-18",
                live_orders_enabled=False,
                pid=5,
                total_capital=300_000.0,
            ),
            live_orders_enabled=False,
            admin_config_db=self.admin,
            clock_fn=lambda: clock,
            feed_age_seconds_fn=_fresh_feed_age,
        )
        if prior.sl_order_id:
            broker.reject_order(prior.sl_order_id, status="CANCELLED")
        cycle.tick()
        cycle.tick()
        admin = AdminConfigStore(self.admin)
        self.assertTrue(admin.read_entries_paused())
        admin.close()
        refreshed = store2.get_trade(prior.trade_id)
        assert refreshed is not None
        self.assertEqual(refreshed.status, "reconciliation_required")
        self.assertIn(
            "mode_mismatch_recovery",
            [str(r["action"]) for r in store2.list_events(prior.trade_id)],
        )
        # PAPER engine must not rewrite protection for LIVE-origin exposure.
        self.assertEqual(broker.slm_place_count, sl_places)
        self.assertEqual(broker.market_place_count, places)
        store2.close()

    def test_restart_with_orphan_and_lost_sl_visibility(self) -> None:
        store, broker, prior = self._seed_prior_protected(symbol="LOSTSL")
        assert prior.sl_order_id is not None
        # Cancel owned stop + hide so replacement becomes needed; inject orphan stop.
        broker.reject_order(prior.sl_order_id, status="CANCELLED")
        orphan = BrokerOrder(
            order_id="orphan-sl-x",
            tradingsymbol="FOREIGN",
            transaction_type="SELL",
            quantity=3,
            product="MIS",
            order_type="SL",
            status="TRIGGER PENDING",
            tag="FOREIGNSL",
            trigger_price=50.0,
            pending_quantity=3,
            filled_quantity=0,
        )
        broker.orders[orphan.order_id] = orphan
        places = broker.slm_place_count

        store2 = TradingEngineStore(self.te)
        clock = datetime(2026, 8, 18, 14, 0, tzinfo=_IST)
        cycle = TradingEngineCycle(
            store2,
            broker,
            live_db=self.live,
            session_date="2026-08-18",
            started_at="2026-08-18T04:30:00+00:00",
            run_id=store2.start_run(
                session_date="2026-08-18",
                live_orders_enabled=False,
                pid=6,
                total_capital=300_000.0,
            ),
            live_orders_enabled=False,
            admin_config_db=self.admin,
            clock_fn=lambda: clock,
            feed_age_seconds_fn=_fresh_feed_age,
        )
        cycle.tick()
        # Owned trade may place exactly one replacement; orphan never adopted/flattened.
        cycle.tick()
        self.assertGreaterEqual(broker.slm_place_count, places)
        owned_tags = {str(prior.broker_tag)}
        for row in store2.list_events(prior.trade_id):
            if str(row["action"]) == "sl_submit_attempt":
                import json

                owned_tags.add(str(json.loads(row["payload_json"]).get("tag") or ""))
        owned_new = [
            o
            for o in broker.orders.values()
            if _is_sl(o) and str(o.tag or "") in owned_tags and o.order_id != prior.sl_order_id
        ]
        # At most one replacement protective write for the owned trade across ticks.
        self.assertLessEqual(len(owned_new), 1)
        self.assertEqual(str(broker.orders["orphan-sl-x"].status), "TRIGGER PENDING")
        actions = [str(r["action"]) for r in store2.list_events(RECOVERY_EVENTS_TRADE_ID)]
        self.assertIn("unlinked_historical_stop", actions)
        admin = AdminConfigStore(self.admin)
        self.assertTrue(admin.read_entries_paused())
        admin.close()
        store2.close()

    def test_duplicate_write_prevention_on_recovery_tick_storm(self) -> None:
        store, broker, prior = self._seed_prior_protected(symbol="STORM")
        assert prior.sl_order_id is not None
        broker.reject_order(prior.sl_order_id, status="CANCELLED")
        store2 = TradingEngineStore(self.te)
        clock = datetime(2026, 8, 18, 14, 0, tzinfo=_IST)
        cycle = TradingEngineCycle(
            store2,
            broker,
            live_db=self.live,
            session_date="2026-08-18",
            started_at="2026-08-18T04:30:00+00:00",
            run_id=store2.start_run(
                session_date="2026-08-18",
                live_orders_enabled=False,
                pid=7,
                total_capital=300_000.0,
            ),
            live_orders_enabled=False,
            admin_config_db=self.admin,
            clock_fn=lambda: clock,
            feed_age_seconds_fn=_fresh_feed_age,
        )
        for _ in range(8):
            cycle.tick()
        working = [
            o
            for o in broker.orders.values()
            if o.tradingsymbol == "STORM"
            and _is_sl(o)
            and str(o.status).upper() in {"TRIGGER PENDING", "OPEN"}
            and o.order_id != prior.sl_order_id
        ]
        self.assertLessEqual(len(working), 1)
        store2.close()

    def test_resume_while_orphan_present_reblocks_entries(self) -> None:
        broker = FakeBroker(last_prices={"ORPH2": 100})
        broker.position_quotes["ORPH2"] = PositionQuote(
            quantity=7, average_price=100.0, last_price=100.0
        )
        clock = datetime(2026, 8, 18, 14, 0, tzinfo=_IST)
        store, cycle = self._cycle(broker, clock=clock)
        cycle.tick()
        admin = AdminConfigStore(self.admin)
        self.assertTrue(admin.read_entries_paused())
        admin.set_entries_paused(False)
        admin.close()
        store.enqueue_command("resume_entries")
        cycle.tick()
        admin = AdminConfigStore(self.admin)
        self.assertTrue(admin.read_entries_paused())
        admin.close()
        store.close()

    def test_daily_loss_cap_2995_preserved(self) -> None:
        admin = AdminConfigStore(self.admin)
        payload = admin.load_active_payload()
        self.assertEqual(float(payload["daily_loss_cap_inr"]), 2995.0)
        admin.close()


def _is_sl(order: BrokerOrder) -> bool:
    return str(order.order_type) in {"SL", "SL-M"}


if __name__ == "__main__":
    unittest.main()
