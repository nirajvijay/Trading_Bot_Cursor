"""Thin orchestrator: the replacement for TradingEngineCycle.tick().

Same tick-driven shape as the old engine (a poll loop suits a system that's
fundamentally polling quotes and broker order status) but decomposed: each
step below delegates to an injected collaborator instead of being one of
150+ methods on a single class. Every collaborator can be unit-tested and
reasoned about without the other four.

This is a skeleton: step bodies mark where logic gets ported from
trading_engine_cycle.py, one concern at a time, verified in paper mode
before anything touches KiteBroker.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, List, Protocol

from engine_feed import FeedMonitor
from engine_risk import RiskPolicy
from engine_sizing import SizingPolicy
from engine_types import ExecutionState, Position, TriggerCandidate
from trading_engine_broker import BrokerPort, parse_timestamp_text

# How long a trigger may sit without a VWAP verdict before it's given up on.
# The classification is computed synchronously off the same trigger event
# upstream, so this is a safety ceiling for a stalled writer, not an expected
# wait — see engine_core.handle_trigger and trading_engine_handoff.
# fetch_triggered_with_vwap_since for the full rationale.
VWAP_WAIT_SECONDS = 2.0

# Classifications that clear the candidate to size and enter, mapped to
# which risk_cap the sizing policy should be given.
VWAP_ENTRY_CLASSES = {"ACCEPT": "normal", "LIMITED": "vwap_limited"}


class PositionStore(Protocol):
    def open_positions(self) -> List[Position]: ...
    def save(self, position: Position) -> None: ...
    def exists(self, setup_id: str) -> bool: ...


class ExecutionEngine:
    def __init__(
        self,
        *,
        broker: BrokerPort,
        store: PositionStore,
        feed_monitor: FeedMonitor,
        risk_policy: RiskPolicy,
        sizing_policy: SizingPolicy,
        candidate_source: Callable[[], List[TriggerCandidate]],
    ) -> None:
        self.broker = broker
        self.store = store
        self.feed_monitor = feed_monitor
        self.risk_policy = risk_policy
        self.sizing_policy = sizing_policy
        self.candidate_source = candidate_source
        self.entries_paused = False

    def tick(self) -> None:
        health = self.feed_monitor.check()
        if not health.healthy:
            self.entries_paused = True
            return
        self.entries_paused = False

        positions = self.store.open_positions()

        loss_check = self.risk_policy.check_daily_loss(positions)
        if loss_check.breached:
            self.entries_paused = True
            # TODO(port): square off all open positions, same as
            # trading_engine_cycle._enforce_daily_loss_inner did.

        self.ingest_triggers()
        self._drive_open_orders(positions)
        self._ensure_protection(positions)
        # Trailing intentionally removed for now — being rebuilt from scratch.
        # See Reference/execution_engine_rebuild_notes.md. Positions stay in
        # PROTECTED with a static stop until trailing is redesigned.

    def ingest_triggers(self) -> None:
        """Pull candidates (already carrying their VWAP verdict, or None if
        the qualifier hasn't written one yet) and route each one once."""
        for candidate in self.candidate_source():
            if self.store.exists(candidate.setup_id):
                continue
            self.handle_trigger(candidate)

    def handle_trigger(self, candidate: TriggerCandidate) -> None:
        if self.entries_paused:
            return

        classification = candidate.vwap_classification
        if classification is None:
            if self._candidate_age_seconds(candidate) < VWAP_WAIT_SECONDS:
                # Not stored: the same candidate reappears next tick via
                # candidate_source once the qualifier has written a verdict.
                return
            self._skip(candidate, "vwap_unavailable")
            return
        if classification not in VWAP_ENTRY_CLASSES:
            # Covers REJECT and a literal UNAVAILABLE row alike.
            self._skip(candidate, f"vwap_{classification.lower()}")
            return

        risk_cap = self.risk_policy.per_trade_cap(
            vwap_limited=VWAP_ENTRY_CLASSES[classification] == "vwap_limited"
        )
        # TODO(port): call self.sizing_policy.decide(..., risk_cap_rupees=risk_cap),
        # create a Position in PENDING_ENTRY, submit entry order via
        # self.broker, transition to ENTRY_SUBMITTED, store it.
        raise NotImplementedError

    def _skip(self, candidate: TriggerCandidate, reason: str) -> None:
        self.store.save(
            Position(
                trade_id=candidate.setup_id,
                candidate=candidate,
                state=ExecutionState.REJECTED,
                extra={"skip_reason": reason},
            )
        )

    def _candidate_age_seconds(self, candidate: TriggerCandidate) -> float:
        created_at = parse_timestamp_text(candidate.created_at)
        if created_at is None:
            return VWAP_WAIT_SECONDS  # unparseable timestamp: fail closed, skip now
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - created_at.astimezone(timezone.utc)).total_seconds()

    def _drive_open_orders(self, positions: List[Position]) -> None:
        for position in positions:
            if position.state != ExecutionState.ENTRY_SUBMITTED:
                continue
            # TODO(port): poll self.broker for the entry order, apply fill
            # (partial or full), transition to ENTERED or REJECTED.

    def _ensure_protection(self, positions: List[Position]) -> None:
        for position in positions:
            if position.state != ExecutionState.ENTERED:
                continue
            # TODO(port): place the structural stop via self.broker,
            # transition to PROTECTED once the broker confirms.
