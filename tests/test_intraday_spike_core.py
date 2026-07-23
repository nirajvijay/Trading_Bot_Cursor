"""Unit tests for Phase 1 intraday spike pure core."""

from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from candle_aggregation import CompletedOneMinuteCandle
from candle_quality_gate import evaluate_candle_quality, primary_quality_skip_reason
from intraday_spike_config import IntradaySpikeRuleConfig
from intraday_spike_rules import IntradaySpikeRuleEngine, evaluate_intraday_spike
from spike_features import compute_spike_features
from spike_metrics import SpikeMetrics
from spike_types import BaselineSnapshot, SpikeFeatures
from tick_event import IST

_IST = ZoneInfo(IST)


def _candle(
    *,
    minute_h: int = 10,
    minute_m: int = 30,
    open_: float = 100.0,
    high: float = 102.0,
    low: float = 99.0,
    close: float = 101.5,
    volume: int = 20_000,
    is_partial: bool = False,
    has_full_minute_coverage: bool = True,
    volume_reliable: bool = True,
    completion_reason: str = "minute_transition",
) -> CompletedOneMinuteCandle:
    return CompletedOneMinuteCandle(
        instrument_token=738561,
        candle_time=datetime(2026, 7, 23, minute_h, minute_m, 0, tzinfo=_IST),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        tick_count=50,
        volume_reliable=volume_reliable,
        completion_reason=completion_reason,  # type: ignore[arg-type]
        has_full_minute_coverage=has_full_minute_coverage,
        is_partial=is_partial,
    )


def _baseline(
    *,
    median_volume: float = 5_000.0,
    trimmed_mean_volume: float = 4_800.0,
    median_abs_return: float = 0.0005,
    is_reliable: bool = True,
    valid_session_count: int = 21,
) -> BaselineSnapshot:
    return BaselineSnapshot(
        instrument_token=738561,
        minute_of_day=630,
        median_volume=median_volume,
        trimmed_mean_volume=trimmed_mean_volume,
        median_abs_return=median_abs_return,
        valid_session_count=valid_session_count,
        is_reliable=is_reliable,
        baseline_as_of_date="2026-07-22",
    )


def _passing_features(**overrides) -> SpikeFeatures:
    base = dict(
        instrument_token=738561,
        minute_of_day=630,  # 10:30 inside window
        session_date="2026-07-23",
        baseline_as_of_date="2026-07-22",
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.5,
        volume=20_000,
        tick_count=50,
        volume_reliable=True,
        absolute_return=0.015,
        signed_return=0.015,
        direction="UP",
        relative_volume_median=4.0,
        relative_volume_trimmed=4.1,
        abs_return_vs_baseline=3.0,
        body_ratio=0.75,
        close_location=0.833,
        median_volume=5_000.0,
        trimmed_mean_volume=4_800.0,
        median_abs_return=0.005,
        valid_session_count=21,
        is_reliable=True,
    )
    base.update(overrides)
    return SpikeFeatures(**base)  # type: ignore[arg-type]


class IntradaySpikeConfigTests(unittest.TestCase):
    def test_defaults_match_locked_v1(self) -> None:
        cfg = IntradaySpikeRuleConfig()
        self.assertEqual(cfg.rule_version, "intraday_spike_v1")
        self.assertEqual(cfg.detection_window_start_minute, 570)
        self.assertEqual(cfg.detection_window_end_minute, 840)
        self.assertEqual(cfg.min_relative_volume_median, 2.0)
        self.assertEqual(cfg.min_relative_volume_trimmed, 2.0)
        self.assertEqual(cfg.min_absolute_return, 0.0015)
        self.assertEqual(cfg.min_abs_return_vs_baseline, 2.0)
        self.assertEqual(cfg.min_body_ratio, 0.60)
        self.assertEqual(cfg.min_bullish_close_location, 0.70)
        self.assertEqual(cfg.max_bearish_close_location, 0.30)

    def test_rejects_absolute_return_outside_allowed_range(self) -> None:
        with self.assertRaises(ValueError):
            IntradaySpikeRuleConfig(min_absolute_return=0.0020)
        with self.assertRaises(ValueError):
            IntradaySpikeRuleConfig(min_absolute_return=0.0005)

    def test_config_is_frozen(self) -> None:
        cfg = IntradaySpikeRuleConfig()
        with self.assertRaises(Exception):
            cfg.min_body_ratio = 0.5  # type: ignore[misc]


class CandleQualityGateTests(unittest.TestCase):
    def test_eligible_clean_candle(self) -> None:
        result = evaluate_candle_quality(_candle(), IntradaySpikeRuleConfig())
        self.assertTrue(result.eligible)
        self.assertEqual(result.reasons, frozenset())

    def test_rejects_partial(self) -> None:
        result = evaluate_candle_quality(
            _candle(is_partial=True, has_full_minute_coverage=False),
            IntradaySpikeRuleConfig(),
        )
        self.assertFalse(result.eligible)
        self.assertIn("partial", result.reasons)
        self.assertEqual(primary_quality_skip_reason(result), "partial")

    def test_rejects_unreliable_volume(self) -> None:
        result = evaluate_candle_quality(
            _candle(volume_reliable=False),
            IntradaySpikeRuleConfig(),
        )
        self.assertFalse(result.eligible)
        self.assertIn("unreliable_volume", result.reasons)

    def test_rejects_shutdown_flush(self) -> None:
        result = evaluate_candle_quality(
            _candle(completion_reason="shutdown_flush"),
            IntradaySpikeRuleConfig(),
        )
        self.assertFalse(result.eligible)
        self.assertIn("bad_completion_reason", result.reasons)

    def test_rejects_incomplete_coverage(self) -> None:
        result = evaluate_candle_quality(
            _candle(has_full_minute_coverage=False, is_partial=False),
            IntradaySpikeRuleConfig(),
        )
        self.assertFalse(result.eligible)
        self.assertIn("incomplete_coverage", result.reasons)


class SpikeFeaturesTests(unittest.TestCase):
    def test_computes_core_ratios(self) -> None:
        # open=100, close=101.5 → abs_return=0.015; range=3; body=1.5/3=0.5
        # close_location=(101.5-99)/3=0.833...
        features, reason = compute_spike_features(
            _candle(open_=100.0, high=102.0, low=99.0, close=101.5, volume=10_000),
            _baseline(median_volume=5_000, trimmed_mean_volume=4_000, median_abs_return=0.005),
        )
        self.assertIsNone(reason)
        assert features is not None
        self.assertAlmostEqual(features.absolute_return, 0.015)
        self.assertAlmostEqual(features.signed_return, 0.015)
        self.assertEqual(features.direction, "UP")
        self.assertAlmostEqual(features.relative_volume_median, 2.0)
        self.assertAlmostEqual(features.relative_volume_trimmed, 2.5)
        self.assertAlmostEqual(features.abs_return_vs_baseline, 3.0)
        self.assertAlmostEqual(features.body_ratio, 0.5)
        self.assertAlmostEqual(features.close_location, (101.5 - 99.0) / 3.0)
        self.assertEqual(features.minute_of_day, 630)
        self.assertEqual(features.session_date, "2026-07-23")
        self.assertEqual(features.baseline_as_of_date, "2026-07-22")

    def test_bearish_direction_and_close_location(self) -> None:
        features, reason = compute_spike_features(
            _candle(open_=100.0, high=100.5, low=97.0, close=97.5, volume=10_000),
            _baseline(),
        )
        self.assertIsNone(reason)
        assert features is not None
        self.assertEqual(features.direction, "DOWN")
        self.assertAlmostEqual(features.close_location, (97.5 - 97.0) / (100.5 - 97.0))

    def test_skips_invalid_open(self) -> None:
        features, reason = compute_spike_features(
            _candle(open_=0.0),
            _baseline(),
        )
        self.assertIsNone(features)
        self.assertEqual(reason, "invalid_open")

    def test_skips_zero_range(self) -> None:
        features, reason = compute_spike_features(
            _candle(open_=100.0, high=100.0, low=100.0, close=100.0),
            _baseline(),
        )
        self.assertIsNone(features)
        self.assertEqual(reason, "zero_range")

    def test_skips_non_positive_baselines(self) -> None:
        _, reason = compute_spike_features(_candle(), _baseline(median_volume=0))
        self.assertEqual(reason, "non_positive_median_volume")
        _, reason = compute_spike_features(
            _candle(), _baseline(trimmed_mean_volume=0)
        )
        self.assertEqual(reason, "non_positive_trimmed_mean_volume")
        _, reason = compute_spike_features(
            _candle(), _baseline(median_abs_return=0)
        )
        self.assertEqual(reason, "non_positive_median_abs_return")


class IntradaySpikeRulesTests(unittest.TestCase):
    def test_accepts_passing_features(self) -> None:
        decision = evaluate_intraday_spike(_passing_features(), IntradaySpikeRuleConfig())
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.reasons, frozenset())
        self.assertEqual(decision.rule_version, "intraday_spike_v1")

    def test_window_edges_inclusive(self) -> None:
        cfg = IntradaySpikeRuleConfig()
        at_start = evaluate_intraday_spike(
            _passing_features(minute_of_day=570), cfg
        )
        at_end = evaluate_intraday_spike(
            _passing_features(minute_of_day=840), cfg
        )
        before = evaluate_intraday_spike(
            _passing_features(minute_of_day=569), cfg
        )
        after = evaluate_intraday_spike(
            _passing_features(minute_of_day=841), cfg
        )
        self.assertTrue(at_start.accepted)
        self.assertTrue(at_end.accepted)
        self.assertFalse(before.accepted)
        self.assertIn("outside_detection_window", before.reasons)
        self.assertFalse(after.accepted)
        self.assertIn("outside_detection_window", after.reasons)

    def test_volume_thresholds_both_required(self) -> None:
        cfg = IntradaySpikeRuleConfig()
        median_fail = evaluate_intraday_spike(
            _passing_features(relative_volume_median=1.99, relative_volume_trimmed=4.0),
            cfg,
        )
        trimmed_fail = evaluate_intraday_spike(
            _passing_features(relative_volume_median=4.0, relative_volume_trimmed=1.99),
            cfg,
        )
        self.assertIn("below_relative_volume_median", median_fail.reasons)
        self.assertIn("below_relative_volume_trimmed", trimmed_fail.reasons)

    def test_absolute_return_and_vs_baseline_boundaries(self) -> None:
        cfg = IntradaySpikeRuleConfig()
        floor_ok = evaluate_intraday_spike(
            _passing_features(absolute_return=0.0015), cfg
        )
        floor_fail = evaluate_intraday_spike(
            _passing_features(absolute_return=0.00149), cfg
        )
        vs_ok = evaluate_intraday_spike(
            _passing_features(abs_return_vs_baseline=2.0), cfg
        )
        vs_fail = evaluate_intraday_spike(
            _passing_features(abs_return_vs_baseline=1.999), cfg
        )
        self.assertTrue(floor_ok.accepted)
        self.assertIn("below_absolute_return", floor_fail.reasons)
        self.assertTrue(vs_ok.accepted)
        self.assertIn("below_abs_return_vs_baseline", vs_fail.reasons)

    def test_body_ratio_boundary(self) -> None:
        cfg = IntradaySpikeRuleConfig()
        ok = evaluate_intraday_spike(_passing_features(body_ratio=0.60), cfg)
        fail = evaluate_intraday_spike(_passing_features(body_ratio=0.599), cfg)
        self.assertTrue(ok.accepted)
        self.assertIn("below_body_ratio", fail.reasons)

    def test_bullish_and_bearish_close_location(self) -> None:
        cfg = IntradaySpikeRuleConfig()
        bull_ok = evaluate_intraday_spike(
            _passing_features(direction="UP", close_location=0.70), cfg
        )
        bull_fail = evaluate_intraday_spike(
            _passing_features(direction="UP", close_location=0.699), cfg
        )
        bear_ok = evaluate_intraday_spike(
            _passing_features(
                direction="DOWN",
                signed_return=-0.015,
                close_location=0.30,
            ),
            cfg,
        )
        bear_fail = evaluate_intraday_spike(
            _passing_features(
                direction="DOWN",
                signed_return=-0.015,
                close_location=0.301,
            ),
            cfg,
        )
        self.assertTrue(bull_ok.accepted)
        self.assertIn("bullish_close_location_fail", bull_fail.reasons)
        self.assertTrue(bear_ok.accepted)
        self.assertIn("bearish_close_location_fail", bear_fail.reasons)

    def test_rejects_flat(self) -> None:
        decision = evaluate_intraday_spike(
            _passing_features(direction="FLAT", signed_return=0.0, absolute_return=0.0),
            IntradaySpikeRuleConfig(),
        )
        self.assertFalse(decision.accepted)
        self.assertIn("flat_direction", decision.reasons)

    def test_rule_engine_wrapper_and_purity(self) -> None:
        engine = IntradaySpikeRuleEngine(IntradaySpikeRuleConfig())
        features = _passing_features()
        first = engine.evaluate(features)
        second = engine.evaluate(features)
        self.assertEqual(first, second)
        self.assertTrue(first.accepted)


class SpikeMetricsTests(unittest.TestCase):
    def test_snapshot_counters(self) -> None:
        metrics = SpikeMetrics()
        metrics.candles_seen += 1
        metrics.eligible_candles += 1
        metrics.accepted_spikes += 1
        snap = metrics.snapshot()
        self.assertEqual(snap.candles_seen, 1)
        self.assertEqual(snap.eligible_candles, 1)
        self.assertEqual(snap.accepted_spikes, 1)
        self.assertEqual(snap.rejected_spikes, 0)


if __name__ == "__main__":
    unittest.main()
