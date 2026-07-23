"""
Shared types for intraday spike detection.

Pure data contracts only — no I/O, no rule thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import FrozenSet, Literal

SpikeDirection = Literal["UP", "DOWN", "FLAT"]

QualitySkipReason = Literal[
    "partial",
    "incomplete_coverage",
    "unreliable_volume",
    "bad_completion_reason",
]

FeatureSkipReason = Literal[
    "invalid_open",
    "zero_range",
    "non_positive_median_volume",
    "non_positive_trimmed_mean_volume",
    "non_positive_median_abs_return",
]

RuleRejectReason = Literal[
    "outside_detection_window",
    "below_relative_volume_median",
    "below_relative_volume_trimmed",
    "below_absolute_return",
    "below_abs_return_vs_baseline",
    "below_body_ratio",
    "flat_direction",
    "bullish_close_location_fail",
    "bearish_close_location_fail",
]


@dataclass(frozen=True)
class BaselineSnapshot:
    """Immutable baseline inputs required for spike feature calculation."""

    instrument_token: int
    minute_of_day: int
    median_volume: float
    trimmed_mean_volume: float
    median_abs_return: float
    valid_session_count: int
    is_reliable: bool
    baseline_as_of_date: str


@dataclass(frozen=True)
class SpikeFeatures:
    """Pure computed features for one candle + baseline snapshot."""

    instrument_token: int
    minute_of_day: int
    session_date: str
    baseline_as_of_date: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    tick_count: int
    volume_reliable: bool
    absolute_return: float
    signed_return: float
    direction: SpikeDirection
    relative_volume_median: float
    relative_volume_trimmed: float
    abs_return_vs_baseline: float
    body_ratio: float
    close_location: float
    median_volume: float
    trimmed_mean_volume: float
    median_abs_return: float
    valid_session_count: int
    is_reliable: bool


@dataclass(frozen=True)
class SpikeDecision:
    accepted: bool
    rule_version: str
    reasons: FrozenSet[str]


@dataclass(frozen=True)
class IntradaySpikeEvent:
    """Immutable accepted intraday spike event for downstream stages."""

    instrument_token: int
    tradingsymbol: str
    candle_time: datetime
    session_date: str
    rule_version: str
    direction: SpikeDirection
    open: float
    high: float
    low: float
    close: float
    volume: int
    features: SpikeFeatures
    detected_at: datetime
    decision: SpikeDecision
