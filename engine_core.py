"""Thin orchestrator: the replacement for TradingEngineCycle.tick().

Same tick-driven shape as the old engine (a poll loop suits a system that's
fundamentally polling quotes and broker order status) but decomposed: each
step below delegates to an injected collaborator instead of being one of
150+ methods on a single class. Every collaborator can be unit-tested and
reasoned about without the other four.

Two separate reasons entries can be off, deliberately not collapsed into one
flag:

* ``entries_stopped`` is the human's STOP toggle. It says nothing about health.
* ``entries_paused`` is the engine protecting itself — stale feed, a breached
  daily loss cap, or a step that failed too many times in a row.

Either one blocks new entries; neither stops the loop, and both leave
reconciliation and protection of existing positions running.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, List, Optional, Protocol

import engine_clock
from engine_config import SessionRiskConfig, margin_used_rupees
from engine_entry import EntryOutcome, EntryResult, submit_entry
from engine_feed import FeedMonitor
from engine_priority import VWAP_ENTRY_CLASSES, rank_candidates
from engine_risk import RiskPolicy
from engine_sizing import SizingPolicy
from engine_types import ExecutionState, Position, TriggerCandidate
from trading_engine_broker import BrokerPort, parse_timestamp_text

# How long a trigger may sit without a VWAP verdict before it's given up on.
# Two seconds is 2x the 1-second tick interval: check now, check once more a
# second later, then give up. Tied to the tick interval on purpose, not copied
# from the old engine's constant -- if the interval ever changes, recompute
# this as roughly 2x the new one rather than leaving it stale.
VWAP_WAIT_SECONDS = 2.0


class PositionStore(Protocol):
    def open_positions(self) -> List[Position]: ...
    def save(self, position: Position) -> None: ...
    def save_with_event(
        self, position: Position, event_type: str, payload: Optional[dict] = None
    ) -> None: ...
    def append_event(
        self, trade_id: str, event_type: str, payload: Optional[dict] = None
    ) -> int: ...
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
        session_config: Optional[SessionRiskConfig] = None,
        is_live: bool = False,
        run_id: Optional[str] = None,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.broker = broker
        self.store = store
        self.feed_monitor = feed_monitor
        self.risk_policy = risk_policy
        self.sizing_policy = sizing_policy
        self.candidate_source = candidate_source
        self.session_config = session_config or SessionRiskConfig()
        self.is_live = is_live
        self.run_id = run_id
        self.now_fn = now_fn
        self.entries_paused = False
        self.entries_stopped = False

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

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
            # TODO(phase 7): square off every open position and auto-stop once
            # all of them are confirmed CLOSED.

        self.ingest_triggers()
        self._drive_open_orders(positions)
        self._ensure_protection(positions)
        # Trailing intentionally removed for now — being rebuilt from scratch.
        # See Reference/execution_engine_rebuild_notes.md. Positions stay in
        # PROTECTED with a static stop until trailing is redesigned.

    # ------------------------------------------------------------------
    # Trigger ingestion
    # ------------------------------------------------------------------

    def ingest_triggers(self) -> None:
        """Pull candidates (already carrying their VWAP verdict, or None if
        the qualifier hasn't written one yet) and route each one once.

        Ranked before routing so that when several triggers land in the same
        window, the better setup gets the capital rather than whichever row the
        database happened to return first.
        """
        fresh = [
            candidate
            for candidate in self.candidate_source()
            if not self.store.exists(candidate.setup_id)
        ]
        for candidate in rank_candidates(fresh):
            self.handle_trigger(candidate)

    def handle_trigger(self, candidate: TriggerCandidate) -> Optional[EntryOutcome]:
        if self.entries_paused or self.entries_stopped:
            return None

        classification = candidate.vwap_classification
        if classification is None:
            if self._candidate_age_seconds(candidate) < VWAP_WAIT_SECONDS:
                # Not stored: the same candidate reappears next tick via
                # candidate_source once the qualifier has written a verdict.
                return None
            self._skip(candidate, "vwap_unavailable")
            return None
        if classification not in VWAP_ENTRY_CLASSES:
            # Covers REJECT and a literal UNAVAILABLE row alike. Neither leads
            # to an entry, so none of the submission machinery applies.
            self._skip(candidate, f"vwap_{classification.lower()}")
            return None

        risk_cap = self.risk_policy.per_trade_cap(
            vwap_limited=VWAP_ENTRY_CLASSES[classification] == "vwap_limited"
        )
        return self._enter(candidate, risk_cap_rupees=risk_cap)

    def _enter(
        self, candidate: TriggerCandidate, *, risk_cap_rupees: float
    ) -> EntryOutcome:
        config = self.session_config
        open_positions = self.store.open_positions()
        available_capital = config.remaining_capital_rupees(
            margin_used_rupees(open_positions, config.leverage_factor)
        )
        outcome = submit_entry(
            candidate,
            broker=self.broker,
            store=self.store,
            sizing_policy=self.sizing_policy,
            risk_cap_rupees=risk_cap_rupees,
            available_capital_rupees=available_capital,
            leverage_factor=config.leverage_factor,
            gate=self._entry_gate,
            margin_preflight=self._margin_preflight,
            is_live=self.is_live,
            run_id=self.run_id,
        )
        if outcome.result is EntryResult.FILLED:
            # TODO(phase 4/6): act on outcome.verdict — place the stop, or
            # flatten immediately on abnormal slippage.
            pass
        return outcome

    # ------------------------------------------------------------------
    # Entry gates, rechecked immediately before sending
    # ------------------------------------------------------------------

    def _entry_gate(self, candidate: TriggerCandidate) -> Optional[str]:
        """Time has passed since the trigger fired; re-verify everything."""
        if self.entries_paused:
            return "entries_paused"
        if self.entries_stopped:
            return "entries_stopped"
        if not engine_clock.new_entries_allowed(self.now_fn()):
            return "past_entry_cutoff"
        if not self.feed_monitor.check().healthy:
            return "feed_stale"
        for position in self.store.open_positions():
            if position.candidate.tradingsymbol == candidate.tradingsymbol:
                return "symbol_already_open"
        return None

    def _margin_preflight(self, candidate: TriggerCandidate, qty: int) -> Optional[str]:
        """Read-only margin check, as its own query.

        Deliberately a separate call rather than inferring margin from the
        placement response. A failed *read* does not block: the placement's own
        definite-rejection path catches genuinely insufficient margin, and
        refusing on a broker hiccup would drop good trades.
        """
        order_margins = getattr(self.broker, "order_margins", None)
        if order_margins is None:
            return None
        try:
            quote = order_margins(
                tradingsymbol=candidate.tradingsymbol,
                transaction_type="BUY" if candidate.direction == "UP" else "SELL",
                quantity=int(qty),
            )
        except Exception:  # noqa: BLE001 - an unreadable margin is not a refusal
            return None
        if quote is not None and not quote.ok:
            return "insufficient_margin_preflight"
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _skip(self, candidate: TriggerCandidate, reason: str) -> None:
        position = Position(
            trade_id=candidate.setup_id,
            candidate=candidate,
            state=ExecutionState.REJECTED,
            is_live=self.is_live,
            run_id=self.run_id,
            extra={"skip_reason": reason},
        )
        self.store.save_with_event(position, "skipped", {"reason": reason})

    def _candidate_age_seconds(self, candidate: TriggerCandidate) -> float:
        created_at = parse_timestamp_text(candidate.created_at)
        if created_at is None:
            return VWAP_WAIT_SECONDS  # unparseable timestamp: fail closed, skip now
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        now = self.now_fn()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return (now - created_at.astimezone(timezone.utc)).total_seconds()

    def _drive_open_orders(self, positions: List[Position]) -> None:
        for position in positions:
            if position.state != ExecutionState.ENTRY_SUBMITTED:
                continue
            # TODO(phase 3): continuous reconciliation against broker truth.

    def _ensure_protection(self, positions: List[Position]) -> None:
        for position in positions:
            if position.state != ExecutionState.ENTERED:
                continue
            # TODO(phase 4): place the structural stop, transition to PROTECTED.
