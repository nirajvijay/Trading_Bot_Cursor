"""Continuous reconciliation: adopting broker truth, and the batching rule."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from typing import Dict, List, Optional

from engine_reconcile import (
    BrokerTruth,
    ReconcileAction,
    fetch_broker_truth,
    reconcile,
    symbols_of,
)
from engine_types import ExecutionState, Position, TriggerCandidate
from trading_engine_types import BrokerOrder, PositionQuote


def candidate(setup_id: str = "s1", *, symbol: str = "AAA") -> TriggerCandidate:
    return TriggerCandidate(
        setup_id=setup_id,
        continuation_rule_version="v1",
        session_date="2026-09-22",
        tradingsymbol=symbol,
        instrument_token=1,
        direction="UP",
        trigger_price=110.0,
        pullback_swing_high=113.0,
        pullback_swing_low=107.0,
        tick_size=0.05,
        buffer_ticks=1,
        trigger_exchange_ts="2026-09-22T10:00:00+00:00",
        created_at="2026-09-22T10:00:00+00:00",
        vwap_classification="ACCEPT",
    )


def position(
    *,
    state: ExecutionState = ExecutionState.PROTECTED,
    qty: int = 300,
    symbol: str = "AAA",
    stop_price: Optional[float] = 106.95,
    stop_order_id: Optional[str] = "so1",
    entry_order_id: Optional[str] = "eo1",
    trade_id: str = "s1",
) -> Position:
    return Position(
        trade_id=trade_id,
        candidate=candidate(trade_id, symbol=symbol),
        state=state,
        qty=qty,
        entry_price=110.0,
        stop_price=stop_price,
        entry_order_id=entry_order_id,
        stop_order_id=stop_order_id,
    )


def order(
    *,
    order_id: str = "so1",
    tag: str = "s1",
    order_type: str = "SL-M",
    status: str = "TRIGGER PENDING",
    qty: int = 300,
    trigger_price: Optional[float] = 106.95,
    avg: Optional[float] = None,
    filled: int = 0,
    symbol: str = "AAA",
) -> BrokerOrder:
    return BrokerOrder(
        order_id=order_id,
        tag=tag,
        tradingsymbol=symbol,
        transaction_type="SELL",
        order_type=order_type,
        quantity=qty,
        status=status,
        average_price=avg,
        trigger_price=trigger_price,
        filled_quantity=filled,
    )


def truth(
    *,
    net: Optional[Dict[str, int]] = None,
    orders: Optional[List[BrokerOrder]] = None,
    ok: bool = True,
) -> BrokerTruth:
    by_id: Dict[str, BrokerOrder] = {}
    by_tag: Dict[str, List[BrokerOrder]] = {}
    for o in orders or []:
        by_id[str(o.order_id)] = o
        by_tag.setdefault(str(o.tag or ""), []).append(o)
    return BrokerTruth(
        net_qty=dict(net or {}),
        orders_by_id=by_id,
        orders_by_tag=by_tag,
        fetched_at=datetime.now(timezone.utc),
        ok=ok,
        reason=None if ok else "positions_unreadable",
    )


class CountingBroker:
    """Counts the network reads reconciliation performs."""

    def __init__(self, *, symbols: Optional[List[str]] = None) -> None:
        self.symbols = symbols or []
        self.positions_calls = 0
        self.orders_calls = 0
        self.quote_calls = 0
        self.cache_clears = 0
        self._payload_valid = False

    def clear_quote_cache(self) -> None:
        self.cache_clears += 1
        self._payload_valid = False

    def _load(self) -> None:
        """Mirrors KiteBroker: one payload per tick, cached after first use."""
        if not self._payload_valid:
            self.positions_calls += 1
            self._payload_valid = True

    def list_net_positions(self) -> Dict[str, int]:
        self._load()
        return {s: 300 for s in self.symbols}

    def list_orders(self) -> List[BrokerOrder]:
        self.orders_calls += 1
        return [order(order_id=f"so-{s}", tag=s, symbol=s) for s in self.symbols]

    def position_quote(self, tradingsymbol: str) -> Optional[PositionQuote]:
        self._load()  # served from the cached payload, no new network read
        self.quote_calls += 1
        return PositionQuote(quantity=300, average_price=110.0, last_price=112.0, pnl=600.0)


class BatchingRuleTests(unittest.TestCase):
    """The mechanical guard on the rate-limit constraint."""

    def test_one_positions_read_regardless_of_how_many_positions_are_open(self) -> None:
        for count in (1, 2, 5, 25, 100):
            with self.subTest(open_positions=count):
                symbols = [f"SYM{i}" for i in range(count)]
                broker = CountingBroker(symbols=symbols)
                fetch_broker_truth(broker, symbols)
                self.assertEqual(broker.positions_calls, 1)
                self.assertEqual(broker.orders_calls, 1)

    def test_per_symbol_marks_are_served_from_the_cached_payload(self) -> None:
        symbols = [f"SYM{i}" for i in range(50)]
        broker = CountingBroker(symbols=symbols)
        result = fetch_broker_truth(broker, symbols)
        # 50 marks read, still one positions call.
        self.assertEqual(broker.quote_calls, 50)
        self.assertEqual(broker.positions_calls, 1)
        self.assertEqual(len(result.pnl), 50)

    def test_the_cache_is_cleared_first_so_each_tick_sees_fresh_data(self) -> None:
        broker = CountingBroker(symbols=["AAA"])
        fetch_broker_truth(broker, ["AAA"])
        fetch_broker_truth(broker, ["AAA"])
        self.assertEqual(broker.cache_clears, 2)
        self.assertEqual(broker.positions_calls, 2)

    def test_reconciling_many_positions_performs_no_reads_at_all(self) -> None:
        # reconcile() is pure: every decision comes from the one snapshot.
        snapshot = truth(net={"AAA": 300}, orders=[order()])
        broker = CountingBroker()
        for _ in range(100):
            reconcile(position(), snapshot)
        self.assertEqual(broker.positions_calls, 0)


class FetchFailureTests(unittest.TestCase):
    def test_an_unreadable_positions_book_is_not_an_empty_account(self) -> None:
        class Broker:
            def list_net_positions(self):
                raise RuntimeError("positions_down")

        result = fetch_broker_truth(Broker(), ["AAA"])
        self.assertFalse(result.ok)
        self.assertIn("positions_unreadable", str(result.reason))

    def test_an_unreadable_order_book_also_fails_closed(self) -> None:
        class Broker:
            def list_net_positions(self):
                return {"AAA": 300}

            def list_orders(self):
                raise RuntimeError("orders_down")

        result = fetch_broker_truth(Broker(), ["AAA"])
        self.assertFalse(result.ok)
        self.assertIn("orders_unreadable", str(result.reason))

    def test_nothing_is_decided_from_a_failed_read(self) -> None:
        # The critical safety property: a broker outage must never look like
        # every position having gone flat.
        decision = reconcile(position(), truth(net={"AAA": 300}, ok=False))
        self.assertTrue(decision.is_noop)

    def test_a_failed_read_does_not_finalize_a_held_position(self) -> None:
        decision = reconcile(position(qty=300), truth(net={}, ok=False))
        self.assertFalse(decision.has(ReconcileAction.FINALIZE_EXIT))

    def test_a_missing_mark_does_not_fail_the_whole_fetch(self) -> None:
        class Broker:
            def list_net_positions(self):
                return {"AAA": 300}

            def list_orders(self):
                return []

            def position_quote(self, tradingsymbol):
                raise RuntimeError("no mark")

        result = fetch_broker_truth(Broker(), ["AAA"])
        self.assertTrue(result.ok)
        self.assertEqual(result.pnl, {})

    def test_a_broker_without_a_cache_method_still_works(self) -> None:
        class Broker:
            def list_net_positions(self):
                return {"AAA": 300}

            def list_orders(self):
                return []

        self.assertTrue(fetch_broker_truth(Broker(), ["AAA"]).ok)


class QuantityDriftTests(unittest.TestCase):
    def test_a_differing_quantity_is_adopted(self) -> None:
        decision = reconcile(position(qty=300), truth(net={"AAA": 200}, orders=[order()]))
        self.assertTrue(decision.has(ReconcileAction.ADOPT_QTY))
        self.assertEqual(decision.broker_qty, 200)

    def test_a_matching_quantity_needs_no_action(self) -> None:
        decision = reconcile(position(qty=300), truth(net={"AAA": 300}, orders=[order()]))
        self.assertFalse(decision.has(ReconcileAction.ADOPT_QTY))

    def test_a_short_position_is_compared_on_absolute_size(self) -> None:
        # Kite reports a short as negative; we hold 300 either way.
        decision = reconcile(position(qty=300), truth(net={"AAA": -300}, orders=[order()]))
        self.assertFalse(decision.has(ReconcileAction.ADOPT_QTY))


class StopReconciliationTests(unittest.TestCase):
    def test_a_missing_stop_triggers_a_replace(self) -> None:
        decision = reconcile(position(), truth(net={"AAA": 300}, orders=[]))
        self.assertTrue(decision.has(ReconcileAction.REPLACE_STOP))

    def test_a_cancelled_stop_counts_as_missing(self) -> None:
        decision = reconcile(
            position(), truth(net={"AAA": 300}, orders=[order(status="CANCELLED")])
        )
        self.assertTrue(decision.has(ReconcileAction.REPLACE_STOP))

    def test_a_live_stop_at_the_expected_price_needs_no_action(self) -> None:
        decision = reconcile(
            position(stop_price=106.95),
            truth(net={"AAA": 300}, orders=[order(trigger_price=106.95)]),
        )
        self.assertTrue(decision.is_noop)

    def test_a_human_edited_stop_price_is_adopted_not_overwritten(self) -> None:
        decision = reconcile(
            position(stop_price=106.95),
            truth(net={"AAA": 300}, orders=[order(trigger_price=108.50)]),
        )
        self.assertTrue(decision.has(ReconcileAction.ADOPT_STOP_PRICE))
        self.assertEqual(decision.broker_stop_price, 108.50)
        self.assertFalse(decision.has(ReconcileAction.REPLACE_STOP))

    def test_a_sub_tick_difference_is_not_treated_as_an_edit(self) -> None:
        decision = reconcile(
            position(stop_price=106.95),
            truth(net={"AAA": 300}, orders=[order(trigger_price=106.96)]),
        )
        self.assertFalse(decision.has(ReconcileAction.ADOPT_STOP_PRICE))

    def test_a_stop_is_found_by_tag_when_its_id_was_lost(self) -> None:
        # Crash between placing the stop and persisting its id.
        decision = reconcile(
            position(stop_order_id=None),
            truth(net={"AAA": 300}, orders=[order(order_id="unknown", tag="s1")]),
        )
        self.assertFalse(decision.has(ReconcileAction.REPLACE_STOP))

    def test_an_unprotected_position_is_not_asked_for_a_stop_price_check(self) -> None:
        decision = reconcile(
            position(state=ExecutionState.ENTERED), truth(net={"AAA": 300}, orders=[])
        )
        self.assertFalse(decision.has(ReconcileAction.REPLACE_STOP))
        self.assertFalse(decision.has(ReconcileAction.ADOPT_STOP_PRICE))


class GoingFlatTests(unittest.TestCase):
    def test_a_flat_broker_quantity_finalizes_the_exit(self) -> None:
        decision = reconcile(position(), truth(net={"AAA": 0}, orders=[order()]))
        self.assertTrue(decision.has(ReconcileAction.FINALIZE_EXIT))

    def test_a_symbol_absent_from_a_good_read_is_flat(self) -> None:
        decision = reconcile(position(), truth(net={"OTHER": 100}))
        self.assertTrue(decision.has(ReconcileAction.FINALIZE_EXIT))

    def test_going_flat_overrides_every_other_action(self) -> None:
        # No point adopting a quantity or re-placing a stop on a closed trade.
        decision = reconcile(position(qty=300), truth(net={"AAA": 0}, orders=[]))
        self.assertEqual(decision.actions, [ReconcileAction.FINALIZE_EXIT])

    def test_each_holding_state_can_go_flat(self) -> None:
        for state in (
            ExecutionState.ENTERED,
            ExecutionState.PROTECTED,
            ExecutionState.TRAILING,
            ExecutionState.EXIT_SUBMITTED,
        ):
            with self.subTest(state=state.value):
                decision = reconcile(position(state=state), truth(net={"AAA": 0}))
                self.assertTrue(decision.has(ReconcileAction.FINALIZE_EXIT))

    def test_an_unfilled_entry_at_zero_is_not_an_exit(self) -> None:
        # Zero size on a submitted entry is just "not filled yet".
        decision = reconcile(
            position(state=ExecutionState.ENTRY_SUBMITTED, qty=300),
            truth(net={"AAA": 0}, orders=[order(order_id="eo1", order_type="MARKET", status="OPEN")]),
        )
        self.assertFalse(decision.has(ReconcileAction.FINALIZE_EXIT))

    def test_a_pending_entry_is_left_alone_entirely(self) -> None:
        decision = reconcile(
            position(state=ExecutionState.PENDING_ENTRY, entry_order_id=None), truth(net={})
        )
        self.assertTrue(decision.is_noop)

    def test_terminal_states_are_never_reconciled_again(self) -> None:
        for state in (
            ExecutionState.CLOSED,
            ExecutionState.REJECTED,
            ExecutionState.CANCELLED,
        ):
            with self.subTest(state=state.value):
                self.assertTrue(reconcile(position(state=state), truth(net={})).is_noop)


class EntryFillTests(unittest.TestCase):
    def test_a_filled_entry_is_noticed_with_its_real_price(self) -> None:
        decision = reconcile(
            position(state=ExecutionState.ENTRY_SUBMITTED),
            truth(
                net={"AAA": 300},
                orders=[
                    order(
                        order_id="eo1",
                        order_type="MARKET",
                        status="COMPLETE",
                        avg=110.35,
                        filled=300,
                    )
                ],
            ),
        )
        self.assertTrue(decision.has(ReconcileAction.APPLY_ENTRY_FILL))
        self.assertEqual(decision.fill_price, 110.35)
        self.assertEqual(decision.fill_qty, 300)

    def test_a_rejected_entry_is_noticed(self) -> None:
        decision = reconcile(
            position(state=ExecutionState.ENTRY_SUBMITTED),
            truth(orders=[order(order_id="eo1", order_type="MARKET", status="REJECTED")]),
        )
        self.assertTrue(decision.has(ReconcileAction.ENTRY_REJECTED))

    def test_a_cancelled_entry_is_noticed(self) -> None:
        decision = reconcile(
            position(state=ExecutionState.ENTRY_SUBMITTED),
            truth(orders=[order(order_id="eo1", order_type="MARKET", status="CANCELLED")]),
        )
        self.assertTrue(decision.has(ReconcileAction.ENTRY_CANCELLED))

    def test_a_still_working_entry_waits(self) -> None:
        decision = reconcile(
            position(state=ExecutionState.ENTRY_SUBMITTED),
            truth(orders=[order(order_id="eo1", order_type="MARKET", status="OPEN")]),
        )
        self.assertTrue(decision.is_noop)
        self.assertEqual(decision.reason, "entry_still_working")

    def test_an_invisible_entry_order_waits_rather_than_guessing(self) -> None:
        decision = reconcile(
            position(state=ExecutionState.ENTRY_SUBMITTED), truth(orders=[])
        )
        self.assertTrue(decision.is_noop)
        self.assertEqual(decision.reason, "entry_order_not_visible")

    def test_an_entry_is_found_by_tag_when_its_id_was_lost(self) -> None:
        decision = reconcile(
            position(state=ExecutionState.ENTRY_SUBMITTED, entry_order_id=None),
            truth(
                orders=[
                    order(
                        order_id="whatever",
                        tag="s1",
                        order_type="MARKET",
                        status="COMPLETE",
                        avg=110.0,
                        filled=300,
                    )
                ]
            ),
        )
        self.assertTrue(decision.has(ReconcileAction.APPLY_ENTRY_FILL))

    def test_a_stop_order_is_never_mistaken_for_the_entry(self) -> None:
        decision = reconcile(
            position(state=ExecutionState.ENTRY_SUBMITTED, entry_order_id=None),
            truth(orders=[order(order_id="so1", tag="s1", order_type="SL-M")]),
        )
        self.assertEqual(decision.reason, "entry_order_not_visible")


class BoundaryTests(unittest.TestCase):
    def test_unrelated_account_activity_is_ignored(self) -> None:
        # A manual trade in the same account, nothing to do with us.
        decision = reconcile(
            position(qty=300),
            truth(
                net={"AAA": 300, "UNRELATED": 5_000},
                orders=[order(), order(order_id="x1", tag="somebody-elses")],
            ),
        )
        self.assertTrue(decision.is_noop)

    def test_symbols_of_is_deduplicated_and_sorted(self) -> None:
        positions = [
            position(symbol="BBB", trade_id="b"),
            position(symbol="AAA", trade_id="a"),
            position(symbol="AAA", trade_id="a2"),
        ]
        self.assertEqual(symbols_of(positions), ["AAA", "BBB"])


class LivePnlSourceTests(unittest.TestCase):
    def test_the_brokers_own_pnl_field_is_copied_not_recomputed(self) -> None:
        class Broker:
            def list_net_positions(self):
                return {"AAA": 300}

            def list_orders(self):
                return []

            def position_quote(self, tradingsymbol):
                # A deliberately "wrong" pnl relative to the prices, to prove
                # we copy their number instead of deriving our own.
                return PositionQuote(
                    quantity=300, average_price=110.0, last_price=112.0, pnl=12_345.0
                )

        result = fetch_broker_truth(Broker(), ["AAA"])
        self.assertEqual(result.pnl["AAA"], 12_345.0)


if __name__ == "__main__":
    unittest.main()
