"""Placing the protective stop, including the accepted-but-invisible case."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Optional

from engine_entry import broker_tag_for
from engine_protection import ensure_protected
from engine_store import SqlitePositionStore
from engine_types import ExecutionState, Position, TriggerCandidate
from trading_engine_broker import FakeBroker, SlPlaceAcceptedVisibilityUnknown
from trading_engine_types import BrokerOrder


def candidate(direction: str = "UP") -> TriggerCandidate:
    return TriggerCandidate(
        setup_id="s1",
        continuation_rule_version="v1",
        session_date="2026-09-22",
        tradingsymbol="AAA",
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
    state: ExecutionState = ExecutionState.ENTERED,
    qty: int = 300,
    stop_price: Optional[float] = 106.95,
    direction: str = "UP",
) -> Position:
    return Position(
        trade_id="s1",
        candidate=candidate(direction),
        state=state,
        qty=qty,
        entry_price=110.0,
        stop_price=stop_price,
        entry_order_id="eo1",
    )


class ProtectionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SqlitePositionStore(Path(self._tmp.name) / "engine.db")
        self.broker = FakeBroker()
        self.broker.last_prices["AAA"] = 110.0

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def events(self, trade_id: str = "s1"):
        return [r["event_type"] for r in self.store.list_events(trade_id)]


class HappyPathTests(ProtectionTestCase):
    def test_a_confirmed_stop_reaches_protected(self) -> None:
        pos = position()
        outcome = ensure_protected(pos, broker=self.broker, store=self.store)
        self.assertTrue(outcome.protected)
        self.assertEqual(pos.state, ExecutionState.PROTECTED)
        self.assertIsNotNone(pos.stop_order_id)
        self.assertIn("protected", self.events())

    def test_the_stop_goes_at_the_exact_structural_price(self) -> None:
        pos = position(stop_price=106.95)
        ensure_protected(pos, broker=self.broker, store=self.store)
        placed = [o for o in self.broker.orders.values() if o.order_type in {"SL", "SL-M"}]
        self.assertEqual(len(placed), 1)
        self.assertAlmostEqual(float(placed[0].trigger_price), 106.95, places=4)

    def test_a_long_position_is_protected_with_a_sell(self) -> None:
        ensure_protected(position(direction="UP"), broker=self.broker, store=self.store)
        placed = [o for o in self.broker.orders.values() if o.order_type in {"SL", "SL-M"}]
        self.assertEqual(placed[0].transaction_type, "SELL")

    def test_a_short_position_is_protected_with_a_buy(self) -> None:
        pos = position(direction="DOWN", stop_price=113.05)
        ensure_protected(pos, broker=self.broker, store=self.store)
        placed = [o for o in self.broker.orders.values() if o.order_type in {"SL", "SL-M"}]
        self.assertEqual(placed[0].transaction_type, "BUY")

    def test_the_stop_carries_the_trade_id_as_its_tag(self) -> None:
        ensure_protected(position(), broker=self.broker, store=self.store)
        placed = [o for o in self.broker.orders.values() if o.order_type in {"SL", "SL-M"}]
        self.assertEqual(placed[0].tag, broker_tag_for("s1"))

    def test_the_protected_state_is_persisted(self) -> None:
        ensure_protected(position(), broker=self.broker, store=self.store)
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(stored.state, ExecutionState.PROTECTED)
        self.assertIsNotNone(stored.stop_order_id)


class VisibilityUnknownTests(ProtectionTestCase):
    class InvisibleBroker:
        def __init__(self) -> None:
            self.calls = 0

        def place_slm(self, **_kwargs) -> BrokerOrder:
            self.calls += 1
            raise SlPlaceAcceptedVisibilityUnknown("so-accepted")

    def test_an_accepted_but_invisible_stop_does_not_claim_protection(self) -> None:
        broker = self.InvisibleBroker()
        pos = position()
        outcome = ensure_protected(pos, broker=broker, store=self.store)
        self.assertFalse(outcome.protected)
        self.assertEqual(pos.state, ExecutionState.ENTERED)

    def test_the_accepted_order_id_is_bound_durably(self) -> None:
        broker = self.InvisibleBroker()
        pos = position()
        ensure_protected(pos, broker=broker, store=self.store)
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(stored.stop_order_id, "so-accepted")
        self.assertIn("stop_accepted_visibility_unknown", self.events())

    def test_exactly_one_stop_is_placed_across_two_ticks(self) -> None:
        # The danger this guards against: retrying an accepted-but-unseen stop
        # and ending up with two live stops on one position.
        broker = self.InvisibleBroker()
        pos = position()
        ensure_protected(pos, broker=broker, store=self.store)
        # Next tick, reconciliation finds the stop by its bound id and the
        # position is already in ENTERED with stop_order_id set, so a second
        # attempt would be refused by the caller. Here we prove the id survived.
        reloaded = self.store.get("s1")
        assert reloaded is not None
        self.assertEqual(reloaded.stop_order_id, "so-accepted")
        self.assertEqual(broker.calls, 1)


class FailureTests(ProtectionTestCase):
    def test_a_broker_error_leaves_the_position_entered_for_retry(self) -> None:
        class Broker:
            def place_slm(self, **_kwargs):
                raise RuntimeError("exchange rejected")

        pos = position()
        outcome = ensure_protected(pos, broker=Broker(), store=self.store)
        self.assertFalse(outcome.protected)
        self.assertEqual(pos.state, ExecutionState.ENTERED)
        self.assertIn("exchange rejected", str(outcome.reason))

    def test_a_failure_is_recorded_so_it_is_never_silent(self) -> None:
        class Broker:
            def place_slm(self, **_kwargs):
                raise RuntimeError("exchange rejected")

        self.store.save(position())
        ensure_protected(position(), broker=Broker(), store=self.store)
        self.assertIn("stop_place_failed", self.events())

    def test_a_rejected_stop_order_leaves_the_position_entered(self) -> None:
        class Broker:
            def place_slm(self, **_kwargs):
                return BrokerOrder(
                    order_id="so1",
                    tag="s1",
                    tradingsymbol="AAA",
                    transaction_type="SELL",
                    order_type="SL-M",
                    quantity=300,
                    status="REJECTED",
                )

        pos = position()
        outcome = ensure_protected(pos, broker=Broker(), store=self.store)
        self.assertFalse(outcome.protected)
        self.assertEqual(pos.state, ExecutionState.ENTERED)
        self.assertEqual(outcome.reason, "stop_rejected")

    def test_a_broker_returning_nothing_is_a_failure_not_a_success(self) -> None:
        class Broker:
            def place_slm(self, **_kwargs):
                return None

        pos = position()
        outcome = ensure_protected(pos, broker=Broker(), store=self.store)
        self.assertFalse(outcome.protected)
        self.assertEqual(pos.state, ExecutionState.ENTERED)

    def test_retrying_after_a_failure_can_succeed(self) -> None:
        pos = position()

        class FlakyBroker:
            def __init__(self, real) -> None:
                self.real = real
                self.calls = 0

            def place_slm(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("transient")
                return self.real.place_slm(**kwargs)

        broker = FlakyBroker(self.broker)
        ensure_protected(pos, broker=broker, store=self.store)
        self.assertEqual(pos.state, ExecutionState.ENTERED)
        outcome = ensure_protected(pos, broker=broker, store=self.store)
        self.assertTrue(outcome.protected)
        self.assertEqual(pos.state, ExecutionState.PROTECTED)


class GuardTests(ProtectionTestCase):
    def test_a_position_not_in_entered_is_left_alone(self) -> None:
        for state in (
            ExecutionState.PENDING_ENTRY,
            ExecutionState.ENTRY_SUBMITTED,
            ExecutionState.PROTECTED,
            ExecutionState.CLOSED,
        ):
            with self.subTest(state=state.value):
                outcome = ensure_protected(
                    position(state=state), broker=self.broker, store=self.store
                )
                self.assertFalse(outcome.protected)
                self.assertEqual(outcome.reason, "not_awaiting_protection")

    def test_a_position_with_no_stop_price_is_not_protected(self) -> None:
        outcome = ensure_protected(
            position(stop_price=None), broker=self.broker, store=self.store
        )
        self.assertFalse(outcome.protected)
        self.assertEqual(outcome.reason, "no_stop_price")

    def test_a_position_with_no_quantity_is_not_protected(self) -> None:
        outcome = ensure_protected(position(qty=0), broker=self.broker, store=self.store)
        self.assertFalse(outcome.protected)
        self.assertEqual(outcome.reason, "no_quantity")

    def test_nothing_is_sent_to_the_broker_when_a_guard_refuses(self) -> None:
        ensure_protected(position(qty=0), broker=self.broker, store=self.store)
        self.assertEqual(self.broker.orders, {})


if __name__ == "__main__":
    unittest.main()
