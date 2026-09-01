"""
Immutable VWAP snapshot for classification at trigger time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from vwap_qualifier_v2_types import CacheState, VwapQualityReason


@dataclass(frozen=True)
class VwapSnapshot:
    instrument_token: int
    session_date: str
    vwap: Optional[float]
    cum_pv: float
    cum_volume: int
    last_committed_minute: Optional[datetime]
    requested_cutoff_minute: datetime
    cache_state: CacheState
    feed_stale: bool
    quality_ok: bool
    quality_reason: Optional[VwapQualityReason]
    historical_minute_count: int
    live_minute_count: int
    cache_age_minutes: int

    @property
    def actual_snapshot_cutoff_minute(self) -> Optional[datetime]:
        return self.last_committed_minute
