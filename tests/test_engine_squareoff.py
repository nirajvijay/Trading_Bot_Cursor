"""Close-everything: idempotence, no exceptions for winners, confirmation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Optional

from engine_exit import CloseReason
from engine_risk import RiskPolicy
from engine_squareoff import check_daily_loss, realised_loss_today, squareoff_all
from engine_store import SqlitePositionStore
from engine_types import ExecutionState, Position, RiskLimits, TriggerCandidate
from trading_engine_broker import FakeBroker
from trading_engine_types import BrokerOrder

LIMITS = RiskLimits(
    per_trade_cap_rupees=900.0,
    per_trade_cap_vwap_limited_rupees=450.0,
    daily_loss_cap_rupees=3000.0,
)


def candidate(setup_id: str, symbol: str = "AAA") -> TriggerCandidate:
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
    trade_id: str,
    *,
    state: ExecutionState = ExecutionState.PROTECTED,
    qty: int = 300,
    symbol: str = "AAA",
    realised_pnl: Optional[float] = None,
    entry_order_id: Optional[str] = "eo1",
    stop_order_id: Optional[str] = None,
) -> Position:
    return Position(
        trade_id=trade_id,
        candidate=candidate(trade_id, symbol),
        state=state,
        qty=qty,
        entry_price=110.0,
        stop_price=106.95,
        entry_order_id=entry_order_id,
        stop_order_id=stop_order_id,
        realised_pnl=realised_pnl,
    )


class SquareoffTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SqlitePositionStore(Path(self._tmp.name) / "engine.db")
        self.broker = FakeBroker()
        for symbol in ("AAA", "BBB", "CCC"):
            self.broker.last_prices[symbol] = 108.0

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()


class DailyLossTriggerTests(unittest.TestCase):
    def test_breach_is_decided_on_realized_losses_only(self) -> None:
        policy = RiskPolicy(LIMITS)
        closed = [
            position("a", state=ExecutionState.CLOSED, realised_pnl=-1_500.0),
            position("b", state=ExecutionState.CLOSED, realised_pnl=-1_500.0),
        ]
        self.assertTrue(check_daily_loss(policy, closed).breached)

    def test_open_positions_do_not_contribute_a_projection(self) -> None:
        # Deliberately not forward-looking: an open position that *would* lose
        # if it hit its stop is not a breach.
        policy = RiskPolicy(LIMITS)
        closed = [position("a", state=ExecutionState.CLOSED, realised_pnl=-1_000.0)]
        self.assertFalse(check_daily_loss(policy, closed).breached)

    def test_profits_do_not_buy_back_room_under_the_cap(self) -> None:
        closed = [
            position("a", state=ExecutionState.CLOSED, realised_pnl=-3_000.0),
            position("b", state=ExecutionState.CLOSED, realised_pnl=5_000.0),
        ]
        self.assertEqual(realised_loss_today(closed), 3_000.0)
        self.assertTrue(check_daily_loss(RiskPolicy(LIMITS), closed).breached)

    def test_exactly_at_the_cap_is_a_breach(self) -> None:
        closed = [position("a", state=ExecutionState.CLOSED, realised_pnl=-3_000.0)]
        self.assertTrue(check_daily_loss(RiskPolicy(LIMITS), closed).breached)

    def test_just_under_the_cap_is_not(self) -> None:
        closed = [position("a", state=ExecutionState.CLOSED, realised_pnl=-2_999.0)]
        self.assertFalse(check_daily_loss(RiskPolicy(LIMITS), closed).breached)


class CloseEverythingTests(SquareoffTestCase):
    def test_every_holding_position_gets_an_exit(self) -> None:
        positions = [
            position("a", symbol="AAA"),
            position("b", symbol="BBB"),
            position("c", symbol="CCC"),
        ]
        progress = squareoff_all(
            positions,
            reason=CloseReason.DAILY_LOSS_BREACH,
            broker=self.broker,
            store=self.store,
        )
        self.assertEqual(progress.exits_submitted, 3)
        self.assertTrue(all(p.state == ExecutionState.EXIT_SUBMITTED for p in positions))

    def test_a_currently_green_position_is_closed_too(self) -> None:
        # The rejected "let the winner ride" exception must not exist.
        self.broker.last_prices["AAA"] = 130.0  # deep in profit
        pos = position("winner")
        progress = squareoff_all(
            [pos], reason=CloseReason.DAILY_LOSS_BREACH, broker=self.broker, store=self.store
        )
        self.assertEqual(progress.exits_submitted, 1)
        self.assertEqual(pos.state, ExecutionState.EXIT_SUBMITTED)

    def test_the_reason_tag_is_carried_onto_every_position(self) -> None:
        positions = [position("a", symbol="AAA"), position("b", symbol="BBB")]
        squareoff_all(
            positions, reason=CloseReason.KILL_ALL, broker=self.broker, store=self.store
        )
        for pos in positions:
            self.assertEqual(pos.extra["exit_reason"], "kill_all")

    def test_all_three_triggers_behave_identically(self) -> None:
        for reason in (
            CloseReason.DAILY_LOSS_BREACH,
            CloseReason.EOD_SQUAREOFF,
            CloseReason.KILL_ALL,
        ):
            with self.subTest(reason=reason.value):
                broker = FakeBroker()
                broker.last_prices["AAA"] = 108.0
                store = SqlitePositionStore(Path(self._tmp.name) / f"{reason.value}.db")
                try:
                    pos = position("a")
                    progress = squareoff_all(
                        [pos], reason=reason, broker=broker, store=store
                    )
                    self.assertEqual(progress.exits_submitted, 1)
                    self.assertEqual(pos.state, ExecutionState.EXIT_SUBMITTED)
                finally:
                    store.close()


class UnfilledEntryTests(SquareoffTestCase):
    def test_a_pending_entry_is_cancelled_with_nothing_sent(self) -> None:
        pos = position("a", state=ExecutionState.PENDING_ENTRY, entry_order_id=None)
        progress = squareoff_all(
            [pos], reason=CloseReason.KILL_ALL, broker=self.broker, store=self.store
        )
        self.assertEqual(progress.cancelled, 1)
        self.assertEqual(pos.state, ExecutionState.CANCELLED)
        self.assertEqual(self.broker.orders, {})

    def test_a_submitted_entry_is_cancelled_at_the_broker(self) -> None:
        placed = self.broker.place_market_mis(
            tradingsymbol="AAA", transaction_type="BUY", quantity=300, tag="a"
        )
        self.broker.auto_fill_entry = False
        pos = position(
            "a", state=ExecutionState.ENTRY_SUBMITTED, entry_order_id=placed.order_id
        )
        progress = squareoff_all(
            [pos], reason=CloseReason.KILL_ALL, broker=self.broker, store=self.store
        )
        self.assertIn(progress.cancelled + progress.awaiting_confirmation, (1,))

    def test_an_entry_that_filled_while_cancelling_is_not_marked_cancelled(self) -> None:
        # It really is a live position now; reconciliation must handle it.
        class Broker:
            def cancel_order(self, order_id):
                return BrokerOrder(
                    order_id=order_id,
                    tag="a",
                    tradingsymbol="AAA",
                    transaction_type="BUY",
                    order_type="MARKET",
                    quantity=300,
                    status="COMPLETE",
                    filled_quantity=300,
                    average_price=110.0,
                )

        pos = position("a", state=ExecutionState.ENTRY_SUBMITTED)
        progress = squareoff_all(
            [pos], reason=CloseReason.KILL_ALL, broker=Broker(), store=self.store
        )
        self.assertEqual(pos.state, ExecutionState.ENTRY_SUBMITTED)
        self.assertFalse(progress.complete)
        events = [r["event_type"] for r in self.store.list_events("a")]
        self.assertIn("entry_cancel_raced_fill", events)

    def test_an_ambiguous_cancel_is_not_treated_as_done(self) -> None:
        class Broker:
            def cancel_order(self, order_id):
                raise TimeoutError("timed out")

        pos = position("a", state=ExecutionState.ENTRY_SUBMITTED)
        progress = squareoff_all(
            [pos], reason=CloseReason.KILL_ALL, broker=Broker(), store=self.store
        )
        self.assertEqual(pos.state, ExecutionState.ENTRY_SUBMITTED)
        self.assertFalse(progress.complete)


class IdempotenceTests(SquareoffTestCase):
    def test_repeating_it_never_sends_a_second_exit(self) -> None:
        pos = position("a")
        squareoff_all([pos], reason=CloseReason.KILL_ALL, broker=self.broker, store=self.store)
        for _ in range(5):
            squareoff_all(
                [pos], reason=CloseReason.KILL_ALL, broker=self.broker, store=self.store
            )
        exits = [o for o in self.broker.orders.values() if o.tag == "a-x"]
        self.assertEqual(len(exits), 1)

    def test_an_already_closed_position_is_not_considered_at_all(self) -> None:
        progress = squareoff_all(
            [position("a", state=ExecutionState.CLOSED)],
            reason=CloseReason.KILL_ALL,
            broker=self.broker,
            store=self.store,
        )
        self.assertEqual(progress.considered, 0)
        self.assertTrue(progress.complete)

    def test_terminal_states_are_all_skipped(self) -> None:
        positions = [
            position("a", state=ExecutionState.CLOSED),
            position("b", state=ExecutionState.REJECTED),
            position("c", state=ExecutionState.CANCELLED),
        ]
        progress = squareoff_all(
            positions, reason=CloseReason.KILL_ALL, broker=self.broker, store=self.store
        )
        self.assertEqual(progress.considered, 0)

    def test_progress_does_less_work_as_positions_finish(self) -> None:
        a = position("a", symbol="AAA")
        b = position("b", symbol="BBB")
        first = squareoff_all(
            [a, b], reason=CloseReason.KILL_ALL, broker=self.broker, store=self.store
        )
        self.assertEqual(first.considered, 2)
        a.state = ExecutionState.CLOSED
        second = squareoff_all(
            [a, b], reason=CloseReason.KILL_ALL, broker=self.broker, store=self.store
        )
        self.assertEqual(second.considered, 1)

    def test_nothing_open_is_complete(self) -> None:
        progress = squareoff_all(
            [], reason=CloseReason.EOD_SQUAREOFF, broker=self.broker, store=self.store
        )
        self.assertTrue(progress.complete)
        self.assertEqual(progress.considered, 0)


class ConfirmationTests(SquareoffTestCase):
    def test_submitting_an_exit_is_not_completion(self) -> None:
        # Auto-stop must wait for confirmed CLOSED, not for the order going out.
        pos = position("a")
        progress = squareoff_all(
            [pos], reason=CloseReason.KILL_ALL, broker=self.broker, store=self.store
        )
        self.assertEqual(progress.exits_submitted, 1)
        self.assertFalse(progress.complete)

    def test_an_in_flight_exit_still_counts_as_awaiting(self) -> None:
        progress = squareoff_all(
            [position("a", state=ExecutionState.EXIT_SUBMITTED)],
            reason=CloseReason.KILL_ALL,
            broker=self.broker,
            store=self.store,
        )
        self.assertEqual(progress.awaiting_confirmation, 1)
        self.assertFalse(progress.complete)

    def test_a_blocked_position_is_named_so_it_can_be_surfaced(self) -> None:
        class Broker:
            def cancel_order(self, order_id):
                return None

        pos = position("a", stop_order_id="so1")
        progress = squareoff_all(
            [pos], reason=CloseReason.KILL_ALL, broker=Broker(), store=self.store
        )
        self.assertTrue(any("a:" in b for b in progress.blocked))


if __name__ == "__main__":
    unittest.main()
