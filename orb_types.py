"""Shared ORB value types. No I/O."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

DIRECTION_UP = "UP"
DIRECTION_DOWN = "DOWN"

# Where an opening range's numbers came from.
SOURCE_TICKS = "ticks"      # live: the exchange's day OHLC and volume on ticks
SOURCE_CANDLES = "candles"  # replay: Kite 1-minute candles, as the backtest

# Why a stock has no usable baseline today.
BASELINE_NO_HISTORY = "no_history"
BASELINE_INSUFFICIENT = "insufficient_history"
BASELINE_STALE = "stale_history"
BASELINE_INCOMPLETE = "incomplete_history"
BASELINE_DEGENERATE = "degenerate_history"
BASELINE_CALENDAR = "calendar_unconfigured"

# Why a stock has no usable opening range today.
RANGE_NO_BASELINE = "no_baseline"
RANGE_NO_PREOPEN = "no_preopen_volume"
RANGE_NO_TICKS = "no_range_ticks"
RANGE_STALE_TICKS = "range_ticks_stale"
RANGE_BAD_OHLC = "bad_day_ohlc"
RANGE_BAD_VOLUME = "volume_went_backwards"
RANGE_NO_VOLUME = "no_range_volume"
RANGE_NO_DIRECTION = "no_direction"
RANGE_OFF_GRID = "level_off_tick_grid"
RANGE_INCOMPLETE_CANDLES = "incomplete_range_candles"

# Why a valid range was not armed.
NOT_SELECTED_RVOL = "rvol_below_min"
NOT_SELECTED_RANK = "not_in_top_n"

# Arm lifecycle. ARMED is the only non-terminal state.
ARM_ARMED = "ARMED"
DECISION_TRIGGERED = "TRIGGERED"
DECISION_MISSED = "MISSED"
DECISION_EXPIRED = "EXPIRED"
DECISION_TYPES = (DECISION_TRIGGERED, DECISION_MISSED, DECISION_EXPIRED)

MISSED_OVERSHOOT = "overshoot_beyond_limit"
MISSED_NO_STOP = "no_valid_stop"
EXPIRED_CUTOFF = "not_triggered_by_cutoff"


@dataclass(frozen=True)
class DaySummary:
    """One prior session of one stock, reduced to what the baseline needs."""

    session_date: str
    high: float
    low: float
    close: float
    or_volume: int      # volume of the 1-minute bars before 09:20
    or_bars: int        # how many such bars there were (5 on a clean day)
    first_bar: str      # "HH:MM" of the first candle
    last_bar: str       # "HH:MM" of the last candle


@dataclass(frozen=True)
class OrbBaseline:
    instrument_token: int
    tradingsymbol: str
    ready: bool
    reason: Optional[str] = None
    detail: Optional[str] = None
    atr14: Optional[float] = None
    avg_or_volume: Optional[float] = None
    history_as_of: Optional[str] = None
    sessions: Tuple[str, ...] = ()
    min_or_bars: Optional[int] = None


@dataclass(frozen=True)
class OpeningRange:
    """One stock's first five minutes, and whether it can be ranked."""

    instrument_token: int
    tradingsymbol: str
    valid: bool
    reason: Optional[str] = None
    source: str = SOURCE_TICKS
    or_open: Optional[float] = None
    or_high: Optional[float] = None
    or_low: Optional[float] = None
    or_close: Optional[float] = None
    or_volume: Optional[int] = None
    preopen_volume: Optional[int] = None
    direction: Optional[str] = None
    level: Optional[float] = None
    atr14: Optional[float] = None
    avg_or_volume: Optional[float] = None
    rvol: Optional[float] = None
    snapshot_at: Optional[datetime] = None
    preopen_at: Optional[datetime] = None


@dataclass(frozen=True)
class RankedRange:
    range: OpeningRange
    # 1-based position by RVOL among stocks at or above the minimum; None for
    # a stock that is invalid or below it.
    rank: Optional[int]
    selected: bool
    reason: Optional[str] = None


@dataclass(frozen=True)
class OrbArm:
    """A stock armed at 09:20: a stop-entry at ``level`` until the cutoff."""

    setup_id: str
    rule_version: str
    session_date: str
    instrument_token: int
    tradingsymbol: str
    direction: str
    level: float
    level_ticks: int
    tick_size: float
    atr14: float
    stop_distance: float
    rvol: float
    rank: int
    or_open: float
    or_high: float
    or_low: float
    or_close: float
    or_volume: int
    avg_or_volume: float


@dataclass(frozen=True)
class OrbDecision:
    """The one terminal outcome of an arm."""

    setup_id: str
    rule_version: str
    decision_type: str
    reason: Optional[str] = None
    tick_sequence: Optional[int] = None
    exchange_ts: Optional[datetime] = None
    trigger_price: Optional[float] = None
    planned_stop: Optional[float] = None
    overshoot: Optional[float] = None
    overshoot_atr: Optional[float] = None


def orb_setup_id(instrument_token: int, session_date: str, rule_version: str) -> str:
    """One ORB setup per stock per day. Also the engine's trade id."""
    return f"{int(instrument_token)}|{session_date}|ORB|{rule_version}"
