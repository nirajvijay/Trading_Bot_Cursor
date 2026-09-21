"""SessionRiskConfig: capital derivations and the start-time sanity guard."""

from __future__ import annotations

import unittest

from engine_config import (
    DEFAULT_LEVERAGE_FACTOR,
    DEFAULT_TOTAL_CAPITAL_RUPEES,
    InvalidSessionConfig,
    SessionRiskConfig,
    validate,
)


class CapitalDerivationTests(unittest.TestCase):
    def test_defaults_are_three_lakh_at_five_times(self) -> None:
        config = SessionRiskConfig()
        self.assertEqual(config.total_capital_rupees, 300_000.0)
        self.assertEqual(config.leverage_factor, 5.0)
        self.assertEqual(config.buying_power_rupees, 1_500_000.0)

    def test_remaining_capital_subtracts_margin_used(self) -> None:
        config = SessionRiskConfig()
        self.assertEqual(config.remaining_capital_rupees(100_000.0), 200_000.0)
        # Leverage applies once, to whatever capital is left.
        self.assertEqual(config.remaining_buying_power_rupees(100_000.0), 1_000_000.0)

    def test_flat_account_has_full_capital_available(self) -> None:
        config = SessionRiskConfig()
        self.assertEqual(
            config.remaining_capital_rupees(0.0), DEFAULT_TOTAL_CAPITAL_RUPEES
        )
        self.assertEqual(
            config.remaining_buying_power_rupees(0.0),
            DEFAULT_TOTAL_CAPITAL_RUPEES * DEFAULT_LEVERAGE_FACTOR,
        )

    def test_over_committed_account_floors_at_zero_not_negative(self) -> None:
        config = SessionRiskConfig()
        self.assertEqual(config.remaining_capital_rupees(500_000.0), 0.0)
        self.assertEqual(config.remaining_buying_power_rupees(500_000.0), 0.0)

    def test_negative_margin_used_is_ignored_rather_than_crediting_capital(self) -> None:
        config = SessionRiskConfig()
        self.assertEqual(
            config.remaining_capital_rupees(-50_000.0), DEFAULT_TOTAL_CAPITAL_RUPEES
        )

    def test_to_risk_limits_carries_all_three_caps(self) -> None:
        config = SessionRiskConfig(
            per_trade_cap_rupees=900.0,
            per_trade_cap_vwap_limited_rupees=450.0,
            daily_loss_cap_rupees=3_000.0,
        )
        limits = config.to_risk_limits()
        self.assertEqual(limits.per_trade_cap_rupees, 900.0)
        self.assertEqual(limits.per_trade_cap_vwap_limited_rupees, 450.0)
        self.assertEqual(limits.daily_loss_cap_rupees, 3_000.0)


class ValidationTests(unittest.TestCase):
    def test_defaults_are_valid(self) -> None:
        validate(SessionRiskConfig())  # must not raise

    def test_accept_cap_above_daily_cap_is_refused(self) -> None:
        with self.assertRaises(InvalidSessionConfig) as ctx:
            validate(
                SessionRiskConfig(per_trade_cap_rupees=5_000.0, daily_loss_cap_rupees=3_000.0)
            )
        self.assertIn("per_trade_cap_rupees", str(ctx.exception))

    def test_limited_cap_above_daily_cap_is_refused(self) -> None:
        with self.assertRaises(InvalidSessionConfig):
            validate(
                SessionRiskConfig(
                    per_trade_cap_rupees=900.0,
                    per_trade_cap_vwap_limited_rupees=4_000.0,
                    daily_loss_cap_rupees=3_000.0,
                )
            )

    def test_per_trade_cap_equal_to_daily_cap_is_allowed(self) -> None:
        # Equal is a legitimate "one trade can lose the whole day" choice.
        validate(
            SessionRiskConfig(
                per_trade_cap_rupees=3_000.0,
                per_trade_cap_vwap_limited_rupees=3_000.0,
                daily_loss_cap_rupees=3_000.0,
            )
        )

    def test_tiny_validation_caps_are_allowed(self) -> None:
        # The whole point of editable caps: set them small to validate cheaply.
        validate(
            SessionRiskConfig(
                per_trade_cap_rupees=20.0,
                per_trade_cap_vwap_limited_rupees=10.0,
                daily_loss_cap_rupees=50.0,
            )
        )

    def test_non_positive_values_are_refused(self) -> None:
        for kwargs in (
            {"per_trade_cap_rupees": 0.0},
            {"per_trade_cap_vwap_limited_rupees": 0.0},
            {"daily_loss_cap_rupees": 0.0},
            {"total_capital_rupees": 0.0},
            {"per_trade_cap_rupees": -900.0},
            {"total_capital_rupees": -300_000.0},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(InvalidSessionConfig):
                    validate(SessionRiskConfig(**kwargs))

    def test_leverage_below_one_is_refused(self) -> None:
        with self.assertRaises(InvalidSessionConfig):
            validate(SessionRiskConfig(leverage_factor=0.5))

    def test_leverage_of_exactly_one_is_allowed(self) -> None:
        validate(SessionRiskConfig(leverage_factor=1.0))


if __name__ == "__main__":
    unittest.main()
