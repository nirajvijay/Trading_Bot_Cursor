"""Continuation arm runtime state."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Deque, Dict, Optional

from continuation_types import (
    TERMINAL_CONTINUATION_STATES,
    ContinuationArmState,
)
from spike_types import SpikeDirection


@dataclass
class ContinuationArmRuntime:
    setup_id: str
    instrument_token: int
    tradingsymbol: str
    session_date: str
    direction: SpikeDirection
    pullback_swing_high: Optional[float]
    pullback_swing_low: Optional[float]
    tick_size: float
    buffer_ticks: int
    trigger_price: float
    trigger_price_ticks: int
    continuation_rule_version: str
    ready_5m_candle_time: Optional[datetime]
    state: ContinuationArmState = "ARMED"
    reached: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_CONTINUATION_STATES


_ALLOWED: Dict[ContinuationArmState, frozenset[ContinuationArmState]] = {
    "ARMED": frozenset({"TRIGGERED", "REJECTED", "DISARMED"}),
}


def can_transition(current: ContinuationArmState, new: ContinuationArmState) -> bool:
    if current in TERMINAL_CONTINUATION_STATES:
        return False
    allowed = _ALLOWED.get(current)
    if allowed is None:
        return False
    return new in allowed


def transition(runtime: ContinuationArmRuntime, new_state: ContinuationArmState) -> None:
    if not can_transition(runtime.state, new_state):
        raise ValueError(
            "illegal continuation transition %s -> %s for %s"
            % (runtime.state, new_state, runtime.setup_id)
        )
    runtime.state = new_state


@dataclass
class TokenVolumeState:
    """Per-token in-progress 1m volume tracking from cumulative day volume."""

    current_minute_start: Optional[datetime] = None
    minute_baseline_cumulative: Optional[int] = None
    last_valid_cumulative_volume: Optional[int] = None
    last_valid_minute_start: Optional[datetime] = None
    volume_reliable: bool = False
    in_progress_volume: int = 0
    prior_1m_volumes: Deque[int] = field(default_factory=lambda: deque(maxlen=3))
