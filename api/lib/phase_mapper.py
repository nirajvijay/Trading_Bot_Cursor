"""Map internal observation states to the 8 allowed UI phase values."""

from __future__ import annotations

from typing import Optional

ALLOWED_PHASES = frozenset(
    {
        "IDLE",
        "SPIKE_DETECTED",
        "PULLBACK_ACTIVE",
        "PULLBACK_READY",
        "CONTINUATION_ARMED",
        "TRIGGERED",
        "REJECTED",
        "DISARMED",
    }
)

_DISARMED_INTERNAL_STATES = frozenset(
    {
        "EXPIRED",
        "INVALIDATED",
        "SESSION_CLOSED",
        "CANCELLED",
    }
)


def map_to_ui_phase(
    *,
    has_spike: bool,
    setup_state: Optional[str],
    continuation_decision: Optional[str],
) -> str:
    """Return one of the 8 UI phase values."""
    if continuation_decision == "TRIGGERED":
        return "TRIGGERED"
    if continuation_decision == "REJECTED":
        return "REJECTED"
    if continuation_decision == "DISARMED":
        return "DISARMED"

    if setup_state in _DISARMED_INTERNAL_STATES:
        return "DISARMED"
    if setup_state == "CONTINUATION_REJECTED":
        return "REJECTED"
    if setup_state == "CONTINUATION_TRIGGERED":
        return "TRIGGERED"
    if setup_state == "CONTINUATION_MONITORING":
        return "CONTINUATION_ARMED"
    if setup_state == "PULLBACK_READY":
        return "PULLBACK_READY"
    if setup_state == "PULLBACK_MONITORING":
        return "PULLBACK_ACTIVE"
    if setup_state in {"SPIKE_ACCEPTED", "IMPULSE_MONITORING"}:
        return "SPIKE_DETECTED"
    if setup_state is not None:
        return "IDLE"

    if has_spike:
        return "SPIKE_DETECTED"
    return "IDLE"


def format_last_event(
    *,
    phase: str,
    setup_state: Optional[str],
    event_type: Optional[str],
    event_payload: Optional[str],
    continuation_decision: Optional[str],
    continuation_reason: Optional[str],
) -> str:
    """Human-readable last event for the Last Event column."""
    if continuation_decision == "TRIGGERED":
        return continuation_reason or "Continuation triggered"
    if continuation_decision == "REJECTED":
        return continuation_reason or "Continuation rejected"
    if continuation_decision == "DISARMED":
        return continuation_reason or "Continuation disarmed"

    if setup_state in _DISARMED_INTERNAL_STATES:
        label = setup_state.replace("_", " ").title()
        if event_type:
            return "%s: %s" % (label, event_type.replace("_", " ").lower())
        return label

    if setup_state == "CONTINUATION_REJECTED":
        return continuation_reason or event_type or "Continuation rejected"
    if setup_state == "CONTINUATION_TRIGGERED":
        return event_type or "Continuation triggered"
    if event_type:
        return event_type.replace("_", " ").title()
    if phase == "IDLE":
        return "Scanning"
    return "-"


def format_timeline_event(
    *,
    event_type: Optional[str],
    resulting_state: Optional[str],
    continuation_reason: Optional[str] = None,
) -> str:
    """Human-readable label for a single timeline event row."""
    if event_type == "CONTINUATION_TRIGGERED":
        return continuation_reason or "Continuation triggered"
    if event_type == "CONTINUATION_REJECTED":
        return continuation_reason or "Continuation rejected"
    if event_type == "CANCELLED":
        return "Cancelled"
    if resulting_state in _DISARMED_INTERNAL_STATES:
        label = resulting_state.replace("_", " ").title()
        if event_type and event_type != resulting_state:
            return "%s: %s" % (label, event_type.replace("_", " ").lower())
        return label
    if event_type:
        return event_type.replace("_", " ").title()
    if resulting_state:
        return resulting_state.replace("_", " ").title()
    return "-"


def map_setup_status(
    *,
    final_state: Optional[str],
    continuation_decision: Optional[str] = None,
) -> str:
    """Map internal setup state to a compact timeline status badge."""
    if continuation_decision == "TRIGGERED" or final_state == "CONTINUATION_TRIGGERED":
        return "triggered"
    if continuation_decision == "REJECTED" or final_state == "CONTINUATION_REJECTED":
        return "rejected"
    if continuation_decision == "DISARMED":
        return "disarmed"
    if final_state == "CANCELLED":
        return "cancelled"
    if final_state in {"EXPIRED", "INVALIDATED", "SESSION_CLOSED"}:
        return "expired"
    if final_state == "TRADED":
        return "traded"
    if final_state in {
        "SPIKE_ACCEPTED",
        "IMPULSE_MONITORING",
        "PULLBACK_MONITORING",
        "PULLBACK_READY",
        "CONTINUATION_MONITORING",
    }:
        return "active"
    return "unknown"
