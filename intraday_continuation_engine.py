"""
Intraday continuation trigger engine.

Tick-driven breakout confirmation after pullback READY.
Authoritative for TRIGGERED / REJECTED / DISARMED.
Non-fatal to market data.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Dict, Optional, Protocol

from candle_aggregation import (
    CompletedOneMinuteCandle,
    ensure_ist,
    minute_start_from_exchange_timestamp,
)
from continuation_features import compute_trigger_ticks, price_to_ticks
from continuation_metrics import ContinuationMetrics
from continuation_state import ContinuationArmRuntime, TokenVolumeState, transition
from continuation_tick_size import TickSizeMap, TickSizePreflightError
from continuation_types import (
    ContinuationArmedEvent,
    ContinuationDisarmedEvent,
    ContinuationRejectedEvent,
    ContinuationTriggeredEvent,
)
from intraday_continuation_config import IntradayContinuationRuleConfig
from intraday_continuation_rules import IntradayContinuationRuleEngine
from intraday_continuation_writer import (
    ContinuationConflictError,
    IntradayContinuationWriter,
)
from pullback_types import ContinuationCloseOutcome, PullbackCandidateEvent
from tick_event import TickEvent

logger = logging.getLogger(__name__)


class PullbackCloser(Protocol):
    def close_after_continuation_outcome(
        self,
        setup_id: str,
        outcome: ContinuationCloseOutcome,
        *,
        detail: Optional[dict] = None,
        evaluation_candle_time: Optional[datetime] = None,
    ) -> bool: ...


OnContinuationTriggered = Callable[[ContinuationTriggeredEvent], None]
OnContinuationRejected = Callable[[ContinuationRejectedEvent], None]
OnContinuationArmed = Callable[[ContinuationArmedEvent], None]
OnContinuationDisarmed = Callable[[ContinuationDisarmedEvent], None]


class IntradayContinuationEngine:
    def __init__(
        self,
        *,
        writer: IntradayContinuationWriter,
        tick_sizes: TickSizeMap,
        config: Optional[IntradayContinuationRuleConfig] = None,
        metrics: Optional[ContinuationMetrics] = None,
        pullback_closer: Optional[PullbackCloser] = None,
        on_triggered: Optional[OnContinuationTriggered] = None,
        on_rejected: Optional[OnContinuationRejected] = None,
        on_armed: Optional[OnContinuationArmed] = None,
        on_disarmed: Optional[OnContinuationDisarmed] = None,
    ) -> None:
        self._writer = writer
        self._tick_sizes = tick_sizes
        self._config = config if config is not None else IntradayContinuationRuleConfig()
        self._rules = IntradayContinuationRuleEngine(self._config)
        self._metrics = metrics if metrics is not None else ContinuationMetrics()
        self._pullback_closer = pullback_closer
        self._on_triggered = on_triggered
        self._on_rejected = on_rejected
        self._on_armed = on_armed
        self._on_disarmed = on_disarmed

        self._arms_by_setup: Dict[str, ContinuationArmRuntime] = {}
        self._armed_by_token: Dict[int, str] = {}
        self._volume_by_token: Dict[int, TokenVolumeState] = {}
        self._degraded = False

    @property
    def metrics(self) -> ContinuationMetrics:
        return self._metrics

    @property
    def config(self) -> IntradayContinuationRuleConfig:
        return self._config

    @property
    def degraded(self) -> bool:
        return self._degraded

    def _mark_degraded(self, exc: BaseException) -> None:
        self._degraded = True
        self._metrics.writer_failures += 1
        self._metrics.degraded += 1
        logger.error("continuation subsystem degraded: %s", exc, exc_info=True)

    def on_pullback_ready(self, candidate: PullbackCandidateEvent) -> None:
        self._metrics.candidates_received += 1
        if self._degraded:
            return
        try:
            self._arm(candidate)
        except Exception:  # noqa: BLE001
            self._metrics.strategy_failure += 1
            logger.exception("continuation on_pullback_ready failed")

    def _arm(self, candidate: PullbackCandidateEvent) -> None:
        setup = candidate.setup
        setup_id = setup.setup_id
        if setup_id in self._arms_by_setup:
            self._metrics.arm_duplicates_ignored += 1
            return

        direction = setup.direction
        swing_high = candidate.pullback_swing_high
        swing_low = candidate.pullback_swing_low
        if direction == "UP" and swing_high is None:
            self._metrics.arm_rejected_preflight += 1
            logger.error("arm rejected: missing pullback_swing_high setup=%s", setup_id)
            return
        if direction == "DOWN" and swing_low is None:
            self._metrics.arm_rejected_preflight += 1
            logger.error("arm rejected: missing pullback_swing_low setup=%s", setup_id)
            return

        try:
            tick_size = self._tick_sizes.get(setup.instrument_token)
        except TickSizePreflightError as exc:
            self._metrics.arm_rejected_preflight += 1
            logger.error("arm rejected: %s", exc)
            return

        buffer_ticks = self._config.continuation_breakout_buffer_ticks
        try:
            trigger_ticks, trigger_price = compute_trigger_ticks(
                direction=direction,
                swing_high=swing_high,
                swing_low=swing_low,
                tick_size=tick_size,
                buffer_ticks=buffer_ticks,
            )
        except ValueError as exc:
            self._metrics.arm_rejected_preflight += 1
            logger.error("arm rejected trigger calc setup=%s: %s", setup_id, exc)
            return

        ready_time = candidate.features.eval_5m_candle_time
        armed_at = datetime.now(timezone.utc)
        try:
            inserted = self._writer.insert_arm(
                setup_id=setup_id,
                continuation_rule_version=self._config.rule_version,
                instrument_token=setup.instrument_token,
                tradingsymbol=setup.tradingsymbol,
                session_date=setup.session_date,
                direction=direction,
                pullback_swing_high=swing_high,
                pullback_swing_low=swing_low,
                tick_size=tick_size,
                buffer_ticks=buffer_ticks,
                trigger_price=trigger_price,
                trigger_price_ticks=trigger_ticks,
                pullback_type=candidate.pullback_type,
                ready_5m_candle_time=ready_time,
                armed_at=armed_at,
                payload={
                    "pullback_candle_count": candidate.sequence.pullback_candle_count,
                },
            )
        except (ContinuationConflictError, RuntimeError) as exc:
            self._mark_degraded(exc)
            return

        if not inserted:
            self._metrics.arm_duplicates_ignored += 1
            return

        runtime = ContinuationArmRuntime(
            setup_id=setup_id,
            instrument_token=setup.instrument_token,
            tradingsymbol=setup.tradingsymbol,
            session_date=setup.session_date,
            direction=direction,
            pullback_swing_high=swing_high,
            pullback_swing_low=swing_low,
            tick_size=tick_size,
            buffer_ticks=buffer_ticks,
            trigger_price=trigger_price,
            trigger_price_ticks=trigger_ticks,
            continuation_rule_version=self._config.rule_version,
            ready_5m_candle_time=ready_time,
            state="ARMED",
        )
        self._arms_by_setup[setup_id] = runtime
        self._armed_by_token[setup.instrument_token] = setup_id
        self._metrics.arms_created += 1

        event = ContinuationArmedEvent(
            setup_id=setup_id,
            instrument_token=setup.instrument_token,
            tradingsymbol=setup.tradingsymbol,
            session_date=setup.session_date,
            direction=direction,
            pullback_swing_high=swing_high,
            pullback_swing_low=swing_low,
            tick_size=tick_size,
            buffer_ticks=buffer_ticks,
            trigger_price=trigger_price,
            trigger_price_ticks=trigger_ticks,
            continuation_rule_version=self._config.rule_version,
            ready_5m_candle_time=ready_time,
            armed_at=armed_at,
        )
        if self._on_armed is not None:
            try:
                self._on_armed(event)
            except Exception:  # noqa: BLE001
                self._metrics.callback_failures += 1
                logger.exception("on_armed callback failed")

    def on_one_minute(self, candle: CompletedOneMinuteCandle) -> None:
        if self._degraded:
            return
        try:
            self._handle_one_minute(candle)
        except Exception:  # noqa: BLE001
            self._metrics.strategy_failure += 1
            logger.exception("continuation on_one_minute failed")

    def _handle_one_minute(self, candle: CompletedOneMinuteCandle) -> None:
        if self._config.exclude_partial_1m_from_average and candle.is_partial:
            return
        if self._config.require_volume_reliable and not candle.volume_reliable:
            return
        state = self._volume_state(candle.instrument_token)
        state.prior_1m_volumes.append(int(candle.volume))

    def _volume_state(self, token: int) -> TokenVolumeState:
        if token not in self._volume_by_token:
            self._volume_by_token[token] = TokenVolumeState(
                prior_1m_volumes=deque(maxlen=self._config.prior_completed_1m_count)
            )
        return self._volume_by_token[token]

    def on_tick(self, tick: TickEvent) -> None:
        if self._degraded:
            return
        try:
            self._handle_tick(tick)
        except Exception:  # noqa: BLE001
            self._metrics.strategy_failure += 1
            logger.exception("continuation on_tick failed")

    def _handle_tick(self, tick: TickEvent) -> None:
        token = tick.instrument_token
        setup_id = self._armed_by_token.get(token)
        vol_state = self._update_volume_from_tick(tick)

        if setup_id is None:
            return
        runtime = self._arms_by_setup.get(setup_id)
        if runtime is None or runtime.is_terminal or runtime.state != "ARMED":
            return

        self._metrics.ticks_evaluated += 1
        reached = self._rules.price_reached(
            direction=runtime.direction,
            last_price=tick.last_price,
            tick_size=runtime.tick_size,
            trigger_price_ticks=runtime.trigger_price_ticks,
        )
        if not reached or runtime.reached:
            return

        runtime.reached = True
        self._metrics.reaches_detected += 1

        prior = list(vol_state.prior_1m_volumes)
        volume_decision = self._rules.volume_decision(
            in_progress_volume=vol_state.in_progress_volume,
            prior_volumes=prior,
            volume_reliable=vol_state.volume_reliable,
        )

        minute_start = minute_start_from_exchange_timestamp(
            ensure_ist(tick.exchange_timestamp)
        )
        last_ticks = price_to_ticks(tick.last_price, runtime.tick_size)

        if volume_decision.outcome == "triggered":
            self._finalize_triggered(
                runtime,
                tick=tick,
                last_ticks=last_ticks,
                breakout_candle_time=minute_start,
                breakout_volume=vol_state.in_progress_volume,
                avg_prior=volume_decision.avg_prior_3_1m_volume or 0.0,
            )
        else:
            reason = volume_decision.reason or "failed_breakout_volume_confirmation"
            self._finalize_rejected(
                runtime,
                tick=tick,
                last_ticks=last_ticks,
                breakout_candle_time=minute_start,
                breakout_volume=vol_state.in_progress_volume,
                avg_prior=volume_decision.avg_prior_3_1m_volume,
                reason=reason,
                volume_ok=volume_decision.volume_ok,
                volume_reliable=volume_decision.volume_reliable,
            )

    def _update_volume_from_tick(self, tick: TickEvent) -> TokenVolumeState:
        state = self._volume_state(tick.instrument_token)
        exchange_ts = ensure_ist(tick.exchange_timestamp)
        minute_start = minute_start_from_exchange_timestamp(exchange_ts)
        cum = int(tick.volume_traded)

        if state.current_minute_start is None:
            state.current_minute_start = minute_start
            if state.last_valid_cumulative_volume is not None and (
                state.last_valid_minute_start is not None
                and state.last_valid_minute_start < minute_start
            ):
                state.minute_baseline_cumulative = state.last_valid_cumulative_volume
                state.volume_reliable = True
            else:
                # First observation in this process / unknown open baseline.
                state.minute_baseline_cumulative = cum
                state.volume_reliable = False
                state.in_progress_volume = 0
        elif minute_start > state.current_minute_start:
            # New minute — baseline from last valid cumulative of prior minute.
            if (
                state.last_valid_cumulative_volume is not None
                and state.last_valid_minute_start is not None
                and state.last_valid_minute_start < minute_start
            ):
                state.minute_baseline_cumulative = state.last_valid_cumulative_volume
                state.volume_reliable = True
            else:
                state.minute_baseline_cumulative = None
                state.volume_reliable = False
            state.current_minute_start = minute_start
            state.in_progress_volume = 0
        elif minute_start < state.current_minute_start:
            # Late tick — do not update baseline; leave unreliable for safety.
            state.volume_reliable = False
            return state

        # Cumulative decrease / reset.
        if (
            state.last_valid_cumulative_volume is not None
            and cum < state.last_valid_cumulative_volume
        ):
            state.volume_reliable = False
            state.in_progress_volume = 0
            # Still record as last seen to allow recovery next minute.
            state.last_valid_cumulative_volume = cum
            state.last_valid_minute_start = minute_start
            return state

        if state.volume_reliable and state.minute_baseline_cumulative is not None:
            progress = cum - state.minute_baseline_cumulative
            if progress < 0:
                state.volume_reliable = False
                state.in_progress_volume = 0
            else:
                state.in_progress_volume = progress

        state.last_valid_cumulative_volume = cum
        state.last_valid_minute_start = minute_start
        return state

    def on_setup_terminal(self, setup_id: str, reason: str) -> None:
        """Pullback structural terminal — DISARM if still ARMED."""
        if self._degraded:
            return
        runtime = self._arms_by_setup.get(setup_id)
        if runtime is None or runtime.is_terminal or runtime.state != "ARMED":
            return
        try:
            inserted = self._writer.insert_decision(
                setup_id=setup_id,
                continuation_rule_version=runtime.continuation_rule_version,
                decision_type="DISARMED",
                reason=reason,
                payload={"structural_reason": reason},
            )
        except (ContinuationConflictError, RuntimeError) as exc:
            self._mark_degraded(exc)
            return
        if not inserted:
            # Already had a decision — treat as terminal.
            self._detach_arm(runtime)
            return
        transition(runtime, "DISARMED")
        self._detach_arm(runtime)
        self._metrics.disarmed_pullback_structural += 1
        event = ContinuationDisarmedEvent(
            setup_id=setup_id,
            instrument_token=runtime.instrument_token,
            tradingsymbol=runtime.tradingsymbol,
            reason=reason,
            continuation_rule_version=runtime.continuation_rule_version,
            detected_at=datetime.now(timezone.utc),
        )
        if self._on_disarmed is not None:
            try:
                self._on_disarmed(event)
            except Exception:  # noqa: BLE001
                self._metrics.callback_failures += 1
                logger.exception("on_disarmed callback failed")

    def _finalize_triggered(
        self,
        runtime: ContinuationArmRuntime,
        *,
        tick: TickEvent,
        last_ticks: int,
        breakout_candle_time: datetime,
        breakout_volume: int,
        avg_prior: float,
    ) -> None:
        try:
            inserted = self._writer.insert_decision(
                setup_id=runtime.setup_id,
                continuation_rule_version=runtime.continuation_rule_version,
                decision_type="TRIGGERED",
                trigger_tick_sequence=tick.sequence,
                trigger_exchange_ts=ensure_ist(tick.exchange_timestamp),
                last_price=tick.last_price,
                last_price_ticks=last_ticks,
                breakout_candle_time=breakout_candle_time,
                breakout_candle_volume=breakout_volume,
                avg_prior_3_1m_volume=avg_prior,
                volume_ok=True,
                volume_reliable=True,
            )
        except (ContinuationConflictError, RuntimeError) as exc:
            self._mark_degraded(exc)
            return
        if not inserted:
            self._detach_arm(runtime)
            return

        transition(runtime, "TRIGGERED")
        self._detach_arm(runtime)
        self._metrics.triggered += 1

        event = ContinuationTriggeredEvent(
            setup_id=runtime.setup_id,
            instrument_token=runtime.instrument_token,
            tradingsymbol=runtime.tradingsymbol,
            direction=runtime.direction,
            trigger_price=runtime.trigger_price,
            trigger_price_ticks=runtime.trigger_price_ticks,
            last_price=tick.last_price,
            last_price_ticks=last_ticks,
            tick_sequence=tick.sequence,
            exchange_timestamp=ensure_ist(tick.exchange_timestamp),
            breakout_candle_time=breakout_candle_time,
            breakout_candle_volume=breakout_volume,
            avg_prior_3_1m_volume=avg_prior,
            continuation_rule_version=runtime.continuation_rule_version,
            detected_at=datetime.now(timezone.utc),
        )
        if self._on_triggered is not None:
            try:
                self._on_triggered(event)
            except Exception:  # noqa: BLE001
                self._metrics.callback_failures += 1
                logger.exception("on_triggered callback failed")

        self._close_pullback(
            runtime,
            outcome="CONTINUATION_TRIGGERED",
            detail={
                "last_price": tick.last_price,
                "breakout_candle_volume": breakout_volume,
                "avg_prior_3_1m_volume": avg_prior,
            },
            evaluation_candle_time=breakout_candle_time,
            metric_attr="pullback_active_cleared_after_trigger",
        )

    def _finalize_rejected(
        self,
        runtime: ContinuationArmRuntime,
        *,
        tick: TickEvent,
        last_ticks: int,
        breakout_candle_time: datetime,
        breakout_volume: int,
        avg_prior: Optional[float],
        reason: str,
        volume_ok: bool,
        volume_reliable: bool,
    ) -> None:
        try:
            inserted = self._writer.insert_decision(
                setup_id=runtime.setup_id,
                continuation_rule_version=runtime.continuation_rule_version,
                decision_type="REJECTED",
                reason=reason,
                trigger_tick_sequence=tick.sequence,
                trigger_exchange_ts=ensure_ist(tick.exchange_timestamp),
                last_price=tick.last_price,
                last_price_ticks=last_ticks,
                breakout_candle_time=breakout_candle_time,
                breakout_candle_volume=breakout_volume,
                avg_prior_3_1m_volume=avg_prior,
                volume_ok=volume_ok,
                volume_reliable=volume_reliable,
            )
        except (ContinuationConflictError, RuntimeError) as exc:
            self._mark_degraded(exc)
            return
        if not inserted:
            self._detach_arm(runtime)
            return

        transition(runtime, "REJECTED")
        self._detach_arm(runtime)

        if reason == "insufficient_volume_history":
            self._metrics.rejected_insufficient_history += 1
        elif reason == "unreliable_breakout_volume":
            self._metrics.rejected_unreliable_volume += 1
        else:
            self._metrics.rejected_volume += 1

        event = ContinuationRejectedEvent(
            setup_id=runtime.setup_id,
            instrument_token=runtime.instrument_token,
            tradingsymbol=runtime.tradingsymbol,
            direction=runtime.direction,
            reason=reason,  # type: ignore[arg-type]
            trigger_price=runtime.trigger_price,
            trigger_price_ticks=runtime.trigger_price_ticks,
            last_price=tick.last_price,
            last_price_ticks=last_ticks,
            tick_sequence=tick.sequence,
            exchange_timestamp=ensure_ist(tick.exchange_timestamp),
            breakout_candle_time=breakout_candle_time,
            breakout_candle_volume=breakout_volume,
            avg_prior_3_1m_volume=avg_prior,
            volume_ok=volume_ok,
            volume_reliable=volume_reliable,
            continuation_rule_version=runtime.continuation_rule_version,
            detected_at=datetime.now(timezone.utc),
        )
        if self._on_rejected is not None:
            try:
                self._on_rejected(event)
            except Exception:  # noqa: BLE001
                self._metrics.callback_failures += 1
                logger.exception("on_rejected callback failed")

        self._close_pullback(
            runtime,
            outcome="CONTINUATION_REJECTED",
            detail={
                "reason": reason,
                "last_price": tick.last_price,
                "breakout_candle_volume": breakout_volume,
                "avg_prior_3_1m_volume": avg_prior,
            },
            evaluation_candle_time=breakout_candle_time,
            metric_attr="pullback_active_cleared_after_reject",
        )

    def _close_pullback(
        self,
        runtime: ContinuationArmRuntime,
        *,
        outcome: ContinuationCloseOutcome,
        detail: dict,
        evaluation_candle_time: Optional[datetime],
        metric_attr: str,
    ) -> None:
        if self._pullback_closer is None:
            return
        try:
            closed = self._pullback_closer.close_after_continuation_outcome(
                runtime.setup_id,
                outcome,
                detail=detail,
                evaluation_candle_time=evaluation_candle_time,
            )
            if closed:
                setattr(
                    self._metrics,
                    metric_attr,
                    getattr(self._metrics, metric_attr) + 1,
                )
            else:
                self._metrics.pullback_close_idempotent_hits += 1
        except Exception:  # noqa: BLE001
            self._metrics.audit_sync_failures += 1
            logger.exception(
                "pullback close_after_continuation_outcome failed setup=%s",
                runtime.setup_id,
            )

    def _detach_arm(self, runtime: ContinuationArmRuntime) -> None:
        token = runtime.instrument_token
        if self._armed_by_token.get(token) == runtime.setup_id:
            del self._armed_by_token[token]
