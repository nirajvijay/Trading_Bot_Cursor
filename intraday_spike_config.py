"""
Immutable intraday spike rule configuration.

Locked defaults for rule_version=intraday_spike_v1.
Changing thresholds requires a new rule_version.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IntradaySpikeRuleConfig:
    rule_version: str = "intraday_spike_v1"

    # Detection window (IST minute_of_day), inclusive.
    detection_window_start_minute: int = 570  # 09:30
    detection_window_end_minute: int = 840  # 14:00

    min_relative_volume_median: float = 2.0
    min_relative_volume_trimmed: float = 2.0
    min_absolute_return: float = 0.0015  # 0.15%; allowable range 0.0010–0.0015
    min_abs_return_vs_baseline: float = 2.0
    min_body_ratio: float = 0.60
    min_bullish_close_location: float = 0.70
    max_bearish_close_location: float = 0.30

    require_reliable_baseline: bool = True
    require_volume_reliable: bool = True
    require_full_coverage: bool = True
    reject_partial: bool = True

    def __post_init__(self) -> None:
        if self.detection_window_start_minute > self.detection_window_end_minute:
            raise ValueError("detection_window_start_minute must be <= end")
        if not (0.0010 <= self.min_absolute_return <= 0.0015):
            raise ValueError(
                "min_absolute_return must be within 0.0010–0.0015 (0.10%–0.15%)"
            )
