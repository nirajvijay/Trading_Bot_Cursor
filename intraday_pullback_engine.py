"""
Intraday pullback engine: spike activation + completed 5m evaluation.

Non-fatal to market data. Persistence failure degrades the subsystem —
no further unpersisted transitions.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, Dict, Mapping, Optional

from candle_aggregation import (
    CompletedFiveMinuteCandle,
    ensure_ist,
    floor_five_minute_bucket_start,
    minute_of_day_from_datetime,
)
from intraday_pullback_config import IntradayPullbackRuleConfig
from intraday_pullback_rules import IntradayPullbackRuleEngine
from intraday_pullback_writer import IntradayPullbackWriter, PullbackConflictError
from pullback_ema_seed import PullbackEmaSeedStore
from pullback_features import (
    build_features,
    empty_sequence_after_impulse,
    update_sequence,
)
from pullback_gap import compute_gap_analytics
from pullback_indicators import Ema20State, SessionVwapState
from pullback_metrics import PullbackMetrics
from pullback_state import (
    PullbackSetupRuntime,
    make_setup_id,
    transition,
)
from pullback_types import (
    ACTIVE_SETUP_STATES,
    GapAnalytics,
    PullbackCandidateEvent,
    PullbackSetup,
)
from spike_types import IntradaySpikeEvent

logger = logging.getLogger(__name__)

OnPullbackReady = Callable[[PullbackCandidateEvent], None]
OnLifecycleEvent = Callable[
    [str, str, str, str, Optional[datetime]],
    None,
]
"""(tradingsymbol, setup_id, event_type, resulting_state, evaluation_candle_time)."""


class IntradayPullbackEngine:
    def __init__(
        self,
        *,
        writer: IntradayPullbackWriter,
        config: Optional[IntradayPullbackRuleConfig] = None,
        ema_seeds: Optional[PullbackEmaSeedStore] = None,
        gap_by_token: Optional[Mapping[int, GapAnalytics]] = None,
        metrics: Optional[PullbackMetrics] = None,
        on_pullback_ready: Optional[OnPullbackReady] = None,
        on_lifecycle_event: Optional[OnLifecycleEvent] = None,
    ) -> None:
        self._writer = writer
        self._config = config if config is not None else IntradayPullbackRuleConfig()
        self._rules = IntradayPullbackRuleEngine(self._config)
        self._ema_seeds = ema_seeds
        self._gap_by_token = dict(gap_by_token or {})
        self._metrics = metrics if metrics is not None else PullbackMetrics()
        self._on_pullback_ready = on_pullback_ready
        self._on_lifecycle_event = on_lifecycle_event

        self._active_by_token: Dict[int, PullbackSetupRuntime] = {}
        self._runtimes: Dict[str, PullbackSetupRuntime] = {}
        self._ema_by_token: Dict[int, Ema20State] = {}
        self._vwap_by_token: Dict[int, SessionVwapState] = {}
        self._degraded = False

    @property
    def metrics(self) -> PullbackMetrics:
        return self._metrics

    @property
    def config(self) -> IntradayPullbackRuleConfig:
        return self._config

    @property
    def degraded(self) -> bool:
        return self._degraded

    def _mark_degraded(self, exc: BaseException) -> None:
        self._degraded = True
        self._metrics.writer_failure += 1
        self._metrics.subsystem_degraded += 1
        logger.error("pullback subsystem degraded: %s", exc, exc_info=True)

    def _ema(self, token: int) -> Ema20State:
        if token not in self._ema_by_token:
            ema = Ema20State(period=self._config.ema_period)
            if self._ema_seeds is not None:
                applied = self._ema_seeds.apply_to(token, ema)
                if not applied:
                    self._metrics.warmup_unavailable += 1
            self._ema_by_token[token] = ema
        return self._ema_by_token[token]

    def _vwap(self, token: int) -> SessionVwapState:
        if token not in self._vwap_by_token:
            self._vwap_by_token[token] = SessionVwapState()
        return self._vwap_by_token[token]

    def on_spike(self, event: IntradaySpikeEvent) -> None:
        self._metrics.spikes_received += 1
        if self._degraded:
            return
        try:
            self._handle_spike(event)
        except Exception:  # noqa: BLE001
            self._metrics.strategy_failure += 1
            logger.exception("pullback on_spike failed")

    def _handle_spike(self, event: IntradaySpikeEvent) -> None:
        if event.direction not in ("UP", "DOWN"):
            return

        existing = self._active_by_token.get(event.instrument_token)
        if existing is not None and existing.is_active:
            self._metrics.spike_ignored_while_active += 1
            return

        spike_time = ensure_ist(event.candle_time)
        minute = minute_of_day_from_datetime(spike_time)
        if not (
            self._config.spike_detection_start_minute
            <= minute
            <= self._config.spike_detection_end_minute
        ):
            return

        bucket_start = floor_five_minute_bucket_start(minute)
        if bucket_start is None:
            return
        # Reconstruct impulse candle_time from spike date + bucket start minute.
        hour, mins = divmod(bucket_start, 60)
        impulse_time = spike_time.replace(
            hour=hour, minute=mins, second=0, microsecond=0
        )

        spike_iso = spike_time.isoformat(timespec="seconds")
        setup_id = make_setup_id(
            instrument_token=event.instrument_token,
            spike_candle_time_iso=spike_iso,
            spike_rule_version=event.rule_version,
            pullback_rule_version=self._config.rule_version,
        )
        if setup_id in self._runtimes:
            self._metrics.setup_duplicate += 1
            return

        if self._config.record_gap_analytics:
            gap = self._gap_by_token.get(
                event.instrument_token,
                compute_gap_analytics(None, None),
            )
        else:
            gap = compute_gap_analytics(None, None)

        setup = PullbackSetup(
            setup_id=setup_id,
            instrument_token=event.instrument_token,
            tradingsymbol=event.tradingsymbol,
            session_date=event.session_date,
            direction=event.direction,
            spike_candle_time=spike_time,
            spike_rule_version=event.rule_version,
            spike_open=event.open,
            spike_high=event.high,
            spike_low=event.low,
            spike_close=event.close,
            spike_volume=event.volume,
            impulse_5m_candle_time=impulse_time,
            pullback_rule_version=self._config.rule_version,
            gap=gap,
            created_at=datetime.now(timezone.utc),
        )

        try:
            inserted = self._writer.insert_setup(setup)
            if not inserted:
                self._metrics.setup_duplicate += 1
                return
            self._writer.append_event(
                setup_id=setup_id,
                sequence_number=1,
                event_type="SETUP_CREATED",
                resulting_state="SPIKE_ACCEPTED",
                evaluation_candle_time=spike_time,
                payload={"direction": event.direction},
            )
            self._writer.append_event(
                setup_id=setup_id,
                sequence_number=2,
                event_type="SPIKE_ACCEPTED",
                resulting_state="IMPULSE_MONITORING",
                evaluation_candle_time=spike_time,
                payload={"impulse_5m_candle_time": impulse_time.isoformat()},
            )
        except (PullbackConflictError, RuntimeError, ValueError) as exc:
            self._mark_degraded(exc)
            return

        runtime = PullbackSetupRuntime(
            setup=setup,
            state="IMPULSE_MONITORING",
            sequence_number=2,
        )
        self._runtimes[setup_id] = runtime
        self._active_by_token[event.instrument_token] = runtime
        self._metrics.setups_created += 1
        self._emit_lifecycle(
            setup.tradingsymbol,
            setup_id,
            "SETUP_CREATED",
            "SPIKE_ACCEPTED",
            spike_time,
        )
        self._emit_lifecycle(
            setup.tradingsymbol,
            setup_id,
            "SPIKE_ACCEPTED",
            "IMPULSE_MONITORING",
            spike_time,
        )

    def on_five_minute_candle(self, candle: CompletedFiveMinuteCandle) -> None:
        self._metrics.five_minute_candles_seen += 1
        if self._degraded:
            return
        try:
            self._handle_five_minute(candle)
        except Exception:  # noqa: BLE001
            self._metrics.strategy_failure += 1
            logger.exception("pullback on_five_minute failed")

    def _handle_five_minute(self, candle: CompletedFiveMinuteCandle) -> None:
        token = candle.instrument_token
        ema = self._ema(token)
        ema_value = ema.update(candle.close)
        vwap = self._vwap(token).update(
            candle.high, candle.low, candle.close, candle.volume
        )

        runtime = self._active_by_token.get(token)
        if runtime is None or not runtime.is_active:
            return

        if (
            runtime.last_eval_5m_candle_time is not None
            and candle.candle_time <= runtime.last_eval_5m_candle_time
        ):
            self._metrics.out_of_order += 1
            return

        if runtime.state == "IMPULSE_MONITORING":
            if candle.candle_time == runtime.setup.impulse_5m_candle_time:
                self._freeze_impulse(runtime, candle)
            elif candle.candle_time > runtime.setup.impulse_5m_candle_time:
                # Missed impulse bar (incomplete bucket) — cancel setup.
                self._terminal(
                    runtime,
                    "CANCELLED",
                    "CANCELLED",
                    candle.candle_time,
                    {"reason": "missing_impulse_candle"},
                )
            return

        if runtime.state == "PULLBACK_MONITORING":
            self._evaluate_pullback(runtime, candle, ema_value, vwap)
            return

        if runtime.state in ("PULLBACK_READY", "CONTINUATION_MONITORING"):
            # Normalize READY → CONTINUATION if somehow still READY
            if runtime.state == "PULLBACK_READY":
                if self._persist_transition(
                    runtime,
                    "CONTINUATION_MONITORING",
                    "CONTINUATION_ATTEMPT",
                    candle.candle_time,
                    {
                        "note": "auto_enter_continuation",
                        "continuation_attempt_count": 0,
                    },
                ):
                    transition(runtime, "CONTINUATION_MONITORING")
            self._evaluate_continuation(runtime, candle, ema_value, vwap)

    def _freeze_impulse(
        self,
        runtime: PullbackSetupRuntime,
        candle: CompletedFiveMinuteCandle,
    ) -> None:
        runtime.impulse_5m_high = candle.high
        runtime.impulse_5m_low = candle.low
        runtime.sequence = empty_sequence_after_impulse(candle.high, candle.low)
        runtime.last_eval_5m_candle_time = candle.candle_time
        ok = self._persist_transition(
            runtime,
            "PULLBACK_MONITORING",
            "IMPULSE_BOUNDARIES_FROZEN",
            candle.candle_time,
            {
                "impulse_5m_high": candle.high,
                "impulse_5m_low": candle.low,
            },
        )
        if ok:
            transition(runtime, "PULLBACK_MONITORING")

    def _evaluate_pullback(
        self,
        runtime: PullbackSetupRuntime,
        candle: CompletedFiveMinuteCandle,
        ema_value: Optional[float],
        vwap: Optional[float],
    ) -> None:
        if candle.candle_time <= runtime.setup.impulse_5m_candle_time:
            return
        assert runtime.impulse_5m_high is not None
        assert runtime.impulse_5m_low is not None
        assert runtime.sequence is not None

        prev_breach = runtime.sequence.spike_extreme_breached
        runtime.sequence = update_sequence(
            runtime.sequence,
            direction=runtime.setup.direction,
            candle=candle,
            impulse_high=runtime.impulse_5m_high,
            impulse_low=runtime.impulse_5m_low,
            spike_high=runtime.setup.spike_high,
            spike_low=runtime.setup.spike_low,
            ema20=ema_value,
        )
        runtime.last_eval_5m_candle_time = candle.candle_time
        self._metrics.setup_candles_evaluated += 1

        if runtime.sequence.spike_extreme_breached and not prev_breach:
            self._metrics.spike_extreme_breach += 1
            self._append_only(
                runtime,
                "SPIKE_EXTREME_BREACHED",
                runtime.state,
                candle.candle_time,
                {
                    "spike_extreme_breached_at": candle.candle_time.isoformat(),
                },
            )

        features = build_features(
            instrument_token=runtime.setup.instrument_token,
            direction=runtime.setup.direction,
            candle=candle,
            impulse_high=runtime.impulse_5m_high,
            impulse_low=runtime.impulse_5m_low,
            spike_high=runtime.setup.spike_high,
            spike_low=runtime.setup.spike_low,
            sequence=runtime.sequence,
            ema20=ema_value,
            vwap=vwap,
        )
        decision = self._rules.evaluate_monitoring(features, runtime.sequence)

        if decision.outcome == "invalidated":
            self._terminal(
                runtime,
                "INVALIDATED",
                "INVALIDATED",
                candle.candle_time,
                {
                    "invalidation_reason": decision.invalidation_reason,
                    "deepest_retracement_percent": runtime.sequence.deepest_retracement_percent,
                },
            )
            self._metrics.invalidated += 1
            return

        if decision.outcome == "expired":
            self._terminal(
                runtime,
                "EXPIRED",
                "EXPIRED",
                candle.candle_time,
                {"terminal_reason": decision.terminal_reason},
            )
            self._metrics.expired += 1
            return

        if decision.outcome == "pullback_ready":
            assert decision.pullback_type is not None
            runtime.pullback_type = decision.pullback_type
            payload = {
                "pullback_type": decision.pullback_type,
                "retracement_percent": runtime.sequence.retracement_percent,
                "deepest_retracement_percent": runtime.sequence.deepest_retracement_percent,
                "pullback_candle_count": runtime.sequence.pullback_candle_count,
                "ema20_value": ema_value,
                "ema20_interacted": runtime.sequence.ema20_interacted,
            }
            if not self._persist_transition(
                runtime,
                "PULLBACK_READY",
                "PULLBACK_READY",
                candle.candle_time,
                payload,
            ):
                return
            transition(runtime, "PULLBACK_READY")
            if decision.pullback_type == "EMA_PULLBACK":
                self._metrics.pullback_ready_ema += 1
            else:
                self._metrics.pullback_ready_shallow += 1

            candidate = PullbackCandidateEvent(
                setup=runtime.setup,
                pullback_type=decision.pullback_type,
                features=features,
                sequence=runtime.sequence,
                decision=decision,
                detected_at=datetime.now(timezone.utc),
            )
            if self._on_pullback_ready is not None:
                try:
                    self._on_pullback_ready(candidate)
                except Exception:  # noqa: BLE001
                    logger.exception("on_pullback_ready callback failed")

            if not self._persist_transition(
                runtime,
                "CONTINUATION_MONITORING",
                "CONTINUATION_ATTEMPT",
                candle.candle_time,
                {
                    "note": "enter_continuation_monitoring",
                    "continuation_attempt_count": 0,
                },
            ):
                return
            transition(runtime, "CONTINUATION_MONITORING")
            return

        self._metrics.continue_monitoring += 1

    def _evaluate_continuation(
        self,
        runtime: PullbackSetupRuntime,
        candle: CompletedFiveMinuteCandle,
        ema_value: Optional[float],
        vwap: Optional[float],
    ) -> None:
        if runtime.impulse_5m_high is None or runtime.impulse_5m_low is None:
            return
        if runtime.sequence is None:
            runtime.sequence = empty_sequence_after_impulse(
                runtime.impulse_5m_high, runtime.impulse_5m_low
            )

        # Update sequence extremes for structure checks without counting as pb candles
        # for READY (already ready). Still track breaches and depth.
        prev = runtime.sequence
        runtime.sequence = update_sequence(
            prev,
            direction=runtime.setup.direction,
            candle=candle,
            impulse_high=runtime.impulse_5m_high,
            impulse_low=runtime.impulse_5m_low,
            spike_high=runtime.setup.spike_high,
            spike_low=runtime.setup.spike_low,
            ema20=ema_value,
        )
        # Preserve pullback_candle_count from ready era for analytics clarity:
        # continuation bars should not inflate the pullback window count used for READY.
        # But update_sequence increments it — restore prior count for continuation phase.
        from pullback_types import PullbackSequenceState

        runtime.sequence = PullbackSequenceState(
            highest_high_since_impulse=runtime.sequence.highest_high_since_impulse,
            lowest_low_since_impulse=runtime.sequence.lowest_low_since_impulse,
            retracement_percent=runtime.sequence.retracement_percent,
            deepest_retracement_percent=runtime.sequence.deepest_retracement_percent,
            cumulative_pullback_volume=runtime.sequence.cumulative_pullback_volume,
            median_pullback_volume=runtime.sequence.median_pullback_volume,
            number_of_opposing_candles=runtime.sequence.number_of_opposing_candles,
            largest_opposing_body_ratio=runtime.sequence.largest_opposing_body_ratio,
            last_close=runtime.sequence.last_close,
            pullback_candle_count=prev.pullback_candle_count,
            last_eval_5m_candle_time=candle.candle_time,
            spike_extreme_breached=runtime.sequence.spike_extreme_breached,
            spike_extreme_breached_at=runtime.sequence.spike_extreme_breached_at,
            ema20_value=ema_value,
            ema20_interacted=runtime.sequence.ema20_interacted,
            volumes=runtime.sequence.volumes,
        )
        runtime.last_eval_5m_candle_time = candle.candle_time

        features = build_features(
            instrument_token=runtime.setup.instrument_token,
            direction=runtime.setup.direction,
            candle=candle,
            impulse_high=runtime.impulse_5m_high,
            impulse_low=runtime.impulse_5m_low,
            spike_high=runtime.setup.spike_high,
            spike_low=runtime.setup.spike_low,
            sequence=runtime.sequence,
            ema20=ema_value,
            vwap=vwap,
        )
        decision = self._rules.evaluate_continuation(features, runtime.sequence)
        if decision.outcome == "invalidated":
            self._terminal(
                runtime,
                "INVALIDATED",
                "INVALIDATED",
                candle.candle_time,
                {"invalidation_reason": decision.invalidation_reason},
            )
            self._metrics.invalidated += 1
            return

        # Continuation trigger formulas are deferred; structure-only checks here.

    def record_continuation_attempt(
        self,
        setup_id: str,
        *,
        evaluation_candle_time: Optional[datetime] = None,
        detail: Optional[dict] = None,
    ) -> None:
        """Record a failed continuation attempt without closing the setup."""
        if self._degraded:
            return
        runtime = self._runtimes.get(setup_id)
        if runtime is None or runtime.state != "CONTINUATION_MONITORING":
            return
        if not self._config.allow_multiple_continuation_attempts:
            return
        runtime.continuation_attempt_count += 1
        self._metrics.continuation_attempts += 1
        self._append_only(
            runtime,
            "CONTINUATION_ATTEMPT",
            "CONTINUATION_MONITORING",
            evaluation_candle_time,
            {
                "continuation_attempt_count": runtime.continuation_attempt_count,
                **(detail or {}),
            },
        )

    def on_trade_executed(
        self,
        setup_id: str,
        *,
        fill_id: str,
        executed_at: Optional[datetime] = None,
    ) -> None:
        """Terminal TRADED contract for future execution stage."""
        if self._degraded:
            return
        runtime = self._runtimes.get(setup_id)
        if runtime is None or runtime.is_terminal:
            return
        when = executed_at or datetime.now(timezone.utc)
        self._terminal(
            runtime,
            "TRADED",
            "TRADE_EXECUTED",
            when,
            {"fill_id": fill_id},
        )
        self._metrics.traded += 1

    def on_session_closed(self, session_date: str) -> None:
        if self._degraded:
            return
        for runtime in list(self._active_by_token.values()):
            if runtime.setup.session_date != session_date:
                continue
            if not runtime.is_active:
                continue
            self._terminal(
                runtime,
                "SESSION_CLOSED",
                "SESSION_CLOSED",
                None,
                {"session_date": session_date},
            )
            self._metrics.session_closed += 1

    def _emit_lifecycle(
        self,
        tradingsymbol: str,
        setup_id: str,
        event_type: str,
        resulting_state: str,
        evaluation_candle_time: Optional[datetime],
    ) -> None:
        logger.info(
            "lifecycle %s %s %s -> %s",
            tradingsymbol,
            event_type,
            resulting_state,
            setup_id,
        )
        if self._on_lifecycle_event is None:
            return
        try:
            self._on_lifecycle_event(
                tradingsymbol,
                setup_id,
                event_type,
                resulting_state,
                evaluation_candle_time,
            )
        except Exception:  # noqa: BLE001
            logger.exception("on_lifecycle_event callback failed")

    def _persist_transition(
        self,
        runtime: PullbackSetupRuntime,
        new_state: str,
        event_type: str,
        eval_time: Optional[datetime],
        payload: dict,
    ) -> bool:
        next_seq = runtime.sequence_number + 1
        try:
            self._writer.append_event(
                setup_id=runtime.setup.setup_id,
                sequence_number=next_seq,
                event_type=event_type,  # type: ignore[arg-type]
                resulting_state=new_state,  # type: ignore[arg-type]
                evaluation_candle_time=eval_time,
                payload=payload,
            )
        except (PullbackConflictError, RuntimeError) as exc:
            self._mark_degraded(exc)
            return False
        runtime.sequence_number = next_seq
        self._emit_lifecycle(
            runtime.setup.tradingsymbol,
            runtime.setup.setup_id,
            event_type,
            new_state,
            eval_time,
        )
        return True

    def _append_only(
        self,
        runtime: PullbackSetupRuntime,
        event_type: str,
        resulting_state: str,
        eval_time: Optional[datetime],
        payload: dict,
    ) -> None:
        next_seq = runtime.sequence_number + 1
        try:
            self._writer.append_event(
                setup_id=runtime.setup.setup_id,
                sequence_number=next_seq,
                event_type=event_type,  # type: ignore[arg-type]
                resulting_state=resulting_state,  # type: ignore[arg-type]
                evaluation_candle_time=eval_time,
                payload=payload,
            )
            runtime.sequence_number = next_seq
            self._emit_lifecycle(
                runtime.setup.tradingsymbol,
                runtime.setup.setup_id,
                event_type,
                resulting_state,
                eval_time,
            )
        except (PullbackConflictError, RuntimeError) as exc:
            self._mark_degraded(exc)

    def _terminal(
        self,
        runtime: PullbackSetupRuntime,
        new_state: str,
        event_type: str,
        eval_time: Optional[datetime],
        payload: dict,
    ) -> None:
        if runtime.is_terminal:
            return
        if not self._persist_transition(
            runtime, new_state, event_type, eval_time, payload
        ):
            return
        try:
            transition(runtime, new_state)  # type: ignore[arg-type]
        except ValueError:
            # Allow terminal from any active via direct assign if state machine
            # edge missing (e.g. CANCELLED from IMPULSE already allowed).
            if new_state in ACTIVE_SETUP_STATES:
                raise
            runtime.state = new_state  # type: ignore[assignment]
        token = runtime.setup.instrument_token
        if self._active_by_token.get(token) is runtime:
            del self._active_by_token[token]
