"""
Operational counters for intraday spike detection.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpikeMetricsSnapshot:
    candles_seen: int
    eligible_candles: int
    partial_skipped: int
    baseline_miss: int
    baseline_unreliable: int
    feature_skipped: int
    accepted_spikes: int
    rejected_spikes: int
    writer_failures: int


class SpikeMetrics:
    def __init__(self) -> None:
        self.candles_seen = 0
        self.eligible_candles = 0
        self.partial_skipped = 0
        self.baseline_miss = 0
        self.baseline_unreliable = 0
        self.feature_skipped = 0
        self.accepted_spikes = 0
        self.rejected_spikes = 0
        self.writer_failures = 0

    def snapshot(self) -> SpikeMetricsSnapshot:
        return SpikeMetricsSnapshot(
            candles_seen=self.candles_seen,
            eligible_candles=self.eligible_candles,
            partial_skipped=self.partial_skipped,
            baseline_miss=self.baseline_miss,
            baseline_unreliable=self.baseline_unreliable,
            feature_skipped=self.feature_skipped,
            accepted_spikes=self.accepted_spikes,
            rejected_spikes=self.rejected_spikes,
            writer_failures=self.writer_failures,
        )
