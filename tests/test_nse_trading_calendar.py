"""Tests for NSE trading calendar prior-session resolution."""

from __future__ import annotations

import math
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from nse_trading_calendar import (
    SpecialSessionSchedule,
    entry_calendar_block_reason,
    hhmm_to_minutes,
    is_nse_trading_day,
    is_special_session_day,
    parse_hhmm,
    prior_nse_trading_session,
    validate_session_gate_hhmm_pair,
)

_IST = ZoneInfo("Asia/Kolkata")


class NseTradingCalendarTests(unittest.TestCase):
    def test_weekend_not_trading_day(self) -> None:
        self.assertFalse(is_nse_trading_day(date(2026, 8, 8)))  # Saturday

    def test_republic_day_not_trading(self) -> None:
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

    def test_entry_calendar_session_date_mismatch(self) -> None:
        now = datetime(2026, 8, 18, 10, 0, tzinfo=_IST)
        self.assertEqual(
            entry_calendar_block_reason("2026-08-17", now),
            "session_date_mismatch",
        )

    def test_entry_calendar_holiday(self) -> None:
        now = datetime(2026, 1, 26, 10, 0, tzinfo=_IST)
        self.assertEqual(
            entry_calendar_block_reason("2026-01-26", now),
            "nse_holiday_or_weekend",
        )

    def test_special_session_date_alone_never_authorizes(self) -> None:
        """A date allowlist / bare special day must not unlock normal-day trading."""
        now = datetime(2026, 11, 8, 10, 0, tzinfo=_IST)
        self.assertTrue(is_special_session_day(date(2026, 11, 8)))
        self.assertEqual(
            entry_calendar_block_reason("2026-11-08", now),
            "special_session_unconfigured",
        )
        self.assertEqual(
            entry_calendar_block_reason(
                "2026-11-08",
                now,
                special_session_schedule=None,
            ),
            "special_session_unconfigured",
        )

    def test_special_session_incomplete_schedule_blocked(self) -> None:
        now = datetime(2026, 11, 8, 18, 20, tzinfo=_IST)
        incomplete = SpecialSessionSchedule(
            session_date="2026-11-08",
            session_open_ist=1815.0,
            entry_cutoff_ist=1810.0,  # cutoff before open — invalid
            square_off_ist=1830.0,
        )
        self.assertEqual(
            entry_calendar_block_reason(
                "2026-11-08", now, special_session_schedule=incomplete
            ),
            "special_session_schedule_incomplete",
        )

    def test_special_session_requires_explicit_schedule(self) -> None:
        now = datetime(2026, 11, 8, 18, 20, tzinfo=_IST)
        sched = SpecialSessionSchedule(
            session_date="2026-11-08",
            session_open_ist=1815.0,
            entry_cutoff_ist=1825.0,
            square_off_ist=1830.0,
        )
        self.assertIsNone(
            entry_calendar_block_reason(
                "2026-11-08", now, special_session_schedule=sched
            )
        )
        wrong = SpecialSessionSchedule(
            session_date="2025-10-21",
            session_open_ist=1815.0,
            entry_cutoff_ist=1825.0,
            square_off_ist=1830.0,
        )
        self.assertEqual(
            entry_calendar_block_reason(
                "2026-11-08", now, special_session_schedule=wrong
            ),
            "special_session_unconfigured",
        )

    def test_parse_hhmm_strict_rejects_malformed(self) -> None:
        self.assertEqual(parse_hhmm(1445), 14 * 60 + 45)
        self.assertEqual(parse_hhmm(1445.0), 14 * 60 + 45)
        self.assertEqual(parse_hhmm(0), 0)
        self.assertEqual(parse_hhmm(2359), 23 * 60 + 59)
        # 1865 must not become 19:05 via arithmetic coercion.
        self.assertIsNone(parse_hhmm(1865))
        self.assertIsNone(parse_hhmm(1865.0))
        self.assertIsNone(parse_hhmm(2400))
        self.assertIsNone(parse_hhmm(1445.5))
        self.assertIsNone(parse_hhmm(14.45))
        self.assertIsNone(parse_hhmm(float("nan")))
        self.assertIsNone(parse_hhmm(float("inf")))
        self.assertIsNone(parse_hhmm(float("-inf")))
        self.assertIsNone(parse_hhmm("1445"))
        self.assertIsNone(parse_hhmm(None))
        with self.assertRaises(ValueError):
            hhmm_to_minutes(1865)
        with self.assertRaises(ValueError):
            hhmm_to_minutes(float("inf"))

    def test_schedule_rejects_malformed_hhmm_without_exception(self) -> None:
        now = datetime(2026, 11, 8, 18, 20, tzinfo=_IST)
        cases = (
            1865.0,
            2400.0,
            1815.5,
            float("nan"),
            float("inf"),
        )
        for bad in cases:
            with self.subTest(bad=bad):
                sched = SpecialSessionSchedule(
                    session_date="2026-11-08",
                    session_open_ist=1815.0,
                    entry_cutoff_ist=bad,
                    square_off_ist=1830.0,
                )
                self.assertEqual(
                    sched.validation_error(),
                    "special_session_schedule_incomplete",
                )
                self.assertEqual(
                    entry_calendar_block_reason(
                        "2026-11-08", now, special_session_schedule=sched
                    ),
                    "special_session_schedule_incomplete",
                )

    def test_schedule_rejects_invalid_ordering(self) -> None:
        # cutoff == open
        self.assertEqual(
            SpecialSessionSchedule(
                session_date="2026-11-08",
                session_open_ist=1815.0,
                entry_cutoff_ist=1815.0,
                square_off_ist=1830.0,
            ).validation_error(),
            "special_session_schedule_incomplete",
        )
        # square_off before cutoff
        self.assertEqual(
            SpecialSessionSchedule(
                session_date="2026-11-08",
                session_open_ist=1815.0,
                entry_cutoff_ist=1830.0,
                square_off_ist=1825.0,
            ).validation_error(),
            "special_session_schedule_incomplete",
        )
        # cutoff == square_off is allowed for special sessions
        self.assertIsNone(
            SpecialSessionSchedule(
                session_date="2026-11-08",
                session_open_ist=1815.0,
                entry_cutoff_ist=1830.0,
                square_off_ist=1830.0,
            ).validation_error()
        )

    def test_admin_gate_pair_validator(self) -> None:
        self.assertIsNone(validate_session_gate_hhmm_pair(1445.0, 1515.0))
        self.assertEqual(
            validate_session_gate_hhmm_pair(1865.0, 1515.0),
            "entry_cutoff_ist_invalid",
        )
        self.assertEqual(
            validate_session_gate_hhmm_pair(1445.0, 2400.0),
            "square_off_ist_invalid",
        )
        self.assertEqual(
            validate_session_gate_hhmm_pair(1445.5, 1515.0),
            "entry_cutoff_ist_invalid",
        )
        self.assertEqual(
            validate_session_gate_hhmm_pair(1445.0, float("nan")),
            "square_off_ist_invalid",
        )
        self.assertEqual(
            validate_session_gate_hhmm_pair(1445.0, float("inf")),
            "square_off_ist_invalid",
        )
        self.assertEqual(
            validate_session_gate_hhmm_pair(1515.0, 1445.0),
            "square_off_ist_not_after_entry_cutoff",
        )
        self.assertEqual(
            validate_session_gate_hhmm_pair(1445.0, 1445.0),
            "square_off_ist_not_after_entry_cutoff",
        )
        # Confirm NaN does not compare-equal itself into a false pass.
        self.assertTrue(math.isnan(float("nan")))


if __name__ == "__main__":
    unittest.main()
