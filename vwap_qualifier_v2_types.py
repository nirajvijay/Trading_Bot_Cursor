"""
Shared types for vwap_qualifier_v2 — 1m session VWAP qualifier.

Pure data contracts — no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import FrozenSet, Literal, Optional, Sequence

from spike_types import SpikeDirection

VwapClassification = Literal["ACCEPT", "LIMITED", "REJECT", "UNAVAILABLE"]
VwapRiskProfile = Literal["normal", "reduced"]
MinuteProvenance = Literal["live", "historical"]
CacheState = Literal["cold", "warming", "ready", "degraded"]

VwapQualityReason = Literal[
    "cache_cold",
    "cache_warming",
    "cache_degraded",
    "feed_stale",
    "gap_in_minutes",
    "volume_reset",
    "non_finite_vwap",
    "non_finite_gap",
    "zero_volume",
    "persist_failed",
    "invalid_live_candle",
    "invalid_historical_candle",
]

VWAP_CLASSIFICATIONS: FrozenSet[str] = frozenset(
    {"ACCEPT", "LIMITED", "REJECT", "UNAVAILABLE"}
)


@dataclass(frozen=True)
class MinuteLedgerEntry:
    minute: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    pv: float
    provenance: MinuteProvenance


@dataclass(frozen=True)
class VwapQualificationV2:
    """In-memory classify result for one TRIGGERED event."""

    setup_id: str
    continuation_rule_version: str
    vwap_rule_version: str
    instrument_token: int
    tradingsymbol: str
    session_date: str
    direction: SpikeDirection
    trigger_price: float
    last_price: float
    trigger_tick_sequence: int
    trigger_exchange_ts: datetime
    vwap: Optional[float]
    gap: Optional[float]
    classification: VwapClassification
    quality_ok: bool
    quality_reason: Optional[VwapQualityReason]
    risk_profile: Optional[VwapRiskProfile]
    risk_cap_inr: Optional[float]
    requested_cutoff_minute: Optional[datetime]
    actual_snapshot_cutoff_minute: Optional[datetime]
    cache_age_minutes: Optional[int]
    historical_minute_count: int
    live_minute_count: int
    detected_at: datetime
