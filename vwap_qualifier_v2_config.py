"""
Immutable VWAP qualifier v2 configuration.

rule_version=vwap_qualifier_v2 — 1m session VWAP with four-state classification.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class VwapQualifierV2Config:
    rule_version: str = "vwap_qualifier_v2"
    accept_gap_exclusive_max: float = 0.0022
    limited_gap_inclusive_max: float = 0.0040
    risk_cap_normal_inr: float = 900.0
    risk_cap_reduced_inr: float = 450.0
    historical_max_requests_per_second: float = 1.0
    persist_max_retries: int = 5
    persist_retry_base_delay_seconds: float = 0.01

    def __post_init__(self) -> None:
        if self.accept_gap_exclusive_max < 0:
            raise ValueError("accept_gap_exclusive_max must be >= 0")
        if self.limited_gap_inclusive_max < self.accept_gap_exclusive_max:
            raise ValueError("limited_gap_inclusive_max must be >= accept_gap_exclusive_max")
        if self.risk_cap_normal_inr <= 0 or self.risk_cap_reduced_inr <= 0:
            raise ValueError("risk caps must be > 0")
        if self.historical_max_requests_per_second <= 0:
            raise ValueError("historical_max_requests_per_second must be > 0")
        if self.persist_max_retries < 1:
            raise ValueError("persist_max_retries must be >= 1")


def load_vwap_qualifier_v2_config() -> VwapQualifierV2Config:
    kwargs = {}
    rps = os.environ.get("VWAP_HISTORICAL_MAX_REQUESTS_PER_SECOND")
    if rps:
        kwargs["historical_max_requests_per_second"] = float(rps)
    return VwapQualifierV2Config(**kwargs)
