"""Entry submission: the nine-step sequence for an ACCEPT/LIMITED trigger.

"Submit the entry order" is not one step. It is a chain of decisions about
order type, crash safety, and how to react to slippage and uncertain broker
responses. LIMITED-tier triggers use this identical path — the only difference
is which risk cap enters step 2, which automatically scales step 8's
abnormal-slippage line down with it.

Deliberately not implemented, each rejected during design:

* Moving the stop to fit the risk cap after bad slippage. A stop dragged in to
  make the arithmetic work has no relationship to market structure any more and
  is near-certain to be hit by ordinary noise — it converts a slippage problem
  into a guaranteed meaningless loss.
* Trimming quantity to enforce the cap exactly on ordinary overshoots. Adds an
  order that must confirm before the stop can be placed at all, lengthening the
  window where a filled position sits completely unprotected — paid on every
  trade to correct overshoots that are usually small.
* LIMIT entries (for any tier). MARKET was chosen specifically so no
  "reprice if the world moved" logic is ever needed.
* Bumping an existing open position to free capital for a better later setup.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional, Protocol

from engine_orders import transition
from engine_sizing import SizingPolicy
from engine_types import ExecutionState, Position, SizeDecision, TriggerCandidate
from trading_engine_broker import (
    EntryAcceptedVisibilityUnknown,
    LIVE_ORDERS_DISABLED_REASON,
)
from engine_stop import structural_stop_price
from trading_engine_types import BrokerOrder, broker_order_filled_qty

# Real risk above this multiple of the intended cap is abnormal: skip the stop
# and flatten immediately. 1.5x of Rs 900 is Rs 1,350; of Rs 450 it is Rs 675,
# so the line scales with the tier on its own. Tunable if paper evidence says
# it is too strict or too loose.
ABNORMAL_SLIPPAGE_MULTIPLE = 1.5

# Substrings in a broker error that guarantee the order was never created.
# Anything not matched here is treated as genuinely ambiguous, because assuming
# "never created" wrongly is how a real position becomes invisible.
DEFINITE_REJECTION_MARKERS = (
    "insufficient",
    "margin",
    "invalid quantity",
    "invalid order",
    "quantity",
    "rms",
    "blocked",
    "not allowed",
    "freeze",
    LIVE_ORDERS_DISABLED_REASON,
)


class ResponseKind(str, Enum):
    """The three-way classification. The dividing line is "do we know?"."""

    SUCCESS = "success"
    REJECTED = "rejected"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class PlaceResponse:
    kind: ResponseKind
    order: Optional[BrokerOrder] = None
    reason: Optional[str] = None


class FillVerdict(str, Enum):
    """What step 8 concluded about a fill we now own."""

    PROTECT = "protect"
    FLATTEN_ABNORMAL_SLIPPAGE = "flatten_abnormal_slippage"


class EntryResult(str, Enum):
    SKIPPED = "skipped"            # a gate or sizing refused; nothing sent
    REJECTED = "rejected"          # broker definitively refused
    SUBMITTED = "submitted"        # sent, fill not visible yet
    FILLED = "filled"              # sent and filled; see outcome.verdict
    RETRY_NEXT_TICK = "retry"      # ambiguous, resolved to "never reached broker"


@dataclass(frozen=True)
class EntryOutcome:
    """What submit_entry did."""

    result: EntryResult
    position: Optional[Position] = None
    reason: Optional[str] = None
    verdict: Optional[FillVerdict] = None


class EntryGate(Protocol):
    def __call__(self, candidate: TriggerCandidate) -> Optional[str]:
        """Return a refusal reason, or None to allow."""
        ...


# ----------------------------------------------------------------------
# Step 1: the stop price
# ----------------------------------------------------------------------


def compute_stop_price(candidate: TriggerCandidate) -> Optional[float]:
    """Structural: one tick below the swing low (long) or above the swing high
    (short). Fixed once, from market structure. Never recalculated off the
    entry price, and never moved later to make a risk number fit.
    """
    return structural_stop_price(
        direction=candidate.direction,
        swing_high=candidate.pullback_swing_high,
        swing_low=candidate.pullback_swing_low,
        tick_size=candidate.tick_size,
        buffer_ticks=candidate.buffer_ticks,
    )


def transaction_type_for(direction: str) -> str:
    return "BUY" if str(direction).upper() == "UP" else "SELL"


def exit_transaction_type_for(direction: str) -> str:
    return "SELL" if str(direction).upper() == "UP" else "BUY"


# ----------------------------------------------------------------------
# Step 6: classifying the broker's response
# ----------------------------------------------------------------------


def is_definite_rejection(message: str) -> bool:
    lowered = str(message).lower()
    return any(marker in lowered for marker in DEFINITE_REJECTION_MARKERS)


def place_entry(
    broker,
    *,
    candidate: TriggerCandidate,
    quantity: int,
    tag: str,
) -> PlaceResponse:
    """Send the MARKET order and classify the response into exactly one bucket.

    A market order does not wait for a price, which is the whole reason it was
    chosen: there is no staleness to reprice around.
    """
    try:
        order = broker.place_market_mis(
            tradingsymbol=candidate.tradingsymbol,
            transaction_type=transaction_type_for(candidate.direction),
            quantity=int(quantity),
            tag=tag,
        )
    except EntryAcceptedVisibilityUnknown as exc:
        # The broker accepted it but the row is not poll-visible yet. We know
        # an order exists; we just cannot see it. Ambiguous by construction.
        return PlaceResponse(ResponseKind.AMBIGUOUS, reason=str(exc))
    except Exception as exc:  # noqa: BLE001 - every failure must be classified
        message = str(exc) or exc.__class__.__name__
        if is_definite_rejection(message):
            return PlaceResponse(ResponseKind.REJECTED, reason=message)
        return PlaceResponse(ResponseKind.AMBIGUOUS, reason=message)

    if order is None:
        return PlaceResponse(ResponseKind.AMBIGUOUS, reason="broker_returned_no_order")
    status = str(order.status).upper()
    if status == "REJECTED":
        return PlaceResponse(ResponseKind.REJECTED, order=order, reason="broker_rejected")
    return PlaceResponse(ResponseKind.SUCCESS, order=order)


def resolve_ambiguous_entry(broker, *, tag: str) -> Optional[BrokerOrder]:
    """Ask the broker's order book directly, by our own tag.

    Always resolves to exactly one of two things: nothing there (the order
    never reached the broker — safe to retry fresh), or an order that exists
    and filled (identical to a clean success). There is no third
    "found but still pending" case worth handling separately, because a market
    order settles essentially instantly.
    """
    try:
        orders: List[BrokerOrder] = list(broker.orders_by_tag(tag) or [])
    except Exception:  # noqa: BLE001 - unresolvable this tick; try again next
        return None
    live = [o for o in orders if str(o.status).upper() not in {"REJECTED", "CANCELLED"}]
    if not live:
        return None
    # Prefer one that already shows a fill.
    for order in live:
        if broker_order_filled_qty(order) > 0:
            return order
    return live[0]


# ----------------------------------------------------------------------
# Steps 7-8: the real fill, the real risk, and the 1.5x line
# ----------------------------------------------------------------------


def fill_price_of(order: BrokerOrder) -> Optional[float]:
    price = order.average_price
    if price is None or float(price) <= 0:
        return None
    return float(price)


def apply_entry_fill(
    position: Position,
    *,
    fill_price: float,
    filled_qty: int,
    risk_cap_rupees: float,
) -> FillVerdict:
    """Record the real fill, compute the real risk, and judge it.

    Step 7: real risk-per-share is |real fill - stop|, with the stop unchanged
    from step 1. Step 8: compare real total risk against 1.5x the intended cap.
    Normal slippage is accepted and logged, not sized around.
    """
    position.entry_price = float(fill_price)
    position.qty = int(filled_qty)
    stop = position.stop_price
    if stop is None:
        # Cannot judge risk without a stop, and cannot protect without one
        # either. Treat as abnormal: flatten rather than hold blind.
        position.risk_taken_rupees = None
        transition(position, ExecutionState.ENTERED)
        return FillVerdict.FLATTEN_ABNORMAL_SLIPPAGE

    risk_per_share = abs(float(fill_price) - float(stop))
    position.risk_taken_rupees = risk_per_share * int(filled_qty)
    transition(position, ExecutionState.ENTERED)

    threshold = float(risk_cap_rupees) * ABNORMAL_SLIPPAGE_MULTIPLE
    if position.risk_taken_rupees > threshold:
        return FillVerdict.FLATTEN_ABNORMAL_SLIPPAGE
    return FillVerdict.PROTECT


# ----------------------------------------------------------------------
# The sequence
# ----------------------------------------------------------------------


def submit_entry(
    candidate: TriggerCandidate,
    *,
    broker,
    store,
    sizing_policy: SizingPolicy,
    risk_cap_rupees: float,
    available_capital_rupees: float,
    leverage_factor: float,
    gate: Optional[EntryGate] = None,
    margin_preflight: Optional[Callable[[TriggerCandidate, int], Optional[str]]] = None,
    is_live: bool = False,
    run_id: Optional[str] = None,
    trade_id: Optional[str] = None,
) -> EntryOutcome:
    """Run steps 1-8. Step 9 (placing the stop) belongs to engine_protection,
    so a filled-but-unprotected position is always visible in the store as
    ENTERED even if the process dies between the two.
    """
    tag = trade_id or candidate.setup_id

    # Step 1 - the stop, from market structure, computed once.
    stop_price = compute_stop_price(candidate)
    if stop_price is None:
        return _skip(store, candidate, "no_structural_stop", is_live, run_id, tag)

    # Step 2 - quantity, from this tier's risk cap.
    decision: SizeDecision = sizing_policy.decide(
        entry_price=float(candidate.trigger_price),
        stop_price=float(stop_price),
        risk_cap_rupees=float(risk_cap_rupees),
        available_capital_rupees=float(available_capital_rupees),
        leverage_factor=float(leverage_factor),
    )
    if decision.qty <= 0:
        return _skip(store, candidate, "sized_to_zero", is_live, run_id, tag)

    # Step 3 - recheck the gates right before sending, since time has passed
    # since the trigger fired.
    if gate is not None:
        refusal = gate(candidate)
        if refusal:
            return _skip(store, candidate, refusal, is_live, run_id, tag)
    if margin_preflight is not None:
        refusal = margin_preflight(candidate, decision.qty)
        if refusal:
            return _skip(store, candidate, refusal, is_live, run_id, tag)

    # Step 4 - durable intent BEFORE calling the broker. Non-negotiable: this
    # is what turns a crash mid-submission into reconciliation instead of a
    # silent double-order or a silently lost position.
    position = Position(
        trade_id=tag,
        candidate=candidate,
        state=ExecutionState.PENDING_ENTRY,
        qty=decision.qty,
        stop_price=float(stop_price),
        is_live=is_live,
        run_id=run_id,
        extra={
            "risk_cap_rupees": float(risk_cap_rupees),
            "binding_constraint": decision.binding_constraint,
            "risk_based_qty": decision.risk_based_qty,
            "capital_based_qty": decision.capital_based_qty,
        },
    )
    store.save_with_event(
        position,
        "entry_intent",
        {
            "qty": decision.qty,
            "stop_price": float(stop_price),
            "trigger_price": float(candidate.trigger_price),
            "risk_cap_rupees": float(risk_cap_rupees),
            "vwap_classification": candidate.vwap_classification,
        },
    )

    # Step 5 - MARKET order, tagged with the trade's id.
    response = place_entry(broker, candidate=candidate, quantity=decision.qty, tag=tag)

    # Step 6 - exactly one of three buckets.
    order = response.order
    if response.kind is ResponseKind.REJECTED:
        position.extra["reject_reason"] = response.reason
        transition(position, ExecutionState.REJECTED)
        store.save_with_event(
            position, "entry_rejected", {"reason": response.reason}
        )
        return EntryOutcome(EntryResult.REJECTED, position, response.reason)

    if response.kind is ResponseKind.AMBIGUOUS:
        store.append_event(tag, "entry_ambiguous", {"reason": response.reason})
        order = resolve_ambiguous_entry(broker, tag=tag)
        if order is None:
            # Never reached the broker. Leave the intent row in PENDING_ENTRY
            # so the next tick retries this one fresh, with nothing orphaned at
            # the broker.
            store.append_event(tag, "entry_unreached", {"reason": response.reason})
            return EntryOutcome(
                EntryResult.RETRY_NEXT_TICK, position, response.reason
            )

    assert order is not None
    position.entry_order_id = str(order.order_id)
    transition(position, ExecutionState.ENTRY_SUBMITTED)
    store.save_with_event(
        position,
        "entry_submitted",
        {"order_id": order.order_id, "qty": decision.qty, "status": order.status},
    )

    # Steps 7-8, inline when the fill is already visible. A market order
    # settles essentially instantly, so this is the normal case and taking it
    # now rather than next tick is what keeps the unprotected window short.
    filled_qty = broker_order_filled_qty(order)
    fill_price = fill_price_of(order)
    if filled_qty <= 0 or fill_price is None:
        return EntryOutcome(EntryResult.SUBMITTED, position)

    verdict = apply_entry_fill(
        position,
        fill_price=fill_price,
        filled_qty=filled_qty,
        risk_cap_rupees=risk_cap_rupees,
    )
    store.save_with_event(
        position,
        "entry_filled",
        {
            "fill_price": fill_price,
            "filled_qty": filled_qty,
            "risk_taken_rupees": position.risk_taken_rupees,
            "risk_cap_rupees": float(risk_cap_rupees),
            "abnormal_threshold_rupees": float(risk_cap_rupees) * ABNORMAL_SLIPPAGE_MULTIPLE,
            "verdict": verdict.value,
        },
    )
    return EntryOutcome(EntryResult.FILLED, position, verdict=verdict)


def _skip(
    store,
    candidate: TriggerCandidate,
    reason: str,
    is_live: bool,
    run_id: Optional[str],
    trade_id: str,
) -> EntryOutcome:
    """Record a candidate that never reached the broker, and never will."""
    position = Position(
        trade_id=trade_id,
        candidate=candidate,
        state=ExecutionState.REJECTED,
        is_live=is_live,
        run_id=run_id,
        extra={"skip_reason": reason},
    )
    store.save_with_event(position, "skipped", {"reason": reason})
    return EntryOutcome(EntryResult.SKIPPED, position, reason)
