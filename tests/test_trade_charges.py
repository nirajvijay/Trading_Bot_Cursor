"""The Charges tab: legs, pricing, caching and the API. Display only."""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

from api.services import trade_charges as tc
from api.services.trade_charges import (
    LegPlan,
    build_legs,
    compute_day,
    price_plans,
)


def order(
    oid: str,
    side: str,
    qty: int,
    price: Optional[float],
    *,
    at: str = "2026-09-25 10:00:00",
    symbol: str = "AAA",
    status: str = "COMPLETE",
    order_type: str = "LIMIT",
    product: str = "MIS",
) -> Dict[str, Any]:
    return {
        "order_id": oid,
        "tradingsymbol": symbol,
        "exchange": "NSE",
        "transaction_type": side,
        "variety": "regular",
        "product": product,
        "order_type": order_type,
        "status": status,
        "filled_quantity": qty,
        "quantity": qty,
        "average_price": price,
        "order_timestamp": at,
    }


def book_of(*orders: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {o["order_id"]: o for o in orders}


def charges_row(leg: Dict[str, Any], total: float) -> Dict[str, Any]:
    """A contract-note row shaped like the live response (probe 2026-09-25)."""
    return {
        "tradingsymbol": leg["tradingsymbol"],
        "transaction_type": leg["transaction_type"],
        "quantity": leg["quantity"],
        "exchange": "NSE",
        "order_type": leg["order_type"],
        "price": leg["average_price"],
        "product": "MIS",
        "variety": "regular",
        "charges": {
            "transaction_tax": 0.1,
            "transaction_tax_type": "stt",
            "exchange_turnover_charge": 0.2,
            "sebi_turnover_charge": 0.01,
            "brokerage": 1.0,
            "stamp_duty": 0.0,
            "gst": {"igst": 0.3, "cgst": 0, "sgst": 0, "total": 0.3},
            "total": total,
        },
    }


class FakeKite:
    """Orders plus a contract-note calculator that echoes rows in order."""

    def __init__(self, orders: List[Dict[str, Any]], *, total_per_leg: float = 2.0) -> None:
        self._orders = orders
        self.total_per_leg = total_per_leg
        self.note_calls: List[List[Dict[str, Any]]] = []
        self.orders_calls = 0
        self.orders_error: Optional[Exception] = None
        self.note_error: Optional[Exception] = None

    def orders(self) -> List[Dict[str, Any]]:
        self.orders_calls += 1
        if self.orders_error:
            raise self.orders_error
        return list(self._orders)

    def get_virtual_contract_note(self, params: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        self.note_calls.append(params)
        if self.note_error:
            raise self.note_error
        return [charges_row(p, self.total_per_leg) for p in params]


# ----------------------------------------------------------------------
# build_legs
# ----------------------------------------------------------------------


class BuildLegsTests(unittest.TestCase):
    def test_one_exit_makes_a_balanced_pair(self) -> None:
        plan = build_legs("e", ["x"], book_of(order("e", "BUY", 10, 100.0), order("x", "SELL", 10, 101.0)))
        self.assertTrue(plan.ok)
        self.assertEqual([(l.transaction_type, l.quantity) for l in plan.legs], [("BUY", 10), ("SELL", 10)])
        # Kite's booked order type is passed through (MARKET books as LIMIT).
        self.assertEqual(plan.legs[0].order_type, "LIMIT")

    def test_a_short_trade_pairs_sell_then_buy(self) -> None:
        plan = build_legs("e", ["x"], book_of(order("e", "SELL", 5, 200.0), order("x", "BUY", 5, 198.0)))
        self.assertTrue(plan.ok)
        self.assertEqual([l.transaction_type for l in plan.legs], ["SELL", "BUY"])

    def test_several_exits_are_taken_oldest_first(self) -> None:
        book = book_of(
            order("e", "BUY", 10, 100.0),
            order("late", "SELL", 6, 99.0, at="2026-09-25 11:00:00"),
            order("early", "SELL", 4, 98.0, at="2026-09-25 10:30:00"),
        )
        plan = build_legs("e", ["late", "early"], book)
        self.assertEqual([(l.order_id, l.quantity) for l in plan.legs], [("e", 10), ("early", 4), ("late", 6)])

    def test_an_over_exit_is_capped_so_bought_equals_sold(self) -> None:
        book = book_of(
            order("e", "BUY", 10, 100.0),
            order("x1", "SELL", 8, 99.0, at="2026-09-25 10:30:00"),
            order("x2", "SELL", 10, 99.5, at="2026-09-25 10:31:00"),
            # Beyond the entry and unpriced: must not block the covered trade.
            order("x3", "SELL", 3, None, at="2026-09-25 10:32:00"),
        )
        plan = build_legs("e", ["x1", "x2", "x3"], book)
        self.assertTrue(plan.ok)
        self.assertEqual([(l.order_id, l.quantity) for l in plan.legs], [("e", 10), ("x1", 8), ("x2", 2)])

    def test_exits_that_do_not_cover_the_entry_are_refused(self) -> None:
        # A lone remainder would be priced as delivery; never guess.
        plan = build_legs("e", ["x"], book_of(order("e", "BUY", 10, 100.0), order("x", "SELL", 7, 99.0)))
        self.assertEqual(plan.reason, tc.REASON_UNCOVERED)

    def test_missing_orders_are_refused(self) -> None:
        self.assertEqual(build_legs("e", ["x"], {}).reason, tc.REASON_ENTRY_MISSING)
        self.assertEqual(build_legs(None, ["x"], {}).reason, tc.REASON_ENTRY_MISSING)
        book = book_of(order("e", "BUY", 10, 100.0))
        self.assertEqual(build_legs("e", ["x"], book).reason, tc.REASON_EXIT_MISSING)
        self.assertEqual(build_legs("e", [], book).reason, tc.REASON_NO_EXITS)

    def test_an_order_still_working_is_refused(self) -> None:
        book = book_of(order("e", "BUY", 10, 100.0), order("x", "SELL", 10, 99.0, status="TRIGGER PENDING"))
        self.assertEqual(build_legs("e", ["x"], book).reason, tc.REASON_NOT_FINAL)

    def test_a_cancelled_order_with_a_partial_fill_counts(self) -> None:
        book = book_of(
            order("e", "BUY", 10, 100.0),
            order("x1", "SELL", 4, 99.0, status="CANCELLED", at="2026-09-25 10:30:00"),
            order("x2", "SELL", 6, 99.0, at="2026-09-25 10:31:00"),
        )
        self.assertTrue(build_legs("e", ["x1", "x2"], book).ok)

    def test_an_unpriced_fill_is_refused(self) -> None:
        book = book_of(order("e", "BUY", 10, None), order("x", "SELL", 10, 99.0))
        self.assertEqual(build_legs("e", ["x"], book).reason, tc.REASON_FILL_UNKNOWN)
        book = book_of(order("e", "BUY", 10, 100.0), order("x", "SELL", 10, 0.0))
        self.assertEqual(build_legs("e", ["x"], book).reason, tc.REASON_FILL_UNKNOWN)

    def test_an_exit_that_does_not_match_the_entry_is_refused(self) -> None:
        same_side = book_of(order("e", "BUY", 10, 100.0), order("x", "BUY", 10, 99.0))
        self.assertEqual(build_legs("e", ["x"], same_side).reason, tc.REASON_LEG_MISMATCH)
        other_symbol = book_of(order("e", "BUY", 10, 100.0), order("x", "SELL", 10, 99.0, symbol="BBB"))
        self.assertEqual(build_legs("e", ["x"], other_symbol).reason, tc.REASON_LEG_MISMATCH)
        other_product = book_of(order("e", "BUY", 10, 100.0), order("x", "SELL", 10, 99.0, product="CNC"))
        self.assertEqual(build_legs("e", ["x"], other_product).reason, tc.REASON_LEG_MISMATCH)


# ----------------------------------------------------------------------
# price_plans
# ----------------------------------------------------------------------


def pair_plan(prefix: str, symbol: str = "AAA", qty: int = 10) -> LegPlan:
    book = book_of(
        order(f"{prefix}e", "BUY", qty, 100.0, symbol=symbol),
        order(f"{prefix}x", "SELL", qty, 101.0, symbol=symbol),
    )
    return build_legs(f"{prefix}e", [f"{prefix}x"], book)


class PricePlansTests(unittest.TestCase):
    def test_rows_are_split_back_to_their_own_trade(self) -> None:
        kite = FakeKite([])
        plans = [("t1", pair_plan("a", "AAA")), ("t2", pair_plan("b", "BBB"))]
        out = price_plans(kite, plans)
        self.assertEqual(len(kite.note_calls), 1)  # one batched call
        self.assertAlmostEqual(out["t1"].charges["total"], 4.0)
        self.assertAlmostEqual(out["t2"].charges["total"], 4.0)
        self.assertAlmostEqual(out["t1"].charges["gst"], 0.6)  # gst.total summed over 2 legs
        self.assertAlmostEqual(out["t1"].charges["brokerage"], 2.0)

    def test_a_shifted_or_short_response_prices_nothing(self) -> None:
        kite = FakeKite([])
        plans = [("t1", pair_plan("a", "AAA")), ("t2", pair_plan("b", "BBB"))]
        with mock.patch.object(kite, "get_virtual_contract_note", side_effect=lambda p: [charges_row(x, 1) for x in p][::-1]):
            out = price_plans(kite, plans)
        self.assertEqual({v.reason for v in out.values()}, {tc.REASON_BAD_RESPONSE})
        with mock.patch.object(kite, "get_virtual_contract_note", side_effect=lambda p: [charges_row(p[0], 1)]):
            out = price_plans(kite, plans)
        self.assertEqual({v.reason for v in out.values()}, {tc.REASON_BAD_RESPONSE})

    def test_an_unreadable_charge_prices_nothing_for_that_trade(self) -> None:
        kite = FakeKite([])

        def note(params):
            rows = [charges_row(x, 1) for x in params]
            rows[0]["charges"]["total"] = "n/a"
            return rows

        with mock.patch.object(kite, "get_virtual_contract_note", side_effect=note):
            out = price_plans(kite, [("t1", pair_plan("a")), ("t2", pair_plan("b", "BBB"))])
        self.assertEqual(out["t1"].reason, tc.REASON_BAD_RESPONSE)
        self.assertIsNotNone(out["t2"].charges)

    def test_a_failed_call_marks_its_trades_unavailable(self) -> None:
        kite = FakeKite([])
        kite.note_error = RuntimeError("boom")
        out = price_plans(kite, [("t1", pair_plan("a"))])
        self.assertEqual(out["t1"].reason, tc.REASON_KITE_UNAVAILABLE)

    def test_a_dead_token_propagates(self) -> None:
        kite = FakeKite([])
        kite.note_error = tc._KiteTokenException("dead")
        with self.assertRaises(tc._KiteTokenException):
            price_plans(kite, [("t1", pair_plan("a"))])

    def test_chunks_never_split_a_trade(self) -> None:
        kite = FakeKite([])
        plans = [(f"t{i}", pair_plan(f"p{i}", f"S{i}")) for i in range(5)]
        with mock.patch.object(tc, "MAX_LEGS_PER_CALL", 5):
            out = price_plans(kite, plans)
        self.assertEqual([len(c) for c in kite.note_calls], [4, 4, 2])
        self.assertEqual(len(out), 5)
        self.assertTrue(all(v.charges is not None for v in out.values()))


# ----------------------------------------------------------------------
# compute_day: engine DB read-only, cache, dates
# ----------------------------------------------------------------------

TODAY = "2026-09-25"


def _candidate(trade_id: str, symbol: str, session_date: str):
    from engine_types import TriggerCandidate

    return TriggerCandidate(
        setup_id=trade_id,
        continuation_rule_version="v1",
        session_date=session_date,
        tradingsymbol=symbol,
        instrument_token=1,
        direction="UP",
        trigger_price=100.0,
        pullback_swing_high=102.0,
        pullback_swing_low=98.0,
        tick_size=0.05,
        buffer_ticks=1,
        trigger_exchange_ts=f"{session_date}T10:00:00+00:00",
        created_at=f"{session_date}T10:00:00+00:00",
        vwap_classification="ACCEPT",
        last_price=100.0,
        breakout_candle_volume=5000,
        avg_prior_3_1m_volume=1000.0,
    )


class ComputeDayTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.engine_db = self.dir / "execution_engine.db"
        self.cache_db = self.dir / "trade_charges.db"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def seed(self, *rows: Dict[str, Any]) -> None:
        from engine_store import SqlitePositionStore
        from engine_types import ExecutionState, Position

        store = SqlitePositionStore(self.engine_db)
        try:
            for r in rows:
                store.save(
                    Position(
                        trade_id=r["trade_id"],
                        candidate=_candidate(r["trade_id"], r.get("symbol", "AAA"), r.get("date", TODAY)),
                        state=r.get("state", ExecutionState.CLOSED),
                        qty=r.get("qty", 10),
                        entry_price=100.0,
                        entry_order_id=r.get("entry"),
                        realised_pnl=r.get("pnl", 10.0),
                        is_live=r.get("live", True),
                        run_id="run-1",
                        extra=r.get("extra", {}),
                    )
                )
        finally:
            store.close()

    def run_day(self, kite: Optional[FakeKite], session_date: str = TODAY):
        calls = []

        def factory():
            calls.append(1)
            if kite is None:
                raise AssertionError("Kite must not be called")
            return kite

        day = compute_day(
            session_date,
            today=TODAY,
            engine_db=self.engine_db,
            cache_db=self.cache_db,
            kite_factory=factory,
        )
        return day, len(calls)


class ComputeDayTests(ComputeDayTestCase):
    def test_today_is_priced_saved_and_then_served_from_the_cache(self) -> None:
        self.seed({"trade_id": "t1", "entry": "e1", "extra": {"exit_order_ids": ["x1"]}, "pnl": 10.0})
        kite = FakeKite([order("e1", "BUY", 10, 100.0), order("x1", "SELL", 10, 101.0)])
        day, factory_calls = self.run_day(kite)
        [row] = day.trades
        self.assertEqual(row.status, tc.STATUS_OK)
        self.assertAlmostEqual(row.total_charges, 4.0)
        self.assertAlmostEqual(row.net_pnl, 6.0)
        self.assertAlmostEqual(row.entry_avg, 100.0)
        self.assertAlmostEqual(row.exit_avg, 101.0)
        self.assertEqual(factory_calls, 1)

        # Second load: no Kite at all, same numbers.
        day, factory_calls = self.run_day(None)
        self.assertEqual(factory_calls, 0)
        self.assertAlmostEqual(day.trades[0].net_pnl, 6.0)
        self.assertAlmostEqual(day.trades[0].exit_avg, 101.0)

    def test_closing_order_id_is_used_when_no_exit_list_was_recorded(self) -> None:
        self.seed({"trade_id": "t1", "entry": "e1", "extra": {"closing_order_id": "x1"}})
        kite = FakeKite([order("e1", "BUY", 10, 100.0), order("x1", "SELL", 10, 101.0)])
        day, _ = self.run_day(kite)
        self.assertEqual(day.trades[0].status, tc.STATUS_OK)

    def test_a_past_day_never_calls_kite(self) -> None:
        self.seed({"trade_id": "old", "entry": "e1", "date": "2026-09-24", "extra": {"exit_order_ids": ["x1"]}})
        day, factory_calls = self.run_day(None, "2026-09-24")
        self.assertEqual(factory_calls, 0)
        self.assertEqual(day.trades[0].status, tc.STATUS_UNAVAILABLE)
        self.assertEqual(day.trades[0].reason, tc.REASON_NOT_CAPTURED)

    def test_paper_trades_are_shown_but_never_priced(self) -> None:
        self.seed({"trade_id": "p1", "entry": "e1", "live": False, "extra": {"exit_order_ids": ["x1"]}})
        day, factory_calls = self.run_day(None)
        self.assertEqual(factory_calls, 0)
        self.assertEqual(day.trades[0].status, tc.STATUS_PAPER)

    def test_only_closed_trades_are_listed(self) -> None:
        from engine_types import ExecutionState

        self.seed({"trade_id": "open", "entry": "e1", "state": ExecutionState.PROTECTED})
        day, factory_calls = self.run_day(None)
        self.assertEqual(day.trades, [])
        self.assertEqual(factory_calls, 0)

    def test_a_dead_token_saves_nothing_and_retries_next_time(self) -> None:
        self.seed({"trade_id": "t1", "entry": "e1", "extra": {"exit_order_ids": ["x1"]}})
        kite = FakeKite([order("e1", "BUY", 10, 100.0), order("x1", "SELL", 10, 101.0)])
        kite.orders_error = tc._KiteTokenException("dead")
        day, _ = self.run_day(kite)
        self.assertEqual(day.kite_error, tc.REASON_SESSION_EXPIRED)
        self.assertEqual(day.trades[0].reason, tc.REASON_SESSION_EXPIRED)

        kite.note_error = None
        kite.orders_error = None
        day, factory_calls = self.run_day(kite)
        self.assertEqual(factory_calls, 1)
        self.assertEqual(day.trades[0].status, tc.STATUS_OK)

    def test_a_dead_token_on_the_contract_note_saves_nothing(self) -> None:
        self.seed({"trade_id": "t1", "entry": "e1", "extra": {"exit_order_ids": ["x1"]}})
        kite = FakeKite([order("e1", "BUY", 10, 100.0), order("x1", "SELL", 10, 101.0)])
        kite.note_error = tc._KiteTokenException("dead")
        day, _ = self.run_day(kite)
        self.assertEqual(day.trades[0].reason, tc.REASON_SESSION_EXPIRED)
        self.assertEqual(tc.ChargesCache(self.cache_db).get_many(["t1"]), {})

    def test_an_unpriceable_trade_is_not_saved_and_is_retried(self) -> None:
        self.seed({"trade_id": "t1", "entry": "e1", "extra": {"exit_order_ids": ["x1"]}})
        kite = FakeKite([order("e1", "BUY", 10, 100.0), order("x1", "SELL", 10, 101.0, status="OPEN")])
        day, _ = self.run_day(kite)
        self.assertEqual(day.trades[0].reason, tc.REASON_NOT_FINAL)
        kite._orders = [order("e1", "BUY", 10, 100.0), order("x1", "SELL", 10, 101.0)]
        day, factory_calls = self.run_day(kite)
        self.assertEqual(factory_calls, 1)
        self.assertEqual(day.trades[0].status, tc.STATUS_OK)

    def test_a_saved_row_is_never_overwritten(self) -> None:
        self.seed({"trade_id": "t1", "entry": "e1", "extra": {"exit_order_ids": ["x1"]}})
        kite = FakeKite([order("e1", "BUY", 10, 100.0), order("x1", "SELL", 10, 101.0)])
        self.run_day(kite)
        plan = pair_plan("z")
        cache = tc.ChargesCache(self.cache_db)
        try:
            cache.put("t1", TODAY, plan.legs, {"total": 999.0, "brokerage": 0, "stt": 0, "exchange": 0, "sebi": 0, "stamp_duty": 0, "gst": 0})
            self.assertAlmostEqual(cache.get_many(["t1"])["t1"].charges["total"], 4.0)
        finally:
            cache.close()

    def test_the_engine_database_is_never_written(self) -> None:
        self.seed({"trade_id": "t1", "entry": "e1", "extra": {"exit_order_ids": ["x1"]}})

        def digest() -> Dict[str, str]:
            # The database and its WAL hold the data. The -shm file is SQLite's
            # shared-memory index, which every WAL reader (the desk's too)
            # updates with read marks, so it is not compared.
            return {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.dir.iterdir()
                if p.name in ("execution_engine.db", "execution_engine.db-wal")
            }

        before = digest()
        kite = FakeKite([order("e1", "BUY", 10, 100.0), order("x1", "SELL", 10, 101.0)])
        self.run_day(kite)
        self.run_day(None)
        self.assertEqual(digest(), before)

    def test_closed_at_is_the_close_event_not_a_later_save(self) -> None:
        from engine_store import SqlitePositionStore
        from engine_types import ExecutionState, Position

        store = SqlitePositionStore(self.engine_db)
        try:
            position = Position(
                trade_id="t1",
                candidate=_candidate("t1", "AAA", TODAY),
                state=ExecutionState.CLOSED,
                qty=10,
                entry_order_id="e1",
                realised_pnl=10.0,
                is_live=True,
                extra={"exit_order_ids": ["x1"]},
            )
            store.save_with_event(position, "closed", {"reason": "stop_hit"})
            [event] = store.list_events("t1")
            position.extra["stock_day"] = {"kite_pnl": 10.0}
            store.save(position)  # a later save of the closed row
        finally:
            store.close()
        [trade] = tc.read_closed_trades(self.engine_db, TODAY)
        self.assertEqual(trade.closed_at, event["at"])

    def test_no_engine_database_means_no_trades(self) -> None:
        day, factory_calls = self.run_day(None)
        self.assertEqual(day.trades, [])
        self.assertEqual(factory_calls, 0)
        self.assertFalse(self.engine_db.exists())


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------


class ChargesApiTests(ComputeDayTestCase):
    def setUp(self) -> None:
        super().setUp()
        from tests.auth_test_helpers import disable_web_auth_overrides, make_test_client

        self._env = {
            "EXECUTION_ENGINE_DB_PATH": str(self.engine_db),
            "TRADE_CHARGES_DB_PATH": str(self.cache_db),
        }
        self._saved = {k: os.environ.get(k) for k in self._env}
        os.environ.update(self._env)
        disable_web_auth_overrides()
        self.client = make_test_client()

    def tearDown(self) -> None:
        from tests.auth_test_helpers import clear_auth_overrides

        clear_auth_overrides()
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        super().tearDown()

    def test_totals_reconcile_over_priced_trades_only(self) -> None:
        self.seed(
            {"trade_id": "t1", "entry": "e1", "extra": {"exit_order_ids": ["x1"]}, "pnl": 10.0},
            {"trade_id": "t2", "entry": "e2", "symbol": "BBB", "extra": {"exit_order_ids": ["x2"]}, "pnl": -5.0},
            {"trade_id": "t3", "entry": "e3", "symbol": "CCC", "extra": {"exit_order_ids": ["gone"]}, "pnl": 50.0},
            {"trade_id": "p1", "entry": "e4", "live": False, "pnl": 99.0},
        )
        kite = FakeKite([
            order("e1", "BUY", 10, 100.0), order("x1", "SELL", 10, 101.0),
            order("e2", "BUY", 10, 100.0, symbol="BBB"), order("x2", "SELL", 10, 99.5, symbol="BBB"),
            order("e3", "BUY", 10, 100.0, symbol="CCC"),
        ])
        with mock.patch("api.routers.charges._today", return_value=TODAY), \
             mock.patch.object(tc, "_default_kite", return_value=kite):
            response = self.client.get("/api/v1/charges", params={"session_date": TODAY})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["live_closed_count"], 3)
        self.assertEqual(body["priced_count"], 2)
        self.assertAlmostEqual(body["total_gross_pnl"], 5.0)
        self.assertAlmostEqual(body["total_charges"], 8.0)
        self.assertAlmostEqual(body["total_net_pnl"], -3.0)
        by_id = {t["trade_id"]: t for t in body["trades"]}
        self.assertEqual(by_id["t3"]["reason"], tc.REASON_EXIT_MISSING)
        self.assertIsNone(by_id["t3"]["net_pnl"])
        self.assertEqual(by_id["p1"]["status"], "paper")
        self.assertAlmostEqual(by_id["t1"]["charges"]["total"], 4.0)

    def test_a_malformed_date_is_rejected(self) -> None:
        response = self.client.get("/api/v1/charges", params={"session_date": "25-09-2026"})
        self.assertEqual(response.status_code, 422)

    def test_the_route_requires_a_web_session(self) -> None:
        from api.auth.deps import require_web_session
        from api.main import app

        [route] = [r for r in app.routes if getattr(r, "path", "") == "/api/v1/charges"]
        deps = [d.call for d in route.dependant.dependencies]
        self.assertIn(require_web_session, deps)


if __name__ == "__main__":
    unittest.main()
