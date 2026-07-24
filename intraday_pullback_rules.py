"""
Pure deterministic pullback rule evaluation.

No I/O, no wall-clock in decision math.
"""

from __future__ import annotations

from typing import Optional

from intraday_pullback_config import IntradayPullbackRuleConfig
from pullback_features import classify_pullback_type
from pullback_types import (
    PullbackDecision,
    PullbackFeatures,
    PullbackSequenceState,
)


def evaluate_pullback_monitoring(
    features: PullbackFeatures,
    sequence: PullbackSequenceState,
    config: IntradayPullbackRuleConfig,
) -> PullbackDecision:
    reasons: set[str] = set()

    if features.impulse_range <= 0:
        return PullbackDecision(
            outcome="invalidated",
            rule_version=config.rule_version,
            reasons=frozenset({"zero_impulse_range"}),
            invalidation_reason="zero_impulse_range",
            terminal_reason="zero_impulse_range",
        )

    if features.impulse_close_break:
        return PullbackDecision(
            outcome="invalidated",
            rule_version=config.rule_version,
            reasons=frozenset({"impulse_extreme_close_break"}),
            invalidation_reason="impulse_extreme_close_break",
            terminal_reason="impulse_extreme_close_break",
        )

    deepest = sequence.deepest_retracement_percent
    if deepest > config.maximum_retracement_percent:
        return PullbackDecision(
            outcome="invalidated",
            rule_version=config.rule_version,
            reasons=frozenset({"excessive_retracement"}),
            invalidation_reason="excessive_retracement",
            terminal_reason="excessive_retracement",
        )

    count = sequence.pullback_candle_count
    in_band = (
        config.minimum_retracement_percent
        <= deepest
        <= config.maximum_retracement_percent
    )

    if (
        count >= config.minimum_pullback_candles
        and count <= config.maximum_pullback_monitoring_candles
        and in_band
    ):
        pullback_type = classify_pullback_type(
            direction=features.direction,
            lowest_low=sequence.lowest_low_since_impulse,
            highest_high=sequence.highest_high_since_impulse,
            ema20=sequence.ema20_value,
        )
        return PullbackDecision(
            outcome="pullback_ready",
            rule_version=config.rule_version,
            reasons=frozenset(),
            pullback_type=pullback_type,
        )

    if count >= config.maximum_pullback_monitoring_candles and not in_band:
        return PullbackDecision(
            outcome="expired",
            rule_version=config.rule_version,
            reasons=frozenset({"pullback_window_exhausted"}),
            terminal_reason="pullback_window_exhausted",
        )

    if count < config.minimum_pullback_candles:
        reasons.add("below_minimum_pullback_candles")
    if deepest < config.minimum_retracement_percent:
        reasons.add("retracement_too_shallow")

    return PullbackDecision(
        outcome="continue_monitoring",
        rule_version=config.rule_version,
        reasons=frozenset(reasons),
    )


def evaluate_continuation_structure(
    features: PullbackFeatures,
    sequence: PullbackSequenceState,
    config: IntradayPullbackRuleConfig,
) -> PullbackDecision:
    """Post-ready structure checks (invalidation / continue). Trigger rules deferred."""
    if features.impulse_close_break:
        return PullbackDecision(
            outcome="invalidated",
            rule_version=config.rule_version,
            reasons=frozenset({"impulse_extreme_close_break"}),
            invalidation_reason="impulse_extreme_close_break",
            terminal_reason="impulse_extreme_close_break",
        )
    if sequence.deepest_retracement_percent > config.maximum_retracement_percent:
        return PullbackDecision(
            outcome="invalidated",
            rule_version=config.rule_version,
            reasons=frozenset({"excessive_retracement"}),
            invalidation_reason="excessive_retracement",
            terminal_reason="excessive_retracement",
        )
    return PullbackDecision(
        outcome="continue_monitoring",
        rule_version=config.rule_version,
        reasons=frozenset(),
    )


class IntradayPullbackRuleEngine:
    def __init__(self, config: Optional[IntradayPullbackRuleConfig] = None) -> None:
        self._config = config if config is not None else IntradayPullbackRuleConfig()

    @property
    def config(self) -> IntradayPullbackRuleConfig:
        return self._config

    def evaluate_monitoring(
        self,
        features: PullbackFeatures,
        sequence: PullbackSequenceState,
    ) -> PullbackDecision:
        return evaluate_pullback_monitoring(features, sequence, self._config)

    def evaluate_continuation(
        self,
        features: PullbackFeatures,
        sequence: PullbackSequenceState,
    ) -> PullbackDecision:
        return evaluate_continuation_structure(features, sequence, self._config)
