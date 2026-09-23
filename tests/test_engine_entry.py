"""The nine-step entry sequence: caps, gates, crash safety, slippage."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from engine_entry import (
    ABNORMAL_SLIPPAGE_MULTIPLE,
    EntryResult,
    FillVerdict,
    ResponseKind,
    apply_entry_fill,
    broker_tag_for,
    compute_stop_price,
    is_definite_rejection,
    place_entry,
    resolve_ambiguous_entry,
    submit_entry,
    transaction_type_for,
)
from engine_sizing import RiskCappedSizing
from engine_store import SqlitePositionStore
from engine_types import ExecutionState, Position, TriggerCandidate
from trading_engine_broker import EntryAcceptedVisibilityUnknown, FakeBroker
from trading_engine_types import BrokerOrder

ACCEPT_CAP = 900.0
LIMITED_CAP = 450.0


def candidate(
    setup_id: str = "s1",
    *,
    direction: str = "UP",
    trigger_price: float = 110.0,
    swing_low: Optional[float] = 107.0,
    swing_high: Optional[float] = 113.0,
    classification: str = "ACCEPT",
) -> TriggerCandidate:
    return TriggerCandidate(
        setup_id=setup_id,
        continuation_rule_version="v1",
        session_date="2026-09-22",
        tradingsymbol="AAA",
        instrument_token=1,
        direction=direction,
        trigger_price=trigger_price,
        pullback_swing_high=swing_high,
        pullback_swing_low=swing_low,
        tick_size=0.05,
        buffer_ticks=1,
        trigger_exchange_ts="2026-09-22T10:00:00+00:00",
        created_at=datetime.now(timezone.utc).isoformat(),
        vwap_classification=classification,
    )


class RecordingStore(SqlitePositionStore):
    """Real store, plus the order in which events were written."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.calls: List[str] = []

    def save_with_event(self, position, event_type, payload=None):  # type: ignore[override]
        self.calls.append(f"save:{event_type}")
        super().save_with_event(position, event_type, payload)

    def append_event(self, trade_id, event_type, payload=None):  # type: ignore[override]
        self.calls.append(f"event:{event_type}")
        return super().append_event(trade_id, event_type, payload)


class RaisingBroker:
    """A broker whose placement always fails with a given error."""

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc
        self.place_calls = 0
        self.tagged: List[BrokerOrder] = []

    def place_market_mis(self, **_kwargs) -> BrokerOrder:
        self.place_calls += 1
        raise self.exc

    def orders_by_tag(self, tag: str) -> List[BrokerOrder]:
        return [o for o in self.tagged if o.tag == tag]


def order(
    *,
    tag: str = "s1",
    status: str = "COMPLETE",
    qty: int = 300,
    avg: Optional[float] = 110.0,
    order_id: str = "o1",
) -> BrokerOrder:
    return BrokerOrder(
        order_id=order_id,
        tag=tag,
        tradingsymbol="AAA",
        transaction_type="BUY",
        order_type="MARKET",
        quantity=qty,
        status=status,
        average_price=avg,
        filled_quantity=qty if status == "COMPLETE" else 0,
    )


class EntryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = RecordingStore(Path(self._tmp.name) / "engine.db")
        self.broker = FakeBroker()
        self.broker.last_prices["AAA"] = 110.0

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def run_entry(self, cand=None, *, cap=ACCEPT_CAP, capital=300_000.0, **kwargs):
        return submit_entry(
            cand or candidate(),
            broker=kwargs.pop("broker", self.broker),
            store=self.store,
            sizing_policy=RiskCappedSizing(),
            risk_cap_rupees=cap,
            available_capital_rupees=capital,
            leverage_factor=5.0,
            **kwargs,
        )


class StopPriceTests(unittest.TestCase):
    def test_long_stop_is_one_tick_below_the_swing_low(self) -> None:
        self.assertAlmostEqual(compute_stop_price(candidate()), 106.95, places=4)

    def test_short_stop_is_one_tick_above_the_swing_high(self) -> None:
        stop = compute_stop_price(candidate(direction="DOWN"))
        self.assertAlmostEqual(stop, 113.05, places=4)

    def test_missing_swing_yields_no_stop(self) -> None:
        self.assertIsNone(compute_stop_price(candidate(swing_low=None)))
        self.assertIsNone(compute_stop_price(candidate(direction="DOWN", swing_high=None)))

    def test_transaction_type_follows_direction(self) -> None:
        self.assertEqual(transaction_type_for("UP"), "BUY")
        self.assertEqual(transaction_type_for("DOWN"), "SELL")


class SizingIntegrationTests(EntryTestCase):
    def test_accept_cap_sizes_the_worked_example(self) -> None:
        # Entry 110, stop 106.95 -> risk/share 3.05; 900/3.05 = 295.
        outcome = self.run_entry(cap=ACCEPT_CAP)
        assert outcome.position is not None
        self.assertEqual(outcome.position.qty, 295)

    def test_limited_cap_sizes_about_half(self) -> None:
        outcome = self.run_entry(cap=LIMITED_CAP)
        assert outcome.position is not None
        self.assertEqual(outcome.position.qty, 147)

    def test_limited_uses_the_identical_code_path(self) -> None:
        # Same events, same states; only the numbers differ.
        accept = self.run_entry(candidate("a"), cap=ACCEPT_CAP)
        limited = self.run_entry(candidate("b"), cap=LIMITED_CAP)
        self.assertEqual(accept.result, limited.result)
        assert accept.position and limited.position
        self.assertEqual(accept.position.state, limited.position.state)

    def test_capital_constraint_can_bind_instead_of_risk(self) -> None:
        # Tiny capital: 1000 * 5 / 110 = 45 shares, well under the risk cap's 295.
        outcome = self.run_entry(capital=1_000.0)
        assert outcome.position is not None
        self.assertEqual(outcome.position.qty, 45)
        self.assertEqual(outcome.position.extra["binding_constraint"], "capital")

    def test_zero_quantity_skips_without_touching_the_broker(self) -> None:
        outcome = self.run_entry(capital=1.0)
        self.assertEqual(outcome.result, EntryResult.SKIPPED)
        self.assertEqual(outcome.reason, "sized_to_zero")
        self.assertEqual(self.broker.market_place_count, 0)

    def test_no_structural_stop_skips_without_touching_the_broker(self) -> None:
        outcome = self.run_entry(candidate(swing_low=None))
        self.assertEqual(outcome.result, EntryResult.SKIPPED)
        self.assertEqual(outcome.reason, "no_structural_stop")
        self.assertEqual(self.broker.market_place_count, 0)


class GateTests(EntryTestCase):
    def test_a_refusing_gate_prevents_the_order(self) -> None:
        outcome = self.run_entry(gate=lambda _c: "entries_paused")
        self.assertEqual(outcome.result, EntryResult.SKIPPED)
        self.assertEqual(outcome.reason, "entries_paused")
        self.assertEqual(self.broker.market_place_count, 0)

    def test_an_allowing_gate_lets_it_through(self) -> None:
        outcome = self.run_entry(gate=lambda _c: None)
        self.assertEqual(outcome.result, EntryResult.FILLED)

    def test_margin_preflight_can_refuse_after_sizing(self) -> None:
        seen = {}

        def preflight(cand, qty):
            seen["qty"] = qty
            return "insufficient_margin"

        outcome = self.run_entry(margin_preflight=preflight)
        self.assertEqual(outcome.reason, "insufficient_margin")
        # It is checked against the sized quantity, not a guess.
        self.assertEqual(seen["qty"], 295)
        self.assertEqual(self.broker.market_place_count, 0)

    def test_gates_are_rechecked_after_sizing_not_before(self) -> None:
        # Sizing must have happened (so the gate sees the real qty via
        # preflight), yet nothing may be sent.
        calls: List[str] = []
        self.run_entry(
            gate=lambda _c: calls.append("gate") or None,
            margin_preflight=lambda _c, q: calls.append(f"margin:{q}") or "no",
        )
        self.assertEqual(calls, ["gate", "margin:295"])


class CrashSafetyTests(EntryTestCase):
    def test_intent_is_written_before_the_broker_is_called(self) -> None:
        placed_at: List[int] = []
        real_place = self.broker.place_market_mis

        def spy(**kwargs):
            placed_at.append(len(self.store.calls))
            return real_place(**kwargs)

        self.broker.place_market_mis = spy  # type: ignore[method-assign]
        self.run_entry()
        # The first store write happened strictly before the placement call.
        self.assertGreaterEqual(placed_at[0], 1)
        self.assertEqual(self.store.calls[0], "save:entry_intent")

    def test_intent_row_is_durable_and_names_the_plan(self) -> None:
        self.run_entry()
        events = [dict(r) for r in self.store.list_events("s1")]
        intent = next(e for e in events if e["event_type"] == "entry_intent")
        self.assertIn('"qty":295', intent["payload_json"])
        self.assertIn('"stop_price":106.95', intent["payload_json"])

    def test_trade_id_is_the_broker_tag(self) -> None:
        self.run_entry()
        placed = list(self.broker.orders.values())
        self.assertEqual(placed[0].tag, broker_tag_for("s1"))
        self.assertLessEqual(len(placed[0].tag), 20)


class ResponseClassificationTests(unittest.TestCase):
    def test_margin_and_quantity_errors_are_definite_rejections(self) -> None:
        for message in (
            "Insufficient funds",
            "Invalid quantity for this instrument",
            "RMS: blocked for this symbol",
            "margin shortfall",
            "Invalid tags: max allowed tag length is 20",
        ):
            with self.subTest(message=message):
                self.assertTrue(is_definite_rejection(message))

    def test_timeouts_and_connection_errors_are_not_definite(self) -> None:
        for message in (
            "Read timed out",
            "Connection reset by peer",
            "502 Bad Gateway",
            "",
        ):
            with self.subTest(message=message):
                self.assertFalse(is_definite_rejection(message))

    def test_a_timeout_classifies_as_ambiguous(self) -> None:
        broker = RaisingBroker(TimeoutError("Read timed out"))
        response = place_entry(broker, candidate=candidate(), quantity=10, tag="s1")
        self.assertEqual(response.kind, ResponseKind.AMBIGUOUS)

    def test_a_margin_error_classifies_as_rejected(self) -> None:
        broker = RaisingBroker(ValueError("Insufficient margin"))
        response = place_entry(broker, candidate=candidate(), quantity=10, tag="s1")
        self.assertEqual(response.kind, ResponseKind.REJECTED)

    def test_accepted_but_invisible_classifies_as_ambiguous(self) -> None:
        broker = RaisingBroker(EntryAcceptedVisibilityUnknown("o9"))
        response = place_entry(broker, candidate=candidate(), quantity=10, tag="s1")
        self.assertEqual(response.kind, ResponseKind.AMBIGUOUS)

    def test_a_rejected_status_on_a_returned_order_is_a_rejection(self) -> None:
        class Broker:
            def place_market_mis(self, **_k):
                return order(status="REJECTED")

        response = place_entry(Broker(), candidate=candidate(), quantity=10, tag="s1")
        self.assertEqual(response.kind, ResponseKind.REJECTED)


class BrokerTagTests(unittest.TestCase):
    def test_a_long_composite_trade_id_still_fits_kites_limit(self) -> None:
        trade_id = "900609|2026-09-23T11:09:00+05:30|intraday_spike_v1|intraday_pullback_v1"
        tag = broker_tag_for(trade_id)
        self.assertLessEqual(len(tag), 20)
        self.assertTrue(tag.isalnum())

    def test_the_same_trade_id_always_yields_the_same_tag(self) -> None:
        trade_id = "900609|2026-09-23T11:09:00+05:30|intraday_spike_v1|intraday_pullback_v1"
        self.assertEqual(broker_tag_for(trade_id), broker_tag_for(trade_id))

    def test_different_trade_ids_yield_different_tags(self) -> None:
        self.assertNotEqual(broker_tag_for("s1"), broker_tag_for("s2"))

    def test_the_base_tag_leaves_room_for_the_flatten_suffix(self) -> None:
        # engine_exit.flatten_tag_for appends a 2-char suffix; the base must
        # stay short enough that the combined tag is still <= 20 chars.
        self.assertLessEqual(len(broker_tag_for("s1")), 18)


class AmbiguityResolutionTests(unittest.TestCase):
    def test_absent_at_the_broker_resolves_to_none(self) -> None:
        broker = RaisingBroker(TimeoutError("timeout"))
        self.assertIsNone(resolve_ambiguous_entry(broker, tag="s1"))

    def test_found_and_filled_resolves_to_that_order(self) -> None:
        broker = RaisingBroker(TimeoutError("timeout"))
        broker.tagged = [order(order_id="o7")]
        found = resolve_ambiguous_entry(broker, tag="s1")
        assert found is not None
        self.assertEqual(found.order_id, "o7")

    def test_cancelled_and_rejected_orders_are_not_treated_as_existing(self) -> None:
        broker = RaisingBroker(TimeoutError("timeout"))
        broker.tagged = [order(status="CANCELLED"), order(status="REJECTED")]
        self.assertIsNone(resolve_ambiguous_entry(broker, tag="s1"))

    def test_a_filled_order_is_preferred_over_a_merely_open_one(self) -> None:
        broker = RaisingBroker(TimeoutError("timeout"))
        broker.tagged = [
            order(order_id="open", status="OPEN", avg=None),
            order(order_id="filled", status="COMPLETE"),
        ]
        found = resolve_ambiguous_entry(broker, tag="s1")
        assert found is not None
        self.assertEqual(found.order_id, "filled")

    def test_a_failing_lookup_does_not_raise(self) -> None:
        class Broker:
            def orders_by_tag(self, tag):
                raise RuntimeError("broker down")

        self.assertIsNone(resolve_ambiguous_entry(Broker(), tag="s1"))


class SubmitOutcomeTests(EntryTestCase):
    def test_clean_success_reaches_entered_and_filled(self) -> None:
        outcome = self.run_entry()
        self.assertEqual(outcome.result, EntryResult.FILLED)
        assert outcome.position is not None
        self.assertEqual(outcome.position.state, ExecutionState.ENTERED)
        self.assertEqual(outcome.verdict, FillVerdict.PROTECT)
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(stored.state, ExecutionState.ENTERED)
        self.assertEqual(stored.entry_price, 110.0)

    def test_definite_rejection_records_the_brokers_reason_and_stops(self) -> None:
        broker = RaisingBroker(ValueError("Insufficient margin"))
        outcome = self.run_entry(broker=broker)
        self.assertEqual(outcome.result, EntryResult.REJECTED)
        assert outcome.position is not None
        self.assertEqual(outcome.position.state, ExecutionState.REJECTED)
        self.assertIn("Insufficient margin", str(outcome.reason))
        # No retry with the same numbers.
        self.assertEqual(broker.place_calls, 1)

    def test_ambiguous_then_absent_finalizes_as_rejected(self) -> None:
        broker = RaisingBroker(TimeoutError("Read timed out"))
        outcome = self.run_entry(broker=broker)
        self.assertEqual(outcome.result, EntryResult.REJECTED)
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(stored.state, ExecutionState.REJECTED)
        types = [r["event_type"] for r in self.store.list_events("s1")]
        self.assertEqual(
            types, ["entry_intent", "entry_ambiguous", "entry_unreached", "entry_rejected"]
        )

    def test_ambiguous_then_found_filled_proceeds_exactly_like_success(self) -> None:
        broker = RaisingBroker(TimeoutError("Read timed out"))
        broker.tagged = [order(order_id="o7", avg=110.0, tag=broker_tag_for("s1"))]
        outcome = self.run_entry(broker=broker)
        self.assertEqual(outcome.result, EntryResult.FILLED)
        assert outcome.position is not None
        self.assertEqual(outcome.position.state, ExecutionState.ENTERED)
        self.assertEqual(outcome.position.entry_order_id, "o7")

    def test_submitted_but_unfilled_stops_at_entry_submitted(self) -> None:
        self.broker.auto_fill_entry = False
        outcome = self.run_entry()
        self.assertEqual(outcome.result, EntryResult.SUBMITTED)
        assert outcome.position is not None
        self.assertEqual(outcome.position.state, ExecutionState.ENTRY_SUBMITTED)
        self.assertIsNone(outcome.position.entry_price)

    def test_a_fill_with_no_average_price_waits_rather_than_guessing(self) -> None:
        class Broker:
            def place_market_mis(self, **_k):
                return order(avg=None, status="COMPLETE")

        outcome = self.run_entry(broker=Broker())
        self.assertEqual(outcome.result, EntryResult.SUBMITTED)


class SlippageTests(EntryTestCase):
    def _fill_at(self, price: float, cap: float = ACCEPT_CAP):
        self.broker.last_prices["AAA"] = price
        return self.run_entry(cap=cap)

    def test_no_slippage_is_protected(self) -> None:
        outcome = self._fill_at(110.0)
        self.assertEqual(outcome.verdict, FillVerdict.PROTECT)

    def test_ordinary_slippage_is_accepted_and_logged_not_sized_around(self) -> None:
        # 295 shares, stop 106.95. A fill at 111 gives 295*4.05 = 1194.75,
        # over the 900 cap but under the 1350 line.
        outcome = self._fill_at(111.0)
        assert outcome.position is not None
        self.assertGreater(outcome.position.risk_taken_rupees, ACCEPT_CAP)
        self.assertEqual(outcome.verdict, FillVerdict.PROTECT)
        events = [dict(r) for r in self.store.list_events("s1")]
        filled = next(e for e in events if e["event_type"] == "entry_filled")
        self.assertIn("risk_taken_rupees", filled["payload_json"])

    def test_abnormal_slippage_flattens_instead_of_protecting(self) -> None:
        # A fill at 112 gives 295*5.05 = 1489.75, past the 1350 line.
        outcome = self._fill_at(112.0)
        assert outcome.position is not None
        self.assertGreater(
            outcome.position.risk_taken_rupees, ACCEPT_CAP * ABNORMAL_SLIPPAGE_MULTIPLE
        )
        self.assertEqual(outcome.verdict, FillVerdict.FLATTEN_ABNORMAL_SLIPPAGE)

    def test_the_line_scales_with_the_tier_so_both_agree_on_the_same_fill(self) -> None:
        # The property the design actually relies on: because quantity is sized
        # proportionally to the tier's cap, identical slippage lands at the
        # same risk-to-cap RATIO for both tiers, so one threshold multiple
        # governs both without a separate LIMITED code path.
        accept = self._fill_at(111.6, cap=ACCEPT_CAP)
        self.setUp()  # fresh store/broker for the second leg
        limited = self._fill_at(111.6, cap=LIMITED_CAP)
        assert accept.position and limited.position
        accept_ratio = accept.position.risk_taken_rupees / ACCEPT_CAP
        limited_ratio = limited.position.risk_taken_rupees / LIMITED_CAP
        self.assertAlmostEqual(accept_ratio, limited_ratio, places=1)
        self.assertEqual(accept.verdict, limited.verdict)

    def test_limited_tier_flattens_at_its_own_smaller_rupee_threshold(self) -> None:
        # 147 shares at the LIMITED cap; the line is 1.5*450 = 675, not 1350.
        outcome = self._fill_at(111.6, cap=LIMITED_CAP)
        assert outcome.position is not None
        self.assertLess(outcome.position.risk_taken_rupees, ACCEPT_CAP * ABNORMAL_SLIPPAGE_MULTIPLE)
        self.assertGreater(outcome.position.risk_taken_rupees, LIMITED_CAP * ABNORMAL_SLIPPAGE_MULTIPLE)
        self.assertEqual(outcome.verdict, FillVerdict.FLATTEN_ABNORMAL_SLIPPAGE)

    def test_limited_tier_accepts_ordinary_slippage_below_its_line(self) -> None:
        # A fill at 111.0: 147*4.05 = 595.35, under the 675 line.
        outcome = self._fill_at(111.0, cap=LIMITED_CAP)
        self.assertEqual(outcome.verdict, FillVerdict.PROTECT)

    def test_exactly_at_the_threshold_is_accepted_not_flattened(self) -> None:
        pos = Position(
            trade_id="t1",
            candidate=candidate(),
            state=ExecutionState.ENTRY_SUBMITTED,
            stop_price=100.0,
        )
        # 100 shares x 13.50 = 1350.00 == 1.5 * 900 exactly.
        verdict = apply_entry_fill(
            pos, fill_price=113.5, filled_qty=100, risk_cap_rupees=900.0
        )
        self.assertEqual(verdict, FillVerdict.PROTECT)

    def test_risk_is_measured_from_the_real_fill_not_the_trigger_price(self) -> None:
        pos = Position(
            trade_id="t1",
            candidate=candidate(),
            state=ExecutionState.ENTRY_SUBMITTED,
            stop_price=106.95,
        )
        apply_entry_fill(pos, fill_price=111.0, filled_qty=100, risk_cap_rupees=900.0)
        self.assertAlmostEqual(pos.risk_taken_rupees, 405.0, places=4)
        self.assertEqual(pos.entry_price, 111.0)

    def test_the_stop_is_never_moved_to_make_the_risk_fit(self) -> None:
        outcome = self._fill_at(112.0)
        assert outcome.position is not None
        # Still the structural price from step 1, untouched by the overshoot.
        self.assertAlmostEqual(outcome.position.stop_price, 106.95, places=4)

    def test_a_partial_fill_uses_the_filled_quantity(self) -> None:
        pos = Position(
            trade_id="t1",
            candidate=candidate(),
            state=ExecutionState.ENTRY_SUBMITTED,
            qty=300,
            stop_price=107.0,
        )
        apply_entry_fill(pos, fill_price=110.0, filled_qty=100, risk_cap_rupees=900.0)
        self.assertEqual(pos.qty, 100)
        self.assertAlmostEqual(pos.risk_taken_rupees, 300.0, places=4)

    def test_a_fill_with_no_stop_flattens_rather_than_holding_blind(self) -> None:
        pos = Position(
            trade_id="t1",
            candidate=candidate(),
            state=ExecutionState.ENTRY_SUBMITTED,
            stop_price=None,
        )
        verdict = apply_entry_fill(
            pos, fill_price=110.0, filled_qty=100, risk_cap_rupees=900.0
        )
        self.assertEqual(verdict, FillVerdict.FLATTEN_ABNORMAL_SLIPPAGE)
        self.assertEqual(pos.state, ExecutionState.ENTERED)

    def test_short_side_risk_uses_absolute_distance(self) -> None:
        pos = Position(
            trade_id="t1",
            candidate=candidate(direction="DOWN"),
            state=ExecutionState.ENTRY_SUBMITTED,
            stop_price=113.05,
        )
        apply_entry_fill(pos, fill_price=110.0, filled_qty=100, risk_cap_rupees=900.0)
        self.assertAlmostEqual(pos.risk_taken_rupees, 305.0, places=4)


if __name__ == "__main__":
    unittest.main()
