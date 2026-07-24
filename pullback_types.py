"""
Shared types for intraday pullback detection.

Pure data contracts only — no I/O, no rule thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import FrozenSet, Literal, Optional

from spike_types import SpikeDirection

PullbackType = Literal["EMA_PULLBACK", "SHALLOW_STRUCTURE_PULLBACK"]

SetupState = Literal[
    "IDLE",
    "SPIKE_ACCEPTED",
    "IMPULSE_MONITORING",
    "PULLBACK_MONITORING",
    "PULLBACK_READY",
    "CONTINUATION_MONITORING",
    "TRADED",
    "EXPIRED",
    "INVALIDATED",
    "SESSION_CLOSED",
    "CANCELLED",
]

ACTIVE_SETUP_STATES: FrozenSet[str] = frozenset(
    {
        "SPIKE_ACCEPTED",
        "IMPULSE_MONITORING",
        "PULLBACK_MONITORING",
        "PULLBACK_READY",
        "CONTINUATION_MONITORING",
    }
)

TERMINAL_SETUP_STATES: FrozenSet[str] = frozenset(
    {
        "TRADED",
        "EXPIRED",
        "INVALIDATED",
        "SESSION_CLOSED",
        "CANCELLED",
    }
)

GapDirection = Literal["GAP_UP", "GAP_DOWN", "FLAT"]

PullbackDecisionOutcome = Literal[
    "continue_monitoring",
    "pullback_ready",
    "invalidated",
    "expired",
    "session_closed",
    "continuation_attempt",
    "traded",
]

SetupEventType = Literal[
    "SETUP_CREATED",
    "SPIKE_ACCEPTED",
    "IMPULSE_BOUNDARIES_FROZEN",
    "SPIKE_EXTREME_BREACHED",
    "PULLBACK_READY",
    "CONTINUATION_ATTEMPT",
    "CONTINUATION_TRIGGERED",
    "TRADE_EXECUTED",
    "INVALIDATED",
    "EXPIRED",
    "SESSION_CLOSED",
    "CANCELLED",
]


@dataclass(frozen=True)
class GapAnalytics:
    previous_session_close: Optional[float]
    session_open: Optional[float]
    gap_absolute: Optional[float]
    gap_percent: Optional[float]
    gap_direction: Optional[GapDirection]


@dataclass(frozen=True)
class PullbackSequenceState:
    highest_high_since_impulse: float
    lowest_low_since_impulse: float
    retracement_percent: float
    deepest_retracement_percent: float
    cumulative_pullback_volume: int
    median_pullback_volume: float
    number_of_opposing_candles: int
    largest_opposing_body_ratio: float
    last_close: float
    pullback_candle_count: int
    last_eval_5m_candle_time: Optional[datetime]
    spike_extreme_breached: bool
    spike_extreme_breached_at: Optional[datetime]
    ema20_value: Optional[float]
    ema20_interacted: bool
    volumes: tuple[int, ...]


@dataclass(frozen=True)
class PullbackFeatures:
    instrument_token: int
    direction: SpikeDirection
    eval_5m_candle_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    impulse_5m_high: float
    impulse_5m_low: float
    impulse_range: float
    spike_1m_high: float
    spike_1m_low: float
    retracement_percent: float
    deepest_retracement_percent: float
    ema20_value: Optional[float]
    ema20_interacted: bool
    vwap: Optional[float]
    spike_extreme_breached: bool
    impulse_close_break: bool
    pullback_candle_count: int


@dataclass(frozen=True)
class PullbackDecision:
    outcome: PullbackDecisionOutcome
    rule_version: str
    reasons: FrozenSet[str]
    pullback_type: Optional[PullbackType] = None
    invalidation_reason: Optional[str] = None
    terminal_reason: Optional[str] = None


@dataclass(frozen=True)
class PullbackSetup:
    """Immutable setup identity."""

    setup_id: str
    instrument_token: int
    tradingsymbol: str
    session_date: str
    direction: SpikeDirection
    spike_candle_time: datetime
    spike_rule_version: str
    spike_open: float
    spike_high: float
    spike_low: float
    spike_close: float
    spike_volume: int
    impulse_5m_candle_time: datetime
    pullback_rule_version: str
    gap: GapAnalytics
    created_at: datetime


@dataclass(frozen=True)
class PullbackCandidateEvent:
    setup: PullbackSetup
    pullback_type: PullbackType
    features: PullbackFeatures
    sequence: PullbackSequenceState
    decision: PullbackDecision
    detected_at: datetime


@dataclass(frozen=True)
class PullbackInvalidatedEvent:
    setup: PullbackSetup
    features: Optional[PullbackFeatures]
    sequence: Optional[PullbackSequenceState]
    decision: PullbackDecision
    detected_at: datetime


@dataclass(frozen=True)
class PullbackExpiredEvent:
    setup: PullbackSetup
    features: Optional[PullbackFeatures]
    sequence: Optional[PullbackSequenceState]
    decision: PullbackDecision
    detected_at: datetime
