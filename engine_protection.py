"""Step 9: putting the protective stop on a filled position.

One job, kept separate from entry submission so that a position which filled
but has no stop yet is always visible in the store as ENTERED — including if
the process dies between the two. That visibility is the whole point: an
unprotected fill is the most dangerous state this system can be in, and it must
never be inferable only from the absence of something.

The stop goes at the exact structural price computed before the entry was ever
sent. It is never recalculated from the fill price and never nudged to make a
risk number work.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from engine_entry import broker_tag_for, exit_transaction_type_for
from engine_orders import transition
from engine_trailing import INITIAL_STOP_KEY, TRAIL_ENABLED_KEY
from engine_types import ExecutionState, Position
from trading_engine_broker import SlPlaceAcceptedVisibilityUnknown


@dataclass(frozen=True)
class ProtectionOutcome:
    protected: bool
    reason: Optional[str] = None
    order_id: Optional[str] = None


def ensure_protected(position: Position, *, broker, store) -> ProtectionOutcome:
    """Place the structural stop and move to PROTECTED once the broker confirms.

    Three outcomes, and the middle one is the subtle one:

    * Confirmed → PROTECTED.
    * Accepted but not yet poll-visible → bind the accepted order id durably
      and stay ENTERED. The next tick's reconciliation confirms it. Critically,
      this must not place a second stop, and must not claim protection it
      cannot see.
    * Failed → stay ENTERED so the next tick retries. Never silently abandoned.
    """
    if position.state != ExecutionState.ENTERED:
        return ProtectionOutcome(False, reason="not_awaiting_protection")
    if position.stop_price is None:
        return ProtectionOutcome(False, reason="no_stop_price")
    if int(position.qty or 0) <= 0:
        return ProtectionOutcome(False, reason="no_quantity")

    candidate = position.candidate
    try:
        order = broker.place_slm(
            tradingsymbol=candidate.tradingsymbol,
            transaction_type=exit_transaction_type_for(candidate.direction),
            quantity=int(position.qty),
            trigger_price=float(position.stop_price),
            tag=broker_tag_for(position.trade_id),
            tick_size=float(candidate.tick_size or 0.05),
        )
    except SlPlaceAcceptedVisibilityUnknown as exc:
        # The broker took it; we just cannot see the row yet. Record the id so
        # the next tick recognizes the existing stop instead of placing another.
        position.stop_order_id = str(exc.order_id)
        store.save_with_event(
            position,
            "stop_accepted_visibility_unknown",
            {"order_id": exc.order_id, "stop_price": position.stop_price},
        )
        return ProtectionOutcome(
            False, reason="visibility_unknown", order_id=str(exc.order_id)
        )
    except Exception as exc:  # noqa: BLE001 - retried next tick, never dropped
        reason = str(exc) or exc.__class__.__name__
        store.append_event(position.trade_id, "stop_place_failed", {"reason": reason})
        return ProtectionOutcome(False, reason=reason)

    if order is None:
        store.append_event(
            position.trade_id, "stop_place_failed", {"reason": "broker_returned_no_order"}
        )
        return ProtectionOutcome(False, reason="broker_returned_no_order")

    status = str(order.status).upper()
    position.stop_order_id = str(order.order_id)
    if status in {"REJECTED", "CANCELLED"}:
        store.save_with_event(
            position,
            "stop_place_failed",
            {"order_id": order.order_id, "status": status},
        )
        return ProtectionOutcome(False, reason=f"stop_{status.lower()}")

    transition(position, ExecutionState.PROTECTED)
    # First protection only: the structural stop becomes the trailing floor,
    # and auto-trail starts on. A re-protection (a replaced stop) keeps both,
    # including a human's choice to switch auto-trail off.
    position.extra.setdefault(INITIAL_STOP_KEY, position.stop_price)
    position.extra.setdefault(TRAIL_ENABLED_KEY, True)
    store.save_with_event(
        position,
        "protected",
        {
            "order_id": order.order_id,
            "stop_price": position.stop_price,
            "qty": position.qty,
        },
    )
    return ProtectionOutcome(True, order_id=str(order.order_id))
