"""
Live 5-minute candle builder from completed 1-minute candles.

Market-data owned. Emits only full buckets (exactly five consecutive
in-session 1m bars). Never invents or interpolates missing minutes.
Mid-session starts discard the first incomplete exchange-time bucket.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set

from candle_aggregation import (
    BUCKET_SIZE,
    CompletedFiveMinuteCandle,
    CompletedOneMinuteCandle,
    ensure_ist,
    floor_five_minute_bucket_start,
    is_in_session,
    minute_of_day_from_datetime,
)

logger = logging.getLogger(__name__)

OnFiveMinuteCandle = Callable[[CompletedFiveMinuteCandle], None]


@dataclass
class _BucketBuffer:
    bucket_start_minute: int
    session_date: str
    candles: Dict[int, CompletedOneMinuteCandle]


class LiveFiveMinuteCandleBuilder:
    """Buffer completed 1m candles per token and emit completed 5m bars."""

    def __init__(
        self,
        *,
        on_five_minute: Optional[OnFiveMinuteCandle] = None,
    ) -> None:
        self._on_five_minute = on_five_minute
        self._buffers: Dict[int, _BucketBuffer] = {}
        self._candles_seen = 0
        self._buckets_emitted = 0
        self._buckets_incomplete = 0
        self._first_incomplete_logged: Set[int] = set()

    @property
    def candles_seen(self) -> int:
        return self._candles_seen

    @property
    def buckets_emitted(self) -> int:
        return self._buckets_emitted

    @property
    def buckets_incomplete(self) -> int:
        """Incomplete buckets discarded (e.g. mid-session join / gap)."""
        return self._buckets_incomplete

    def on_one_minute(self, candle: CompletedOneMinuteCandle) -> Optional[CompletedFiveMinuteCandle]:
        """Ingest one completed 1m candle; return completed 5m if bucket fills."""
        self._candles_seen += 1
        candle_time = ensure_ist(candle.candle_time)
        minute = minute_of_day_from_datetime(candle_time)
        if not is_in_session(minute):
            return None

        bucket_start = floor_five_minute_bucket_start(minute)
        if bucket_start is None:
            return None

        session_date = candle_time.date().isoformat()
        token = candle.instrument_token
        buf = self._buffers.get(token)

        if (
            buf is None
            or buf.bucket_start_minute != bucket_start
            or buf.session_date != session_date
        ):
            if buf is not None and len(buf.candles) < BUCKET_SIZE:
                self._discard_incomplete_bucket(token, buf)
            self._buffers[token] = _BucketBuffer(
                bucket_start_minute=bucket_start,
                session_date=session_date,
                candles={},
            )
            buf = self._buffers[token]

        buf.candles[minute] = candle
        if len(buf.candles) < BUCKET_SIZE:
            return None

        expected_minutes = list(range(bucket_start, bucket_start + BUCKET_SIZE))
        if any(m not in buf.candles for m in expected_minutes):
            # Gap inside bucket — do not emit partial aggregates.
            return None

        ordered = [buf.candles[m] for m in expected_minutes]
        completed = self._aggregate(token, ordered, session_date)
        # Clear buffer so duplicates of the last minute do not re-emit.
        del self._buffers[token]
        self._buckets_emitted += 1

        if self._on_five_minute is not None:
            self._on_five_minute(completed)
        return completed

    def _discard_incomplete_bucket(self, token: int, buf: _BucketBuffer) -> None:
        self._buckets_incomplete += 1
        first = token not in self._first_incomplete_logged
        if first:
            self._first_incomplete_logged.add(token)
        logger.info(
            "5m incomplete bucket discarded token=%s session=%s bucket_start=%s "
            "constituents=%d/%d mid_session_first=%s",
            token,
            buf.session_date,
            buf.bucket_start_minute,
            len(buf.candles),
            BUCKET_SIZE,
            first,
        )

    @staticmethod
    def _aggregate(
        instrument_token: int,
        candles: List[CompletedOneMinuteCandle],
        session_date: str,
    ) -> CompletedFiveMinuteCandle:
        first = candles[0]
        candle_time = ensure_ist(first.candle_time).replace(second=0, microsecond=0)
        # Align candle_time to bucket start (first minute of the group).
        return CompletedFiveMinuteCandle(
            instrument_token=instrument_token,
            candle_time=candle_time,
            open=first.open,
            high=max(c.high for c in candles),
            low=min(c.low for c in candles),
            close=candles[-1].close,
            volume=sum(c.volume for c in candles),
            session_date=session_date,
            constituent_count=len(candles),
            all_volume_reliable=all(c.volume_reliable for c in candles),
            any_partial=any(c.is_partial for c in candles),
            all_full_coverage=all(c.has_full_minute_coverage for c in candles),
            tick_count=sum(c.tick_count for c in candles),
        )
