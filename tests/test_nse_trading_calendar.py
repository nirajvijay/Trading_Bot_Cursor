"""Tests for NSE trading calendar prior-session resolution."""

from __future__ import annotations

import unittest

from nse_trading_calendar import is_nse_trading_day, prior_nse_trading_session


class NseTradingCalendarTests(unittest.TestCase):
    def test_weekend_not_trading_day(self) -> None:
        from datetime import date

        self.assertFalse(is_nse_trading_day(date(2026, 8, 8)))  # Saturday

    def test_republic_day_not_trading(self) -> None:
        from datetime import date

        self.assertFalse(is_nse_trading_day(date(2026, 1, 26)))

    def test_prior_session_skips_weekend(self) -> None:
        # Monday 2026-08-03 → prior Friday 2026-07-31
        self.assertEqual(prior_nse_trading_session("2026-08-03"), "2026-07-31")

    def test_prior_session_skips_holiday(self) -> None:
        # Day after Republic Day 2026-01-26 (Monday) → prior is 2026-01-23 (Friday)
        self.assertEqual(prior_nse_trading_session("2026-01-27"), "2026-01-23")

    def test_prior_session_for_friday(self) -> None:
        # Friday 2026-08-07 → prior Thursday 2026-08-06
        self.assertEqual(prior_nse_trading_session("2026-08-07"), "2026-08-06")


if __name__ == "__main__":
    unittest.main()
