"""Opening Range Breakout (ORB) rule settings.

The numbers are the ones the backtest behind
Reference/arb_strategy_2026-09/REPORT.md was run with (research orb.py and
combo2.orb_cands: OR=5, rv=3.0, N=3, atr0.35). The guards at the bottom are
live-only: they have no effect on a clean day and exist so a late start, a
feed gap or a restart can never turn into a trade the backtest did not take.
See Reference/arb_strategy_2026-09/PR_B_PLAN.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time

ORB_RULE_VERSION = "orb_v1"


@dataclass(frozen=True)
class OrbConfig:
    rule_version: str = ORB_RULE_VERSION

    # The opening range is 09:15:00-09:19:59 (the first five 1-minute bars).
    range_start: time = time(9, 15)
    range_end: time = time(9, 20)
    # Breakouts count while the exchange clock is before this. The backtest
    # took a breakout in any 1-minute bar labelled up to 11:00, so 11:01.
    trigger_end: time = time(11, 1)

    # Stocks in play: opening volume at least 3x its own 14-session average,
    # and only the three highest that day.
    min_rvol: float = 3.0
    top_n: int = 3
    volume_lookback_sessions: int = 14
    atr_sessions: int = 14

    # The stop sits 0.35 x ATR14 (daily) from the trigger price.
    stop_atr_mult: float = 0.35

    # ---- live-only guards -------------------------------------------------
    # Pre-open volume is taken from ticks stamped between this and 09:15.
    preopen_start: time = time(9, 0)
    # Rank once every stock has ticked at or after 09:20:00, or once any tick
    # is stamped this many seconds past it, whichever comes first.
    rank_grace_seconds: float = 1.0
    # A ranking that could only run this long after 09:20:00 (feed down across
    # the boundary) is not trusted: no arms that day.
    max_rank_delay_seconds: float = 30.0
    # A stock whose last tick before 09:20:00 is older than this is left out.
    max_snapshot_age_seconds: float = 20.0
    # A first tick this far beyond the level (in ATRs) is a breakout already
    # missed, never a trigger. The backtest's largest gap was 0.04 ATR.
    max_overshoot_atr: float = 0.10
    # A tick is judged for a breakout only while it is this fresh. One that
    # reaches the detector later (a backlog, or an old snapshot replayed on
    # reconnect) is not a price anyone can trade at any more.
    max_tick_lag_seconds: float = 10.0
    # A tick stamped this far ahead of the clock is ignored outright.
    max_tick_lead_seconds: float = 30.0
    # History: a prior session whose candles stop before this is incomplete.
    history_min_last_bar: time = time(15, 0)
    history_lookback_calendar_days: int = 45

    @property
    def sessions_needed(self) -> int:
        """Prior sessions the baseline needs: the ATR window plus one close."""
        return max(self.atr_sessions, self.volume_lookback_sessions) + 1


DEFAULT_ORB_CONFIG = OrbConfig()
