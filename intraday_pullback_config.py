"""
Immutable intraday pullback rule configuration.

Locked defaults for rule_version=intraday_pullback_v1.
Changing thresholds requires a new rule_version.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IntradayPullbackRuleConfig:
    rule_version: str = "intraday_pullback_v1"

    # Spike creation window (IST minute_of_day), inclusive — new spikes only.
    spike_detection_start_minute: int = 570  # 09:30
    spike_detection_end_minute: int = 840  # 14:00

    minimum_retracement_percent: float = 30.0
    maximum_retracement_percent: float = 60.0
    minimum_pullback_candles: int = 2
    maximum_pullback_monitoring_candles: int = 7
    maximum_active_setups_per_stock: int = 1
    maximum_pullbacks_per_spike: int = 1
    allow_multiple_continuation_attempts: bool = True
    gap_filter_enabled: bool = False
    record_gap_analytics: bool = True

    ema_period: int = 20

    def __post_init__(self) -> None:
        if self.spike_detection_start_minute > self.spike_detection_end_minute:
            raise ValueError("spike_detection_start_minute must be <= end")
        if self.minimum_retracement_percent > self.maximum_retracement_percent:
            raise ValueError("minimum_retracement_percent must be <= maximum")
        if self.minimum_pullback_candles < 1:
            raise ValueError("minimum_pullback_candles must be >= 1")
        if self.maximum_pullback_monitoring_candles < self.minimum_pullback_candles:
            raise ValueError(
                "maximum_pullback_monitoring_candles must be >= minimum_pullback_candles"
            )
        if self.maximum_active_setups_per_stock != 1:
            raise ValueError("v1 requires maximum_active_setups_per_stock == 1")
        if self.maximum_pullbacks_per_spike != 1:
            raise ValueError("v1 requires maximum_pullbacks_per_spike == 1")
