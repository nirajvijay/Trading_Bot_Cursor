"""Exit to CLOSED: attribution, real-fill P&L, and the shared flatten action."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import List, Optional

from engine_entry import broker_tag_for
from engine_exit import (
    CloseReason,
    ClosingOrder,
    attribute,
    cancel_stop,
    exit_side_orders,
    finalize_exit,
    flatten,
    flatten_tag_for,
    identify_closing_order,
    is_engine_tag,
    kite_realised_pnl,
    realised_pnl,
    select_closing_order,
    trade_exit_orders,
)
from engine_store import SqlitePositionStore
from engine_types import ExecutionState, Position, TriggerCandidate
from trading_engine_broker import FakeBroker
from trading_engine_types import BrokerOrder


def candidate(direction: str = "UP", symbol: str = "AAA") -> TriggerCandidate:
    return TriggerCandidate(
        setup_id="s1",
        continuation_rule_version="v1",
        session_date="2026-09-22",
        tradingsymbol=symbol,
        instrument_token=1,
        direction=direction,
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
    direction: str = "UP",
    qty: int = 300,
    entry_price: Optional[float] = 110.0,
    stop_price: Optional[float] = 106.95,
    stop_order_id: Optional[str] = "so1",
    exit_order_id: Optional[str] = None,
    extra: Optional[dict] = None,
) -> Position:
    return Position(
        trade_id="s1",
        candidate=candidate(direction),
        state=state,
        qty=qty,
        entry_price=entry_price,
        stop_price=stop_price,
        entry_order_id="eo1",
        stop_order_id=stop_order_id,
        exit_order_id=exit_order_id,
        extra=dict(extra or {}),
    )


def order(
    *,
    order_id: str = "so1",
    tag: str = "s1",
    order_type: str = "SL-M",
    side: str = "SELL",
    qty: int = 300,
    filled: int = 300,
    avg: Optional[float] = 106.50,
    status: str = "COMPLETE",
    symbol: str = "AAA",
    timestamp: Optional[str] = "2026-09-22T10:30:00+00:00",
) -> BrokerOrder:
    return BrokerOrder(
        order_id=order_id,
        tag=tag,
        tradingsymbol=symbol,
        transaction_type=side,
        order_type=order_type,
        quantity=qty,
        status=status,
        average_price=avg,
        filled_quantity=filled,
        order_timestamp=timestamp,
    )


class ExitTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SqlitePositionStore(Path(self._tmp.name) / "engine.db")
        self.broker = FakeBroker()
        self.broker.last_prices["AAA"] = 108.0

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def events(self, trade_id: str = "s1") -> List[str]:
        return [r["event_type"] for r in self.store.list_events(trade_id)]


class RealisedPnlTests(unittest.TestCase):
    def test_a_long_winner(self) -> None:
        self.assertAlmostEqual(
            realised_pnl(direction="UP", qty=100, entry_fill=110.0, exit_fill=115.0),
            500.0,
        )

    def test_a_long_loser(self) -> None:
        self.assertAlmostEqual(
            realised_pnl(direction="UP", qty=100, entry_fill=110.0, exit_fill=107.0),
            -300.0,
        )

    def test_a_short_winner(self) -> None:
        self.assertAlmostEqual(
            realised_pnl(direction="DOWN", qty=100, entry_fill=110.0, exit_fill=105.0),
            500.0,
        )

    def test_a_short_loser(self) -> None:
        self.assertAlmostEqual(
            realised_pnl(direction="DOWN", qty=100, entry_fill=110.0, exit_fill=113.0),
            -300.0,
        )


class AttributionTests(unittest.TestCase):
    def test_our_recorded_stop_is_a_stop_hit(self) -> None:
        pos = position(stop_order_id="so1")
        self.assertEqual(attribute(pos, order(order_id="so1")), CloseReason.STOP_HIT)

    def test_our_stop_found_by_tag_is_still_a_stop_hit(self) -> None:
        pos = position(stop_order_id=None)
        self.assertEqual(
            attribute(pos, order(order_id="lost", tag=broker_tag_for("s1"), order_type="SL-M")),
            CloseReason.STOP_HIT,
        )

    def test_each_active_reason_is_named_from_what_we_recorded(self) -> None:
        for reason in (
            CloseReason.EOD_SQUAREOFF,
            CloseReason.DAILY_LOSS_BREACH,
            CloseReason.MANUAL_CLOSE,
            CloseReason.ABNORMAL_SLIPPAGE_FLATTEN,
            CloseReason.KILL_ALL,
        ):
            with self.subTest(reason=reason.value):
                pos = position(
                    exit_order_id="xo1", extra={"exit_reason": reason.value}
                )
                self.assertEqual(
                    attribute(pos, order(order_id="xo1", order_type="MARKET")), reason
                )

    def test_an_unrecognized_order_is_manual_broker_intervention(self) -> None:
        # The corrected rule: never assume "not ours, therefore our stop".
        pos = position(stop_order_id="so1")
        self.assertEqual(
            attribute(pos, order(order_id="somebody-else", tag="", order_type="MARKET")),
            CloseReason.MANUAL_BROKER_INTERVENTION,
        )

    def test_our_exit_order_with_no_recorded_reason_is_unattributed(self) -> None:
        pos = position(exit_order_id="xo1")
        self.assertEqual(
            attribute(pos, order(order_id="xo1", order_type="MARKET")),
            CloseReason.UNATTRIBUTED,
        )

    def test_a_garbage_recorded_reason_does_not_crash(self) -> None:
        pos = position(exit_order_id="xo1", extra={"exit_reason": "nonsense"})
        self.assertEqual(
            attribute(pos, order(order_id="xo1", order_type="MARKET")),
            CloseReason.UNATTRIBUTED,
        )


class ClosingOrderSelectionTests(unittest.TestCase):
    def test_only_closing_side_orders_are_considered(self) -> None:
        pos = position(direction="UP")  # closes with SELL
        orders = [
            order(order_id="buy", side="BUY", order_type="MARKET"),
            order(order_id="sell", side="SELL"),
        ]
        found = exit_side_orders(pos, orders)
        self.assertEqual([o.order_id for o in found], ["sell"])

    def test_unfilled_orders_are_not_candidates(self) -> None:
        pos = position()
        self.assertEqual(
            exit_side_orders(pos, [order(filled=0, status="OPEN", avg=None)]), []
        )

    def test_other_symbols_are_not_candidates(self) -> None:
        pos = position()
        self.assertEqual(exit_side_orders(pos, [order(symbol="ZZZ")]), [])

    def test_nothing_completed_yields_no_closing_order(self) -> None:
        self.assertIsNone(identify_closing_order(position(), []))

    def test_a_single_candidate_is_returned_directly(self) -> None:
        found = identify_closing_order(position(), [order(order_id="so1")])
        assert found is not None
        self.assertEqual(found.reason, CloseReason.STOP_HIT)

    def test_the_earliest_fill_wins_a_double_exit(self) -> None:
        # The real race: we flattened, and the stop fired in the gap between
        # the cancel request and its confirmation. The first fill is what
        # actually closed the trade; the second opened opposite exposure.
        pos = position(exit_order_id="xo1", extra={"exit_reason": "manual_close"})
        orders = [
            order(order_id="xo1", order_type="MARKET", timestamp="2026-09-22T10:31:00+00:00"),
            order(order_id="so1", order_type="SL-M", timestamp="2026-09-22T10:30:00+00:00"),
        ]
        found = identify_closing_order(pos, orders)
        assert found is not None
        self.assertEqual(found.order.order_id, "so1")
        self.assertEqual(found.reason, CloseReason.STOP_HIT)

    def test_a_double_exit_is_recorded_for_a_human_to_see(self) -> None:
        pos = position(exit_order_id="xo1", extra={"exit_reason": "manual_close"})
        identify_closing_order(
            pos,
            [
                order(order_id="xo1", order_type="MARKET", timestamp="2026-09-22T10:31:00+00:00"),
                order(order_id="so1", timestamp="2026-09-22T10:30:00+00:00"),
            ],
        )
        contenders = pos.extra.get("closing_order_contenders")
        self.assertIsNotNone(contenders)
        self.assertEqual(len(contenders), 2)

    def test_an_order_without_a_timestamp_cannot_win_on_chronology(self) -> None:
        pos = position(exit_order_id="xo1", extra={"exit_reason": "manual_close"})
        found = select_closing_order(
            pos,
            [
                ClosingOrder(order(order_id="no-stamp", timestamp=None), CloseReason.STOP_HIT),
                ClosingOrder(
                    order(order_id="stamped", order_type="MARKET", timestamp="2026-09-22T11:00:00+00:00"),
                    CloseReason.MANUAL_CLOSE,
                ),
            ],
        )
        self.assertEqual(found.order.order_id, "stamped")

    def test_a_full_fill_beats_a_partial_one_when_timing_ties(self) -> None:
        pos = position(qty=300)
        found = select_closing_order(
            pos,
            [
                ClosingOrder(order(order_id="partial", filled=100), CloseReason.STOP_HIT),
                ClosingOrder(order(order_id="full", filled=300), CloseReason.STOP_HIT),
            ],
        )
        self.assertEqual(found.order.order_id, "full")

    def test_an_unrecognized_order_wins_a_full_tie_so_it_is_surfaced(self) -> None:
        pos = position(qty=300)
        found = select_closing_order(
            pos,
            [
                ClosingOrder(order(order_id="ours"), CloseReason.STOP_HIT),
                ClosingOrder(
                    order(order_id="theirs"), CloseReason.MANUAL_BROKER_INTERVENTION
                ),
            ],
        )
        self.assertEqual(found.reason, CloseReason.MANUAL_BROKER_INTERVENTION)


class FinalizationTests(ExitTestCase):
    def test_pnl_comes_from_the_real_exit_fill_not_the_stop_level(self) -> None:
        # The bug this exists to prevent: a stop set at 106.95 that actually
        # filled at 105.20 on a fast move.
        pos = position(entry_price=110.0, stop_price=106.95, qty=300)
        closing = ClosingOrder(order(order_id="so1", avg=105.20), CloseReason.STOP_HIT)
        finalize_exit(pos, closing=closing, store=self.store)
        self.assertAlmostEqual(pos.realised_pnl, 300 * (105.20 - 110.0), places=4)
        # Not the number the stop level would have implied.
        self.assertNotAlmostEqual(pos.realised_pnl, 300 * (106.95 - 110.0), places=4)

    def test_state_becomes_closed_and_the_reason_is_recorded(self) -> None:
        pos = position()
        reason = finalize_exit(
            pos,
            closing=ClosingOrder(order(order_id="so1"), CloseReason.STOP_HIT),
            store=self.store,
        )
        self.assertEqual(reason, CloseReason.STOP_HIT)
        self.assertEqual(pos.state, ExecutionState.CLOSED)
        self.assertEqual(pos.extra["close_reason"], "stop_hit")

    def test_the_closed_event_carries_the_real_numbers(self) -> None:
        pos = position()
        finalize_exit(
            pos,
            closing=ClosingOrder(order(order_id="so1", avg=105.20), CloseReason.STOP_HIT),
            store=self.store,
        )
        closed = [r for r in self.store.list_events("s1") if r["event_type"] == "closed"]
        self.assertEqual(len(closed), 1)
        payload = closed[0]["payload_json"]
        self.assertIn("105.2", payload)
        self.assertIn("stop_hit", payload)

    def test_state_and_event_are_written_together(self) -> None:
        pos = position()
        finalize_exit(
            pos,
            closing=ClosingOrder(order(order_id="so1"), CloseReason.STOP_HIT),
            store=self.store,
        )
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(stored.state, ExecutionState.CLOSED)
        self.assertIn("closed", self.events())

    def test_a_flat_position_with_no_explaining_order_is_unattributed(self) -> None:
        pos = position()
        reason = finalize_exit(pos, closing=None, store=self.store, fallback_qty=300)
        self.assertEqual(reason, CloseReason.UNATTRIBUTED)
        self.assertEqual(pos.state, ExecutionState.CLOSED)
        # No P&L invented from a price we never saw.
        self.assertIsNone(pos.realised_pnl)

    def test_a_manually_edited_stop_firing_books_the_edited_price(self) -> None:
        # Reconciliation already adopted the human's price, so by now this is
        # just a normal stop_hit with the right number in hand.
        pos = position(stop_price=108.50)
        finalize_exit(
            pos,
            closing=ClosingOrder(order(order_id="so1", avg=108.45), CloseReason.STOP_HIT),
            store=self.store,
        )
        self.assertAlmostEqual(pos.realised_pnl, 300 * (108.45 - 110.0), places=4)

    def test_a_short_position_books_the_right_sign(self) -> None:
        pos = position(direction="DOWN", entry_price=110.0, stop_price=113.05)
        finalize_exit(
            pos,
            closing=ClosingOrder(
                order(order_id="so1", side="BUY", avg=113.20), CloseReason.STOP_HIT
            ),
            store=self.store,
        )
        self.assertAlmostEqual(pos.realised_pnl, 300 * (110.0 - 113.20), places=4)

    def test_every_reason_finalizes_identically(self) -> None:
        for reason in CloseReason:
            with self.subTest(reason=reason.value):
                store = SqlitePositionStore(Path(self._tmp.name) / f"{reason.value}.db")
                try:
                    pos = position()
                    finalize_exit(
                        pos,
                        closing=ClosingOrder(order(order_id="x", avg=108.0), reason),
                        store=store,
                    )
                    self.assertEqual(pos.state, ExecutionState.CLOSED)
                    self.assertIsNotNone(pos.realised_pnl)
                finally:
                    store.close()


class CancelStopTests(ExitTestCase):
    def test_no_stop_means_nothing_to_cancel(self) -> None:
        outcome = cancel_stop(position(stop_order_id=None), broker=self.broker, store=self.store)
        self.assertTrue(outcome.submitted)
        self.assertEqual(outcome.reason, "no_stop_to_cancel")

    def test_a_confirmed_cancellation_clears_the_way(self) -> None:
        pos = position(state=ExecutionState.ENTERED, stop_price=106.95)
        placed = self.broker.place_slm(
            tradingsymbol="AAA",
            transaction_type="SELL",
            quantity=300,
            trigger_price=106.95,
            tag="s1",
        )
        pos.stop_order_id = placed.order_id
        outcome = cancel_stop(pos, broker=self.broker, store=self.store)
        self.assertTrue(outcome.submitted)
        self.assertIn("stop_cancelled", self.events())

    def test_no_response_is_ambiguous_not_a_success(self) -> None:
        class Broker:
            def cancel_order(self, order_id):
                return None

        outcome = cancel_stop(position(), broker=Broker(), store=self.store)
        self.assertFalse(outcome.submitted)
        self.assertIn("ambiguous", str(outcome.reason))

    def test_a_raising_cancel_is_ambiguous(self) -> None:
        class Broker:
            def cancel_order(self, order_id):
                raise TimeoutError("timed out")

        outcome = cancel_stop(position(), broker=Broker(), store=self.store)
        self.assertFalse(outcome.submitted)
        self.assertIn("ambiguous", str(outcome.reason))
        self.assertIn("stop_cancel_ambiguous", self.events())

    def test_an_unexpected_status_is_not_confirmation(self) -> None:
        class Broker:
            def cancel_order(self, order_id):
                return order(order_id="so1", status="OPEN")

        outcome = cancel_stop(position(), broker=Broker(), store=self.store)
        self.assertFalse(outcome.submitted)


class FlattenTests(ExitTestCase):
    def _protected(self) -> Position:
        pos = position(state=ExecutionState.ENTERED, stop_price=106.95)
        placed = self.broker.place_slm(
            tradingsymbol="AAA",
            transaction_type="SELL",
            quantity=300,
            trigger_price=106.95,
            tag="s1",
        )
        pos.stop_order_id = placed.order_id
        pos.state = ExecutionState.PROTECTED
        return pos

    def test_the_cancel_happens_before_the_flatten(self) -> None:
        pos = self._protected()
        calls: List[str] = []
        real_cancel = self.broker.cancel_order
        real_flatten = self.broker.flatten_mis

        def cancel(order_id):
            calls.append("cancel")
            return real_cancel(order_id)

        def flat(**kwargs):
            calls.append("flatten")
            return real_flatten(**kwargs)

        self.broker.cancel_order = cancel  # type: ignore[method-assign]
        self.broker.flatten_mis = flat  # type: ignore[method-assign]
        flatten(pos, reason=CloseReason.MANUAL_CLOSE, broker=self.broker, store=self.store)
        self.assertEqual(calls, ["cancel", "flatten"])

    def test_an_unconfirmed_cancel_blocks_the_flatten_entirely(self) -> None:
        # Both executing would sell twice and leave the account net short.
        pos = self._protected()
        sent: List[str] = []

        class Broker:
            def cancel_order(self, order_id):
                raise TimeoutError("timed out")

            def flatten_mis(self, **kwargs):
                sent.append("flatten")
                raise AssertionError("must not be reached")

        outcome = flatten(
            pos, reason=CloseReason.MANUAL_CLOSE, broker=Broker(), store=self.store
        )
        self.assertFalse(outcome.submitted)
        self.assertEqual(sent, [])
        self.assertEqual(pos.state, ExecutionState.PROTECTED)

    def test_a_position_with_no_live_stop_goes_straight_to_the_flatten(self) -> None:
        pos = position(state=ExecutionState.ENTERED, stop_order_id=None)
        outcome = flatten(
            pos,
            reason=CloseReason.ABNORMAL_SLIPPAGE_FLATTEN,
            broker=self.broker,
            store=self.store,
        )
        self.assertTrue(outcome.submitted)
        self.assertEqual(pos.state, ExecutionState.EXIT_SUBMITTED)

    def test_the_exit_order_uses_a_tag_distinct_from_the_entry(self) -> None:
        pos = position(state=ExecutionState.ENTERED, stop_order_id=None)
        flatten(pos, reason=CloseReason.KILL_ALL, broker=self.broker, store=self.store)
        expected_tag = f"{broker_tag_for('s1')}-x"
        self.assertEqual(flatten_tag_for(pos), expected_tag)
        exits = [o for o in self.broker.orders.values() if o.tag == expected_tag]
        self.assertEqual(len(exits), 1)

    def test_the_reason_is_stashed_for_later_attribution(self) -> None:
        pos = position(state=ExecutionState.ENTERED, stop_order_id=None)
        flatten(
            pos, reason=CloseReason.DAILY_LOSS_BREACH, broker=self.broker, store=self.store
        )
        self.assertEqual(pos.extra["exit_reason"], "daily_loss_breach")

    def test_all_four_triggers_use_the_identical_action(self) -> None:
        for reason in (
            CloseReason.EOD_SQUAREOFF,
            CloseReason.DAILY_LOSS_BREACH,
            CloseReason.MANUAL_CLOSE,
            CloseReason.ABNORMAL_SLIPPAGE_FLATTEN,
        ):
            with self.subTest(reason=reason.value):
                broker = FakeBroker()
                broker.last_prices["AAA"] = 108.0
                store = SqlitePositionStore(Path(self._tmp.name) / f"f-{reason.value}.db")
                try:
                    pos = position(state=ExecutionState.ENTERED, stop_order_id=None)
                    outcome = flatten(pos, reason=reason, broker=broker, store=store)
                    self.assertTrue(outcome.submitted)
                    self.assertEqual(pos.state, ExecutionState.EXIT_SUBMITTED)
                finally:
                    store.close()

    def test_a_terminal_position_is_not_flattened(self) -> None:
        for state in (
            ExecutionState.CLOSED,
            ExecutionState.REJECTED,
            ExecutionState.CANCELLED,
        ):
            with self.subTest(state=state.value):
                outcome = flatten(
                    position(state=state),
                    reason=CloseReason.KILL_ALL,
                    broker=self.broker,
                    store=self.store,
                )
                self.assertFalse(outcome.submitted)
                self.assertEqual(outcome.reason, "already_terminal")

    def test_a_zero_quantity_position_is_not_flattened(self) -> None:
        outcome = flatten(
            position(qty=0),
            reason=CloseReason.KILL_ALL,
            broker=self.broker,
            store=self.store,
        )
        self.assertFalse(outcome.submitted)
        self.assertEqual(outcome.reason, "no_quantity")

    def test_a_failing_flatten_leaves_the_state_alone_for_a_retry(self) -> None:
        pos = position(state=ExecutionState.ENTERED, stop_order_id=None)

        class Broker:
            def flatten_mis(self, **kwargs):
                raise RuntimeError("exchange busy")

        outcome = flatten(
            pos, reason=CloseReason.KILL_ALL, broker=Broker(), store=self.store
        )
        self.assertFalse(outcome.submitted)
        self.assertEqual(pos.state, ExecutionState.ENTERED)
        self.assertIn("flatten_failed", self.events())

    def test_flattening_is_idempotent_across_repeated_ticks(self) -> None:
        pos = position(state=ExecutionState.ENTERED, stop_order_id=None)
        flatten(pos, reason=CloseReason.KILL_ALL, broker=self.broker, store=self.store)
        second = flatten(
            pos, reason=CloseReason.KILL_ALL, broker=self.broker, store=self.store
        )
        self.assertTrue(second.submitted)
        expected_tag = f"{broker_tag_for('s1')}-x"
        exits = [o for o in self.broker.orders.values() if o.tag == expected_tag]
        self.assertEqual(len(exits), 1)


def entry(
    *,
    qty: int = 300,
    avg: Optional[float] = 110.20,
    side: str = "BUY",
    timestamp: str = "2026-09-22T10:00:05+00:00",
) -> BrokerOrder:
    return order(
        order_id="eo1",
        tag=broker_tag_for("s1"),
        order_type="MARKET",
        side=side,
        qty=qty,
        filled=qty,
        avg=avg,
        timestamp=timestamp,
    )


class TradeExitOrderScopingTests(unittest.TestCase):
    """B2.2: exit orders belong to a trade, not merely to its symbol."""

    def test_own_stop_and_own_flatten_belong(self) -> None:
        pos = position(exit_order_id="xo1")
        orders = [
            order(order_id="so1", tag=broker_tag_for("s1"), filled=100, avg=107.0),
            order(order_id="xo1", tag=flatten_tag_for(pos), order_type="MARKET", filled=200),
        ]
        ids = [o.order_id for o in trade_exit_orders(pos, orders, entry_order=entry())]
        self.assertEqual(sorted(ids), ["so1", "xo1"])

    def test_another_engine_trades_exit_never_belongs(self) -> None:
        # Trade #1's stop, same symbol, same side, earlier: the old bug's input.
        other = order(order_id="old-stop", tag=broker_tag_for("s0"), timestamp="2026-09-22T09:30:00+00:00")
        pos = position(stop_order_id="so1")
        self.assertEqual(trade_exit_orders(pos, [other], entry_order=entry()), [])

    def test_a_manual_close_after_the_entry_belongs(self) -> None:
        manual = order(order_id="m1", tag="", order_type="MARKET", timestamp="2026-09-22T10:40:00+00:00")
        self.assertEqual(
            [o.order_id for o in trade_exit_orders(position(), [manual], entry_order=entry())],
            ["m1"],
        )

    def test_a_manual_order_before_the_entry_does_not_belong(self) -> None:
        manual = order(order_id="m0", tag="", order_type="MARKET", timestamp="2026-09-22T09:59:00+00:00")
        self.assertEqual(trade_exit_orders(position(), [manual], entry_order=entry()), [])

    def test_a_manual_order_claimed_by_an_earlier_trade_does_not_belong(self) -> None:
        manual = order(order_id="m1", tag="", order_type="MARKET", timestamp="2026-09-22T10:40:00+00:00")
        self.assertEqual(
            trade_exit_orders(position(), [manual], claimed={"m1"}, entry_order=entry()), []
        )

    def test_engine_tag_recognition(self) -> None:
        tag = broker_tag_for("anything")
        self.assertTrue(is_engine_tag(tag))
        self.assertTrue(is_engine_tag(tag + "-x"))
        self.assertFalse(is_engine_tag(""))
        self.assertFalse(is_engine_tag("my-manual-tag"))


class KiteRealisedPnlTests(unittest.TestCase):
    """B2.1: realised P&L from Kite's entry and every exit piece."""

    def test_a_single_exit(self) -> None:
        result = kite_realised_pnl(
            position(entry_price=999.0),  # stored price is ignored
            entry_order=entry(avg=110.20),
            exit_orders=[order(avg=106.50, filled=300)],
        )
        self.assertAlmostEqual(result.pnl, (106.50 - 110.20) * 300, places=2)
        self.assertEqual((result.entry_qty, result.exit_qty), (300, 300))

    def test_exits_in_pieces_each_book_their_own_price(self) -> None:
        exits = [
            order(order_id="so1", filled=100, avg=107.00, timestamp="2026-09-22T10:20:00+00:00"),
            order(order_id="xo1", tag="", order_type="MARKET", filled=200, avg=112.00, timestamp="2026-09-22T10:30:00+00:00"),
        ]
        result = kite_realised_pnl(position(), entry_order=entry(avg=110.00), exit_orders=exits)
        self.assertAlmostEqual(result.pnl, 100 * -3.0 + 200 * 2.0, places=2)
        self.assertEqual(result.exit_order_ids, ["so1", "xo1"])

    def test_a_short_trade(self) -> None:
        result = kite_realised_pnl(
            position(direction="DOWN"),
            entry_order=entry(avg=110.00, side="SELL"),
            exit_orders=[order(side="BUY", avg=108.00, filled=300)],
        )
        self.assertAlmostEqual(result.pnl, 600.0, places=2)

    def test_an_over_exit_is_capped_and_reported(self) -> None:
        exits = [
            order(order_id="so1", filled=300, avg=107.00, timestamp="2026-09-22T10:20:00+00:00"),
            order(order_id="xo1", tag="", order_type="MARKET", filled=300, avg=106.00, timestamp="2026-09-22T10:21:00+00:00"),
        ]
        result = kite_realised_pnl(position(), entry_order=entry(avg=110.00), exit_orders=exits)
        self.assertAlmostEqual(result.pnl, 300 * -3.0, places=2)
        self.assertEqual(result.over_exit_qty, 300)
        self.assertEqual(result.exit_order_ids, ["so1"])

    def test_no_exit_orders_is_unattributed_not_zero(self) -> None:
        result = kite_realised_pnl(position(), entry_order=entry(), exit_orders=[])
        self.assertIsNone(result.pnl)
        self.assertEqual(result.reason, "no_exit_orders")

    def test_an_invisible_entry_order_gives_no_number(self) -> None:
        result = kite_realised_pnl(position(), entry_order=None, exit_orders=[order()])
        self.assertIsNone(result.pnl)

    def test_finalize_books_the_kite_figure_and_remembers_the_orders(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        store = SqlitePositionStore(Path(tmp.name) / "engine.db")
        try:
            pos = position(entry_price=999.0)
            result = kite_realised_pnl(
                pos, entry_order=entry(avg=110.00), exit_orders=[order(avg=107.00)]
            )
            finalize_exit(
                pos,
                closing=ClosingOrder(order(avg=107.00), CloseReason.STOP_HIT),
                store=store,
                realised=result,
            )
            self.assertAlmostEqual(pos.realised_pnl, -900.0, places=2)
            self.assertEqual(pos.extra["exit_order_ids"], ["so1"])
        finally:
            store.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
