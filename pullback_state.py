"""Deterministic pullback setup state machine and runtime reconstruction helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from pullback_types import (
    ACTIVE_SETUP_STATES,
    TERMINAL_SETUP_STATES,
    PullbackSequenceState,
    PullbackSetup,
    SetupState,
)


@dataclass
class PullbackSetupRuntime:
    setup: PullbackSetup
    state: SetupState
    impulse_5m_high: Optional[float] = None
    impulse_5m_low: Optional[float] = None
    sequence: Optional[PullbackSequenceState] = None
    pullback_type: Optional[str] = None
    continuation_attempt_count: int = 0
    last_eval_5m_candle_time: Optional[datetime] = None
    sequence_number: int = 0
    events: List[str] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_SETUP_STATES

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_SETUP_STATES


_ALLOWED: Dict[SetupState, frozenset[SetupState]] = {
    "SPIKE_ACCEPTED": frozenset({"IMPULSE_MONITORING", "CANCELLED"}),
    "IMPULSE_MONITORING": frozenset(
        {"PULLBACK_MONITORING", "SESSION_CLOSED", "CANCELLED"}
    ),
    "PULLBACK_MONITORING": frozenset(
        {
            "PULLBACK_READY",
            "INVALIDATED",
            "EXPIRED",
            "SESSION_CLOSED",
            "CANCELLED",
        }
    ),
    "PULLBACK_READY": frozenset({"CONTINUATION_MONITORING", "CANCELLED"}),
    "CONTINUATION_MONITORING": frozenset(
        {
            "CONTINUATION_MONITORING",
            "TRADED",
            "INVALIDATED",
            "EXPIRED",
            "SESSION_CLOSED",
            "CANCELLED",
        }
    ),
}


def can_transition(current: SetupState, new: SetupState) -> bool:
    if current in TERMINAL_SETUP_STATES:
        return False
    allowed = _ALLOWED.get(current)
    if allowed is None:
        return False
    return new in allowed


def transition(runtime: PullbackSetupRuntime, new_state: SetupState) -> None:
    if not can_transition(runtime.state, new_state):
        raise ValueError(
            "illegal transition %s -> %s for setup %s"
            % (runtime.state, new_state, runtime.setup.setup_id)
        )
    runtime.state = new_state
    runtime.events.append(new_state)


def make_setup_id(
    *,
    instrument_token: int,
    spike_candle_time_iso: str,
    spike_rule_version: str,
    pullback_rule_version: str,
) -> str:
    """Deterministic setup identity for replay."""
    return "%s|%s|%s|%s" % (
        instrument_token,
        spike_candle_time_iso,
        spike_rule_version,
        pullback_rule_version,
    )
