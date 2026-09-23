"""Thin orchestrator: the replacement for TradingEngineCycle.tick().

Same tick-driven shape as the old engine (a poll loop suits a system that's
fundamentally polling quotes and broker order status) but decomposed: each
step below delegates to an injected collaborator instead of being one of
150+ methods on a single class. Every collaborator can be unit-tested and
reasoned about without the other four.

**Step order is load-bearing.** Reconciliation runs before anything that makes
a decision, so every decision in a tick is made against what the broker says
is true right now rather than against what we believed last tick.

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
from typing import Callable, Dict, List, Optional, Protocol

import engine_clock
from engine_config import SessionRiskConfig, margin_used_rupees
from engine_entry import (
    ENTRY_STALL_ESCALATE_SECONDS,
    EntryOutcome,
    EntryResult,
    FillVerdict,
    apply_entry_fill,
    submit_entry,
)
from engine_exit import (
    EXIT_ORDER_IDS_KEY,
    CloseReason,
    finalize_exit,
    flatten,
    identify_closing_order,
    kite_realised_pnl,
    trade_exit_orders,
)
from engine_feed import FeedMonitor
from engine_orders import transition
from engine_priority import VWAP_ENTRY_CLASSES, rank_candidates
from engine_protection import ensure_protected
from engine_reconcile import (
    ENTRY_PARTIAL_WAIT_REASON,
    ENTRY_WORKING_WAIT_REASON,
    BrokerTruth,
    ReconcileAction,
    fetch_broker_truth,
    reconcile,
    symbols_of,
)
from engine_risk import RiskPolicy
from engine_runloop import (
    STEP_COMMANDS,
    STEP_INGEST,
    STEP_PROTECTION,
    STEP_RECONCILE,
    STEP_SQUAREOFF,
    StepFailureTracker,
    run_step,
)
from engine_sizing import SizingPolicy
from engine_squareoff import SquareoffProgress, squareoff_all
from engine_types import ExecutionState, Position, TriggerCandidate
from trading_engine_broker import BrokerPort, parse_timestamp_text

# Kite's own day P&L for a stock, pinned on the closed row that made it flat.
STOCK_DAY_KEY = "stock_day"
# Our per-trade sum may differ from Kite's by rounding; beyond this it is flagged.
PNL_MISMATCH_TOLERANCE_RUPEES = 1.0

# Escalation key prefix for an entry stuck waiting at the broker (one per trade).
ENTRY_STALL_ESCALATION_STEP = "entry_stalled"

# Which waiting reasons are watched, and the event each stall is logged as.
ENTRY_STALL_EVENTS = {
    ENTRY_PARTIAL_WAIT_REASON: "entry_partial_fill_stalled",
    ENTRY_WORKING_WAIT_REASON: "entry_unfilled_stalled",
}

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
    def closed_today(self, session_date: str) -> List[Position]: ...


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
        session_date: Optional[str] = None,
        is_live: bool = False,
        run_id: Optional[str] = None,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        command_source: Optional[Callable[[], list]] = None,
    ) -> None:
        self.broker = broker
        self.store = store
        self.feed_monitor = feed_monitor
        self.risk_policy = risk_policy
        self.sizing_policy = sizing_policy
        self.candidate_source = candidate_source
        self.session_config = session_config or SessionRiskConfig()
        self.session_date = session_date or engine_clock.to_ist(
            now_fn()
        ).strftime("%Y-%m-%d")
        self.is_live = is_live
        self.run_id = run_id
        self.now_fn = now_fn
        self.command_source = command_source

        self.entries_paused = False
        self.entries_stopped = False
        self.pause_reason: Optional[str] = None
        self.failures = StepFailureTracker()
        self.tick_count = 0

        # Set once a terminal trigger fires; the loop keeps running until the
        # square-off it started is confirmed complete.
        self.shutdown_reason: Optional[CloseReason] = None
        self.shutdown_complete = False
        self.breached = False
        self.live_pnl: Dict[str, Optional[float]] = {}
        self.last_truth: Optional[BrokerTruth] = None
        self.escalation_notices: List[str] = []
        self.realised_loss_today = 0.0

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    def tick(self) -> None:
        self.tick_count += 1
        now = self.now_fn()

        health = self.feed_monitor.check()
        if not health.healthy:
            # The engine has no market connection of its own: a stale feed and
            # a dead observation runner are the same underlying fact.
            self._pause("feed_stale")
        elif not self.failures.should_auto_pause():
            self._resume()

        positions = self.store.open_positions()

        # Reconciliation first: everything below decides against broker truth.
        run_step(
            STEP_RECONCILE,
            lambda: self._drive_open_orders(positions),
            tracker=self.failures,
            on_escalate=self._on_escalate,
        )

        positions = self.store.open_positions()
        run_step(
            STEP_PROTECTION,
            lambda: self._ensure_protection(positions),
            tracker=self.failures,
            on_escalate=self._on_escalate,
        )

        run_step(
            STEP_COMMANDS,
            self._handle_commands,
            tracker=self.failures,
            on_escalate=self._on_escalate,
        )

        self._check_daily_loss()
        if self.shutdown_reason is None and engine_clock.eod_squareoff_due(now):
            self._begin_shutdown(CloseReason.EOD_SQUAREOFF)

        if self.shutdown_reason is not None:
            run_step(
                STEP_SQUAREOFF,
                self._advance_shutdown,
                tracker=self.failures,
                on_escalate=self._on_escalate,
            )
            # No new entries once the day is ending.
            return

        run_step(
            STEP_INGEST,
            self.ingest_triggers,
            tracker=self.failures,
            on_escalate=self._on_escalate,
        )
        # Trailing intentionally removed for now -- being rebuilt from scratch.
        # See Reference/execution_engine_rebuild_notes.md. Positions stay in
        # PROTECTED with a static stop until trailing is redesigned.

    @property
    def entries_allowed(self) -> bool:
        return (
            not self.entries_paused
            and not self.entries_stopped
            and self.shutdown_reason is None
            and engine_clock.new_entries_allowed(self.now_fn())
        )

    def _pause(self, reason: str) -> None:
        self.entries_paused = True
        self.pause_reason = reason

    def _resume(self) -> None:
        if self.breached:
            return  # a breach is permanent for the session
        self.entries_paused = False
        self.pause_reason = None

    def _on_escalate(self, step: str, detail: str) -> None:
        notice = f"{step}: {detail}"
        self.escalation_notices.append(notice)
        self.store.append_event(
            "__engine__", "step_escalated", {"step": step, "detail": detail}
        )
        if self.failures.should_auto_pause():
            self._pause(f"step_escalated:{step}")

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
        if self.entries_paused or self.entries_stopped or self.shutdown_reason is not None:
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
        if outcome.result is EntryResult.FILLED and outcome.position is not None:
            self._act_on_fill(outcome.position, outcome.verdict)
        return outcome

    def _act_on_fill(
        self, position: Position, verdict: Optional[FillVerdict]
    ) -> None:
        """Protect it, or -- past 1.5x the cap -- skip the stop and flatten."""
        if verdict is FillVerdict.FLATTEN_ABNORMAL_SLIPPAGE:
            flatten(
                position,
                reason=CloseReason.ABNORMAL_SLIPPAGE_FLATTEN,
                broker=self.broker,
                store=self.store,
            )
            position.extra["manual_review"] = "abnormal_slippage"
            self.store.save(position)
            return
        ensure_protected(position, broker=self.broker, store=self.store)

    # ------------------------------------------------------------------
    # Entry gates, rechecked immediately before sending
    # ------------------------------------------------------------------

    def _entry_gate(self, candidate: TriggerCandidate) -> Optional[str]:
        """Time has passed since the trigger fired; re-verify everything."""
        if self.entries_paused:
            return self.pause_reason or "entries_paused"
        if self.entries_stopped:
            return "entries_stopped"
        if self.shutdown_reason is not None:
            return f"session_ending:{self.shutdown_reason.value}"
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
    # Reconciliation
    # ------------------------------------------------------------------

    def _drive_open_orders(self, positions: List[Position]) -> None:
        """Ask the broker what is true and make local records match it.

        One batched read for the whole tick, then a pure decision per position.
        """
        if not positions:
            self.live_pnl = {}
            return

        truth = fetch_broker_truth(self.broker, symbols_of(positions))
        self.last_truth = truth
        if not truth.ok:
            # Never act on a read that failed: a broker outage must not look
            # like every position having gone flat.
            self.store.append_event(
                "__engine__", "broker_truth_unavailable", {"reason": truth.reason}
            )
            return

        self.live_pnl = {
            p.trade_id: truth.pnl.get(p.candidate.tradingsymbol) for p in positions
        }

        for position in positions:
            decision = reconcile(position, truth)
            self._watch_waiting_entry(position, decision)
            if decision.is_noop:
                continue
            self._apply_reconciliation(position, decision, truth)

    def _watch_waiting_entry(self, position: Position, decision) -> None:
        """Escalate an entry left waiting at the broker for too long. Alert only.

        Two waits are watched, each on its own 10s clock:

        * still working with nothing filled -- a MARKET order that has not
          executed at all within seconds means something is wrong at the
          broker or exchange;
        * partly filled -- worse, because the shares already bought have no
          stop until the fill is final.

        Moving from one wait to the other restarts the clock under the new
        reason, so a partial fill after an unfilled alert is surfaced too.
        Nothing is cancelled or placed early because of any of this.
        """
        key = f"{ENTRY_STALL_ESCALATION_STEP}:{position.trade_id}"
        event = ENTRY_STALL_EVENTS.get(str(decision.reason))
        if event is None:
            # Final, cancelled, not visible, or not an entry: clear any alert.
            self.failures.escalated.pop(key, None)
            return

        now = self.now_fn()
        watch = dict(position.extra.get("entry_wait") or {})
        seen = parse_timestamp_text(watch.get("seen_at")) if watch else None
        if seen is None or watch.get("reason") != decision.reason:
            position.extra["entry_wait"] = {
                "reason": decision.reason,
                "seen_at": now.isoformat(),
                "escalated": False,
            }
            self.store.save(position)
            return

        waited = (now - seen).total_seconds()
        if waited <= ENTRY_STALL_ESCALATE_SECONDS or watch.get("escalated"):
            return

        filled = int(decision.fill_qty or 0)
        ordered = int(position.qty or 0)
        watch["escalated"] = True
        position.extra["entry_wait"] = watch
        self.store.save_with_event(
            position,
            event,
            {"filled": filled, "quantity": ordered, "seconds": round(waited, 1)},
        )
        symbol = position.candidate.tradingsymbol
        if decision.reason == ENTRY_PARTIAL_WAIT_REASON:
            detail = (
                f"{symbol} entry partly filled {filled}/{ordered} for "
                f"{waited:.0f}s; filled shares have no stop yet"
            )
        else:
            detail = (
                f"{symbol} entry still working at Kite, 0/{ordered} filled "
                f"for {waited:.0f}s; nothing held yet"
            )
        # Shown with the other escalations on the heartbeat and the desk.
        self.failures.escalated[key] = detail
        self._on_escalate(key, detail)

    def _record_stock_day(self, position: Position, truth: BrokerTruth) -> None:
        """When a stock goes fully flat, pin Kite's own day P&L for it here.

        Kite is the source of truth: the desk shows this number as the stock's
        day total on this (its latest) closed row and in Total Realised P&L.
        It is read from the same positions payload that just showed the stock
        flat, so it already includes the exit that flattened it.

        Our own per-trade figures are compared against it. A difference over
        PNL_MISMATCH_TOLERANCE_RUPEES is flagged on the row and logged, for
        information only; it never replaces Kite's number.
        """
        symbol = position.candidate.tradingsymbol
        if truth.net_for(symbol) != 0:
            return
        kite_pnl = truth.pnl.get(symbol)
        if kite_pnl is None:
            return
        ours_values = [
            p.realised_pnl
            for p in self.store.closed_today(self.session_date)
            if p.candidate.tradingsymbol == symbol
        ]
        ours = round(sum(v for v in ours_values if v is not None), 2)
        diff = round(float(kite_pnl) - ours, 2)
        mismatch = abs(diff) > PNL_MISMATCH_TOLERANCE_RUPEES
        position.extra[STOCK_DAY_KEY] = {
            "kite_pnl": round(float(kite_pnl), 2),
            "ours": ours,
            "diff": diff,
            "mismatch": mismatch,
            "unattributed_trades": sum(1 for v in ours_values if v is None),
        }
        if mismatch:
            self.store.save_with_event(
                position,
                "realised_pnl_mismatch",
                {"symbol": symbol, "kite_pnl": kite_pnl, "ours": ours, "diff": diff},
            )
        else:
            self.store.save(position)

    def _claimed_exit_order_ids(self, position: Position) -> set:
        """Exit orders already booked by earlier closed trades in this stock."""
        claimed: set = set()
        symbol = position.candidate.tradingsymbol
        for closed in self.store.closed_today(self.session_date):
            if closed.trade_id == position.trade_id:
                continue
            if closed.candidate.tradingsymbol != symbol:
                continue
            claimed.update(str(i) for i in closed.extra.get(EXIT_ORDER_IDS_KEY) or [])
            if closed.extra.get("closing_order_id"):
                claimed.add(str(closed.extra["closing_order_id"]))
        return claimed

    def _apply_reconciliation(self, position: Position, decision, truth: BrokerTruth) -> None:
        if decision.has(ReconcileAction.FINALIZE_EXIT):
            entry_order = truth.order(position.entry_order_id)
            exits = trade_exit_orders(
                position,
                truth.orders_by_id.values(),
                claimed=self._claimed_exit_order_ids(position),
                entry_order=entry_order,
            )
            closing = identify_closing_order(position, exits)
            realised = kite_realised_pnl(
                position, entry_order=entry_order, exit_orders=exits
            )
            finalize_exit(
                position,
                closing=closing,
                store=self.store,
                fallback_qty=int(position.qty or 0),
                realised=realised,
            )
            self._record_stock_day(position, truth)
            if realised.over_exit_qty > 0:
                self.store.append_event(
                    position.trade_id,
                    "over_exit_detected",
                    {
                        "over_exit_qty": realised.over_exit_qty,
                        "entry_qty": realised.entry_qty,
                        "exit_order_ids": realised.exit_order_ids,
                    },
                )
            return

        if decision.has(ReconcileAction.APPLY_ENTRY_FILL):
            cap = float(position.extra.get("risk_cap_rupees") or 0.0) or self.risk_policy.per_trade_cap(
                vwap_limited=position.candidate.vwap_classification == "LIMITED"
            )
            verdict = apply_entry_fill(
                position,
                fill_price=float(decision.fill_price),
                filled_qty=int(decision.fill_qty),
                risk_cap_rupees=cap,
            )
            if decision.order_id:
                position.entry_order_id = decision.order_id
            self.store.save_with_event(
                position,
                "entry_filled",
                {
                    "fill_price": decision.fill_price,
                    "filled_qty": decision.fill_qty,
                    "risk_taken_rupees": position.risk_taken_rupees,
                    "verdict": verdict.value,
                    "source": "reconciliation",
                },
            )
            self._act_on_fill(position, verdict)
            return

        if decision.has(ReconcileAction.ENTRY_REJECTED):
            position.extra["reject_reason"] = decision.reason
            transition(position, ExecutionState.REJECTED)
            self.store.save_with_event(
                position, "entry_rejected", {"reason": decision.reason}
            )
            return

        if decision.has(ReconcileAction.ENTRY_CANCELLED):
            position.extra["cancel_reason"] = decision.reason
            transition(position, ExecutionState.CANCELLED)
            self.store.save_with_event(
                position, "cancelled", {"reason": decision.reason}
            )
            return

        changed = False
        if decision.has(ReconcileAction.ADOPT_QTY):
            position.extra["qty_adopted_from_broker"] = {
                "was": position.qty,
                "now": decision.broker_qty,
            }
            position.qty = int(decision.broker_qty or 0)
            changed = True

        if decision.has(ReconcileAction.ADOPT_STOP_PRICE):
            position.extra["stop_adopted_from_broker"] = {
                "was": position.stop_price,
                "now": decision.broker_stop_price,
            }
            position.stop_price = decision.broker_stop_price
            changed = True

        if decision.has(ReconcileAction.REPLACE_STOP):
            # The stop is genuinely gone, so the position genuinely is not
            # protected. Say so, and let the protection step re-place it.
            position.stop_order_id = None
            if position.state in (ExecutionState.PROTECTED, ExecutionState.TRAILING):
                transition(position, ExecutionState.ENTERED)
            self.store.save_with_event(
                position, "stop_missing_at_broker", {"qty": position.qty}
            )
            return

        if changed:
            self.store.save_with_event(
                position,
                "reconciled",
                {
                    "qty": position.qty,
                    "stop_price": position.stop_price,
                    "actions": [a.value for a in decision.actions],
                },
            )

    def _ensure_protection(self, positions: List[Position]) -> None:
        if not engine_clock.protection_retry_allowed(self.now_fn()):
            return
        for position in positions:
            if position.state != ExecutionState.ENTERED:
                continue
            ensure_protected(position, broker=self.broker, store=self.store)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def _handle_commands(self) -> None:
        if self.command_source is None:
            return
        for command in self.command_source():
            self.apply_command(command)

    def apply_command(self, command) -> None:
        """Apply one command. The queue marks it applied or rejected."""
        from engine_commands import CommandKind

        kind = command.kind
        if kind is CommandKind.STOP:
            self.entries_stopped = True
            command.applied({"entries_stopped": True})
            return

        if kind is CommandKind.START:
            if not engine_clock.new_entries_allowed(self.now_fn()):
                # Same window rule as the initial start, applied to every
                # click -- not just the first one of the day.
                command.rejected("past_entry_cutoff")
                return
            self.entries_stopped = False
            command.applied({"entries_stopped": False})
            return

        if kind is CommandKind.CLOSE_POSITION:
            position = self._find_open(command.trade_id)
            if position is None:
                command.rejected("position_not_open")
                return
            outcome = flatten(
                position,
                reason=CloseReason.MANUAL_CLOSE,
                broker=self.broker,
                store=self.store,
            )
            if outcome.submitted:
                command.applied({"order_id": outcome.order_id})
            else:
                command.rejected(str(outcome.reason))
            return

        if kind is CommandKind.KILL_ALL:
            self._begin_shutdown(CloseReason.KILL_ALL)
            command.applied({"shutdown": CloseReason.KILL_ALL.value})
            return

        command.rejected("unhandled_command")

    def _find_open(self, trade_id: Optional[str]) -> Optional[Position]:
        if not trade_id:
            return None
        for position in self.store.open_positions():
            if position.trade_id == trade_id:
                return position
        return None

    # ------------------------------------------------------------------
    # Daily loss and shutdown
    # ------------------------------------------------------------------

    def _check_daily_loss(self) -> None:
        closed = self.store.closed_today(self.session_date)
        check = self.risk_policy.check_daily_loss(closed)
        self.realised_loss_today = check.closed_loss_rupees
        if not check.breached:
            return
        if not self.breached:
            self.breached = True
            self.store.append_event(
                "__engine__",
                "daily_loss_breached",
                {"closed_loss": check.closed_loss_rupees, "cap": check.cap_rupees},
            )
        # Permanent for the session: realised losses cannot decrease.
        self._pause("daily_loss_breached")
        if self.shutdown_reason is None:
            self._begin_shutdown(CloseReason.DAILY_LOSS_BREACH)

    def _begin_shutdown(self, reason: CloseReason) -> None:
        self.shutdown_reason = reason
        self.shutdown_complete = False
        self._pause(f"shutdown:{reason.value}")
        self.store.append_event(
            "__engine__", "shutdown_started", {"reason": reason.value}
        )

    def _advance_shutdown(self) -> SquareoffProgress:
        """Close everything, every tick, until nothing is left.

        Idempotent by construction: the set of open positions only shrinks, and
        a CLOSED position can never reappear in it.
        """
        assert self.shutdown_reason is not None
        positions = self.store.open_positions()
        progress = squareoff_all(
            positions, reason=self.shutdown_reason, broker=self.broker, store=self.store
        )
        if progress.complete and not self.store.open_positions():
            if not self.shutdown_complete:
                self.shutdown_complete = True
                self.store.append_event(
                    "__engine__",
                    "shutdown_complete",
                    {"reason": self.shutdown_reason.value},
                )
        return progress

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def unprotected_count(self) -> int:
        return sum(
            1
            for p in self.store.open_positions()
            if p.state in (ExecutionState.ENTERED, ExecutionState.ENTRY_SUBMITTED)
        )

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
