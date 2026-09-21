"""ExecutionEngine.handle_trigger: VWAP-verdict branching, no poll/retry state."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from typing import List

from engine_core import ExecutionEngine
from engine_feed import FeedHealth
from engine_risk import RiskPolicy
from engine_types import ExecutionState, Position, RiskLimits, TriggerCandidate


def _candidate(setup_id: str, *, vwap_classification, age_seconds: float = 0.0) -> TriggerCandidate:
    created_at = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return TriggerCandidate(
        setup_id=setup_id,
        continuation_rule_version="v1",
        session_date="2026-08-17",
        tradingsymbol="AAA",
        instrument_token=1,
        direction="UP",
        trigger_price=110.0,
        pullback_swing_high=109.0,
        pullback_swing_low=100.0,
        tick_size=0.05,
        buffer_ticks=1,
        trigger_exchange_ts="2026-08-17T10:01:16+00:00",
        created_at=created_at.isoformat(),
        vwap_classification=vwap_classification,
    )


class FakeStore:
    def __init__(self) -> None:
        self.saved: List[Position] = []

    def open_positions(self) -> List[Position]:
        return [p for p in self.saved if p.state not in (ExecutionState.REJECTED, ExecutionState.CLOSED)]

    def save(self, position: Position) -> None:
        self.saved.append(position)

    def exists(self, setup_id: str) -> bool:
        return any(p.candidate.setup_id == setup_id for p in self.saved)


class AlwaysHealthyFeed:
    def check(self, *, now=None) -> FeedHealth:
        return FeedHealth(healthy=True, age_seconds=0.5)


def _engine(store: FakeStore) -> ExecutionEngine:
    return ExecutionEngine(
        broker=None,  # unused by handle_trigger's pre-sizing branches
        store=store,
        feed_monitor=AlwaysHealthyFeed(),
        risk_policy=RiskPolicy(
            RiskLimits(
                per_trade_cap_rupees=900.0,
                per_trade_cap_vwap_limited_rupees=450.0,
                daily_loss_cap_rupees=3000.0,
            )
        ),
        sizing_policy=None,  # not reached: entry path still raises NotImplementedError
        candidate_source=lambda: [],
    )


class HandleTriggerVwapTests(unittest.TestCase):
    def test_reject_classification_skips_immediately(self) -> None:
        store = FakeStore()
        engine = _engine(store)
        engine.handle_trigger(_candidate("rejected", vwap_classification="REJECT"))
        self.assertEqual(len(store.saved), 1)
        self.assertEqual(store.saved[0].state, ExecutionState.REJECTED)
        self.assertEqual(store.saved[0].extra["skip_reason"], "vwap_reject")

    def test_literal_unavailable_classification_skips_immediately(self) -> None:
        store = FakeStore()
        engine = _engine(store)
        engine.handle_trigger(_candidate("unavail", vwap_classification="UNAVAILABLE"))
        self.assertEqual(store.saved[0].extra["skip_reason"], "vwap_unavailable")

    def test_none_classification_within_wait_window_defers_without_storing(self) -> None:
        store = FakeStore()
        engine = _engine(store)
        engine.handle_trigger(_candidate("pending", vwap_classification=None, age_seconds=0.5))
        self.assertEqual(store.saved, [])

    def test_none_classification_past_wait_window_skips_as_unavailable(self) -> None:
        store = FakeStore()
        engine = _engine(store)
        engine.handle_trigger(_candidate("stale_pending", vwap_classification=None, age_seconds=5.0))
        self.assertEqual(len(store.saved), 1)
        self.assertEqual(store.saved[0].state, ExecutionState.REJECTED)
        self.assertEqual(store.saved[0].extra["skip_reason"], "vwap_unavailable")

    def test_accept_and_limited_route_to_entry_path_with_matching_cap(self) -> None:
        store = FakeStore()
        engine = _engine(store)
        with self.assertRaises(NotImplementedError):
            engine.handle_trigger(_candidate("accepted", vwap_classification="ACCEPT"))
        # Nothing stored yet: the entry path (sizing/submit) is still a TODO(port).
        self.assertEqual(store.saved, [])

    def test_paused_entries_skip_everything_including_accept(self) -> None:
        store = FakeStore()
        engine = _engine(store)
        engine.entries_paused = True
        engine.handle_trigger(_candidate("accepted", vwap_classification="ACCEPT"))
        engine.handle_trigger(_candidate("rejected", vwap_classification="REJECT"))
        self.assertEqual(store.saved, [])

    def test_ingest_triggers_skips_setups_already_known_to_store(self) -> None:
        store = FakeStore()
        store.save(
            Position(
                trade_id="seen",
                candidate=_candidate("seen", vwap_classification="REJECT"),
                state=ExecutionState.REJECTED,
            )
        )
        engine = ExecutionEngine(
            broker=None,
            store=store,
            feed_monitor=AlwaysHealthyFeed(),
            risk_policy=RiskPolicy(
                RiskLimits(
                    per_trade_cap_rupees=900.0,
                    per_trade_cap_vwap_limited_rupees=450.0,
                    daily_loss_cap_rupees=3000.0,
                )
            ),
            sizing_policy=None,
            candidate_source=lambda: [_candidate("seen", vwap_classification="REJECT")],
        )
        engine.ingest_triggers()
        self.assertEqual(len(store.saved), 1)  # unchanged: not re-handled


if __name__ == "__main__":
    unittest.main()
