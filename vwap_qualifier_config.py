"""
Immutable VWAP qualifier configuration.

Locked defaults for rule_version=vwap_qualifier_v1.
Changing thresholds requires a new rule_version.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class VwapQualifierConfig:
    rule_version: str = "vwap_qualifier_v1"
    accept_gap_exclusive_max: float = 0.0022
    limited_gap_inclusive_max: float = 0.0040
    historical_max_requests_per_second: float = 1.0
    bootstrap_tick_buffer_max_per_token: int = 1024
    repair_backoff_seconds: Tuple[float, ...] = (5.0, 15.0, 30.0, 60.0)
    repair_max_attempts: int = 5
    persist_queue_maxsize: int = 256

    def __post_init__(self) -> None:
        if self.accept_gap_exclusive_max < 0:
            raise ValueError("accept_gap_exclusive_max must be >= 0")
        if self.limited_gap_inclusive_max < self.accept_gap_exclusive_max:
            raise ValueError("limited_gap_inclusive_max must be >= accept_gap_exclusive_max")
        if self.historical_max_requests_per_second <= 0:
            raise ValueError("historical_max_requests_per_second must be > 0")
        if self.bootstrap_tick_buffer_max_per_token < 1:
            raise ValueError("bootstrap_tick_buffer_max_per_token must be >= 1")
        if self.repair_max_attempts < 1:
            raise ValueError("repair_max_attempts must be >= 1")
        if any(delay < 0 for delay in self.repair_backoff_seconds):
            raise ValueError("repair_backoff_seconds must be >= 0")
        if self.persist_queue_maxsize < 1:
            raise ValueError("persist_queue_maxsize must be >= 1")


def load_vwap_qualifier_config() -> VwapQualifierConfig:
    """Load frozen config, allowing env overrides for REST rate and buffer size."""
    rps_raw = os.environ.get("VWAP_HISTORICAL_MAX_REQUESTS_PER_SECOND")
    buf_raw = os.environ.get("VWAP_BOOTSTRAP_TICK_BUFFER_MAX_PER_TOKEN")
    kwargs = {}
    if rps_raw:
        kwargs["historical_max_requests_per_second"] = float(rps_raw)
    if buf_raw:
        kwargs["bootstrap_tick_buffer_max_per_token"] = int(buf_raw)
    return VwapQualifierConfig(**kwargs)
