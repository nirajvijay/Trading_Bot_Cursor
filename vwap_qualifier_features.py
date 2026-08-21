"""
Pure VWAP qualifier math: HLC3-volume, directional gap, classification.

No I/O, no wall-clock, no mutable globals.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

from vwap_qualifier_config import VwapQualifierConfig
from vwap_qualifier_types import VwapClassification, VwapQualityReason


def hlc3_pv(high: float, low: float, close: float, volume: int) -> float:
    """Typical-price * volume for one 5-minute bar."""
    return ((high + low + close) / 3.0) * float(volume)


def directional_gap(
    direction: str,
    trigger_price: float,
    vwap: float,
) -> Optional[float]:
    """Direction-normalised gap using armed trigger_price, not last trade."""
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
    config: Optional[VwapQualifierConfig] = None,
) -> Tuple[VwapClassification, Optional[VwapQualityReason]]:
    """
    Final classification.

    0.0000% <= gap < 0.2200% → ACCEPT
    0.2200% <= gap <= 0.4000% → LIMITED
    negative or gap > 0.4000% → REJECT
    unreliable/missing → UNAVAILABLE (never strategy REJECT)
    """
    cfg = config or VwapQualifierConfig()
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
