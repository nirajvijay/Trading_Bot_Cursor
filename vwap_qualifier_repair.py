"""
Async reconstruct of a full 5-minute VWAP bucket from five historical 1m bars.

Used for the manual-start bucket B0 (provenance bootstrap) and later feed-gap
buckets (provenance repaired). Never mixes live minutes with historical minutes.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Sequence

from candle_aggregation import (
    OneMinuteCandle,
    aggregate_five_candles,
    ensure_ist,
)
from vwap_qualifier_bootstrap import expected_bucket_one_minute_starts
from vwap_qualifier_features import hlc3_pv
from vwap_qualifier_state import bucket_is_closed
from vwap_qualifier_types import VwapProvenance

logger = logging.getLogger(__name__)

REPAIR_BACKOFF_SECONDS = (5.0, 15.0, 30.0, 60.0)
REPAIR_MAX_ATTEMPTS = 5


class RepairValidateError(ValueError):
    """Historical 1m set cannot form a canonical 5m bucket."""


def backoff_delay_seconds(attempt_index: int, schedule: Sequence[float] = REPAIR_BACKOFF_SECONDS) -> float:
    """Delay after a failed attempt. attempt_index is 0-based after the first try."""
    if not schedule:
        return 0.0
    if attempt_index >= len(schedule):
        return float(schedule[-1])
    return float(schedule[attempt_index])


def select_bucket_one_minute(
    candles: Sequence[OneMinuteCandle],
    bucket_start: datetime,
) -> List[OneMinuteCandle]:
    wanted = {dt.isoformat(timespec="seconds") for dt in expected_bucket_one_minute_starts(bucket_start)}
    by_time = {}
    for candle in candles:
        if candle.candle_time in wanted:
            by_time[candle.candle_time] = candle
    ordered = [by_time[ts] for ts in sorted(wanted) if ts in by_time]
    return ordered


def reconstruct_five_minute_hlc3(
    candles: Sequence[OneMinuteCandle],
    bucket_start: datetime,
) -> tuple[float, int]:
    """Validate five consecutive 1m bars covering bucket_start and return (pv, volume)."""
    start = ensure_ist(bucket_start)
    expected = expected_bucket_one_minute_starts(start)
    selected = select_bucket_one_minute(candles, start)
    if len(selected) != 5:
        raise RepairValidateError(
            "expected 5 one-minute candles for %s, got %d"
            % (start.isoformat(timespec="seconds"), len(selected))
        )
    for candle, exp in zip(selected, expected):
        got = ensure_ist(datetime.fromisoformat(candle.candle_time))
        if got != exp:
            raise RepairValidateError(
                "candle time mismatch want=%s got=%s" % (exp.isoformat(), got.isoformat())
            )
        if candle.volume < 0:
            raise RepairValidateError("negative volume")
        if any(
            not _finite(v)
            for v in (candle.open, candle.high, candle.low, candle.close)
        ):
            raise RepairValidateError("non-finite OHLC")
    bar = aggregate_five_candles(list(selected))
    bar_start = ensure_ist(datetime.fromisoformat(bar.candle_time))
    if bar_start != start:
        raise RepairValidateError("aggregated bucket start mismatch")
    pv = hlc3_pv(bar.high, bar.low, bar.close, bar.volume)
    return pv, int(bar.volume)


def _finite(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))


def provenance_for_bucket(bucket_start: datetime, b0: datetime) -> VwapProvenance:
    if ensure_ist(bucket_start) == ensure_ist(b0):
        return "bootstrap"
    return "repaired"


def can_fetch_bucket(bucket_start: datetime, now: datetime) -> bool:
    return bucket_is_closed(bucket_start, now)
