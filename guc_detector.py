"""Gap-Up Continuation (GUC) detector: ORB's detector with a different rule.

Same ticks, same baselines, same writer and tables (rows carry rule version
``guc_v1``), same restart and write-first guarantees. What differs
(Reference/guc_build_plan_2026-11.md, research strategy2_2026-11/batch4.py):

* **Arm at 09:20:** every stock whose day open is at least 1.5 % above
  yesterday's close (the tick's OHLC close). Long only. Level = 5-minute
  high; stop = 5-minute low, but never more than 0.35 ATR14 below the level,
  fixed at arm time (ORB's stop is measured from the fill instead).
* **Watch from 09:30**, not 09:20. On an arm's first fresh tick at or after
  09:30 the 15-minute volume (09:15-09:29, cumulative tick volume minus the
  pre-open auction) must be at least 1.5x its 14-session average, else the
  arm is dropped (EXPIRED, watch_rvol_below_min). A stock already above its
  level on that tick is bought there: the backtest's realistic
  "confirmation" fill, allowed up to 0.25 ATR above the level (MISSED
  beyond). After that first tick ORB's own breakout logic applies.
* The equal-weight average gap of the universe is recorded on every arm as
  ``market_gap`` (display and research; the engine reads the NIFTY gap from
  Kite itself).

Nothing here touches ORB: OrbDetector gains only the three hooks this class
overrides, and the ORB instance keeps its defaults.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Set

from orb_config import DEFAULT_GUC_CONFIG, GucConfig
from orb_detector import ARM_ARMED, OrbDetector, _ArmRuntime, _ensure_ist, _now_ist
from orb_rules import guc_stop_distance, make_guc_arm, select_guc
from orb_rules import price_to_ticks
from orb_types import (
    DECISION_EXPIRED,
    EXPIRED_WATCH_RVOL,
    NOT_SELECTED_STOP_TIGHT,
    RANGE_OFF_GRID,
    OpeningRange,
    OrbArm,
    OrbBaseline,
    OrbDecision,
    RankedRange,
)
from orb_writer import OrbWriter
from tick_event import TickEvent

logger = logging.getLogger(__name__)

# The confirmation fill (buy a stock already above its level) is only the
# first tick at the watch start. A restart later in the morning must not turn
# a stock 0.2 ATR above its level into a chase.
CONFIRM_WINDOW_SECONDS = 60.0
# Dropped at 09:30 because the 15-minute volume could not be judged at all.
EXPIRED_WATCH_RVOL_UNKNOWN = "watch_rvol_unknown"


def market_gap_of(ranges: Sequence[OpeningRange]) -> Optional[float]:
    """Equal-weight average opening gap of the valid ranges, or None."""
    gaps = [float(r.gap_pct) for r in ranges if r.valid and r.gap_pct is not None]
    if len(gaps) < 20:
        return None
    return sum(gaps) / len(gaps)


class GucDetector(OrbDetector):
    def __init__(
        self,
        *,
        session_date: str,
        token_to_symbol: Mapping[int, str],
        tick_sizes: Mapping[int, float],
        baselines: Mapping[int, OrbBaseline],
        writer: OrbWriter,
        config: GucConfig = DEFAULT_GUC_CONFIG,
        on_ranked=None,
        on_decision=None,
        now_fn: Callable[[], datetime] = _now_ist,
    ) -> None:
        self._guc_config = config
        self._market_gap: Optional[float] = None
        self._watched: Set[int] = set()
        super().__init__(
            session_date=session_date,
            token_to_symbol=token_to_symbol,
            tick_sizes=tick_sizes,
            baselines=baselines,
            writer=writer,
            config=config,
            on_ranked=on_ranked,
            on_decision=on_decision,
            now_fn=now_fn,
        )
        day = self._range_end.date()
        self._watch_start = datetime.combine(day, config.watch_start, tzinfo=self._range_end.tzinfo)
        if self._restored:
            arms = [runtime.arm for runtime in self._arms.values()]
            gaps = [a.market_gap for a in arms if a.market_gap is not None]
            self._market_gap = gaps[0] if gaps else None

    @property
    def market_gap(self) -> Optional[float]:
        return self._market_gap

    def status(self) -> Dict[str, object]:
        out = super().status()
        out["rule"] = "GUC"
        out["market_gap"] = self._market_gap
        out["watch_start"] = self._watch_start.isoformat()
        return out

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    def _select(self, ranges: Sequence[OpeningRange]) -> List[RankedRange]:
        self._market_gap = market_gap_of(ranges)
        ranked = select_guc(ranges, self._guc_config)
        out: List[RankedRange] = []
        for entry in ranked:
            if entry.selected and not self._on_grid(entry.range):
                entry = RankedRange(entry.range, entry.rank, False, RANGE_OFF_GRID)
            elif entry.selected and self._stop_too_tight(entry.range):
                entry = RankedRange(entry.range, entry.rank, False, NOT_SELECTED_STOP_TIGHT)
            out.append(entry)
        return out

    def _stop_too_tight(self, item: OpeningRange) -> bool:
        if item.or_high is None or item.or_low is None or item.atr14 is None:
            return True
        level = float(item.or_high)
        distance = guc_stop_distance(level, float(item.or_low), float(item.atr14), self._guc_config)
        return distance < self._guc_config.min_stop_pct * level

    def _on_grid(self, item: OpeningRange) -> bool:
        tick = self._tick_sizes.get(item.instrument_token)
        if not tick or item.or_high is None:
            return False
        try:
            price_to_ticks(float(item.or_high), tick)
        except ValueError:
            return False
        return True

    def _make_arm(self, entry: RankedRange) -> OrbArm:
        token = entry.range.instrument_token
        baseline = self._baselines.get(token)
        return make_guc_arm(
            entry,
            session_date=self._session_date,
            tick_size=self._tick_sizes[token],
            config=self._guc_config,
            market_gap=self._market_gap,
            avg_watch_volume=None if baseline is None else baseline.avg_watch_volume,
        )

    def _evaluate(self, runtime: _ArmRuntime, tick: TickEvent, at: datetime) -> None:
        arm = runtime.arm
        config = self._guc_config
        first = arm.instrument_token not in self._watched
        extra: dict = {"gap_pct": arm.gap_pct, "market_gap": arm.market_gap}
        if first:
            self._watched.add(arm.instrument_token)
            rvol = self._watch_rvol(arm, tick)
            extra["watch_rvol"] = rvol
            if rvol is None or rvol < config.min_rvol:
                reason = EXPIRED_WATCH_RVOL if rvol is not None else EXPIRED_WATCH_RVOL_UNKNOWN
                self._decide(
                    runtime,
                    OrbDecision(
                        setup_id=arm.setup_id,
                        rule_version=arm.rule_version,
                        decision_type=DECISION_EXPIRED,
                        reason=reason,
                        tick_sequence=int(tick.sequence),
                        exchange_ts=at,
                    ),
                    payload={"level": arm.level, "direction": arm.direction, **extra},
                )
                return
        in_confirm_window = (at - self._watch_start).total_seconds() <= CONFIRM_WINDOW_SECONDS
        confirm = first and in_confirm_window
        extra["confirm_fill"] = confirm
        self._judge(
            runtime,
            tick,
            at,
            max_overshoot_atr=config.confirm_overshoot_atr if confirm else config.max_overshoot_atr,
            stop_reference=arm.level,
            extra_payload=extra,
        )

    @staticmethod
    def _watch_rvol(arm: OrbArm, tick: TickEvent) -> Optional[float]:
        """15-minute RVOL from the tick's cumulative volume. None = unknown."""
        if arm.preopen_volume is None or not arm.avg_watch_volume:
            return None
        try:
            volume = int(tick.volume_traded) - int(arm.preopen_volume)
        except (TypeError, ValueError):
            return None
        if volume < 0:
            return None
        return volume / float(arm.avg_watch_volume)
