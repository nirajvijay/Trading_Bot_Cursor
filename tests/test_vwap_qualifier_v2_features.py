"""Pure classify / gap tests for VWAP qualifier v2."""

from __future__ import annotations

import unittest

from vwap_qualifier_v2_config import VwapQualifierV2Config
from vwap_qualifier_v2_features import classify_gap, directional_gap, hlc3_pv, risk_for_classification


class Hlc3Tests(unittest.TestCase):
    def test_typical_price_times_volume(self) -> None:
        self.assertEqual(hlc3_pv(110, 100, 105, 10), 1050.0)


class DirectionalGapTests(unittest.TestCase):
    def test_up_uses_trigger_not_last(self) -> None:
        gap = directional_gap("UP", trigger_price=100.0, vwap=99.78)
        self.assertIsNotNone(gap)
        self.assertAlmostEqual(gap or 0, 0.0022, places=8)

    def test_down_uses_trigger(self) -> None:
        gap = directional_gap("DOWN", trigger_price=100.0, vwap=100.22)
        self.assertAlmostEqual(gap or 0, 0.0022, places=8)


class ClassifyGapTests(unittest.TestCase):
    def test_zero_is_accept(self) -> None:
        cls, reason = classify_gap(0.0, quality_ok=True)
        self.assertEqual(cls, "ACCEPT")
        self.assertIsNone(reason)

    def test_just_below_0_22_is_accept(self) -> None:
        cls, _ = classify_gap(0.002199999, quality_ok=True)
        self.assertEqual(cls, "ACCEPT")

    def test_exact_0_22_is_limited(self) -> None:
        cls, _ = classify_gap(0.0022, quality_ok=True)
        self.assertEqual(cls, "LIMITED")

    def test_exact_0_40_is_limited(self) -> None:
        cls, _ = classify_gap(0.0040, quality_ok=True)
        self.assertEqual(cls, "LIMITED")

    def test_just_above_0_40_is_reject(self) -> None:
        cls, _ = classify_gap(0.0040001, quality_ok=True)
        self.assertEqual(cls, "REJECT")

    def test_negative_is_reject(self) -> None:
        cls, _ = classify_gap(-0.0001, quality_ok=True)
        self.assertEqual(cls, "REJECT")

    def test_quality_failure_is_unavailable(self) -> None:
        cls, reason = classify_gap(0.001, quality_ok=False, quality_reason="feed_stale")
        self.assertEqual(cls, "UNAVAILABLE")
        self.assertEqual(reason, "feed_stale")


class RiskProfileTests(unittest.TestCase):
    def test_accept_normal_900(self) -> None:
        profile, cap = risk_for_classification("ACCEPT")
        self.assertEqual(profile, "normal")
        self.assertEqual(cap, 900.0)

    def test_limited_reduced_450(self) -> None:
        profile, cap = risk_for_classification("LIMITED")
        self.assertEqual(profile, "reduced")
        self.assertEqual(cap, 450.0)

    def test_reject_no_risk(self) -> None:
        profile, cap = risk_for_classification("REJECT")
        self.assertIsNone(profile)
        self.assertIsNone(cap)


if __name__ == "__main__":
    unittest.main()
