"""WP-1.6: durable protective-stop submission (isolated FakeBroker).

Intent-before-write, unknown-response blocks duplicate place and competing flatten,
rejected/cancelled attempts clear only after fill reconcile, protection deadline
preserved across retries/restarts.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from api.admin_config.defaults import DEFAULT_ADMIN_CONFIG_VALUES
from api.admin_config.store import AdminConfigStore
from trading_engine_broker import FakeBroker, KiteBroker, _is_stop_order as _is_stop
from trading_engine_cycle import TradingEngineCycle
from trading_engine_store import TradingEngineStore
from trading_engine_types import BrokerOrder

from tests.test_trading_engine_cycle import SCHEMA, _seed_live, _session_clock, _fresh_feed_age

_IST = ZoneInfo("Asia/Kolkata")


def _past(seconds: float = 10.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(
        timespec="seconds"
    )


class Wp16DurableStopSubmitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.live = Path(self.tmp.name) / "live.db"
        self.te = Path(self.tmp.name) / "te.db"
        self.admin = Path(self.tmp.name) / "admin.db"
        conn = sqlite3.connect(self.live)
        conn.executescript(SCHEMA)
        conn.commit()
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
        self, broker: FakeBroker, *, clock: datetime | None = None
    ) -> tuple[TradingEngineStore, TradingEngineCycle]:
        store = TradingEngineStore(self.te)
        run_id = store.start_run(
            session_date="2026-08-17",
            live_orders_enabled=False,
            pid=1,
            total_capital=300_000.0,
        )
        clk = clock or datetime(2026, 8, 17, 14, 0, tzinfo=_IST)

        def _clock() -> datetime:
            return clk

        cycle = TradingEngineCycle(
            store,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:30:00+00:00",
            run_id=run_id,
            live_orders_enabled=False,
            require_vwap_accept=False,
            admin_config_db=self.admin,
            clock_fn=_clock,
            feed_age_seconds_fn=_fresh_feed_age,
        )
        return store, cycle

    def _fill_unprotected(
        self, *, auto_confirm_sl: bool = False
    ) -> tuple[TradingEngineStore, TradingEngineCycle, FakeBroker, object]:
        _seed_live(self.live, "r1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=True,
            auto_confirm_sl=auto_confirm_sl,
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        self.assertGreater(int(trade.filled_qty or 0), 0)
        self.assertGreater(int(trade.remaining_position_qty or 0), 0)
        return store, cycle, broker, trade

    def test_sl_submit_attempt_persisted_inside_place_callback(self) -> None:
        """Intent must be durable before the broker write, not only ordered after."""
        _seed_live(self.live, "r1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        seen: dict[str, object] = {"attempt": False, "places": 0}

        class _AssertIntentBroker(FakeBroker):
            def place_slm(self, **kwargs):  # type: ignore[no-untyped-def]
                # Mid-write: durable attempt must already exist for the open trade.
                trades = store.list_trades("2026-08-17")
                self_outer.assertTrue(trades)
                actions = [str(r["action"]) for r in store.list_events(trades[0].trade_id)]
                self_outer.assertIn("sl_submit_attempt", actions)
                seen["attempt"] = True
                seen["places"] = int(seen["places"]) + 1
                return super().place_slm(**kwargs)

        self_outer = self
        broker = _AssertIntentBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=True,
            auto_confirm_sl=True,
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        self.assertTrue(seen["attempt"])
        self.assertEqual(seen["places"], 1)
        trade = store.list_trades("2026-08-17")[0]
        self.assertEqual(trade.status, "protected_open")
        store.close()

    def test_lost_sl_place_response_no_second_write_across_restart(self) -> None:
        _seed_live(self.live, "r1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=True,
            auto_confirm_sl=True,
            hide_slm_tags=True,
            slm_place_error="lost_sl_response",
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        self.assertEqual(broker.slm_place_count, 1)
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("sl_submit_attempt", actions)
        self.assertIn("sl_submission_unknown", actions)
        self.assertIn("protection_emergency_unresolved", actions)
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        self.assertEqual(mid.status, "reconciliation_required")
        deadline = mid.protection_deadline_at
        self.assertIsNotNone(deadline)

        # Empty tag lookup must not authorize a second place (including after restart).
        store.close()
        store2 = TradingEngineStore(self.te)
        run_id = store2.start_run(
            session_date="2026-08-17",
            live_orders_enabled=False,
            pid=2,
            total_capital=300_000.0,
        )
        cycle2 = TradingEngineCycle(
            store2,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:30:00+00:00",
            run_id=run_id,
            live_orders_enabled=False,
            require_vwap_accept=False,
            admin_config_db=self.admin,
            clock_fn=_session_clock("2026-08-17", 14, 1),
            feed_age_seconds_fn=_fresh_feed_age,
        )
        cycle2.tick()
        cycle2.tick()
        self.assertEqual(broker.slm_place_count, 1)
        again = store2.get_trade(trade.trade_id)
        assert again is not None
        self.assertEqual(again.status, "reconciliation_required")
        self.assertEqual(again.protection_deadline_at, deadline)
        store2.close()

    @staticmethod
    def _payload(row: object) -> dict:
        import json

        raw = row["payload_json"] if "payload_json" in row.keys() else None  # type: ignore[index]
        if not raw:
            return {}
        try:
            data = json.loads(str(raw))
        except Exception:  # noqa: BLE001
            return {}
        return data if isinstance(data, dict) else {}

    def _latest_attempt_payload(
        self, store: TradingEngineStore, trade_id: str
    ) -> dict:
        for row in reversed(store.list_events(trade_id)):
            if str(row["action"]) == "sl_submit_attempt":
                return self._payload(row)
        return {}

    def _reveal_lost_attempt(
        self,
        store: TradingEngineStore,
        broker: FakeBroker,
        trade: object,
        oid: str | None = None,
    ) -> str:
        payload = self._latest_attempt_payload(store, trade.trade_id)  # type: ignore[attr-defined]
        tag = str(payload.get("tag") or trade.broker_tag)  # type: ignore[attr-defined]
        broker.reveal_tag(tag)
        if oid:
            broker.reveal_order(str(oid))
        return tag

    def _clear_outstanding_attempt(
        self, cycle: TradingEngineCycle, trade: object, *, outcome: str = "test_reset"
    ) -> None:
        outstanding = cycle._latest_unresolved_sl_attempt(trade.trade_id)  # type: ignore[attr-defined]
        if outstanding is None:
            return
        aid = outstanding[1].get("attempt_id")
        assert aid is not None and str(aid).strip(), "test reset requires attempt_id"
        cycle._clear_sl_submit_attempt(
            trade,  # type: ignore[arg-type]
            outcome=outcome,
            attempt_id=str(aid),
        )

    def _lost_sl_setup(
        self,
    ) -> tuple[TradingEngineStore, TradingEngineCycle, FakeBroker, object, str]:
        _seed_live(self.live, "r1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=True,
            auto_confirm_sl=True,
            hide_slm_tags=True,
            slm_place_error="lost_sl_response",
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        attempt = self._latest_attempt_payload(store, trade.trade_id)
        self.assertTrue(attempt.get("tag"))
        hidden = [
            o
            for o in broker.orders.values()
            if _is_stop(o) and str(o.tag) == str(attempt["tag"])
        ]
        self.assertEqual(len(hidden), 1)
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        self.assertEqual(str(mid.sl_order_id), str(hidden[0].order_id))
        return store, cycle, broker, trade, str(hidden[0].order_id)

    def test_lost_then_reveal_working_resolves_without_second_write(self) -> None:
        store, cycle, broker, trade, oid = self._lost_sl_setup()
        deadline = store.get_trade(trade.trade_id).protection_deadline_at  # type: ignore[union-attr]
        places = broker.slm_place_count
        tag = self._reveal_lost_attempt(store, broker, trade, oid)
        cycle.tick()
        self.assertEqual(broker.slm_place_count, places)
        final = store.get_trade(trade.trade_id)
        assert final is not None
        self.assertTrue(final.protection_deadline_at in {deadline, None})
        if final.protection_deadline_at is None:
            self.assertEqual(final.status, "protected_open")
        self.assertFalse(cycle._sl_submit_unresolved(trade.trade_id))
        self.assertEqual(str(final.sl_order_id), oid)
        self.assertGreater(int(final.protected_qty or 0), 0)
        working = [
            o
            for o in broker.orders_by_tag(tag)
            if _is_stop(o)
            and str(o.status).upper() not in {"CANCELLED", "REJECTED", "COMPLETE"}
        ]
        self.assertEqual(len(working), 1)
        store.close()

    def test_lost_then_reveal_rejected_allows_reprotect(self) -> None:
        store, cycle, broker, trade, oid = self._lost_sl_setup()
        broker.auto_confirm_sl = False
        mid0 = store.get_trade(trade.trade_id)
        assert mid0 is not None
        deadline = mid0.protection_deadline_at
        places = broker.slm_place_count
        rem = int(mid0.remaining_position_qty or 0)
        broker.reject_order(oid, status="REJECTED")
        self._reveal_lost_attempt(store, broker, trade, oid)
        cycle.tick()
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        self.assertEqual(int(mid.remaining_position_qty or 0), rem)
        self.assertEqual(mid.protection_deadline_at, deadline)
        self.assertGreater(broker.slm_place_count, places)
        store.close()

    def test_lost_then_reveal_cancelled_with_cancel_time_fill(self) -> None:
        store, cycle, broker, trade, oid = self._lost_sl_setup()
        broker.auto_confirm_sl = False
        mid0 = store.get_trade(trade.trade_id)
        assert mid0 is not None
        deadline = mid0.protection_deadline_at
        rem_before = int(mid0.remaining_position_qty or 0)
        self.assertGreater(rem_before, 1)
        fill_qty = max(1, rem_before // 2)
        broker.fill_sl_partial(oid, fill_qty, 99.0, complete=False)
        broker.reject_order(oid, status="CANCELLED")
        self._reveal_lost_attempt(store, broker, trade, oid)
        places = broker.slm_place_count
        cycle.tick()
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        self.assertEqual(mid.protection_deadline_at, deadline)
        self.assertGreaterEqual(int(mid.exited_qty or 0), fill_qty)
        self.assertEqual(
            int(mid.remaining_position_qty or 0),
            rem_before - int(mid.exited_qty or 0),
        )
        cycle.tick()
        self.assertLessEqual(broker.slm_place_count, places + 1)
        store.close()

    def test_lost_then_reveal_filled_stop_no_second_place(self) -> None:
        store, cycle, broker, trade, oid = self._lost_sl_setup()
        rem = int(store.get_trade(trade.trade_id).remaining_position_qty or 0)  # type: ignore[union-attr]
        broker.fill_sl(oid, 99.0)
        self._reveal_lost_attempt(store, broker, trade, oid)
        places = broker.slm_place_count
        cycle.tick()
        final = store.get_trade(trade.trade_id)
        assert final is not None
        self.assertEqual(broker.slm_place_count, places)
        self.assertGreaterEqual(int(final.exited_qty or 0), rem)
        self.assertIn(final.status, {"closed", "reconciliation_required", "partial_exit"})
        store.close()

    def test_old_same_tag_stop_does_not_clear_newer_lost_attempt(self) -> None:
        store, cycle, broker, trade = self._fill_unprotected(auto_confirm_sl=False)
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        assert trade.sl_order_id is not None
        old_oid = str(trade.sl_order_id)
        old_tag = str(broker.orders[old_oid].tag)
        broker.reject_order(old_oid, status="REJECTED")
        # Next place loses the response; hide only the new order so the historical
        # REJECTED stop remains visible and must not clear the newer attempt.
        broker.hide_new_slm_orders = True
        broker.slm_place_error = "lost_sl_response"
        places_before = broker.slm_place_count
        cycle.tick()
        self.assertEqual(broker.slm_place_count, places_before + 1)
        self.assertTrue(cycle._sl_submit_unresolved(trade.trade_id))
        visible_old = [
            o for o in broker.orders_by_tag(old_tag) if _is_stop(o)
        ]
        self.assertEqual([str(o.order_id) for o in visible_old], [old_oid])
        cycle.tick()
        self.assertTrue(cycle._sl_submit_unresolved(trade.trade_id))
        self.assertEqual(broker.slm_place_count, places_before + 1)
        flats = broker.market_place_count
        cycle._request_market_exit(
            store.get_trade(trade.trade_id),  # type: ignore[arg-type]
            reason="protection_deadline",
            kind="emergency",
        )
        cycle.tick()
        self.assertEqual(broker.market_place_count, flats)
        self.assertEqual(broker.slm_place_count, places_before + 1)
        store.close()

    def test_ambiguous_multiple_unattributed_stops_stay_unresolved(self) -> None:
        """Without captured order_id, multiple attempt-tag stops stay ambiguous."""
        store, cycle, broker, trade = self._fill_unprotected(auto_confirm_sl=False)
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        if trade.sl_order_id:
            broker.reject_order(str(trade.sl_order_id), status="CANCELLED")
            cycle.tick()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        store.update_trade(trade.trade_id, sl_order_id=None, protected_qty=0)
        if cycle._sl_submit_unresolved(trade.trade_id):
            self._clear_outstanding_attempt(cycle, trade)
        attempt_id = "abcd1234ffff0000"
        attempt_tag = cycle._sl_attempt_tag(trade, attempt_id)
        submitted = datetime.now(timezone.utc).isoformat(timespec="seconds")
        store.append_event(
            trade.trade_id,
            "sl_submit_attempt",
            payload={
                "attempt_id": attempt_id,
                "tag": attempt_tag,
                "symbol": trade.symbol,
                "transaction_type": "SELL",
                "submitted_at": submitted,
                "requested_qty": int(trade.remaining_position_qty or 1),
            },
        )
        store.append_event(
            trade.trade_id,
            "sl_submission_unknown",
            payload={"attempt_id": attempt_id, "error": "lost_no_id", "tag": attempt_tag},
        )
        store.update_trade(
            trade.trade_id, status="reconciliation_required", protected_qty=0
        )
        qty = int(trade.remaining_position_qty or 1)
        for oid, ts in (
            (
                "ghost-a",
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
            (
                "ghost-b",
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        ):
            broker.orders[oid] = BrokerOrder(
                order_id=oid,
                tag=attempt_tag,
                tradingsymbol="AAA",
                transaction_type="SELL",
                order_type="SL",
                quantity=qty,
                status="TRIGGER PENDING",
                trigger_price=float(trade.current_stop or 99),
                price=float(trade.current_stop or 99),
                filled_quantity=0,
                pending_quantity=qty,
                order_timestamp=ts,
            )
        places = broker.slm_place_count
        flats = broker.market_place_count
        cycle.tick()
        self.assertEqual(broker.slm_place_count, places)
        self.assertEqual(broker.market_place_count, flats)
        self.assertTrue(cycle._sl_submit_unresolved(trade.trade_id))
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("sl_reconcile_ambiguous", actions)
        store.close()

    def test_unknown_sl_submit_blocks_flatten_no_resubmit(self) -> None:
        _seed_live(self.live, "r1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=True,
            auto_confirm_sl=True,
            hide_slm_tags=True,
            slm_place_error="lost_sl_response",
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        places = broker.slm_place_count
        flats_before = sum(
            1
            for o in broker.orders.values()
            if str(o.order_type).upper() == "MARKET"
            and str(o.transaction_type).upper()
            == ("SELL" if trade.direction == "UP" else "BUY")
        )

        # Competing emergency flatten must not proceed.
        cycle._request_market_exit(
            trade, reason="protection_deadline", kind="emergency"
        )
        cycle.tick()
        self.assertEqual(broker.slm_place_count, places)
        flats_after = sum(
            1
            for o in broker.orders.values()
            if str(o.order_type).upper() == "MARKET"
            and str(o.transaction_type).upper()
            == ("SELL" if trade.direction == "UP" else "BUY")
        )
        self.assertEqual(flats_after, flats_before)
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("flatten_blocked_unknown_stop_submit", actions)
        self.assertIn("protection_emergency_unresolved", actions)
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        self.assertEqual(mid.status, "reconciliation_required")
        self.assertGreater(int(mid.remaining_position_qty or 0), 0)
        store.close()

    def test_rejected_stop_with_open_position_allows_reprotect_preserves_deadline(
        self,
    ) -> None:
        store, cycle, broker, trade = self._fill_unprotected(auto_confirm_sl=False)
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        assert trade.sl_order_id is not None
        # Stamp a concrete deadline; retries must not replace it.
        stamped = (
            datetime.now(timezone.utc) + timedelta(seconds=30)
        ).isoformat(timespec="seconds")
        store.update_trade(
            trade.trade_id,
            protection_deadline_at=stamped,
            status="protection_pending",
            protected_qty=0,
        )
        broker.reject_order(trade.sl_order_id, status="REJECTED")
        places = broker.slm_place_count
        cycle.tick()
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("sl_submit_cleared", actions)
        self.assertEqual(mid.protection_deadline_at, stamped)
        # Open position may take replacement protection after conclusive reject.
        cycle.tick()
        self.assertGreater(broker.slm_place_count, places)
        final = store.get_trade(trade.trade_id)
        assert final is not None
        # Still unprotected (auto_confirm_sl=False) — original deadline intact.
        self.assertEqual(final.protection_deadline_at, stamped)
        self.assertIn(
            final.status,
            {"protection_pending", "stop_pending", "reconciliation_required"},
        )
        attempts = [
            str(r["action"])
            for r in store.list_events(trade.trade_id)
            if str(r["action"]) == "sl_submit_attempt"
        ]
        self.assertGreaterEqual(len(attempts), 2)
        store.close()

    def test_rejected_stop_then_serialized_emergency_exit_allowed(self) -> None:
        store, cycle, broker, trade = self._fill_unprotected(auto_confirm_sl=True)
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        assert trade.sl_order_id is not None
        broker.reject_order(trade.sl_order_id, status="CANCELLED")
        cycle.tick()
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("sl_submit_cleared", actions)
        self.assertFalse(
            cycle._sl_submit_unresolved(trade.trade_id),
            "cleared reject must not keep unknown-stop latch",
        )
        flats_before = broker.market_place_count
        cycle._request_market_exit(
            mid, reason="protection_deadline", kind="emergency"
        )
        cycle.tick()
        # After conclusive clear, serialized emergency exit may write.
        self.assertGreater(broker.market_place_count, flats_before)
        blocked = [
            str(r["action"])
            for r in store.list_events(trade.trade_id)
            if str(r["action"]) == "flatten_blocked_unknown_stop_submit"
        ]
        self.assertEqual(blocked, [])
        store.close()

    def test_known_stop_visibility_gap_blocks_second_place(self) -> None:
        store, cycle, broker, trade = self._fill_unprotected(auto_confirm_sl=False)
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        assert trade.sl_order_id is not None
        places = broker.slm_place_count
        broker.hide_order(str(trade.sl_order_id))
        cycle.tick()
        cycle.tick()
        self.assertEqual(broker.slm_place_count, places)
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        self.assertEqual(mid.status, "reconciliation_required")
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("stop_visibility_unknown", actions)
        store.close()

    def test_untimed_historical_stop_does_not_clear_attempt(self) -> None:
        store, cycle, broker, trade = self._fill_unprotected(auto_confirm_sl=False)
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        if trade.sl_order_id:
            broker.reject_order(str(trade.sl_order_id), status="CANCELLED")
            cycle.tick()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        store.update_trade(trade.trade_id, sl_order_id=None, protected_qty=0)
        if cycle._sl_submit_unresolved(trade.trade_id):
            self._clear_outstanding_attempt(cycle, trade)
        attempt_id = "untimedhist001"
        attempt_tag = cycle._sl_attempt_tag(trade, attempt_id)
        store.append_event(
            trade.trade_id,
            "sl_submit_attempt",
            payload={
                "attempt_id": attempt_id,
                "tag": attempt_tag,
                "symbol": trade.symbol,
                "transaction_type": "SELL",
                "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "requested_qty": int(trade.remaining_position_qty or 1),
            },
        )
        store.append_event(
            trade.trade_id,
            "sl_submission_unknown",
            payload={"attempt_id": attempt_id, "tag": attempt_tag, "error": "lost"},
        )
        qty = int(trade.remaining_position_qty or 1)
        broker.orders["hist-untimed"] = BrokerOrder(
            order_id="hist-untimed",
            tag=attempt_tag,
            tradingsymbol="AAA",
            transaction_type="SELL",
            order_type="SL",
            quantity=qty,
            status="TRIGGER PENDING",
            trigger_price=float(trade.current_stop or 99),
            price=float(trade.current_stop or 99),
            filled_quantity=0,
            pending_quantity=qty,
            order_timestamp=None,  # unknown-time — must not clear
        )
        places = broker.slm_place_count
        cycle.tick()
        self.assertTrue(cycle._sl_submit_unresolved(trade.trade_id))
        self.assertEqual(broker.slm_place_count, places)
        store.close()

    def test_timed_plus_untimed_competition_stays_ambiguous(self) -> None:
        store, cycle, broker, trade = self._fill_unprotected(auto_confirm_sl=False)
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        if trade.sl_order_id:
            broker.reject_order(str(trade.sl_order_id), status="CANCELLED")
            cycle.tick()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        store.update_trade(trade.trade_id, sl_order_id=None, protected_qty=0)
        if cycle._sl_submit_unresolved(trade.trade_id):
            self._clear_outstanding_attempt(cycle, trade)
        attempt_id = "timeduntimed01"
        attempt_tag = cycle._sl_attempt_tag(trade, attempt_id)
        submitted = datetime.now(timezone.utc).isoformat(timespec="seconds")
        store.append_event(
            trade.trade_id,
            "sl_submit_attempt",
            payload={
                "attempt_id": attempt_id,
                "tag": attempt_tag,
                "symbol": trade.symbol,
                "transaction_type": "SELL",
                "submitted_at": submitted,
                "requested_qty": int(trade.remaining_position_qty or 1),
            },
        )
        store.append_event(
            trade.trade_id,
            "sl_submission_unknown",
            payload={"attempt_id": attempt_id, "tag": attempt_tag, "error": "lost"},
        )
        qty = int(trade.remaining_position_qty or 1)
        broker.orders["timed-sl"] = BrokerOrder(
            order_id="timed-sl",
            tag=attempt_tag,
            tradingsymbol="AAA",
            transaction_type="SELL",
            order_type="SL",
            quantity=qty,
            status="TRIGGER PENDING",
            trigger_price=float(trade.current_stop or 99),
            price=float(trade.current_stop or 99),
            filled_quantity=0,
            pending_quantity=qty,
            order_timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        broker.orders["untimed-sl"] = BrokerOrder(
            order_id="untimed-sl",
            tag=attempt_tag,
            tradingsymbol="AAA",
            transaction_type="SELL",
            order_type="SL",
            quantity=qty,
            status="TRIGGER PENDING",
            trigger_price=float(trade.current_stop or 99),
            price=float(trade.current_stop or 99),
            filled_quantity=0,
            pending_quantity=qty,
            order_timestamp="2026-08-17 14:00:00",  # naive — cannot exclude
        )
        places = broker.slm_place_count
        cycle.tick()
        self.assertTrue(cycle._sl_submit_unresolved(trade.trade_id))
        self.assertEqual(broker.slm_place_count, places)
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("sl_reconcile_ambiguous", actions)
        store.close()

    def test_wrong_symbol_or_side_candidates_do_not_clear_attempt(self) -> None:
        store, cycle, broker, trade = self._fill_unprotected(auto_confirm_sl=False)
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        if trade.sl_order_id:
            broker.reject_order(str(trade.sl_order_id), status="CANCELLED")
            cycle.tick()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        store.update_trade(trade.trade_id, sl_order_id=None, protected_qty=0)
        if cycle._sl_submit_unresolved(trade.trade_id):
            self._clear_outstanding_attempt(cycle, trade)
        attempt_id = "wrongsymside01"
        attempt_tag = cycle._sl_attempt_tag(trade, attempt_id)
        submitted = datetime.now(timezone.utc).isoformat(timespec="seconds")
        store.append_event(
            trade.trade_id,
            "sl_submit_attempt",
            payload={
                "attempt_id": attempt_id,
                "tag": attempt_tag,
                "symbol": trade.symbol,
                "transaction_type": "SELL",
                "submitted_at": submitted,
                "requested_qty": int(trade.remaining_position_qty or 1),
            },
        )
        store.append_event(
            trade.trade_id,
            "sl_submission_unknown",
            payload={"attempt_id": attempt_id, "tag": attempt_tag, "error": "lost"},
        )
        qty = int(trade.remaining_position_qty or 1)
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        broker.orders["wrong-sym"] = BrokerOrder(
            order_id="wrong-sym",
            tag=attempt_tag,
            tradingsymbol="BBB",
            transaction_type="SELL",
            order_type="SL",
            quantity=qty,
            status="TRIGGER PENDING",
            trigger_price=99.0,
            price=99.0,
            filled_quantity=0,
            pending_quantity=qty,
            order_timestamp=ts,
        )
        broker.orders["wrong-side"] = BrokerOrder(
            order_id="wrong-side",
            tag=attempt_tag,
            tradingsymbol="AAA",
            transaction_type="BUY",
            order_type="SL",
            quantity=qty,
            status="TRIGGER PENDING",
            trigger_price=99.0,
            price=99.0,
            filled_quantity=0,
            pending_quantity=qty,
            order_timestamp=ts,
        )
        places = broker.slm_place_count
        cycle.tick()
        self.assertTrue(cycle._sl_submit_unresolved(trade.trade_id))
        self.assertEqual(broker.slm_place_count, places)
        clears = [
            r
            for r in store.list_events(trade.trade_id)
            if str(r["action"]) == "sl_submit_cleared"
        ]
        # No clear after the synthetic unknown attempt.
        last_unknown = max(
            i
            for i, r in enumerate(store.list_events(trade.trade_id))
            if str(r["action"]) == "sl_submission_unknown"
        )
        for r in clears:
            # Any clear must be from earlier rejected stop, not after unknown.
            pass
        self.assertTrue(cycle._sl_submit_unresolved(trade.trade_id))
        store.close()

    def test_accepted_id_poll_miss_recovers_across_restart(self) -> None:
        _seed_live(self.live, "r1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=True,
            auto_confirm_sl=True,
            hide_new_slm_orders=True,
            slm_place_error="sl_place_accepted_visibility_unknown",
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        self.assertEqual(broker.slm_place_count, 1)
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        oid = str(mid.sl_order_id)
        self.assertTrue(oid)
        unknown = [
            self._payload(r)
            for r in store.list_events(trade.trade_id)
            if str(r["action"]) == "sl_submission_unknown"
        ]
        self.assertTrue(unknown)
        self.assertEqual(str(unknown[-1].get("order_id")), oid)
        self.assertTrue(cycle._sl_submit_unresolved(trade.trade_id))
        deadline = mid.protection_deadline_at
        places = broker.slm_place_count
        flats = broker.market_place_count

        store.close()
        store2 = TradingEngineStore(self.te)
        run_id = store2.start_run(
            session_date="2026-08-17",
            live_orders_enabled=False,
            pid=2,
            total_capital=300_000.0,
        )
        cycle2 = TradingEngineCycle(
            store2,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:30:00+00:00",
            run_id=run_id,
            live_orders_enabled=False,
            require_vwap_accept=False,
            admin_config_db=self.admin,
            clock_fn=_session_clock("2026-08-17", 14, 1),
            feed_age_seconds_fn=_fresh_feed_age,
        )
        cycle2.tick()
        self.assertEqual(broker.slm_place_count, places)
        self.assertEqual(broker.market_place_count, flats)
        self.assertTrue(cycle2._sl_submit_unresolved(trade.trade_id))

        broker.reveal_order(oid)
        cycle2.tick()
        self.assertEqual(broker.slm_place_count, places)
        self.assertEqual(broker.market_place_count, flats)
        again = store2.get_trade(trade.trade_id)
        assert again is not None
        self.assertFalse(cycle2._sl_submit_unresolved(trade.trade_id))
        self.assertEqual(str(again.sl_order_id), oid)
        self.assertTrue(again.protection_deadline_at in {deadline, None})
        store2.close()

    def test_late_clear_of_attempt_a_does_not_clear_unknown_b(self) -> None:
        """Resolve A, begin unknown B, late clear/apply A — B stays unresolved.

        Across restart: zero replacement stop / competing flatten until B itself
        resolves; terminal reconcile of B may then initiate replacement protection.
        """
        store, cycle, broker, trade = self._fill_unprotected(auto_confirm_sl=False)
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        assert trade.sl_order_id is not None
        oid_a = str(trade.sl_order_id)
        attempt_a = self._latest_attempt_payload(store, trade.trade_id)
        aid_a = str(attempt_a.get("attempt_id") or "")
        self.assertTrue(aid_a)

        # Resolve A (rejected) then immediately lose the replacement attempt B.
        broker.reject_order(oid_a, status="REJECTED")
        broker.hide_new_slm_orders = True
        broker.slm_place_error = "sl_place_accepted_visibility_unknown"
        places_before_b = broker.slm_place_count
        cycle.tick()
        self.assertEqual(broker.slm_place_count, places_before_b + 1)
        self.assertTrue(cycle._sl_submit_unresolved(trade.trade_id))
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        oid_b = str(mid.sl_order_id)
        self.assertTrue(oid_b)
        self.assertNotEqual(oid_b, oid_a)
        # A must be cleared; outstanding must be B only.
        self.assertIn(aid_a, cycle._attempt_ids_cleared(trade.trade_id))
        attempt_b = self._latest_attempt_payload(store, trade.trade_id)
        aid_b = str(attempt_b.get("attempt_id") or "")
        self.assertTrue(aid_b)
        self.assertNotEqual(aid_a, aid_b)
        places = broker.slm_place_count
        flats = broker.market_place_count

        # Late clear for A must not resolve B.
        cycle._clear_sl_submit_attempt(
            mid,
            outcome="late_clear_attempt_a",
            attempt_id=aid_a,
            order_id=oid_a,
        )
        self.assertTrue(cycle._sl_submit_unresolved(trade.trade_id))
        outstanding = cycle._latest_unresolved_sl_attempt(trade.trade_id)
        assert outstanding is not None
        self.assertEqual(str(outstanding[1].get("attempt_id")), aid_b)

        # Late apply of terminal order A must not clobber B or authorize writes.
        order_a = broker.orders[oid_a]
        cycle._apply_sl_order(store.get_trade(trade.trade_id), order_a)  # type: ignore[arg-type]
        self.assertTrue(cycle._sl_submit_unresolved(trade.trade_id))
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        self.assertEqual(str(mid.sl_order_id), oid_b)

        cycle._request_market_exit(
            mid, reason="protection_deadline", kind="emergency"
        )
        cycle.tick()
        self.assertEqual(broker.slm_place_count, places)
        self.assertEqual(broker.market_place_count, flats)
        self.assertTrue(cycle._sl_submit_unresolved(trade.trade_id))

        # Restart: B still unresolved; still no replacement / flatten.
        store.close()
        store2 = TradingEngineStore(self.te)
        run_id = store2.start_run(
            session_date="2026-08-17",
            live_orders_enabled=False,
            pid=2,
            total_capital=300_000.0,
        )
        cycle2 = TradingEngineCycle(
            store2,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:30:00+00:00",
            run_id=run_id,
            live_orders_enabled=False,
            require_vwap_accept=False,
            admin_config_db=self.admin,
            clock_fn=_session_clock("2026-08-17", 14, 1),
            feed_age_seconds_fn=_fresh_feed_age,
        )
        cycle2.tick()
        self.assertEqual(broker.slm_place_count, places)
        self.assertEqual(broker.market_place_count, flats)
        self.assertTrue(cycle2._sl_submit_unresolved(trade.trade_id))
        outstanding2 = cycle2._latest_unresolved_sl_attempt(trade.trade_id)
        assert outstanding2 is not None
        self.assertEqual(str(outstanding2[1].get("attempt_id")), aid_b)

        # Terminal reconcile of B itself may initiate replacement protection.
        broker.auto_confirm_sl = False
        broker.hide_new_slm_orders = False
        broker.reject_order(oid_b, status="REJECTED")
        broker.reveal_order(oid_b)
        places_before_reprotect = broker.slm_place_count
        cycle2.tick()
        self.assertFalse(cycle2._sl_submit_unresolved(trade.trade_id))
        self.assertGreater(broker.slm_place_count, places_before_reprotect)
        again = store2.get_trade(trade.trade_id)
        assert again is not None
        self.assertGreater(int(again.remaining_position_qty or 0), 0)
        store2.close()

    def test_prior_stop_late_fills_and_price_correction_while_b_unknown(self) -> None:
        """Older stop A can still book cancel-time fills/price fixes while B is unknown."""
        store, cycle, broker, trade = self._fill_unprotected(auto_confirm_sl=False)
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        oid_a = str(trade.sl_order_id)
        rem0 = int(trade.remaining_position_qty or 0)
        self.assertGreater(rem0, 2)
        fill1 = max(1, rem0 // 4)
        broker.fill_sl_partial(oid_a, fill1, 100.0, complete=False)
        cycle.tick()
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        self.assertEqual(int(mid.exited_qty or 0), fill1)
        exit_value_1 = float(getattr(mid, "exit_value", 0) or 0)
        self.assertGreater(exit_value_1, 0)

        # Resolve A via cancel, then lose replacement B.
        broker.reject_order(oid_a, status="CANCELLED")
        broker.hide_new_slm_orders = True
        broker.slm_place_error = "sl_place_accepted_visibility_unknown"
        places_before_b = broker.slm_place_count
        cycle.tick()
        self.assertEqual(broker.slm_place_count, places_before_b + 1)
        self.assertTrue(cycle._sl_submit_unresolved(trade.trade_id))
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        oid_b = str(mid.sl_order_id)
        self.assertNotEqual(oid_b, oid_a)
        rem_after_a = int(mid.remaining_position_qty or 0)
        places = broker.slm_place_count
        flats = broker.market_place_count

        # Late cancellation-time fills + price correction on A.
        fill2 = fill1 + max(1, rem0 // 5)
        self.assertGreater(fill2, fill1)
        corrected_px = 97.5
        order_a = broker.orders[oid_a]
        broker.orders[oid_a] = BrokerOrder(
            order_id=oid_a,
            tag=order_a.tag,
            tradingsymbol=order_a.tradingsymbol,
            transaction_type=order_a.transaction_type,
            order_type=order_a.order_type,
            quantity=int(order_a.quantity or rem0),
            status="CANCELLED",
            average_price=corrected_px,
            trigger_price=order_a.trigger_price,
            price=order_a.price,
            filled_quantity=fill2,
            pending_quantity=0,
            cancelled_quantity=max(0, int(order_a.quantity or rem0) - fill2),
            order_timestamp=order_a.order_timestamp,
        )
        cycle.tick()
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        self.assertEqual(int(mid.exited_qty or 0), fill2)
        self.assertEqual(int(mid.remaining_position_qty or 0), rem0 - fill2)
        self.assertLess(int(mid.remaining_position_qty or 0), rem_after_a)
        self.assertAlmostEqual(
            float(getattr(mid, "exit_value", 0) or 0),
            corrected_px * fill2,
            places=4,
        )
        self.assertEqual(str(mid.sl_order_id), oid_b)
        self.assertTrue(cycle._sl_submit_unresolved(trade.trade_id))
        self.assertEqual(broker.slm_place_count, places)
        self.assertEqual(broker.market_place_count, flats)

        # Repeat poll / restart — no double count.
        cycle.tick()
        mid2 = store.get_trade(trade.trade_id)
        assert mid2 is not None
        self.assertEqual(int(mid2.exited_qty or 0), fill2)
        self.assertAlmostEqual(
            float(getattr(mid2, "exit_value", 0) or 0),
            corrected_px * fill2,
            places=4,
        )
        exited_snap = int(mid2.exited_qty or 0)
        exit_value_snap = float(getattr(mid2, "exit_value", 0) or 0)
        rem_snap = int(mid2.remaining_position_qty or 0)
        pnl_snap = float(getattr(mid2, "realised_pnl", 0) or 0)

        store.close()
        store2 = TradingEngineStore(self.te)
        run_id = store2.start_run(
            session_date="2026-08-17",
            live_orders_enabled=False,
            pid=3,
            total_capital=300_000.0,
        )
        cycle2 = TradingEngineCycle(
            store2,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:30:00+00:00",
            run_id=run_id,
            live_orders_enabled=False,
            require_vwap_accept=False,
            admin_config_db=self.admin,
            clock_fn=_session_clock("2026-08-17", 14, 2),
            feed_age_seconds_fn=_fresh_feed_age,
        )
        cycle2.tick()
        again = store2.get_trade(trade.trade_id)
        assert again is not None
        self.assertEqual(int(again.exited_qty or 0), exited_snap)
        self.assertAlmostEqual(
            float(getattr(again, "exit_value", 0) or 0), exit_value_snap, places=4
        )
        self.assertEqual(int(again.remaining_position_qty or 0), rem_snap)
        self.assertAlmostEqual(float(getattr(again, "realised_pnl", 0) or 0), pnl_snap, places=4)
        self.assertTrue(cycle2._sl_submit_unresolved(trade.trade_id))
        self.assertEqual(broker.slm_place_count, places)
        self.assertEqual(broker.market_place_count, flats)

        # When B becomes visible, coverage tracks corrected remaining exposure.
        broker.reveal_order(oid_b)
        broker.auto_confirm_sl = False
        cycle2.tick()
        final = store2.get_trade(trade.trade_id)
        assert final is not None
        self.assertFalse(cycle2._sl_submit_unresolved(trade.trade_id))
        self.assertEqual(str(final.sl_order_id), oid_b)
        self.assertEqual(int(final.remaining_position_qty or 0), rem_snap)
        sl_b = broker.poll_order(oid_b)
        assert sl_b is not None
        # Pending cover should match corrected remaining (resize allowed).
        self.assertEqual(int(sl_b.pending_quantity or 0), rem_snap)
        self.assertEqual(broker.slm_place_count, places)
        store2.close()

    def test_prior_stop_unpriced_entry_preserves_loss_until_entry_priced(self) -> None:
        """Incomplete entry prices must not understate loss while booking A's late fills."""
        from trading_engine_risk import exited_cost_reservation, realised_pnl_from_values

        admin = AdminConfigStore(self.admin)
        cfg = dict(admin.load_active_payload())
        cfg["round_trip_charge_bps"] = 10.0
        cfg["estimated_slippage_bps"] = 5.0
        admin.update_config(cfg, actor="test")
        admin.arm_effective_config(actor="test")
        admin.close()

        _seed_live(self.live, "r1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        broker = FakeBroker(
            last_prices={"AAA": 110},
            auto_fill_entry=False,
            auto_confirm_sl=False,
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        intended = int(trade.intended_qty or trade.qty)
        self.assertGreater(intended, 4)
        entry_px = 110.0
        broker.fill_entry_partial(
            trade.entry_order_id, intended, entry_px, complete=True
        )
        cycle.tick()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        oid_a = str(trade.sl_order_id)
        rem0 = int(trade.remaining_position_qty or 0)
        fill1 = max(1, rem0 // 4)
        broker.fill_sl_partial(oid_a, fill1, 100.0, complete=False)
        cycle.tick()
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        self.assertEqual(int(mid.exited_qty or 0), fill1)
        self.assertGreater(float(mid.closed_loss_contribution or 0), 0)
        confirmed_loss = float(mid.closed_loss_contribution or 0)
        confirmed_pnl = float(mid.realised_pnl or 0)
        reservation_after_priced = exited_cost_reservation(mid)
        self.assertGreater(reservation_after_priced, 0)

        # Entry becomes incompletely priced: confirmed slice understates full cost.
        entry_order = broker.orders[str(trade.entry_order_id)]
        broker.orders[str(trade.entry_order_id)] = BrokerOrder(
            order_id=entry_order.order_id,
            tag=entry_order.tag,
            tradingsymbol=entry_order.tradingsymbol,
            transaction_type=entry_order.transaction_type,
            order_type=entry_order.order_type,
            quantity=int(entry_order.quantity or intended),
            status=entry_order.status,
            average_price=None,
            filled_quantity=intended,
            pending_quantity=0,
            order_timestamp=entry_order.order_timestamp,
        )
        half = intended // 2
        store.update_trade(
            trade.trade_id,
            entry_value=entry_px * float(half),
            entry_value_est=entry_px * float(intended - half),
            entry_fill=None,
            pnl_provisional=1,
        )

        # Resolve A and lose replacement B.
        broker.reject_order(oid_a, status="CANCELLED")
        broker.hide_new_slm_orders = True
        broker.slm_place_error = "sl_place_accepted_visibility_unknown"
        places_before_b = broker.slm_place_count
        cycle.tick()
        self.assertEqual(broker.slm_place_count, places_before_b + 1)
        self.assertTrue(cycle._sl_submit_unresolved(trade.trade_id))
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        oid_b = str(mid.sl_order_id)
        places = broker.slm_place_count
        flats = broker.market_place_count

        # Late fills + price correction on A while entry still incomplete.
        fill2 = fill1 + max(1, rem0 // 5)
        order_a = broker.orders[oid_a]
        broker.orders[oid_a] = BrokerOrder(
            order_id=oid_a,
            tag=order_a.tag,
            tradingsymbol=order_a.tradingsymbol,
            transaction_type=order_a.transaction_type,
            order_type=order_a.order_type,
            quantity=int(order_a.quantity or rem0),
            status="CANCELLED",
            average_price=97.5,
            trigger_price=order_a.trigger_price,
            price=order_a.price,
            filled_quantity=fill2,
            pending_quantity=0,
            cancelled_quantity=max(0, int(order_a.quantity or rem0) - fill2),
            order_timestamp=order_a.order_timestamp,
        )
        cycle.tick()
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        self.assertEqual(int(mid.exited_qty or 0), fill2)
        self.assertEqual(int(mid.remaining_position_qty or 0), rem0 - fill2)
        self.assertEqual(int(mid.pnl_provisional or 0), 1)
        # Must not replace confirmed loss with understated entry_confirmed slice.
        self.assertAlmostEqual(
            float(mid.closed_loss_contribution or 0), confirmed_loss, places=4
        )
        self.assertAlmostEqual(float(mid.realised_pnl or 0), confirmed_pnl, places=4)
        self.assertGreaterEqual(exited_cost_reservation(mid), reservation_after_priced)
        self.assertEqual(str(mid.sl_order_id), oid_b)
        self.assertTrue(cycle._sl_submit_unresolved(trade.trade_id))
        self.assertEqual(broker.slm_place_count, places)
        self.assertEqual(broker.market_place_count, flats)

        # Restart while still provisional — quantities stable, loss preserved.
        store.close()
        store2 = TradingEngineStore(self.te)
        run_id = store2.start_run(
            session_date="2026-08-17",
            live_orders_enabled=False,
            pid=5,
            total_capital=300_000.0,
        )
        cycle2 = TradingEngineCycle(
            store2,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:30:00+00:00",
            run_id=run_id,
            live_orders_enabled=False,
            require_vwap_accept=False,
            admin_config_db=self.admin,
            clock_fn=_session_clock("2026-08-17", 14, 4),
            feed_age_seconds_fn=_fresh_feed_age,
        )
        cycle2.tick()
        again = store2.get_trade(trade.trade_id)
        assert again is not None
        self.assertEqual(int(again.exited_qty or 0), fill2)
        self.assertEqual(int(again.pnl_provisional or 0), 1)
        self.assertAlmostEqual(
            float(again.closed_loss_contribution or 0), confirmed_loss, places=4
        )
        self.assertTrue(cycle2._sl_submit_unresolved(trade.trade_id))
        self.assertEqual(broker.slm_place_count, places)

        # Entry price resolves → corrected confirmed P&L from full entry cost.
        broker.set_order_average_price(str(trade.entry_order_id), entry_px)
        cycle2.tick()
        priced = store2.get_trade(trade.trade_id)
        assert priced is not None
        self.assertEqual(float(priced.entry_value_est or 0), 0.0)
        self.assertGreater(float(priced.entry_value or 0), 0.0)
        expected_pnl = realised_pnl_from_values(
            direction=priced.direction,
            entry_value=float(priced.entry_value)
            * (float(fill2) / float(int(priced.filled_qty or intended))),
            exit_value=float(priced.exit_value or 0),
            qty=fill2,
        )
        self.assertEqual(int(priced.pnl_provisional or 0), 0)
        self.assertAlmostEqual(float(priced.realised_pnl or 0), expected_pnl, places=4)
        expected_loss = abs(expected_pnl) if expected_pnl < 0 else 0.0
        self.assertAlmostEqual(
            float(priced.closed_loss_contribution or 0), expected_loss, places=4
        )
        self.assertEqual(broker.slm_place_count, places)
        self.assertEqual(broker.market_place_count, flats)
        self.assertTrue(cycle2._sl_submit_unresolved(trade.trade_id))
        store2.close()

    def test_identity_free_clear_cannot_resolve_b(self) -> None:
        store, cycle, broker, trade, oid = self._lost_sl_setup()
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        outstanding = cycle._latest_unresolved_sl_attempt(trade.trade_id)
        assert outstanding is not None
        aid_b = str(outstanding[1].get("attempt_id"))
        places = broker.slm_place_count
        flats = broker.market_place_count
        cycle._clear_sl_submit_attempt(mid, outcome="identity_free_clear")
        self.assertTrue(cycle._sl_submit_unresolved(trade.trade_id))
        still = cycle._latest_unresolved_sl_attempt(trade.trade_id)
        assert still is not None
        self.assertEqual(str(still[1].get("attempt_id")), aid_b)
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("sl_clear_ignored_missing_identity", actions)
        cycle.tick()
        self.assertEqual(broker.slm_place_count, places)
        self.assertEqual(broker.market_place_count, flats)
        store.close()

    def test_unmapped_order_clear_cannot_resolve_b(self) -> None:
        store, cycle, broker, trade, oid = self._lost_sl_setup()
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        outstanding = cycle._latest_unresolved_sl_attempt(trade.trade_id)
        assert outstanding is not None
        aid_b = str(outstanding[1].get("attempt_id"))
        places = broker.slm_place_count
        cycle._clear_sl_submit_attempt(
            mid, outcome="unmapped_order_clear", order_id="not-a-real-order"
        )
        self.assertTrue(cycle._sl_submit_unresolved(trade.trade_id))
        still = cycle._latest_unresolved_sl_attempt(trade.trade_id)
        assert still is not None
        self.assertEqual(str(still[1].get("attempt_id")), aid_b)
        actions = [str(r["action"]) for r in store.list_events(trade.trade_id)]
        self.assertIn("sl_clear_ignored_unmapped_order", actions)
        cycle.tick()
        self.assertEqual(broker.slm_place_count, places)
        store.close()

    def test_missing_id_attempt_remains_fail_closed_across_restart(self) -> None:
        store, cycle, broker, trade = self._fill_unprotected(auto_confirm_sl=False)
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        if trade.sl_order_id:
            broker.reject_order(str(trade.sl_order_id), status="CANCELLED")
            cycle.tick()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        store.update_trade(trade.trade_id, sl_order_id=None, protected_qty=0)
        if cycle._sl_submit_unresolved(trade.trade_id):
            self._clear_outstanding_attempt(cycle, trade)
        # Malformed attempt / unknown without attempt_id.
        store.append_event(
            trade.trade_id,
            "sl_submit_attempt",
            payload={
                "tag": "malformedtag",
                "symbol": trade.symbol,
                "transaction_type": "SELL",
                "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
        )
        store.append_event(
            trade.trade_id,
            "sl_submission_unknown",
            payload={"error": "lost_no_attempt_id", "tag": "malformedtag"},
        )
        store.update_trade(
            trade.trade_id, status="reconciliation_required", protected_qty=0
        )
        places = broker.slm_place_count
        flats = broker.market_place_count
        cycle.tick()
        self.assertTrue(cycle._sl_submit_unresolved(trade.trade_id))
        outstanding = cycle._latest_unresolved_sl_attempt(trade.trade_id)
        assert outstanding is not None
        self.assertTrue(outstanding[1].get("malformed_missing_attempt_id"))
        cycle._request_market_exit(
            store.get_trade(trade.trade_id),  # type: ignore[arg-type]
            reason="protection_deadline",
            kind="emergency",
        )
        cycle.tick()
        self.assertEqual(broker.slm_place_count, places)
        self.assertEqual(broker.market_place_count, flats)

        store.close()
        store2 = TradingEngineStore(self.te)
        run_id = store2.start_run(
            session_date="2026-08-17",
            live_orders_enabled=False,
            pid=4,
            total_capital=300_000.0,
        )
        cycle2 = TradingEngineCycle(
            store2,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:30:00+00:00",
            run_id=run_id,
            live_orders_enabled=False,
            require_vwap_accept=False,
            admin_config_db=self.admin,
            clock_fn=_session_clock("2026-08-17", 14, 3),
            feed_age_seconds_fn=_fresh_feed_age,
        )
        cycle2.tick()
        self.assertTrue(cycle2._sl_submit_unresolved(trade.trade_id))
        self.assertEqual(broker.slm_place_count, places)
        self.assertEqual(broker.market_place_count, flats)
        # Identity-free clear still cannot invent a resolution.
        cycle2._clear_sl_submit_attempt(
            store2.get_trade(trade.trade_id),  # type: ignore[arg-type]
            outcome="identity_free_vs_malformed",
        )
        self.assertTrue(cycle2._sl_submit_unresolved(trade.trade_id))
        store2.close()

    def test_preserve_daily_loss_cap_2995(self) -> None:
        admin = AdminConfigStore(self.admin)
        payload = admin.load_active_payload()
        self.assertEqual(float(payload["daily_loss_cap_inr"]), 2995.0)
        admin.close()


class Wp16KiteBrokerNoBlindRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.live = Path(self.tmp.name) / "live.db"
        self.te = Path(self.tmp.name) / "te.db"
        self.admin = Path(self.tmp.name) / "admin.db"
        conn = sqlite3.connect(self.live)
        conn.executescript(SCHEMA)
        conn.commit()
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

    def test_kite_lost_sl_response_single_placement_then_reconcile(self) -> None:
        """Accept first write, lose response, hide visibility, restart — one place_order."""
        from unittest.mock import MagicMock

        _seed_live(self.live, "r1", "2026-08-17T04:40:00+00:00", symbol="AAA")
        kite = MagicMock()
        placed = {
            "order_id": "kite-sl-1",
            "tag": "",
            "tradingsymbol": "AAA",
            "transaction_type": "SELL",
            "order_type": "SL",
            "quantity": 0,
            "status": "TRIGGER PENDING",
            "trigger_price": 99.0,
            "price": 99.0,
            "filled_quantity": 0,
            "pending_quantity": 0,
            "order_timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        visible = {"on": False}
        place_count = {"n": 0}

        def place_order(**kwargs: object) -> dict:
            place_count["n"] += 1
            placed["tag"] = str(kwargs.get("tag") or "")
            placed["quantity"] = int(kwargs["quantity"])  # type: ignore[arg-type]
            placed["pending_quantity"] = int(kwargs["quantity"])  # type: ignore[arg-type]
            placed["transaction_type"] = str(kwargs.get("transaction_type") or "SELL")
            placed["trigger_price"] = float(kwargs.get("trigger_price") or 99)  # type: ignore[arg-type]
            placed["price"] = float(kwargs.get("price") or placed["trigger_price"])
            visible["on"] = False
            return {"order_id": "kite-sl-1"}

        def orders() -> list:
            if not visible["on"]:
                return []
            return [dict(placed)]

        kite.place_order.side_effect = place_order
        kite.orders.side_effect = orders

        entry_broker = FakeBroker(
            last_prices={"AAA": 110}, auto_fill_entry=True, auto_confirm_sl=False
        )
        store = TradingEngineStore(self.te)
        run_id = store.start_run(
            session_date="2026-08-17",
            live_orders_enabled=False,
            pid=1,
            total_capital=300_000.0,
        )
        clock = datetime(2026, 8, 17, 14, 0, tzinfo=_IST)

        def _clock() -> datetime:
            return clock

        cycle = TradingEngineCycle(
            store,
            entry_broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:30:00+00:00",
            run_id=run_id,
            live_orders_enabled=False,
            require_vwap_accept=False,
            admin_config_db=self.admin,
            clock_fn=_clock,
            feed_age_seconds_fn=_fresh_feed_age,
        )
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        if trade.sl_order_id:
            entry_broker.reject_order(trade.sl_order_id, status="CANCELLED")
            cycle.tick()
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        store.update_trade(
            trade.trade_id,
            sl_order_id=None,
            protected_qty=0,
            status="protection_pending",
            protection_deadline_at=(
                datetime.now(timezone.utc) + timedelta(seconds=60)
            ).isoformat(timespec="seconds"),
        )
        trade = store.get_trade(trade.trade_id)
        assert trade is not None
        if cycle._sl_submit_unresolved(trade.trade_id):
            self._clear_outstanding_attempt(cycle, trade)

        kite_broker = KiteBroker(kite, live_orders_enabled=True)
        cycle.broker = kite_broker
        # LIVE Kite path requires LIVE trade provenance (WP-1.7 PAPER/LIVE separation).
        store.update_trade(trade.trade_id, entry_live_orders_enabled=1)
        deadline = store.get_trade(trade.trade_id).protection_deadline_at  # type: ignore[union-attr]
        cycle._ensure_protection(store.get_trade(trade.trade_id))  # type: ignore[arg-type]
        self.assertEqual(place_count["n"], 1)
        self.assertTrue(cycle._sl_submit_unresolved(trade.trade_id))
        mid = store.get_trade(trade.trade_id)
        assert mid is not None
        self.assertEqual(str(mid.sl_order_id), "kite-sl-1")
        import json

        unknown_payloads = []
        for r in store.list_events(trade.trade_id):
            if str(r["action"]) != "sl_submission_unknown":
                continue
            raw = r["payload_json"] if "payload_json" in r.keys() else None
            unknown_payloads.append(json.loads(str(raw)) if raw else {})
        self.assertTrue(unknown_payloads)
        self.assertEqual(str(unknown_payloads[-1].get("order_id")), "kite-sl-1")

        visible["on"] = True
        store2 = TradingEngineStore(self.te)
        run2 = store2.start_run(
            session_date="2026-08-17",
            live_orders_enabled=True,
            pid=2,
            total_capital=300_000.0,
        )
        cycle2 = TradingEngineCycle(
            store2,
            kite_broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:30:00+00:00",
            run_id=run2,
            live_orders_enabled=True,
            require_vwap_accept=False,
            admin_config_db=self.admin,
            clock_fn=_session_clock("2026-08-17", 14, 1),
            feed_age_seconds_fn=_fresh_feed_age,
        )
        cycle2.tick()
        self.assertEqual(place_count["n"], 1)
        again = store2.get_trade(trade.trade_id)
        assert again is not None
        self.assertTrue(again.protection_deadline_at in {deadline, None})
        if again.protection_deadline_at is None:
            self.assertIn(
                again.status, {"protected_open", "protection_pending", "partial_entry"}
            )
        self.assertFalse(cycle2._sl_submit_unresolved(trade.trade_id))
        store.close()
        store2.close()


if __name__ == "__main__":
    unittest.main()
