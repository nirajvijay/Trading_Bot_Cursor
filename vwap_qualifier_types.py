"""
Shared types for the independent live 5-minute VWAP qualifier.

Pure data contracts — no I/O, no rule thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import FrozenSet, Literal, Optional, Sequence

from spike_types import SpikeDirection

VwapClassification = Literal["ACCEPT", "LIMITED", "REJECT", "UNAVAILABLE"]

VwapProvenance = Literal["live", "bootstrap", "repaired"]

VwapUiState = Literal["bootstrapping", "ready", "repairing", "unavailable"]

VwapQualityReason = Literal[
    "bootstrap_incomplete",
    "bootstrap_not_ready",
    "bootstrap_open_bucket",
    "bootstrap_buffer_overflow",
    "feed_stale",
    "volume_reset",
    "gap_in_current_bucket",
    "repair_pending",
    "repair_failed",
    "repair_attempts_exhausted",
    "trigger_tick_not_applied",
    "vwap_unavailable",
    "non_finite_gap",
]

VWAP_CLASSIFICATIONS: FrozenSet[str] = frozenset(
    {"ACCEPT", "LIMITED", "REJECT", "UNAVAILABLE"}
)
VWAP_PROVENANCES: FrozenSet[str] = frozenset({"live", "bootstrap", "repaired"})


@dataclass(frozen=True)
class VwapContribution:
    bucket_start: datetime
    volume: int
    pv: float
    provenance: VwapProvenance


@dataclass(frozen=True)
class VwapQualification:
    """In-memory classify result for one raw TRIGGERED event."""

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
    vwap_provenance: Optional[VwapProvenance]
    bootstrap_cutoff_exchange_ts: Optional[datetime]
    completed_5m_count: int
    in_progress_bucket_start: Optional[datetime]
    in_progress_volume: int
    contributions: Sequence[VwapContribution]
    detected_at: datetime
