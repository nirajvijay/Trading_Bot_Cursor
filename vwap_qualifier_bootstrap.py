"""
Historical 1-minute bootstrap and shared REST rate limiter for VWAP qualifier.

Seeds only fully closed 5-minute buckets strictly before the frozen cutoff
and never the startup bucket B0.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Callable, List, Optional, Protocol, Sequence

from candle_aggregation import (
    OneMinuteCandle,
    aggregate_session,
    ensure_ist,
)
from vwap_qualifier_features import hlc3_pv
from vwap_qualifier_state import bucket_end_dt
from vwap_qualifier_types import VwapProvenance

logger = logging.getLogger(__name__)


class HistoricalOneMinuteFetcher(Protocol):
    def fetch_one_minute(
        self,
        instrument_token: int,
        start: datetime,
        end_exclusive: datetime,
    ) -> List[OneMinuteCandle]: ...


class SharedRateLimiter:
    """Sleeping limiter for Kite historical REST. Never call from the tick thread."""

    def __init__(self, requests_per_second: float) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be > 0")
        self._min_interval = 1.0 / requests_per_second
        self._lock = threading.Lock()
        self._last = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()


def kite_row_to_one_minute(row: dict) -> Optional[OneMinuteCandle]:
    raw_dt = row.get("date")
    if raw_dt is None:
        return None
    if isinstance(raw_dt, datetime):
        ts = ensure_ist(raw_dt)
    else:
        ts = ensure_ist(datetime.fromisoformat(str(raw_dt)))
    ts = ts.replace(second=0, microsecond=0)
    try:
        return OneMinuteCandle(
            candle_time=ts.isoformat(timespec="seconds"),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=int(row["volume"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


class KiteHistoricalFetcher:
    def __init__(
        self,
        *,
        kite: Optional[object] = None,
        rate_limiter: Optional[SharedRateLimiter] = None,
        kite_factory: Optional[Callable[[], object]] = None,
    ) -> None:
        self._kite = kite
        self._rate_limiter = rate_limiter
        self._kite_factory = kite_factory

    def fetch_one_minute(
        self,
        instrument_token: int,
        start: datetime,
        end_exclusive: datetime,
    ) -> List[OneMinuteCandle]:
        if self._rate_limiter is not None:
            self._rate_limiter.acquire()
        kite = self._kite
        if kite is None:
            factory = self._kite_factory
            if factory is None:
                from login import _get_kite

                kite = _get_kite()
            else:
                kite = factory()
            self._kite = kite
        start_ist = ensure_ist(start)
        end_ist = ensure_ist(end_exclusive)
        to_date = end_ist - timedelta(seconds=1)
        raw = kite.historical_data(  # type: ignore[union-attr]
            instrument_token=instrument_token,
            from_date=start_ist,
            to_date=to_date,
            interval="minute",
            continuous=False,
            oi=False,
        )
        out: List[OneMinuteCandle] = []
        for row in raw or []:
            candle = kite_row_to_one_minute(row)
            if candle is None:
                continue
            ts = ensure_ist(datetime.fromisoformat(candle.candle_time))
            if start_ist <= ts < end_ist:
                out.append(candle)
        return out


def filter_one_minute_before_cutoff(
    candles: Sequence[OneMinuteCandle],
    cutoff: datetime,
) -> List[OneMinuteCandle]:
    """Keep 1m bars whose exclusive period end is <= cutoff."""
    cutoff_ist = ensure_ist(cutoff)
    kept: List[OneMinuteCandle] = []
    for candle in candles:
        start = ensure_ist(datetime.fromisoformat(candle.candle_time))
        if start + timedelta(minutes=1) <= cutoff_ist:
            kept.append(candle)
    return kept


def seed_closed_five_minute_contributions(
    candles: Sequence[OneMinuteCandle],
    *,
    cutoff: datetime,
    b0: datetime,
) -> List[tuple[datetime, float, int, VwapProvenance]]:
    """
    Aggregate 1m → 5m and keep only buckets fully closed at/before cutoff
    that are not the startup bucket B0.
    """
    cutoff_ist = ensure_ist(cutoff)
    b0_ist = ensure_ist(b0)
    kept = filter_one_minute_before_cutoff(candles, cutoff_ist)
    if not kept:
        return []
    generated, _skipped = aggregate_session(list(kept))
    out: List[tuple[datetime, float, int, VwapProvenance]] = []
    for bar in generated:
        start = ensure_ist(datetime.fromisoformat(bar.candle_time))
        if start == b0_ist:
            continue
        if bucket_end_dt(start) > cutoff_ist:
            continue
        pv = hlc3_pv(bar.high, bar.low, bar.close, bar.volume)
        out.append((start, pv, int(bar.volume), "bootstrap"))
    return out


def expected_bucket_one_minute_starts(bucket_start: datetime) -> List[datetime]:
    start = ensure_ist(bucket_start)
    return [start + timedelta(minutes=offset) for offset in range(5)]
