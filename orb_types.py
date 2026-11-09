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
BASELINE_GAP = "history_gap"
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
# GUC (gap-up continuation) only.
NOT_SELECTED_GAP = "gap_below_min"
NOT_SELECTED_NO_PREV_CLOSE = "no_prev_close"
NOT_SELECTED_STOP_TIGHT = "stop_below_min_pct"

# Arm lifecycle. ARMED is the only non-terminal state.
ARM_ARMED = "ARMED"
DECISION_TRIGGERED = "TRIGGERED"
DECISION_MISSED = "MISSED"
DECISION_EXPIRED = "EXPIRED"
DECISION_TYPES = (DECISION_TRIGGERED, DECISION_MISSED, DECISION_EXPIRED)

MISSED_OVERSHOOT = "overshoot_beyond_limit"
MISSED_NO_STOP = "no_valid_stop"
EXPIRED_CUTOFF = "not_triggered_by_cutoff"
# GUC only: at the watch start (09:30) the 15-minute volume was below the
# minimum, so the arm was dropped without a trade.
EXPIRED_WATCH_RVOL = "watch_rvol_below_min"


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
    # GUC: volume of the 1-minute bars before 09:30 (the watch start).
    watch_volume: int = 0
    watch_bars: int = 0


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
    # GUC: average volume of the bars before 09:30 over the same sessions.
    avg_watch_volume: Optional[float] = None


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
    # GUC: yesterday's close (the tick's OHLC close) and the opening gap.
    prev_close: Optional[float] = None
    gap_pct: Optional[float] = None


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
    # GUC only (None on an ORB arm). The stop is a fixed price set at arm
    # time: ``stop_distance`` is measured from ``level``, not from the fill.
    gap_pct: Optional[float] = None
    market_gap: Optional[float] = None
    preopen_volume: Optional[int] = None
    avg_watch_volume: Optional[float] = None


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


def orb_setup_id(
    instrument_token: int, session_date: str, rule_version: str, kind: str = "ORB"
) -> str:
    """One ORB (or GUC) setup per stock per day. Also the engine's trade id."""
    return f"{int(instrument_token)}|{session_date}|{kind}|{rule_version}"
