"""
Pure intraday spike rule evaluation.

No I/O. Same features + config always yield the same SpikeDecision.
"""

from __future__ import annotations

from spike_types import RuleRejectReason, SpikeDecision, SpikeFeatures
from intraday_spike_config import IntradaySpikeRuleConfig


def evaluate_intraday_spike(
    features: SpikeFeatures,
    config: IntradaySpikeRuleConfig,
) -> SpikeDecision:
    reasons: set[RuleRejectReason] = set()

    if not (
        config.detection_window_start_minute
        <= features.minute_of_day
        <= config.detection_window_end_minute
    ):
        reasons.add("outside_detection_window")

    if features.relative_volume_median < config.min_relative_volume_median:
        reasons.add("below_relative_volume_median")
    if features.relative_volume_trimmed < config.min_relative_volume_trimmed:
        reasons.add("below_relative_volume_trimmed")
    if features.absolute_return < config.min_absolute_return:
        reasons.add("below_absolute_return")
    if features.abs_return_vs_baseline < config.min_abs_return_vs_baseline:
        reasons.add("below_abs_return_vs_baseline")
    if features.body_ratio < config.min_body_ratio:
        reasons.add("below_body_ratio")

    if features.direction == "FLAT":
        reasons.add("flat_direction")
    elif features.direction == "UP":
        if features.close_location < config.min_bullish_close_location:
            reasons.add("bullish_close_location_fail")
    elif features.direction == "DOWN":
        if features.close_location > config.max_bearish_close_location:
            reasons.add("bearish_close_location_fail")

    frozen = frozenset(reasons)
    return SpikeDecision(
        accepted=len(frozen) == 0,
        rule_version=config.rule_version,
        reasons=frozen,
    )


class IntradaySpikeRuleEngine:
    """Thin callable wrapper for pure evaluate_intraday_spike."""

    def __init__(self, config: IntradaySpikeRuleConfig) -> None:
        self._config = config

    @property
    def config(self) -> IntradaySpikeRuleConfig:
        return self._config

    def evaluate(self, features: SpikeFeatures) -> SpikeDecision:
        return evaluate_intraday_spike(features, self._config)
