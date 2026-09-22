"""Close everything, then confirm it, then let the caller stop.

Three separate triggers converge on this one sequence — a daily-loss breach,
the 15:15 EOD cutoff, and a manual Kill-It-All-Now. Mechanically they are the
same thing with a different reason tag: three doors into one room.

**Naturally idempotent, with no flag or counter.** The action is always "close
everything currently open", recomputed fresh every tick. A position that
reaches CLOSED simply stops appearing in that set — permanently, since it
cannot reopen — so repeating the check every tick does progressively less work
until there is nothing left to do.

**Auto-stop waits for confirmation, not for detection.** At the moment of a
breach, positions are still open and mid-closing; stopping the loop right then
would leave them being squared off with nobody watching to confirm completion.
So: begin closing immediately, keep running and watching, and only once every
position is confirmed CLOSED is there no useful work left for the day.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from engine_exit import CloseReason, flatten
from engine_orders import transition
from engine_reconcile import HOLDING_STATES
from engine_risk import DailyLossCheck, RiskPolicy
from engine_store import realised_loss_rupees
from engine_types import ExecutionState, Position

# Nothing has filled yet, so there is no size to flatten — these get cancelled.
UNFILLED_STATES = (ExecutionState.PENDING_ENTRY, ExecutionState.ENTRY_SUBMITTED)


@dataclass(frozen=True)
class SquareoffProgress:
    """How far "close everything" has got, as of this tick."""

    reason: CloseReason
    considered: int = 0
    exits_submitted: int = 0
    cancelled: int = 0
    awaiting_confirmation: int = 0
    blocked: List[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.blocked is None:
            object.__setattr__(self, "blocked", [])

    @property
    def complete(self) -> bool:
        """True only when nothing is left open and nothing is still in flight."""
        return self.awaiting_confirmation == 0


def check_daily_loss(
    risk_policy: RiskPolicy, closed_positions: Sequence[Position]
) -> DailyLossCheck:
    """Breach is decided on realized losses only.

    Deliberately not a forward-looking "what if every open position hits its
    stop" projection. Force-closing positions that have not hit their own stop
    is drastic, and should only ever fire on something factual and already
    happened. The forward-looking concern belongs to entry-time sizing, which
    already refuses new entries as committed risk approaches the cap.

    Realized losses cannot decrease, so a breach is permanent for the session —
    there is no un-breaching, and entries stay off for the rest of the day.
    """
    return risk_policy.check_daily_loss(closed_positions)


def realised_loss_today(closed_positions: Sequence[Position]) -> float:
    return realised_loss_rupees(closed_positions)


def squareoff_all(
    positions: Sequence[Position],
    *,
    reason: CloseReason,
    broker,
    store,
) -> SquareoffProgress:
    """Close every position in `positions`. Safe to call every tick.

    Every open position, no exceptions — including ones currently sitting
    green. The tempting "let the winner ride" exception is deliberately not
    implemented: the point of a hard cap is to stop taking risk of any kind the
    moment it is crossed, not to selectively keep the risk that happens to feel
    comfortable right now.
    """
    considered = 0
    exits_submitted = 0
    cancelled = 0
    awaiting = 0
    blocked: List[str] = []

    for position in positions:
        if position.state in (
            ExecutionState.CLOSED,
            ExecutionState.REJECTED,
            ExecutionState.CANCELLED,
        ):
            continue
        considered += 1

        if position.state == ExecutionState.EXIT_SUBMITTED:
            # Already in flight; reconciliation confirms it went flat.
            awaiting += 1
            continue

        if position.state in UNFILLED_STATES:
            if _cancel_unfilled(position, reason=reason, broker=broker, store=store):
                cancelled += 1
            else:
                awaiting += 1
                blocked.append(f"{position.trade_id}:cancel_unconfirmed")
            continue

        if position.state in HOLDING_STATES:
            outcome = flatten(position, reason=reason, broker=broker, store=store)
            if outcome.submitted:
                exits_submitted += 1
                awaiting += 1
            else:
                awaiting += 1
                blocked.append(f"{position.trade_id}:{outcome.reason}")
            continue

        awaiting += 1

    return SquareoffProgress(
        reason=reason,
        considered=considered,
        exits_submitted=exits_submitted,
        cancelled=cancelled,
        awaiting_confirmation=awaiting,
        blocked=blocked,
    )


def _cancel_unfilled(
    position: Position, *, reason: CloseReason, broker, store
) -> bool:
    """Withdraw an entry that has not filled. Nothing to flatten."""
    if position.state == ExecutionState.PENDING_ENTRY:
        # Intent was written but nothing was ever sent, so there is nothing at
        # the broker to withdraw.
        position.extra["cancel_reason"] = reason.value
        transition(position, ExecutionState.CANCELLED)
        store.save_with_event(position, "cancelled", {"reason": reason.value})
        return True

    if not position.entry_order_id:
        position.extra["cancel_reason"] = reason.value
        transition(position, ExecutionState.CANCELLED)
        store.save_with_event(position, "cancelled", {"reason": reason.value})
        return True

    try:
        result = broker.cancel_order(str(position.entry_order_id))
    except Exception as exc:  # noqa: BLE001
        store.append_event(
            position.trade_id,
            "entry_cancel_ambiguous",
            {"reason": str(exc) or exc.__class__.__name__},
        )
        return False

    if result is None:
        store.append_event(
            position.trade_id, "entry_cancel_ambiguous", {"reason": "no_response"}
        )
        return False

    status = str(result.status).upper()
    if status == "COMPLETE":
        # It filled while we were cancelling. Do not mark it cancelled: the
        # next tick's reconciliation will see the fill and protect or close it.
        store.append_event(
            position.trade_id, "entry_cancel_raced_fill", {"order_id": result.order_id}
        )
        return False
    if status not in {"CANCELLED", "REJECTED"}:
        store.append_event(
            position.trade_id, "entry_cancel_ambiguous", {"status": status}
        )
        return False

    position.extra["cancel_reason"] = reason.value
    transition(position, ExecutionState.CANCELLED)
    store.save_with_event(
        position, "cancelled", {"reason": reason.value, "order_id": result.order_id}
    )
    return True
