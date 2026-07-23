"""
Candle quality gate for strategy eligibility.

Pure checks against CompletedOneMinuteCandle quality flags.
Does not score against baselines or thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Optional

from candle_aggregation import CompletedOneMinuteCandle
from intraday_spike_config import IntradaySpikeRuleConfig
from spike_types import QualitySkipReason

_ALLOWED_COMPLETION_REASONS = frozenset({"minute_transition", "session_end"})


@dataclass(frozen=True)
class QualityGateResult:
    eligible: bool
    reasons: FrozenSet[QualitySkipReason]


def evaluate_candle_quality(
    candle: CompletedOneMinuteCandle,
    config: IntradaySpikeRuleConfig,
) -> QualityGateResult:
    reasons: set[QualitySkipReason] = set()

    if config.reject_partial and candle.is_partial:
        reasons.add("partial")
    if config.require_full_coverage and not candle.has_full_minute_coverage:
        reasons.add("incomplete_coverage")
    if config.require_volume_reliable and not candle.volume_reliable:
        reasons.add("unreliable_volume")
    if candle.completion_reason not in _ALLOWED_COMPLETION_REASONS:
        reasons.add("bad_completion_reason")

    frozen = frozenset(reasons)
    return QualityGateResult(eligible=len(frozen) == 0, reasons=frozen)


def primary_quality_skip_reason(
    result: QualityGateResult,
) -> Optional[QualitySkipReason]:
    """Stable primary reason for metrics when ineligible."""
    if result.eligible:
        return None
    for reason in (
        "partial",
        "incomplete_coverage",
        "unreliable_volume",
        "bad_completion_reason",
    ):
        if reason in result.reasons:
            return reason  # type: ignore[return-value]
    return next(iter(result.reasons))
