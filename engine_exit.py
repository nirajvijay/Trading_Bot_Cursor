"""What actually makes a position stop being open and become CLOSED.

Two ways it happens:

* **Passive** — the standing stop fires on its own at the broker. We are just
  watching, and one tick reconciliation notices the real quantity has dropped
  to zero without us having sent anything.
* **Active** — we decide to exit and send a fresh order. Covers EOD
  square-off, a daily-loss breach, a manual close, and the abnormal-slippage
  flatten from entry submission. All four reuse the one flatten action below.

**Attribution is looked up, never assumed.** An earlier version of this design
assumed "no exit order sent by us, so it must have been our own stop." That is
wrong: a human can place a completely different order in Kite that closes the
position, unrelated to our stop or to anything we sent. So when a position goes
flat we find the order that actually executed the close and tag the reason from
what we find.

**Realized P&L always comes from real fill prices**, on both sides. The price
we recorded for a stop is only ever a target — a triggered stop executes as a
market order from that point, so on a fast move it can fill materially worse
than its trigger. Using the level we set instead of the fill we got would
silently misreport every violent exit.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Iterable, List, Optional

from engine_entry import broker_tag_for, exit_transaction_type_for
from engine_orders import transition
from engine_types import ExecutionState, Position
from trading_engine_broker import parse_timestamp_text
from trading_engine_types import (
    STOP_ORDER_TYPES,
    BrokerOrder,
    broker_order_filled_qty,
)


class CloseReason(str, Enum):
    STOP_HIT = "stop_hit"
    EOD_SQUAREOFF = "eod_squareoff"
    DAILY_LOSS_BREACH = "daily_loss_breach"
    MANUAL_CLOSE = "manual_close"
    ABNORMAL_SLIPPAGE_FLATTEN = "abnormal_slippage_flatten"
    KILL_ALL = "kill_all"
    # A closure we did not initiate and cannot attribute to any order of ours.
    MANUAL_BROKER_INTERVENTION = "manual_broker_intervention"
    # Flat at the broker, but no completed order explains it. Recorded honestly
    # rather than guessed at.
    UNATTRIBUTED = "unattributed"


# Reasons we ourselves initiate, stashed on the position when we send the exit
# so attribution can name the right one later.
ACTIVE_EXIT_REASONS = frozenset(
    {
        CloseReason.EOD_SQUAREOFF,
        CloseReason.DAILY_LOSS_BREACH,
        CloseReason.MANUAL_CLOSE,
        CloseReason.ABNORMAL_SLIPPAGE_FLATTEN,
        CloseReason.KILL_ALL,
    }
)

EXIT_REASON_KEY = "exit_reason"
FLATTEN_TAG_SUFFIX = "-x"


@dataclass(frozen=True)
class ClosingOrder:
    """The order that actually closed the position, and why."""

    order: BrokerOrder
    reason: CloseReason

    @property
    def fill_price(self) -> Optional[float]:
        price = self.order.average_price
        return None if price is None else float(price)

    @property
    def fill_qty(self) -> int:
        return broker_order_filled_qty(self.order)


@dataclass(frozen=True)
class FlattenOutcome:
    submitted: bool
    reason: Optional[str] = None
    order_id: Optional[str] = None


def realised_pnl(
    *, direction: str, qty: int, entry_fill: float, exit_fill: float
) -> float:
    """Booked P&L from the two real fills.

    Price risk only, consistent with how the risk cap is defined: brokerage and
    slippage are real additional costs, accounted for separately, never folded
    into this number.
    """
    if str(direction).upper() == "UP":
        return int(qty) * (float(exit_fill) - float(entry_fill))
    return int(qty) * (float(entry_fill) - float(exit_fill))


def flatten_tag_for(position: Position) -> str:
    """A tag distinct from the entry's, so entry reconciliation can never
    mistake our exit order for the entry (a BrokerPort contract requirement).

    Built from broker_tag_for's bounded, Kite-safe form (not the raw
    trade_id, which can run well past the 20-char tag limit) plus the
    distinguishing suffix.
    """
    return f"{broker_tag_for(position.trade_id)}{FLATTEN_TAG_SUFFIX}"


def exit_side_orders(
    position: Position, orders: Iterable[BrokerOrder]
) -> List[BrokerOrder]:
    """Completed orders on this symbol that moved size in the closing direction.

    Drawn from the order book already fetched for this tick, so identifying a
    close costs no additional broker call.
    """
    symbol = position.candidate.tradingsymbol
    closing_side = exit_transaction_type_for(position.candidate.direction)
    out: List[BrokerOrder] = []
    for order in orders:
        if str(order.tradingsymbol) != symbol:
            continue
        if str(order.transaction_type).upper() != closing_side:
            continue
        if broker_order_filled_qty(order) <= 0:
            continue
        out.append(order)
    return out


def attribute(position: Position, order: BrokerOrder) -> CloseReason:
    """Name the reason for one closing order, from what we recognize."""
    order_id = str(order.order_id)
    if position.stop_order_id and order_id == str(position.stop_order_id):
        return CloseReason.STOP_HIT
    if position.exit_order_id and order_id == str(position.exit_order_id):
        recorded = position.extra.get(EXIT_REASON_KEY)
        if recorded:
            try:
                reason = CloseReason(str(recorded))
            except ValueError:
                return CloseReason.UNATTRIBUTED
            if reason in ACTIVE_EXIT_REASONS:
                return reason
        return CloseReason.UNATTRIBUTED
    if str(order.tag or "") == flatten_tag_for(position):
        recorded = position.extra.get(EXIT_REASON_KEY)
        if recorded:
            try:
                return CloseReason(str(recorded))
            except ValueError:
                return CloseReason.UNATTRIBUTED
    if str(order.order_type) in STOP_ORDER_TYPES and str(order.tag or "") == broker_tag_for(position.trade_id):
        # Our own stop, found by tag after its id was lost.
        return CloseReason.STOP_HIT
    # Nothing we recognize at all.
    return CloseReason.MANUAL_BROKER_INTERVENTION


def identify_closing_order(
    position: Position, orders: Iterable[BrokerOrder]
) -> Optional[ClosingOrder]:
    """Find the order that actually closed this position.

    Builds every plausible candidate with its attributed reason, then picks
    one. Returns None when nothing completed explains the close, which
    finalize_exit records as UNATTRIBUTED rather than inventing a cause.
    """
    candidates = [
        ClosingOrder(order=order, reason=attribute(position, order))
        for order in exit_side_orders(position, orders)
    ]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    # More than one completed closing order means the position was very likely
    # exited twice (a stop firing in the gap between our cancel request and its
    # confirmation), which leaves accidental opposite exposure. Record that it
    # happened so it reaches the closed event and a human, rather than being
    # silently resolved away by picking one.
    position.extra["closing_order_contenders"] = [
        {
            "order_id": str(c.order.order_id),
            "reason": c.reason.value,
            "fill_qty": c.fill_qty,
            "fill_price": c.fill_price,
        }
        for c in candidates
    ]
    return select_closing_order(position, candidates)


def select_closing_order(
    position: Position, candidates: List[ClosingOrder]
) -> ClosingOrder:
    """Choose which of several completed closing orders to book the trade against.

    Reached when more than one order on this symbol could explain the close.
    The important real case: we sent a flatten and the standing stop also
    filled. The cancel-before-flatten rule exists to prevent that, but a stop
    can trigger in the gap between the cancel request and its confirmation, so
    this has to resolve sanely rather than pick arbitrarily.

    Precedence, in order:

    1. **Earliest fill wins.** Whichever order executed first is the one that
       actually took the position flat; anything that filled after that opened
       a *new* opposite exposure rather than closing this trade. Booking the
       later fill would misreport both the P&L and the reason.
    2. **A timestamp we have beats one we do not.** An order with no broker
       timestamp cannot claim to have been first, so it sorts behind every
       timestamped candidate instead of winning by accident.
    3. **A full-size fill beats a partial one.** Matching `position.qty`
       explains a complete close; a smaller fill explains only part of it.
    4. **An unrecognized order beats one of ours.** Only reachable when
       everything above ties. Consistent with the attribution rule this module
       exists for: a closure we did not initiate is the fact a human most needs
       surfaced, and quietly tagging it as our own stop would bury it.
    """
    def key(candidate: ClosingOrder):
        stamp = parse_timestamp_text(candidate.order.order_timestamp)
        return (
            0 if stamp is not None else 1,                     # (2)
            stamp.timestamp() if stamp is not None else 0.0,   # (1)
            0 if candidate.fill_qty >= int(position.qty or 0) else 1,  # (3)
            0 if candidate.reason is CloseReason.MANUAL_BROKER_INTERVENTION else 1,  # (4)
        )

    return sorted(candidates, key=key)[0]


def finalize_exit(
    position: Position,
    *,
    closing: Optional[ClosingOrder],
    store,
    fallback_qty: Optional[int] = None,
) -> CloseReason:
    """The unified finalization: identical for every path and every reason.

    1. Read the real fill price and quantity from whichever order executed the
       close.
    2. Compute realized P&L from the real entry fill and the real exit fill.
    3. Write a closed event with the real numbers and the reason.
    4. Set state CLOSED and realised_pnl -- in the same transaction as (3).
    """
    reason = closing.reason if closing is not None else CloseReason.UNATTRIBUTED
    exit_fill = closing.fill_price if closing is not None else None
    exit_qty = closing.fill_qty if closing is not None else (fallback_qty or 0)
    closing_order_id = str(closing.order.order_id) if closing is not None else None

    pnl: Optional[float] = None
    if exit_fill is not None and position.entry_price is not None:
        qty = exit_qty or int(position.qty or 0)
        pnl = realised_pnl(
            direction=position.candidate.direction,
            qty=qty,
            entry_fill=float(position.entry_price),
            exit_fill=float(exit_fill),
        )

    position.realised_pnl = pnl
    position.extra["close_reason"] = reason.value
    if closing_order_id:
        position.extra["closing_order_id"] = closing_order_id
    transition(position, ExecutionState.CLOSED)
    store.save_with_event(
        position,
        "closed",
        {
            "reason": reason.value,
            "closing_order_id": closing_order_id,
            "exit_fill": exit_fill,
            "exit_qty": exit_qty,
            "entry_fill": position.entry_price,
            "realised_pnl": pnl,
        },
    )
    return reason


def cancel_stop(position: Position, *, broker, store) -> FlattenOutcome:
    """Cancel the live stop, and report only what is actually confirmed.

    Sending a flatten while a stop is still live risks both executing, which
    would sell twice and leave the account accidentally net short. So the
    flatten waits on this. Same three-way discipline as every other broker
    call: an ambiguous cancel is not treated as a success.
    """
    if not position.stop_order_id:
        return FlattenOutcome(True, reason="no_stop_to_cancel")
    try:
        result = broker.cancel_order(str(position.stop_order_id))
    except Exception as exc:  # noqa: BLE001
        reason = str(exc) or exc.__class__.__name__
        store.append_event(
            position.trade_id, "stop_cancel_ambiguous", {"reason": reason}
        )
        return FlattenOutcome(False, reason=f"cancel_ambiguous: {reason}")

    if result is None:
        # No confirmation to read. Do not proceed on an assumption; the next
        # tick's reconciliation resolves whether the stop is really gone.
        store.append_event(
            position.trade_id, "stop_cancel_ambiguous", {"reason": "no_response"}
        )
        return FlattenOutcome(False, reason="cancel_ambiguous: no_response")

    status = str(result.status).upper()
    if status not in {"CANCELLED", "REJECTED", "COMPLETE"}:
        store.append_event(
            position.trade_id, "stop_cancel_ambiguous", {"status": status}
        )
        return FlattenOutcome(False, reason=f"cancel_unconfirmed: {status}")

    store.append_event(
        position.trade_id, "stop_cancelled", {"order_id": position.stop_order_id, "status": status}
    )
    return FlattenOutcome(True, order_id=str(position.stop_order_id))


def flatten(
    position: Position, *, reason: CloseReason, broker, store
) -> FlattenOutcome:
    """Actively exit a position: cancel the stop, then market out.

    The single action behind all four active-exit triggers — abnormal
    slippage, EOD square-off, daily-loss breach, and a manual close or
    kill-all. Nothing about it varies by trigger except the reason tag.
    """
    if position.state in (
        ExecutionState.CLOSED,
        ExecutionState.REJECTED,
        ExecutionState.CANCELLED,
    ):
        return FlattenOutcome(False, reason="already_terminal")
    if int(position.qty or 0) <= 0:
        return FlattenOutcome(False, reason="no_quantity")

    cancelled = cancel_stop(position, broker=broker, store=store)
    if not cancelled.submitted:
        # Try again next tick rather than risk a double exit.
        return FlattenOutcome(False, reason=cancelled.reason)

    candidate = position.candidate
    try:
        order = broker.flatten_mis(
            tradingsymbol=candidate.tradingsymbol,
            transaction_type=exit_transaction_type_for(candidate.direction),
            quantity=int(position.qty),
            tag=flatten_tag_for(position),
        )
    except Exception as exc:  # noqa: BLE001
        message = str(exc) or exc.__class__.__name__
        store.append_event(position.trade_id, "flatten_failed", {"reason": message})
        return FlattenOutcome(False, reason=message)

    if order is None:
        store.append_event(
            position.trade_id, "flatten_failed", {"reason": "broker_returned_no_order"}
        )
        return FlattenOutcome(False, reason="broker_returned_no_order")

    position.exit_order_id = str(order.order_id)
    position.stop_order_id = None  # cancelled above; no longer live
    position.extra[EXIT_REASON_KEY] = reason.value
    if position.state != ExecutionState.EXIT_SUBMITTED:
        transition(position, ExecutionState.EXIT_SUBMITTED)
    store.save_with_event(
        position,
        "exit_submitted",
        {"reason": reason.value, "order_id": order.order_id, "qty": position.qty},
    )
    return FlattenOutcome(True, order_id=str(order.order_id))
