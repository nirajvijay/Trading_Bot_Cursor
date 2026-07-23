"""
Pure spike feature calculation from a completed candle and baseline snapshot.

No I/O and no threshold decisions.
"""

from __future__ import annotations

from typing import Optional, Tuple

from candle_aggregation import (
    CompletedOneMinuteCandle,
    ensure_ist,
    minute_of_day_from_datetime,
    session_date_from_candle_time,
)
from spike_types import BaselineSnapshot, FeatureSkipReason, SpikeDirection, SpikeFeatures


def _direction(signed_return: float) -> SpikeDirection:
    if signed_return > 0:
        return "UP"
    if signed_return < 0:
        return "DOWN"
    return "FLAT"


def compute_spike_features(
    candle: CompletedOneMinuteCandle,
    baseline: BaselineSnapshot,
) -> Tuple[Optional[SpikeFeatures], Optional[FeatureSkipReason]]:
    """
    Compute SpikeFeatures or return a skip reason when inputs are invalid.

    Does not apply strategy thresholds.
    """
    if candle.open <= 0:
        return None, "invalid_open"

    candle_range = candle.high - candle.low
    if candle_range <= 0:
        return None, "zero_range"

    if baseline.median_volume <= 0:
        return None, "non_positive_median_volume"
    if baseline.trimmed_mean_volume <= 0:
        return None, "non_positive_trimmed_mean_volume"
    if baseline.median_abs_return <= 0:
        return None, "non_positive_median_abs_return"

    signed_return = (candle.close - candle.open) / candle.open
    absolute_return = abs(signed_return)
    body_ratio = abs(candle.close - candle.open) / candle_range
    close_location = (candle.close - candle.low) / candle_range

    ist_time = ensure_ist(candle.candle_time)
    minute = minute_of_day_from_datetime(ist_time)
    session_date = session_date_from_candle_time(
        ist_time.isoformat(timespec="seconds")
    )

    features = SpikeFeatures(
        instrument_token=candle.instrument_token,
        minute_of_day=minute,
        session_date=session_date,
        baseline_as_of_date=baseline.baseline_as_of_date,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        volume=candle.volume,
        tick_count=candle.tick_count,
        volume_reliable=candle.volume_reliable,
        absolute_return=absolute_return,
        signed_return=signed_return,
        direction=_direction(signed_return),
        relative_volume_median=candle.volume / baseline.median_volume,
        relative_volume_trimmed=candle.volume / baseline.trimmed_mean_volume,
        abs_return_vs_baseline=absolute_return / baseline.median_abs_return,
        body_ratio=body_ratio,
        close_location=close_location,
        median_volume=baseline.median_volume,
        trimmed_mean_volume=baseline.trimmed_mean_volume,
        median_abs_return=baseline.median_abs_return,
        valid_session_count=baseline.valid_session_count,
        is_reliable=baseline.is_reliable,
    )
    return features, None
