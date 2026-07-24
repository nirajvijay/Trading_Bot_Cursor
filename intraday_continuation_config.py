"""
Immutable intraday continuation rule configuration.

Locked defaults for rule_version=intraday_continuation_v1.
Changing thresholds requires a new rule_version.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IntradayContinuationRuleConfig:
    rule_version: str = "intraday_continuation_v1"
    continuation_breakout_buffer_ticks: int = 1
    prior_completed_1m_count: int = 3
    require_volume_reliable: bool = True
    exclude_partial_1m_from_average: bool = True

    def __post_init__(self) -> None:
        if self.continuation_breakout_buffer_ticks < 0:
            raise ValueError("continuation_breakout_buffer_ticks must be >= 0")
        if self.prior_completed_1m_count < 1:
            raise ValueError("prior_completed_1m_count must be >= 1")
