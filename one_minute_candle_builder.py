"""
Live 1-minute OHLCV candle builder from TickEvent streams.

Consumes Kite-agnostic TickEvent objects and emits completed 1-minute candles
via callback. No database, strategy, or receiver wiring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, Optional, Set, Tuple
from zoneinfo import ZoneInfo

from candle_aggregation import (
    SESSION_MINUTE_END,
    SESSION_MINUTE_START,
    CompletionReason,
    CompletedOneMinuteCandle,
    ensure_ist,
    is_in_session,
    minute_of_day_from_datetime,
    minute_start_from_exchange_timestamp,
)
from tick_event import IST, TickEvent

EventKey = Tuple[datetime, int]
CandleCallback = Callable[[CompletedOneMinuteCandle], None]

_IST = ZoneInfo(IST)


@dataclass(frozen=True)
class BuilderMetrics:
    late_ticks_dropped: int
    duplicate_ticks_ignored: int
    out_of_session_ticks: int
    invalid_price_ticks: int
    cumulative_volume_decreases: int
    candles_emitted: int


@dataclass
class _FeedContinuityState:
    feed_healthy_since: Optional[datetime] = None


@dataclass
class _InstrumentVolumeState:
    session_date: str
    day_started_at_0915: bool
    minute_open_baseline: int
    latest_volume_key: Optional[EventKey] = None
    latest_cumulative_volume: int = 0


@dataclass
class _ActiveCandleState:
    candle_time: datetime
    open: float
    high: float
    low: float
    close: float
    has_full_minute_coverage: bool
    open_key: Optional[EventKey] = None
    close_key: Optional[EventKey] = None
    completed_volume_segments: int = 0
    segment_baseline: int = 0
    segment_max_cumulative: int = 0
    tick_count: int = 0
    seen_sequences: Set[int] = field(default_factory=set)
    volume_reliable: bool = True


def _is_minute_complete(candle_time: datetime, now: datetime) -> bool:
    """True when wall-clock IST has reached the start of the next minute."""
    candle_start = ensure_ist(candle_time)
    now_ist = ensure_ist(now)
    return now_ist >= candle_start + timedelta(minutes=1)


class OneMinuteCandleBuilder:
    def __init__(
        self,
        on_candle: CandleCallback,
        feed_ready_at: Optional[datetime] = None,
    ) -> None:
        self._on_candle = on_candle
        self._feed = _FeedContinuityState(
            feed_healthy_since=ensure_ist(feed_ready_at) if feed_ready_at is not None else None,
        )
        self._active: Dict[int, _ActiveCandleState] = {}
        self._volume_state: Dict[int, _InstrumentVolumeState] = {}
        self._last_emitted_candle_time: Dict[int, datetime] = {}
        self._late_ticks_dropped = 0
        self._duplicate_ticks_ignored = 0
        self._out_of_session_ticks = 0
        self._invalid_price_ticks = 0
        self._cumulative_volume_decreases = 0
        self._candles_emitted = 0

    @property
    def metrics(self) -> BuilderMetrics:
        return BuilderMetrics(
            late_ticks_dropped=self._late_ticks_dropped,
            duplicate_ticks_ignored=self._duplicate_ticks_ignored,
            out_of_session_ticks=self._out_of_session_ticks,
            invalid_price_ticks=self._invalid_price_ticks,
            cumulative_volume_decreases=self._cumulative_volume_decreases,
            candles_emitted=self._candles_emitted,
        )

    def mark_feed_interrupted(self, interrupted_at: datetime) -> None:
        """Clear feed continuity and mark active candles as lacking full coverage."""
        del interrupted_at  # timestamp reserved for future audit/logging
        self._feed.feed_healthy_since = None
        for active in self._active.values():
            active.has_full_minute_coverage = False

    def mark_feed_restored(self, restored_at: datetime) -> None:
        """Record when continuous healthy feed coverage began."""
        self._feed.feed_healthy_since = ensure_ist(restored_at)

    def on_tick(self, tick: TickEvent) -> None:
        exchange_ts = ensure_ist(tick.exchange_timestamp)
        minute_of_day = minute_of_day_from_datetime(exchange_ts)
        session_date = ensure_ist(exchange_ts).date().isoformat()

        if not is_in_session(minute_of_day):
            self._out_of_session_ticks += 1
            return

        if tick.last_price <= 0:
            self._invalid_price_ticks += 1
            return

        token = tick.instrument_token
        tick_minute_start = minute_start_from_exchange_timestamp(exchange_ts)

        last_emitted = self._last_emitted_candle_time.get(token)
        if last_emitted is not None and tick_minute_start <= last_emitted:
            self._late_ticks_dropped += 1
            return

        active = self._active.get(token)
        if active is not None:
            active_session = self._volume_state[token].session_date
            if active_session != session_date:
                self._finalize(token, "day_rollover", now=exchange_ts)
                self._volume_state.pop(token, None)
                active = None
            elif tick_minute_start < active.candle_time:
                self._late_ticks_dropped += 1
                return
            elif tick_minute_start > active.candle_time:
                self._finalize(token, "minute_transition", now=exchange_ts)
                active = None

        if active is None:
            vol_state = self._ensure_volume_state(
                token,
                session_date,
                tick_minute_start,
                tick,
            )
            active = self._start_active_candle(tick_minute_start, vol_state)
            self._active[token] = active

        self._apply_tick(active, tick, exchange_ts)

    def flush(
        self,
        instrument_token: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> None:
        now_ist = ensure_ist(now or datetime.now(_IST))
        if instrument_token is None:
            tokens = list(self._active.keys())
        else:
            tokens = [instrument_token]
        for token in tokens:
            active = self._active.get(token)
            if active is None:
                continue
            minute = minute_of_day_from_datetime(active.candle_time)
            if _is_minute_complete(active.candle_time, now_ist) and minute == SESSION_MINUTE_END:
                reason: CompletionReason = "session_end"
            else:
                reason = "shutdown_flush"
            self._finalize(token, reason, now=now_ist)

    def _has_full_minute_coverage_at(self, candle_time: datetime) -> bool:
        since = self._feed.feed_healthy_since
        if since is None:
            return False
        return ensure_ist(since) <= ensure_ist(candle_time)

    def _ensure_volume_state(
        self,
        token: int,
        session_date: str,
        tick_minute_start: datetime,
        tick: TickEvent,
    ) -> _InstrumentVolumeState:
        existing = self._volume_state.get(token)
        if existing is not None and existing.session_date == session_date:
            return existing

        minute_of_day = minute_of_day_from_datetime(tick_minute_start)
        if minute_of_day == SESSION_MINUTE_START:
            minute_open_baseline = 0
            day_started_at_0915 = True
        else:
            minute_open_baseline = tick.volume_traded
            day_started_at_0915 = False

        vol_state = _InstrumentVolumeState(
            session_date=session_date,
            day_started_at_0915=day_started_at_0915,
            minute_open_baseline=minute_open_baseline,
        )
        self._volume_state[token] = vol_state
        return vol_state

    def _start_active_candle(
        self,
        tick_minute_start: datetime,
        vol_state: _InstrumentVolumeState,
    ) -> _ActiveCandleState:
        return _ActiveCandleState(
            candle_time=tick_minute_start,
            open=0.0,
            high=0.0,
            low=0.0,
            close=0.0,
            has_full_minute_coverage=self._has_full_minute_coverage_at(tick_minute_start),
            completed_volume_segments=0,
            segment_baseline=vol_state.minute_open_baseline,
            segment_max_cumulative=vol_state.minute_open_baseline,
        )

    def _apply_tick(
        self,
        active: _ActiveCandleState,
        tick: TickEvent,
        exchange_ts: datetime,
    ) -> None:
        if tick.sequence in active.seen_sequences:
            self._duplicate_ticks_ignored += 1
            return

        active.seen_sequences.add(tick.sequence)
        active.tick_count += 1

        event_key: EventKey = (exchange_ts, tick.sequence)
        price = tick.last_price

        if active.open_key is None:
            active.open_key = event_key
            active.close_key = event_key
            active.open = price
            active.high = price
            active.low = price
            active.close = price
        else:
            if event_key < active.open_key:
                active.open_key = event_key
                active.open = price
            if active.close_key is not None and event_key > active.close_key:
                active.close_key = event_key
                active.close = price
            active.high = max(active.high, price)
            active.low = min(active.low, price)

        vol_state = self._volume_state[tick.instrument_token]
        self._apply_volume(active, tick, event_key, vol_state)

    def _apply_volume(
        self,
        active: _ActiveCandleState,
        tick: TickEvent,
        event_key: EventKey,
        vol_state: _InstrumentVolumeState,
    ) -> None:
        if vol_state.latest_volume_key is None:
            vol_state.latest_volume_key = event_key
            vol_state.latest_cumulative_volume = tick.volume_traded
            active.segment_max_cumulative = max(
                active.segment_max_cumulative,
                tick.volume_traded,
            )
            return

        if event_key < vol_state.latest_volume_key:
            active.segment_max_cumulative = max(
                active.segment_max_cumulative,
                tick.volume_traded,
            )
            return

        if tick.volume_traded < vol_state.latest_cumulative_volume:
            active.completed_volume_segments += max(
                0,
                active.segment_max_cumulative - active.segment_baseline,
            )
            active.segment_baseline = tick.volume_traded
            active.segment_max_cumulative = tick.volume_traded
            active.volume_reliable = False
            self._cumulative_volume_decreases += 1

        vol_state.latest_volume_key = event_key
        vol_state.latest_cumulative_volume = tick.volume_traded
        active.segment_max_cumulative = max(
            active.segment_max_cumulative,
            tick.volume_traded,
        )

    def _compute_volume(self, active: _ActiveCandleState) -> int:
        volume = active.completed_volume_segments + max(
            0,
            active.segment_max_cumulative - active.segment_baseline,
        )
        return max(0, volume)

    def _finalize(
        self,
        token: int,
        completion_reason: CompletionReason,
        now: Optional[datetime] = None,
    ) -> None:
        active = self._active.get(token)
        if active is None or active.tick_count == 0:
            if active is not None:
                del self._active[token]
            return

        if completion_reason == "day_rollover":
            active.has_full_minute_coverage = False

        now_ist = ensure_ist(now or datetime.now(_IST))
        minute_complete = _is_minute_complete(active.candle_time, now_ist)
        is_partial = (not minute_complete) or (not active.has_full_minute_coverage)

        completed = CompletedOneMinuteCandle(
            instrument_token=token,
            candle_time=active.candle_time,
            open=active.open,
            high=active.high,
            low=active.low,
            close=active.close,
            volume=self._compute_volume(active),
            tick_count=active.tick_count,
            volume_reliable=active.volume_reliable,
            completion_reason=completion_reason,
            has_full_minute_coverage=active.has_full_minute_coverage,
            is_partial=is_partial,
        )

        self._on_candle(completed)

        self._last_emitted_candle_time[token] = completed.candle_time
        self._candles_emitted += 1
        vol_state = self._volume_state.get(token)
        if vol_state is not None:
            vol_state.minute_open_baseline = vol_state.latest_cumulative_volume
        del self._active[token]
