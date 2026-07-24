"""Continuation engine metrics."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ContinuationMetrics:
    candidates_received: int = 0
    arms_created: int = 0
    arm_duplicates_ignored: int = 0
    arm_rejected_preflight: int = 0
    ticks_evaluated: int = 0
    reaches_detected: int = 0
    triggered: int = 0
    rejected_volume: int = 0
    rejected_insufficient_history: int = 0
    rejected_unreliable_volume: int = 0
    disarmed_pullback_structural: int = 0
    pullback_active_cleared_after_trigger: int = 0
    pullback_active_cleared_after_reject: int = 0
    pullback_close_idempotent_hits: int = 0
    writer_failures: int = 0
    degraded: int = 0
    audit_sync_failures: int = 0
    strategy_failure: int = 0
    callback_failures: int = 0

    def snapshot(self) -> "ContinuationMetricsSnapshot":
        return ContinuationMetricsSnapshot(
            candidates_received=self.candidates_received,
            arms_created=self.arms_created,
            arm_duplicates_ignored=self.arm_duplicates_ignored,
            arm_rejected_preflight=self.arm_rejected_preflight,
            ticks_evaluated=self.ticks_evaluated,
            reaches_detected=self.reaches_detected,
            triggered=self.triggered,
            rejected_volume=self.rejected_volume,
            rejected_insufficient_history=self.rejected_insufficient_history,
            rejected_unreliable_volume=self.rejected_unreliable_volume,
            disarmed_pullback_structural=self.disarmed_pullback_structural,
            pullback_active_cleared_after_trigger=self.pullback_active_cleared_after_trigger,
            pullback_active_cleared_after_reject=self.pullback_active_cleared_after_reject,
            pullback_close_idempotent_hits=self.pullback_close_idempotent_hits,
            writer_failures=self.writer_failures,
            degraded=self.degraded,
            audit_sync_failures=self.audit_sync_failures,
            strategy_failure=self.strategy_failure,
            callback_failures=self.callback_failures,
        )


@dataclass(frozen=True)
class ContinuationMetricsSnapshot:
    candidates_received: int
    arms_created: int
    arm_duplicates_ignored: int
    arm_rejected_preflight: int
    ticks_evaluated: int
    reaches_detected: int
    triggered: int
    rejected_volume: int
    rejected_insufficient_history: int
    rejected_unreliable_volume: int
    disarmed_pullback_structural: int
    pullback_active_cleared_after_trigger: int
    pullback_active_cleared_after_reject: int
    pullback_close_idempotent_hits: int
    writer_failures: int
    degraded: int
    audit_sync_failures: int
    strategy_failure: int
    callback_failures: int
