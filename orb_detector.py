"""ORB detector: tick-driven, observation only. It places no orders.

One day, per stock:

1. Before 09:15 it remembers the cumulative volume on the last pre-open tick
   (the auction volume, which is not part of the opening range).
2. From 09:15:00 to 09:19:59 it keeps the stock's latest tick. That tick
   carries the exchange's own day open / high / low and cumulative volume,
   which at 09:19:59 are the opening range.
3. At 09:20, once every stock has ticked past the boundary (or one second
   later, whichever is first), it ranks the ranges, writes the whole ranking
   and the armed stocks in one transaction, and only then starts watching.
4. An armed stock triggers on its first fresh tick at or beyond the level. A
   first tick already far beyond it is recorded as MISSED instead: that
   breakout was not seen in time, and chasing it is not the tested trade. A
   tick that is no longer fresh when it gets here is not judged at all.
5. Arms still waiting at the cutoff are written off as EXPIRED.

The ranking is persisted before anything is armed and each outcome before it
is announced, so a restart resumes from the database and never ranks twice.
Nothing here can stop market data: every entry point swallows its own errors.
"""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from orb_baselines import ready_count
from orb_config import DEFAULT_ORB_CONFIG, OrbConfig
from orb_rules import (
    VERDICT_MISSED,
    VERDICT_NONE,
    PreopenVolume,
    TickSnapshot,
    evaluate_price,
    make_arm,
    planned_stop,
    range_from_snapshot,
    rank_ranges,
)
from orb_types import (
    ARM_ARMED,
    DECISION_EXPIRED,
    DECISION_MISSED,
    DECISION_TRIGGERED,
    EXPIRED_CUTOFF,
    MISSED_NO_STOP,
    MISSED_OVERSHOOT,
    OpeningRange,
    OrbArm,
    OrbBaseline,
    OrbDecision,
    RankedRange,
)
from orb_writer import OrbWriter
from tick_event import TickEvent

logger = logging.getLogger(__name__)
_IST = ZoneInfo("Asia/Kolkata")

STATE_COLLECTING = "collecting"   # before the ranking
STATE_RANKED = "ranked"           # arms are live
STATE_DONE = "done"               # nothing left to watch today
STATE_DISABLED = "disabled"       # something failed; no arms today

REASON_NO_STOCKS_IN_PLAY = "no_stocks_in_play"
REASON_RANKED_TOO_LATE = "ranked_too_late"
REASON_RANKING_WRITE_FAILED = "ranking_write_failed"
REASON_RANKING_CONFLICT = "ranking_conflict"
REASON_RANKING_FAILED = "ranking_failed"
REASON_RESTORE_FAILED = "restore_failed"
REASON_NO_VALID_RANGES = "no_valid_ranges"
REASON_TOO_MANY_FAILURES = "too_many_tick_failures"
REASON_ALL_ARMS_DECIDED = "all_arms_decided"
RANGE_NO_TICK_SIZE = "no_tick_size"

# A detector that keeps failing is switched off for the day rather than left
# to log a stack trace on every tick.
MAX_TICK_FAILURES = 25

# In-memory only: the outcome could not be written, so the arm is abandoned.
ARM_WRITE_FAILED = "WRITE_FAILED"
# In-memory only: another writer recorded this arm's outcome first.
ARM_DECIDED_ELSEWHERE = "DECIDED"

OnRanked = Callable[[Sequence[RankedRange], Sequence[OrbArm]], None]
OnDecision = Callable[[OrbArm, OrbDecision], None]


@dataclass
class _ArmRuntime:
    arm: OrbArm
    state: str = ARM_ARMED


@dataclass
class OrbMetrics:
    ticks_seen: int = 0
    ticks_evaluated: int = 0
    off_grid_ticks: int = 0
    triggered: int = 0
    missed: int = 0
    expired: int = 0
    write_failures: int = 0
    tick_failures: int = 0
    callback_failures: int = 0
    # Ticks not judged for a breakout because they arrived too old, and ticks
    # ignored because their timestamp was ahead of the clock.
    stale_ticks: int = 0
    future_ticks: int = 0


def _now_ist() -> datetime:
    return datetime.now(_IST)


def _ensure_ist(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=_IST)
    return value.astimezone(_IST)


def _summarise(ranked: Sequence[RankedRange], *, delay_seconds: float) -> Dict[str, object]:
    """Counts for the status row: how the day's ranking came out, and why."""
    invalid: Dict[str, int] = {}
    for entry in ranked:
        if not entry.range.valid:
            key = str(entry.range.reason)
            invalid[key] = invalid.get(key, 0) + 1
    return {
        "valid": sum(1 for entry in ranked if entry.range.valid),
        "in_play": sum(1 for entry in ranked if entry.rank is not None),
        "selected": sum(1 for entry in ranked if entry.selected),
        "invalid": invalid,
        "delay_seconds": round(float(delay_seconds), 3),
    }


class OrbDetector:
    def __init__(
        self,
        *,
        session_date: str,
        token_to_symbol: Mapping[int, str],
        tick_sizes: Mapping[int, float],
        baselines: Mapping[int, OrbBaseline],
        writer: OrbWriter,
        config: OrbConfig = DEFAULT_ORB_CONFIG,
        on_ranked: Optional[OnRanked] = None,
        on_decision: Optional[OnDecision] = None,
        now_fn: Callable[[], datetime] = _now_ist,
    ) -> None:
        self._session_date = session_date
        self._symbols: Dict[int, str] = {int(t): str(s) for t, s in token_to_symbol.items()}
        self._tick_sizes: Dict[int, float] = {int(t): float(s) for t, s in tick_sizes.items()}
        self._baselines: Dict[int, OrbBaseline] = {int(t): b for t, b in baselines.items()}
        self._writer = writer
        self._config = config
        self._on_ranked = on_ranked
        self._on_decision = on_decision
        self._now_fn = now_fn
        self.metrics = OrbMetrics()

        day = date.fromisoformat(session_date)
        self._preopen_start = datetime.combine(day, config.preopen_start, tzinfo=_IST)
        self._range_start = datetime.combine(day, config.range_start, tzinfo=_IST)
        self._range_end = datetime.combine(day, config.range_end, tzinfo=_IST)
        self._trigger_end = datetime.combine(day, config.trigger_end, tzinfo=_IST)
        self._rank_deadline = self._range_end + timedelta(seconds=config.rank_grace_seconds)

        self._preopen: Dict[int, PreopenVolume] = {}
        self._snapshots: Dict[int, TickSnapshot] = {}
        # Each stock's latest tick at or after 09:20:00, kept only until the
        # ranking so a breakout during the grace second is seen at arm time.
        self._after: Dict[int, TickEvent] = {}
        self._arms: Dict[int, _ArmRuntime] = {}

        self._state = STATE_COLLECTING
        self._reason: Optional[str] = None
        self._ranked_at: Optional[datetime] = None
        self._rank_summary: Dict[str, object] = {}
        self._restored = False
        self._restore()
        self._save_status()

    # ------------------------------------------------------------------
    # Read-only views
    # ------------------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def reason(self) -> Optional[str]:
        return self._reason

    @property
    def restored(self) -> bool:
        return self._restored

    def arms(self) -> List[OrbArm]:
        return [runtime.arm for runtime in self._by_rank()]

    def arm_state(self, instrument_token: int) -> Optional[str]:
        runtime = self._arms.get(int(instrument_token))
        return None if runtime is None else runtime.state

    def status(self) -> Dict[str, object]:
        states = [runtime.state for runtime in self._arms.values()]
        return {
            "state": self._state,
            "reason": self._reason,
            "restored": self._restored,
            "tokens": len(self._symbols),
            "baselines_ready": ready_count(self._baselines),
            "arms": len(states),
            "armed": states.count(ARM_ARMED),
            "triggered": states.count(DECISION_TRIGGERED),
            "missed": states.count(DECISION_MISSED),
            "expired": states.count(DECISION_EXPIRED),
            "ranked_at": None if self._ranked_at is None else self._ranked_at.isoformat(),
            "ranking": dict(self._rank_summary),
            "tick_failures": self.metrics.tick_failures,
            "write_failures": self.metrics.write_failures,
            "stale_ticks": self.metrics.stale_ticks,
            "future_ticks": self.metrics.future_ticks,
        }

    # ------------------------------------------------------------------
    # Ticks
    # ------------------------------------------------------------------

    def on_tick(self, tick: TickEvent) -> None:
        if self._state in (STATE_DONE, STATE_DISABLED):
            return
        try:
            self._handle_tick(tick)
        except Exception:  # noqa: BLE001 - ORB must never disturb market data
            self.metrics.tick_failures += 1
            logger.exception("orb detector on_tick failed token=%s", tick.instrument_token)
            if self.metrics.tick_failures >= MAX_TICK_FAILURES:
                logger.error("orb detector disabled after %d failures", MAX_TICK_FAILURES)
                self._finish(STATE_DISABLED, REASON_TOO_MANY_FAILURES)

    def _handle_tick(self, tick: TickEvent) -> None:
        token = int(tick.instrument_token)
        if token not in self._symbols:
            return
        at = _ensure_ist(tick.exchange_timestamp)
        if at.date().isoformat() != self._session_date:
            return  # yesterday's closing snapshot, replayed on connect
        lag = (_ensure_ist(self._now_fn()) - at).total_seconds()
        if -lag > self._config.max_tick_lead_seconds:
            # A timestamp from the future must not rank early or expire arms.
            self.metrics.future_ticks += 1
            return
        self.metrics.ticks_seen += 1

        if self._state == STATE_COLLECTING:
            self._collect(token, tick, at)
            return

        # STATE_RANKED
        if at >= self._trigger_end:
            self._expire_all(at)
            return
        runtime = self._arms.get(token)
        if runtime is None or runtime.state != ARM_ARMED:
            return
        if at < self._range_end:
            return  # a late tick from inside the range
        if lag > self._config.max_tick_lag_seconds:
            self.metrics.stale_ticks += 1
            return  # too old to act on; the next fresh tick decides
        self._evaluate(runtime, tick, at)

    def _collect(self, token: int, tick: TickEvent, at: datetime) -> None:
        if at < self._preopen_start:
            return
        key = (at, int(tick.sequence))
        if at < self._range_start:
            current = self._preopen.get(token)
            if current is None or key >= (current.at, current.sequence):
                self._preopen[token] = PreopenVolume(
                    at=at, sequence=int(tick.sequence), volume=int(tick.volume_traded)
                )
            return
        if at < self._range_end:
            current_snapshot = self._snapshots.get(token)
            if current_snapshot is None or key >= (
                current_snapshot.at,
                current_snapshot.sequence,
            ):
                self._snapshots[token] = TickSnapshot(
                    at=at,
                    sequence=int(tick.sequence),
                    last_price=float(tick.last_price),
                    day_open=float(tick.ohlc.open),
                    day_high=float(tick.ohlc.high),
                    day_low=float(tick.ohlc.low),
                    volume=int(tick.volume_traded),
                )
            return

        # At or after 09:20:00: this stock's range is final.
        latest = self._after.get(token)
        if latest is None or key >= (
            _ensure_ist(latest.exchange_timestamp),
            int(latest.sequence),
        ):
            self._after[token] = tick
        if len(self._after) >= len(self._symbols) or at >= self._rank_deadline:
            try:
                self._rank(at)
            except Exception:  # noqa: BLE001 - one attempt; never a retry per tick
                self.metrics.tick_failures += 1
                logger.exception("orb ranking failed; no arms today")
                if self._state == STATE_COLLECTING:
                    self._finish(STATE_DISABLED, REASON_RANKING_FAILED)

    # ------------------------------------------------------------------
    # Ranking
    # ------------------------------------------------------------------

    def _build_range(self, token: int) -> OpeningRange:
        tick_size = self._tick_sizes.get(token)
        usable_tick = tick_size if tick_size is not None and tick_size > 0 else None
        built = range_from_snapshot(
            instrument_token=token,
            tradingsymbol=self._symbols[token],
            baseline=self._baselines.get(token),
            preopen=self._preopen.get(token),
            snapshot=self._snapshots.get(token),
            range_end=self._range_end,
            tick_size=usable_tick,
            config=self._config,
        )
        if built.valid and usable_tick is None:
            return dataclasses.replace(built, valid=False, reason=RANGE_NO_TICK_SIZE)
        return built

    def _rank(self, at: datetime) -> None:
        ranges = [self._build_range(token) for token in sorted(self._symbols)]
        ranked = rank_ranges(ranges, self._config)
        delay = (at - self._range_end).total_seconds()
        too_late = delay > self._config.max_rank_delay_seconds
        if too_late:
            # Keep the numbers for the record, but arm nothing.
            ranked = [
                RankedRange(entry.range, entry.rank, False, REASON_RANKED_TOO_LATE)
                if entry.selected
                else entry
                for entry in ranked
            ]
        arms = [
            make_arm(
                entry,
                session_date=self._session_date,
                tick_size=self._tick_sizes[entry.range.instrument_token],
                config=self._config,
            )
            for entry in ranked
            if entry.selected
        ]
        try:
            written = self._writer.save_ranking(
                self._session_date,
                self._config.rule_version,
                ranked,
                arms,
                ranked_at=at,
            )
        except Exception:  # noqa: BLE001 - nothing is armed without a record
            self.metrics.write_failures += 1
            logger.exception("orb ranking could not be written; no arms today")
            self._finish(STATE_DISABLED, REASON_RANKING_WRITE_FAILED)
            return
        if not written:
            # Someone ranked this session first. Theirs stands.
            self._restore()
            if self._state == STATE_COLLECTING:
                self._finish(STATE_DISABLED, REASON_RANKING_CONFLICT)
            else:
                self._save_status()
            return

        self._ranked_at = at
        self._rank_summary = _summarise(ranked, delay_seconds=delay)
        self._arms = {arm.instrument_token: _ArmRuntime(arm) for arm in arms}
        pending = dict(self._after)
        self._release_collection()
        if not arms:
            if too_late:
                why = REASON_RANKED_TOO_LATE
            elif not any(entry.range.valid for entry in ranked):
                why = REASON_NO_VALID_RANGES
            else:
                why = REASON_NO_STOCKS_IN_PLAY
            self._finish(STATE_DONE, why)
        else:
            self._state = STATE_RANKED
            self._reason = None
            self._save_status()
        if self._on_ranked is not None:
            try:
                self._on_ranked(ranked, arms)
            except Exception:  # noqa: BLE001
                self.metrics.callback_failures += 1
                logger.exception("orb on_ranked callback failed")

        # A breakout during the grace second is already in hand: judge each
        # armed stock on its newest tick before waiting for the next one.
        for runtime in self._by_rank():
            tick = pending.get(runtime.arm.instrument_token)
            if tick is None or runtime.state != ARM_ARMED:
                continue
            tick_at = _ensure_ist(tick.exchange_timestamp)
            if not self._range_end <= tick_at < self._trigger_end:
                continue
            lag = (_ensure_ist(self._now_fn()) - tick_at).total_seconds()
            if lag > self._config.max_tick_lag_seconds:
                self.metrics.stale_ticks += 1
                continue
            try:
                self._evaluate(runtime, tick, tick_at)
            except Exception:  # noqa: BLE001 - the next tick will judge it again
                self.metrics.tick_failures += 1
                logger.exception(
                    "orb arm-time check failed for %s", runtime.arm.tradingsymbol
                )

    def _release_collection(self) -> None:
        self._preopen.clear()
        self._snapshots.clear()
        self._after.clear()

    # ------------------------------------------------------------------
    # Breakout
    # ------------------------------------------------------------------

    def _evaluate(self, runtime: _ArmRuntime, tick: TickEvent, at: datetime) -> None:
        arm = runtime.arm
        try:
            verdict = evaluate_price(arm, float(tick.last_price), self._config)
        except ValueError:
            self.metrics.off_grid_ticks += 1
            return
        self.metrics.ticks_evaluated += 1
        if verdict.kind == VERDICT_NONE:
            return

        price = float(tick.last_price)
        stop: Optional[float] = None
        if verdict.kind == VERDICT_MISSED:
            decision_type, reason = DECISION_MISSED, MISSED_OVERSHOOT
        else:
            stop = planned_stop(
                direction=arm.direction,
                reference_price=price,
                distance=arm.stop_distance,
                tick_size=arm.tick_size,
            )
            if stop is None:
                decision_type, reason = DECISION_MISSED, MISSED_NO_STOP
            else:
                decision_type, reason = DECISION_TRIGGERED, None
        decision = OrbDecision(
            setup_id=arm.setup_id,
            rule_version=arm.rule_version,
            decision_type=decision_type,
            reason=reason,
            tick_sequence=int(tick.sequence),
            exchange_ts=at,
            trigger_price=price,
            planned_stop=stop,
            overshoot=verdict.overshoot,
            overshoot_atr=verdict.overshoot_atr,
        )
        self._decide(
            runtime,
            decision,
            payload={
                "level": arm.level,
                "direction": arm.direction,
                "stop_distance": arm.stop_distance,
                "overshoot_bps": (verdict.overshoot / arm.level * 1e4) if arm.level else None,
                "received_at": tick.received_at.isoformat(),
                "restored": self._restored,
            },
        )

    def _expire_all(self, at: datetime) -> None:
        for runtime in self._by_rank():
            if runtime.state != ARM_ARMED:
                continue
            self._decide(
                runtime,
                OrbDecision(
                    setup_id=runtime.arm.setup_id,
                    rule_version=runtime.arm.rule_version,
                    decision_type=DECISION_EXPIRED,
                    reason=EXPIRED_CUTOFF,
                    exchange_ts=at,
                ),
                payload={"level": runtime.arm.level, "direction": runtime.arm.direction},
            )
        self._settle()

    def _decide(self, runtime: _ArmRuntime, decision: OrbDecision, *, payload: dict) -> None:
        """Write the outcome first; only a written outcome is announced."""
        try:
            inserted = self._writer.insert_decision(decision, payload=payload)
        except Exception:  # noqa: BLE001
            # One attempt only. Retrying on every tick would stall the tick
            # thread; a trigger that cannot be recorded is a trade not taken.
            self.metrics.write_failures += 1
            runtime.state = ARM_WRITE_FAILED
            logger.exception(
                "orb decision could not be written; %s abandoned", runtime.arm.tradingsymbol
            )
            self._settle()
            return
        if not inserted:
            runtime.state = ARM_DECIDED_ELSEWHERE
            self._settle()
            return
        runtime.state = decision.decision_type
        if decision.decision_type == DECISION_TRIGGERED:
            self.metrics.triggered += 1
        elif decision.decision_type == DECISION_MISSED:
            self.metrics.missed += 1
        else:
            self.metrics.expired += 1
        if self._on_decision is not None:
            try:
                self._on_decision(runtime.arm, decision)
            except Exception:  # noqa: BLE001
                self.metrics.callback_failures += 1
                logger.exception("orb on_decision callback failed")
        self._settle()

    def _settle(self) -> None:
        """Stop watching once no arm is still waiting."""
        if self._state != STATE_RANKED:
            return
        if any(runtime.state == ARM_ARMED for runtime in self._arms.values()):
            self._save_status()
            return
        self._finish(STATE_DONE, REASON_ALL_ARMS_DECIDED)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def _by_rank(self) -> List[_ArmRuntime]:
        return sorted(self._arms.values(), key=lambda runtime: runtime.arm.rank)

    def _finish(self, state: str, reason: Optional[str]) -> None:
        self._state = state
        self._reason = reason
        self._release_collection()
        self._save_status()

    def _restore(self) -> None:
        """Adopt whatever this session already has on disk."""
        try:
            record = self._writer.load_day(self._session_date, self._config.rule_version)
        except Exception:  # noqa: BLE001 - never rank over an unreadable record
            logger.exception("orb state could not be read back; detector disabled")
            self._state = STATE_DISABLED
            self._reason = REASON_RESTORE_FAILED
            return
        if not record.ranked:
            return
        self._restored = True
        self._arms = {
            arm.instrument_token: _ArmRuntime(arm, record.decisions.get(arm.setup_id, ARM_ARMED))
            for arm in record.arms
        }
        self._release_collection()
        if any(runtime.state == ARM_ARMED for runtime in self._arms.values()):
            self._state = STATE_RANKED
            self._reason = None
        else:
            self._state = STATE_DONE
            self._reason = REASON_ALL_ARMS_DECIDED if self._arms else REASON_NO_STOCKS_IN_PLAY

    def _save_status(self) -> None:
        try:
            self._writer.save_status(
                self._session_date,
                self._config.rule_version,
                self._state,
                reason=self._reason,
                detail=self.status(),
            )
        except Exception:  # noqa: BLE001 - the status row is for display only
            logger.exception("orb status could not be written")
