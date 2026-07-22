"""
Pure 5-minute candle aggregation from 1-minute OHLCV bars.

No database, logging, CLI, or project-configuration dependencies.
Shared by the historical generator and the future live tick logger.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from tick_event import IST

# NSE cash session minutes (inclusive), minutes since midnight IST.
SESSION_MINUTE_START = 9 * 60 + 15  # 09:15 → 555
SESSION_MINUTE_END = 15 * 60 + 29  # 15:29 → 929
BUCKET_SIZE = 5

_IST = ZoneInfo(IST)


@dataclass(frozen=True)
class OneMinuteCandle:
    candle_time: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class FiveMinuteCandle:
    candle_time: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class CompletedOneMinuteCandle:
    instrument_token: int
    candle_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    tick_count: int
    volume_reliable: bool

    def to_one_minute_candle(self) -> OneMinuteCandle:
        """Convert candle_time to ISO text only at the persistence boundary."""
        return OneMinuteCandle(
            candle_time=self.candle_time.isoformat(timespec="seconds"),
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
        )


def ensure_ist(dt: datetime) -> datetime:
    """Normalize naive or aware datetimes to Asia/Kolkata."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_IST)
    return dt.astimezone(_IST)


def minute_of_day_from_datetime(dt: datetime) -> int:
    """Return IST wall-clock minutes since midnight."""
    ist_dt = ensure_ist(dt)
    return ist_dt.hour * 60 + ist_dt.minute


def minute_start_from_exchange_timestamp(dt: datetime) -> datetime:
    """Floor exchange_timestamp to IST minute start as timezone-aware datetime."""
    ist_dt = ensure_ist(dt)
    return ist_dt.replace(second=0, microsecond=0)


def minute_of_day_from_candle_time(candle_time: str) -> int:
    """Return IST wall-clock minutes since midnight (09:15 → 555)."""
    dt = datetime.fromisoformat(candle_time)
    return dt.hour * 60 + dt.minute


def session_date_from_candle_time(candle_time: str) -> str:
    """Return trading date as YYYY-MM-DD from an ISO candle timestamp."""
    return candle_time[:10]


def is_in_session(minute_of_day: int) -> bool:
    return SESSION_MINUTE_START <= minute_of_day <= SESSION_MINUTE_END


def five_minute_bucket_start(minute_of_day: int) -> int | None:
    """Return aligned 5m bucket start minute, or None if outside session."""
    if not is_in_session(minute_of_day):
        return None
    offset = minute_of_day - SESSION_MINUTE_START
    if offset % BUCKET_SIZE != 0:
        return None
    return minute_of_day


def expected_bucket_starts() -> list[int]:
    """Return all valid 5m bucket start minutes for a full NSE session."""
    return list(range(SESSION_MINUTE_START, SESSION_MINUTE_END + 1, BUCKET_SIZE))


def aggregate_five_candles(candles: list[OneMinuteCandle]) -> FiveMinuteCandle:
    """
    Aggregate exactly five consecutive 1-minute candles into one 5-minute bar.

    Raises ValueError when input is not exactly five consecutive in-session
    candles from the same trading date.
    """
    if len(candles) != BUCKET_SIZE:
        raise ValueError(f"expected {BUCKET_SIZE} candles, got {len(candles)}")

    session_dates = {session_date_from_candle_time(c.candle_time) for c in candles}
    if len(session_dates) != 1:
        raise ValueError("all candles must belong to the same trading session date")

    minutes = [minute_of_day_from_candle_time(c.candle_time) for c in candles]
    for minute in minutes:
        if not is_in_session(minute):
            raise ValueError(f"candle minute {minute} is outside NSE session")

    expected = list(range(minutes[0], minutes[0] + BUCKET_SIZE))
    if minutes != expected:
        raise ValueError("candles must be five consecutive 1-minute bars")

    return FiveMinuteCandle(
        candle_time=candles[0].candle_time,
        open=candles[0].open,
        high=max(c.high for c in candles),
        low=min(c.low for c in candles),
        close=candles[-1].close,
        volume=sum(c.volume for c in candles),
    )


def aggregate_session(
    one_minute_candles: list[OneMinuteCandle],
) -> tuple[list[FiveMinuteCandle], int]:
    """
    Aggregate one trading session's 1-minute candles into 5-minute bars.

    The caller must pass candles for a single trading date only.
    Returns (generated_5m_candles, skipped_bucket_count).
    """
    if not one_minute_candles:
        return [], 0

    session_dates = {
        session_date_from_candle_time(c.candle_time) for c in one_minute_candles
    }
    if len(session_dates) != 1:
        raise ValueError("one_minute_candles must belong to a single trading date")

    by_minute: dict[int, OneMinuteCandle] = {}
    for candle in one_minute_candles:
        minute = minute_of_day_from_candle_time(candle.candle_time)
        if not is_in_session(minute):
            continue
        by_minute[minute] = candle

    generated: list[FiveMinuteCandle] = []
    skipped = 0

    for bucket_start in expected_bucket_starts():
        group = [
            by_minute.get(bucket_start + offset)
            for offset in range(BUCKET_SIZE)
        ]
        if any(candle is None for candle in group):
            skipped += 1
            continue

        generated.append(aggregate_five_candles(group))  # type: ignore[arg-type]

    return generated, skipped
