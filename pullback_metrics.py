"""Operational counters for the intraday pullback engine."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PullbackMetrics:
    spikes_received: int = 0
    setups_created: int = 0
    spike_ignored_while_active: int = 0
    setup_duplicate: int = 0
    five_minute_candles_seen: int = 0
    setup_candles_evaluated: int = 0
    pullback_ready_ema: int = 0
    pullback_ready_shallow: int = 0
    spike_extreme_breach: int = 0
    invalidated: int = 0
    expired: int = 0
    session_closed: int = 0
    cancelled: int = 0
    continuation_attempts: int = 0
    traded: int = 0
    warmup_unavailable: int = 0
    out_of_order: int = 0
    writer_failure: int = 0
    strategy_failure: int = 0
    subsystem_degraded: int = 0
    continue_monitoring: int = 0

    def snapshot(self) -> "PullbackMetricsSnapshot":
        return PullbackMetricsSnapshot(
            spikes_received=self.spikes_received,
            setups_created=self.setups_created,
            spike_ignored_while_active=self.spike_ignored_while_active,
            setup_duplicate=self.setup_duplicate,
            five_minute_candles_seen=self.five_minute_candles_seen,
            setup_candles_evaluated=self.setup_candles_evaluated,
            pullback_ready_ema=self.pullback_ready_ema,
            pullback_ready_shallow=self.pullback_ready_shallow,
            spike_extreme_breach=self.spike_extreme_breach,
            invalidated=self.invalidated,
            expired=self.expired,
            session_closed=self.session_closed,
            cancelled=self.cancelled,
            continuation_attempts=self.continuation_attempts,
            traded=self.traded,
            warmup_unavailable=self.warmup_unavailable,
            out_of_order=self.out_of_order,
            writer_failure=self.writer_failure,
            strategy_failure=self.strategy_failure,
            subsystem_degraded=self.subsystem_degraded,
            continue_monitoring=self.continue_monitoring,
        )


@dataclass(frozen=True)
class PullbackMetricsSnapshot:
    spikes_received: int
    setups_created: int
    spike_ignored_while_active: int
    setup_duplicate: int
    five_minute_candles_seen: int
    setup_candles_evaluated: int
    pullback_ready_ema: int
    pullback_ready_shallow: int
    spike_extreme_breach: int
    invalidated: int
    expired: int
    session_closed: int
    cancelled: int
    continuation_attempts: int
    traded: int
    warmup_unavailable: int
    out_of_order: int
    writer_failure: int
    strategy_failure: int
    subsystem_degraded: int
    continue_monitoring: int
