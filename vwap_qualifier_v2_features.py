"""
Pure VWAP qualifier v2 math: HLC3-volume, directional gap, classification.

No I/O, no wall-clock, no mutable globals.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

from vwap_qualifier_v2_config import VwapQualifierV2Config
from vwap_qualifier_v2_types import VwapClassification, VwapQualityReason, VwapRiskProfile


def hlc3_pv(high: float, low: float, close: float, volume: int) -> float:
    return ((high + low + close) / 3.0) * float(volume)


def directional_gap(
    direction: str,
    trigger_price: float,
    vwap: float,
) -> Optional[float]:
    if trigger_price <= 0 or not math.isfinite(trigger_price):
        return None
    if not math.isfinite(vwap):
        return None
    if direction == "UP":
        return (trigger_price - vwap) / trigger_price
    if direction == "DOWN":
        return (vwap - trigger_price) / trigger_price
    return None


def classify_gap(
    gap: Optional[float],
    *,
    quality_ok: bool,
    quality_reason: Optional[VwapQualityReason] = None,
    config: Optional[VwapQualifierV2Config] = None,
) -> Tuple[VwapClassification, Optional[VwapQualityReason]]:
    cfg = config or VwapQualifierV2Config()
    if not quality_ok:
        return "UNAVAILABLE", quality_reason
    if gap is None or not math.isfinite(gap):
        return "UNAVAILABLE", "non_finite_gap"
    if gap < 0.0:
        return "REJECT", None
    if gap < cfg.accept_gap_exclusive_max:
        return "ACCEPT", None
    if gap <= cfg.limited_gap_inclusive_max:
        return "LIMITED", None
    return "REJECT", None


def risk_for_classification(
    classification: VwapClassification,
    *,
    config: Optional[VwapQualifierV2Config] = None,
) -> Tuple[Optional[VwapRiskProfile], Optional[float]]:
    cfg = config or VwapQualifierV2Config()
    if classification == "ACCEPT":
        return "normal", cfg.risk_cap_normal_inr
    if classification == "LIMITED":
        return "reduced", cfg.risk_cap_reduced_inr
    return None, None
