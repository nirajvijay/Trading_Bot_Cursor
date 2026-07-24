"""
Shared types for intraday continuation trigger engine.

Pure data contracts only — no I/O, no rule thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import FrozenSet, Literal, Optional

from spike_types import SpikeDirection

ContinuationArmState = Literal["IDLE", "ARMED", "TRIGGERED", "REJECTED", "DISARMED"]

ContinuationDecisionType = Literal["TRIGGERED", "REJECTED", "DISARMED"]

ContinuationRejectReason = Literal[
    "failed_breakout_volume_confirmation",
    "insufficient_volume_history",
    "unreliable_breakout_volume",
]

TERMINAL_CONTINUATION_STATES: FrozenSet[str] = frozenset(
    {"TRIGGERED", "REJECTED", "DISARMED"}
)


@dataclass(frozen=True)
class ContinuationArmedEvent:
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
    armed_at: datetime


@dataclass(frozen=True)
class ContinuationTriggeredEvent:
    setup_id: str
    instrument_token: int
    tradingsymbol: str
    direction: SpikeDirection
    trigger_price: float
    trigger_price_ticks: int
    last_price: float
    last_price_ticks: int
    tick_sequence: int
    exchange_timestamp: datetime
    breakout_candle_time: datetime
    breakout_candle_volume: int
    avg_prior_3_1m_volume: float
    continuation_rule_version: str
    detected_at: datetime


@dataclass(frozen=True)
class ContinuationRejectedEvent:
    setup_id: str
    instrument_token: int
    tradingsymbol: str
    direction: SpikeDirection
    reason: ContinuationRejectReason
    trigger_price: float
    trigger_price_ticks: int
    last_price: Optional[float]
    last_price_ticks: Optional[int]
    tick_sequence: Optional[int]
    exchange_timestamp: Optional[datetime]
    breakout_candle_time: Optional[datetime]
    breakout_candle_volume: Optional[int]
    avg_prior_3_1m_volume: Optional[float]
    volume_ok: bool
    volume_reliable: bool
    continuation_rule_version: str
    detected_at: datetime


@dataclass(frozen=True)
class ContinuationDisarmedEvent:
    setup_id: str
    instrument_token: int
    tradingsymbol: str
    reason: str
    continuation_rule_version: str
    detected_at: datetime
