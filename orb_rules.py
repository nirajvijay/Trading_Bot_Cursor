"""ORB rules as pure functions: the opening range, the ranking, the breakout.

No I/O and no clock. Two ways to build an opening range lead into the same
ranking:

* ``range_from_snapshot`` is the live path. The numbers are the exchange's own
  day open / high / low and cumulative volume, read off the stock's last tick
  before 09:20:00. Kite's snapshot ticks arrive about once a second, so a range
  built from last-traded prices would miss the true high and low; the day
  OHLC on the same tick does not. Volume excludes the pre-open auction, as
  Kite's historical 09:15 candle does.
* ``range_from_candles`` is the backtest's definition from 1-minute candles
  (research orb.py). The post-market parity check and the golden tests use it.

Selection is the causal rule: rank at 09:20 on what is known at 09:20, arm the
top three by RVOL among those at 3x or more. (The first backtest ranked among
stocks that went on to break out, which cannot be known at 09:20; see
PR_B_PLAN.md.)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import List, Optional, Sequence, Tuple

from continuation_features import price_to_ticks
from orb_config import DEFAULT_ORB_CONFIG, OrbConfig
from orb_types import (
    DIRECTION_DOWN,
    DIRECTION_UP,
    NOT_SELECTED_RANK,
    NOT_SELECTED_RVOL,
    RANGE_BAD_OHLC,
    RANGE_BAD_VOLUME,
    RANGE_INCOMPLETE_CANDLES,
    RANGE_NO_BASELINE,
    RANGE_NO_DIRECTION,
    RANGE_NO_PREOPEN,
    RANGE_NO_TICKS,
    RANGE_NO_VOLUME,
    RANGE_OFF_GRID,
    RANGE_STALE_TICKS,
    SOURCE_CANDLES,
    SOURCE_TICKS,
    OpeningRange,
    OrbArm,
    OrbBaseline,
    RankedRange,
    orb_setup_id,
)

VERDICT_NONE = "none"
VERDICT_TRIGGER = "trigger"
VERDICT_MISSED = "missed"


@dataclass(frozen=True)
class TickSnapshot:
    """What one tick says about a stock's day so far."""

    at: datetime       # exchange time, IST
    sequence: int
    last_price: float
    day_open: float
    day_high: float
    day_low: float
    volume: int        # cumulative for the day, pre-open auction included


@dataclass(frozen=True)
class PreopenVolume:
    """Cumulative volume on the stock's last tick before 09:15:00."""

    at: datetime
    sequence: int
    volume: int


@dataclass(frozen=True)
class MinuteBar:
    time: str          # "HH:MM", IST
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class TickVerdict:
    kind: str
    overshoot: float = 0.0       # rupees beyond the level, never negative
    overshoot_atr: float = 0.0


def _sign(direction: str) -> int:
    return 1 if direction == DIRECTION_UP else -1


def _usable(baseline: Optional[OrbBaseline]) -> bool:
    return (
        baseline is not None
        and baseline.ready
        and baseline.atr14 is not None
        and baseline.avg_or_volume is not None
        and baseline.atr14 > 0
        and baseline.avg_or_volume > 0
    )


def _finish(
    *,
    instrument_token: int,
    tradingsymbol: str,
    baseline: OrbBaseline,
    source: str,
    or_open: float,
    or_high: float,
    or_low: float,
    or_close: float,
    or_volume: int,
    preopen_volume: Optional[int],
    snapshot_at: Optional[datetime],
    preopen_at: Optional[datetime],
    tick_size: Optional[float],
) -> OpeningRange:
    """Direction, level and RVOL for a range whose raw numbers are sound."""
    assert baseline.atr14 is not None and baseline.avg_or_volume is not None
    rvol = or_volume / baseline.avg_or_volume
    common = dict(
        instrument_token=instrument_token,
        tradingsymbol=tradingsymbol,
        source=source,
        or_open=or_open,
        or_high=or_high,
        or_low=or_low,
        or_close=or_close,
        or_volume=or_volume,
        preopen_volume=preopen_volume,
        atr14=baseline.atr14,
        avg_or_volume=baseline.avg_or_volume,
        rvol=rvol,
        snapshot_at=snapshot_at,
        preopen_at=preopen_at,
    )
    if or_close == or_open:
        return OpeningRange(valid=False, reason=RANGE_NO_DIRECTION, **common)
    direction = DIRECTION_UP if or_close > or_open else DIRECTION_DOWN
    level = or_high if direction == DIRECTION_UP else or_low
    if tick_size is not None:
        try:
            price_to_ticks(level, tick_size)
        except ValueError:
            return OpeningRange(
                valid=False, reason=RANGE_OFF_GRID, direction=direction, level=level, **common
            )
    return OpeningRange(valid=True, direction=direction, level=level, **common)


def range_from_snapshot(
    *,
    instrument_token: int,
    tradingsymbol: str,
    baseline: Optional[OrbBaseline],
    preopen: Optional[PreopenVolume],
    snapshot: Optional[TickSnapshot],
    range_end: datetime,
    tick_size: Optional[float] = None,
    config: OrbConfig = DEFAULT_ORB_CONFIG,
) -> OpeningRange:
    """The live opening range, from the stock's last tick before ``range_end``."""

    def invalid(reason: str, **known) -> OpeningRange:
        return OpeningRange(
            instrument_token=instrument_token,
            tradingsymbol=tradingsymbol,
            valid=False,
            reason=reason,
            source=SOURCE_TICKS,
            atr14=None if baseline is None else baseline.atr14,
            avg_or_volume=None if baseline is None else baseline.avg_or_volume,
            **known,
        )

    seen = {}
    if snapshot is not None:
        seen = dict(
            or_open=snapshot.day_open,
            or_high=snapshot.day_high,
            or_low=snapshot.day_low,
            or_close=snapshot.last_price,
            snapshot_at=snapshot.at,
        )
    if preopen is not None:
        seen.update(preopen_volume=preopen.volume, preopen_at=preopen.at)

    if not _usable(baseline):
        return invalid(RANGE_NO_BASELINE, **seen)
    if preopen is None:
        return invalid(RANGE_NO_PREOPEN, **seen)
    if snapshot is None:
        return invalid(RANGE_NO_TICKS, **seen)
    age = (range_end - snapshot.at).total_seconds()
    if age < 0 or age > config.max_snapshot_age_seconds:
        return invalid(RANGE_STALE_TICKS, **seen)
    prices = (snapshot.day_open, snapshot.day_high, snapshot.day_low, snapshot.last_price)
    if not all(isinstance(p, (int, float)) and math.isfinite(p) and p > 0 for p in prices):
        return invalid(RANGE_BAD_OHLC, **seen)
    if not (
        snapshot.day_low <= snapshot.day_open <= snapshot.day_high
        and snapshot.day_low <= snapshot.last_price <= snapshot.day_high
    ):
        return invalid(RANGE_BAD_OHLC, **seen)
    if preopen.volume < 0 or snapshot.volume < preopen.volume:
        return invalid(RANGE_BAD_VOLUME, **seen)
    or_volume = int(snapshot.volume) - int(preopen.volume)
    if or_volume <= 0:
        return invalid(RANGE_NO_VOLUME, or_volume=or_volume, **seen)
    assert baseline is not None
    return _finish(
        instrument_token=instrument_token,
        tradingsymbol=tradingsymbol,
        baseline=baseline,
        source=SOURCE_TICKS,
        or_open=float(snapshot.day_open),
        or_high=float(snapshot.day_high),
        or_low=float(snapshot.day_low),
        or_close=float(snapshot.last_price),
        or_volume=or_volume,
        preopen_volume=int(preopen.volume),
        snapshot_at=snapshot.at,
        preopen_at=preopen.at,
        tick_size=tick_size,
    )


def range_bars(bars: Sequence[MinuteBar], config: OrbConfig = DEFAULT_ORB_CONFIG) -> List[MinuteBar]:
    """The day's bars that fall inside the opening range."""
    end = config.range_end.strftime("%H:%M")
    return [bar for bar in bars if bar.time < end]


def range_from_candles(
    *,
    instrument_token: int,
    tradingsymbol: str,
    baseline: Optional[OrbBaseline],
    bars: Sequence[MinuteBar],
    config: OrbConfig = DEFAULT_ORB_CONFIG,
) -> OpeningRange:
    """The backtest's opening range, from one day's 1-minute candles."""
    opening = range_bars(bars, config)
    expected = (
        config.range_end.hour * 60
        + config.range_end.minute
        - config.range_start.hour * 60
        - config.range_start.minute
    )
    known = {}
    if opening:
        known = dict(
            or_open=float(opening[0].open),
            or_high=float(max(bar.high for bar in opening)),
            or_low=float(min(bar.low for bar in opening)),
            or_close=float(opening[-1].close),
            or_volume=int(sum(bar.volume for bar in opening)),
        )

    def invalid(reason: str) -> OpeningRange:
        return OpeningRange(
            instrument_token=instrument_token,
            tradingsymbol=tradingsymbol,
            valid=False,
            reason=reason,
            source=SOURCE_CANDLES,
            atr14=None if baseline is None else baseline.atr14,
            avg_or_volume=None if baseline is None else baseline.avg_or_volume,
            **known,
        )

    if not _usable(baseline):
        return invalid(RANGE_NO_BASELINE)
    if len(opening) < expected:
        return invalid(RANGE_INCOMPLETE_CANDLES)
    assert baseline is not None
    return _finish(
        instrument_token=instrument_token,
        tradingsymbol=tradingsymbol,
        baseline=baseline,
        source=SOURCE_CANDLES,
        preopen_volume=None,
        snapshot_at=None,
        preopen_at=None,
        tick_size=None,
        **known,
    )


def rank_ranges(
    ranges: Sequence[OpeningRange], config: OrbConfig = DEFAULT_ORB_CONFIG
) -> List[RankedRange]:
    """Arm the top ``top_n`` by RVOL among valid ranges at ``min_rvol`` or more.

    Returned best first: the armed stocks, then the rest in play by rank, then
    the other valid ranges by RVOL, then the unusable ones by symbol.
    """
    in_play = sorted(
        (r for r in ranges if r.valid and r.rvol is not None and r.rvol >= config.min_rvol),
        key=lambda r: (-float(r.rvol), r.tradingsymbol),
    )
    rank_of = {r.instrument_token: index + 1 for index, r in enumerate(in_play)}
    ranked: List[RankedRange] = []
    for item in ranges:
        rank = rank_of.get(item.instrument_token)
        if not item.valid:
            ranked.append(RankedRange(item, None, False, item.reason))
        elif rank is None:
            ranked.append(RankedRange(item, None, False, NOT_SELECTED_RVOL))
        elif rank > config.top_n:
            ranked.append(RankedRange(item, rank, False, NOT_SELECTED_RANK))
        else:
            ranked.append(RankedRange(item, rank, True, None))

    def order(entry: RankedRange) -> Tuple:
        if entry.rank is not None:
            return (0, entry.rank, entry.range.tradingsymbol)
        if entry.range.valid:
            return (1, -float(entry.range.rvol or 0.0), entry.range.tradingsymbol)
        return (2, 0.0, entry.range.tradingsymbol)

    return sorted(ranked, key=order)


def stop_distance(atr: float, config: OrbConfig = DEFAULT_ORB_CONFIG) -> float:
    """Rupees from the trigger price to the stop: 0.35 x ATR14."""
    return config.stop_atr_mult * atr


def make_arm(
    ranked: RankedRange,
    *,
    session_date: str,
    tick_size: float,
    config: OrbConfig = DEFAULT_ORB_CONFIG,
) -> OrbArm:
    """The arm for a selected range. Raises ValueError for anything else."""
    item = ranked.range
    if not ranked.selected or ranked.rank is None or not item.valid:
        raise ValueError(f"{item.tradingsymbol} is not selected")
    if item.direction not in (DIRECTION_UP, DIRECTION_DOWN) or item.level is None:
        raise ValueError(f"{item.tradingsymbol} has no direction or level")
    assert item.atr14 is not None and item.rvol is not None
    assert item.avg_or_volume is not None and item.or_volume is not None
    assert None not in (item.or_open, item.or_high, item.or_low, item.or_close)
    return OrbArm(
        setup_id=orb_setup_id(item.instrument_token, session_date, config.rule_version),
        rule_version=config.rule_version,
        session_date=session_date,
        instrument_token=item.instrument_token,
        tradingsymbol=item.tradingsymbol,
        direction=item.direction,
        level=float(item.level),
        level_ticks=price_to_ticks(float(item.level), tick_size),
        tick_size=float(tick_size),
        atr14=float(item.atr14),
        stop_distance=stop_distance(float(item.atr14), config),
        rvol=float(item.rvol),
        rank=int(ranked.rank),
        or_open=float(item.or_open),
        or_high=float(item.or_high),
        or_low=float(item.or_low),
        or_close=float(item.or_close),
        or_volume=int(item.or_volume),
        avg_or_volume=float(item.avg_or_volume),
    )


def planned_stop(
    *, direction: str, reference_price: float, distance: float, tick_size: float
) -> Optional[float]:
    """The stop ``distance`` rupees from ``reference_price``, on the tick grid.

    Rounded away from the trade (down for a long, up for a short), so the stop
    is never tighter than planned. None when no valid price results.
    """
    try:
        if not (
            math.isfinite(reference_price)
            and math.isfinite(distance)
            and math.isfinite(tick_size)
        ):
            return None
        if reference_price <= 0 or distance <= 0 or tick_size <= 0:
            return None
        tick = Decimal(str(tick_size))
        reference = Decimal(str(reference_price))
        gap = Decimal(str(distance))
        if direction == DIRECTION_UP:
            ticks = ((reference - gap) / tick).to_integral_value(rounding=ROUND_FLOOR)
        elif direction == DIRECTION_DOWN:
            ticks = ((reference + gap) / tick).to_integral_value(rounding=ROUND_CEILING)
        else:
            return None
        price = float(ticks * tick)
    except (ArithmeticError, ValueError):
        return None
    if price <= 0:
        return None
    if direction == DIRECTION_UP and price >= reference_price:
        return None
    if direction == DIRECTION_DOWN and price <= reference_price:
        return None
    return price


def evaluate_price(
    arm: OrbArm, last_price: float, config: OrbConfig = DEFAULT_ORB_CONFIG
) -> TickVerdict:
    """Has this price reached the level, and is it still close enough to it?

    Works in whole ticks so a price exactly at the level always counts.
    Raises ValueError for a price off the tick grid.
    """
    ticks = price_to_ticks(float(last_price), arm.tick_size)
    beyond = (ticks - arm.level_ticks) * _sign(arm.direction)
    if beyond < 0:
        return TickVerdict(VERDICT_NONE)
    overshoot = float(Decimal(beyond) * Decimal(str(arm.tick_size)))
    overshoot_atr = overshoot / arm.atr14 if arm.atr14 > 0 else math.inf
    if overshoot_atr > config.max_overshoot_atr:
        return TickVerdict(VERDICT_MISSED, overshoot, overshoot_atr)
    return TickVerdict(VERDICT_TRIGGER, overshoot, overshoot_atr)


def first_breakout_bar(
    bars: Sequence[MinuteBar],
    *,
    direction: str,
    level: float,
    config: OrbConfig = DEFAULT_ORB_CONFIG,
) -> Optional[Tuple[MinuteBar, float]]:
    """The backtest's breakout: the first bar after the range to touch the level.

    Returns (bar, entry price), where the entry is the level, or the bar's
    open when it opened beyond the level. None when nothing breaks out before
    the cutoff. ``bars`` is the whole day, oldest first.
    """
    start = config.range_end.strftime("%H:%M")
    end = config.trigger_end.strftime("%H:%M")
    for bar in bars:
        if bar.time < start:
            continue
        if bar.time >= end:
            break
        if direction == DIRECTION_UP:
            if bar.high >= level:
                return bar, max(level, bar.open)
        elif bar.low <= level:
            return bar, min(level, bar.open)
    return None
