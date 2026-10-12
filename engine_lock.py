"""ARB's profit-lock stop: the backtest's "lock" exit, as pure functions.

The rule (research trail_research.py ``BEG_be1_l0.25_a2_f0.25`` for the
funnel, orb.py ``lock`` for ORB; they are the same rule):

* The **peak** is the best price since entry: the high (long) or low (short)
  of every completed 1-minute bar after the entry minute. The entry minute
  itself counts its close only, because the earlier part of that bar was
  before the trade.
* Once the peak is at least **+1R**, the stop goes to entry **+0.25R**.
* Once the peak is at least **+2R**, the stop keeps **25% of the peak gain**
  (never less than +0.25R).
* The stop is never placed closer than **one tick inside that bar's close**.
* It only ever tightens.

**Judged once per completed bar, never per tick.** The Rs 41.9k/month figure
was tested on 1-minute bars; a tick-level version is a different strategy.

**A fold, with no memory of its own.** ``fold`` replays every bar since entry
and always gives the same answer for the same bars, so a restart recomputes
the stop instead of having to remember a peak, and the post-market check can
re-derive every move.

Arithmetic is exact (Decimal) and every stop is on the instrument's tick
grid, away from the market, like every other stop in the engine. The backtest
left ORB stops off the grid and rounded a short's lock stop toward the
market; see Reference/arb_strategy_2026-09/PR_C_PLAN.md for what that costs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable, Optional

import engine_clock
from engine_trailing import is_long, is_tighter, round_stop

# Peak gain, in R, that arms the lock, and where the stop goes then.
LOCK_FROM_R = Decimal("1")
LOCK_LEVEL_R = Decimal("0.25")
# Peak gain, in R, from which the stop keeps a share of the peak gain.
GIVEBACK_FROM_R = Decimal("2")
GIVEBACK_KEEP = Decimal("0.25")

# How a bar's minute is written: the form live_1m_candles.candle_time uses
# (the minute's start, in IST). Keys are compared as text.
MINUTE_KEY_FORMAT = "%Y-%m-%dT%H:%M:00+05:30"


def minute_key(moment: datetime) -> str:
    """The key of the minute ``moment`` falls in. ``moment`` must carry a zone."""
    return engine_clock.to_ist(moment).strftime(MINUTE_KEY_FORMAT)


STAGE_INITIAL = "initial"
STAGE_LOCKED = "locked"
STAGE_GIVEBACK = "giveback"


@dataclass(frozen=True)
class LockBar:
    """One completed 1-minute bar. ``minute`` is only compared and reported."""

    minute: str
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class LockState:
    """Where the rule stands after the bars folded so far."""

    bars: int = 0
    last_minute: Optional[str] = None
    # Best price since entry; None until a bar has been folded.
    peak: Optional[float] = None
    # The tightest stop the rule has asked for; None below +1R.
    target: Optional[float] = None
    stage: str = STAGE_INITIAL


def advance(
    state: LockState,
    bar: LockBar,
    *,
    direction: str,
    entry: float,
    initial_stop: float,
    tick_size: float,
    entry_bar: bool = False,
) -> LockState:
    """Fold one more completed bar into the state.

    R is the distance from ``entry`` to ``initial_stop``, taken exactly: a
    float subtraction can turn 1.6 into 1.5999999999999943, and "the peak is
    at least +1R" must not depend on that.
    """
    long = is_long(direction)
    entry_d = Decimal(str(entry))
    r = abs(entry_d - Decimal(str(initial_stop)))
    tick = Decimal(str(tick_size))

    seen = Decimal(str(bar.close if entry_bar else (bar.high if long else bar.low)))
    peak = entry_d if state.peak is None else Decimal(str(state.peak))
    if (seen > peak) if long else (seen < peak):
        peak = seen

    gain = (peak - entry_d) if long else (entry_d - peak)
    target = state.target
    stage = state.stage
    if r > 0 and gain >= LOCK_FROM_R * r:
        if gain >= GIVEBACK_FROM_R * r:
            locked = max(GIVEBACK_KEEP * gain, LOCK_LEVEL_R * r)
            stage = STAGE_GIVEBACK
        else:
            locked = LOCK_LEVEL_R * r
            if stage == STAGE_INITIAL:
                stage = STAGE_LOCKED
        level = entry_d + locked if long else entry_d - locked
        close = Decimal(str(bar.close))
        # One tick inside this bar's close: a stop at the last price would
        # fill at once.
        cap = close - tick if long else close + tick
        candidate = round_stop(float(min(level, cap) if long else max(level, cap)), tick_size, direction)
        if target is None or is_tighter(candidate, target, direction):
            target = candidate
    return LockState(
        bars=state.bars + 1,
        last_minute=bar.minute,
        peak=float(peak),
        target=target,
        stage=stage,
    )


def fold(
    bars: Iterable[LockBar],
    *,
    direction: str,
    entry: float,
    initial_stop: float,
    tick_size: float,
    entry_minute: str,
    state: Optional[LockState] = None,
) -> LockState:
    """Fold ``bars`` (ascending) onto ``state``. Bars before the entry minute,
    and bars at or before the last one already folded, are ignored."""
    current = state or LockState()
    for bar in bars:
        if bar.minute < entry_minute:
            continue
        if current.last_minute is not None and bar.minute <= current.last_minute:
            continue
        current = advance(
            current,
            bar,
            direction=direction,
            entry=entry,
            initial_stop=initial_stop,
            tick_size=tick_size,
            entry_bar=bar.minute == entry_minute,
        )
    return current


def stage_of_stop(
    *,
    direction: str,
    entry: Optional[float],
    initial_stop: Optional[float],
    stop: Optional[float],
    tick_size: Optional[float],
) -> Optional[str]:
    """Which stage a broker-confirmed stop corresponds to (for the desk).

    Read from the stop Kite holds, not from the rule's own state, so a stop
    moved by hand is described as what it is.
    """
    if entry is None or initial_stop is None or stop is None or not tick_size:
        return None
    r = abs(float(entry) - float(initial_stop))
    if r <= 0:
        return None
    locked = (float(stop) - float(entry)) if is_long(direction) else (float(entry) - float(stop))
    lock_level = float(LOCK_LEVEL_R) * r
    half_tick = float(tick_size) / 2.0
    if locked > lock_level + half_tick:
        return STAGE_GIVEBACK
    # The lock level is rounded away from the market, so up to a tick short.
    if locked >= lock_level - float(tick_size) - half_tick and locked > 0:
        return STAGE_LOCKED
    return STAGE_INITIAL
