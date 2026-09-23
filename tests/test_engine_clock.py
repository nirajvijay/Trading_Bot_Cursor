"""Session clock: every boundary minute on both sides, in IST."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from engine_clock import (
    IST,
    eod_squareoff_due,
    ist_time,
    market_session_open,
    new_entries_allowed,
    protection_retry_allowed,
    start_allowed,
    start_refusal_reason,
    to_ist,
)


def at(hour: int, minute: int, second: int = 0) -> datetime:
    """An IST-aware instant on an ordinary trading day."""
    return datetime(2026, 9, 22, hour, minute, second, tzinfo=IST)


class MarketSessionTests(unittest.TestCase):
    def test_boundaries_are_inclusive(self) -> None:
        self.assertTrue(market_session_open(at(9, 15)))
        self.assertTrue(market_session_open(at(15, 30)))

    def test_just_outside_boundaries_is_closed(self) -> None:
        self.assertFalse(market_session_open(at(9, 14, 59)))
        self.assertFalse(market_session_open(at(15, 30, 1)))

    def test_mid_session_and_far_outside(self) -> None:
        self.assertTrue(market_session_open(at(12, 0)))
        self.assertFalse(market_session_open(at(3, 0)))
        self.assertFalse(market_session_open(at(23, 59)))


class EntryCutoffTests(unittest.TestCase):
    def test_entries_allowed_right_up_to_two_pm(self) -> None:
        self.assertTrue(new_entries_allowed(at(13, 59, 59)))

    def test_entries_blocked_from_two_pm_exactly(self) -> None:
        self.assertFalse(new_entries_allowed(at(14, 0)))
        self.assertFalse(new_entries_allowed(at(14, 0, 1)))

    def test_entries_blocked_before_market_open(self) -> None:
        # Before 09:15 is before the cutoff numerically, but still no market.
        self.assertFalse(new_entries_allowed(at(9, 0)))

    def test_entries_stay_blocked_for_the_rest_of_the_session(self) -> None:
        self.assertFalse(new_entries_allowed(at(15, 0)))
        self.assertFalse(new_entries_allowed(at(15, 29)))


class StartWindowTests(unittest.TestCase):
    def test_start_allowed_only_inside_the_entry_window(self) -> None:
        self.assertTrue(start_allowed(at(9, 15)))
        self.assertTrue(start_allowed(at(13, 59)))
        self.assertFalse(start_allowed(at(14, 0)))
        self.assertFalse(start_allowed(at(9, 14)))

    def test_refusal_reasons_are_specific(self) -> None:
        self.assertIsNone(start_refusal_reason(at(10, 30)))
        self.assertEqual(start_refusal_reason(at(8, 0)), "market_not_open_yet")
        self.assertEqual(start_refusal_reason(at(14, 30)), "past_entry_cutoff")
        self.assertEqual(start_refusal_reason(at(16, 0)), "market_closed")

    def test_refusal_reason_agrees_with_start_allowed_at_every_boundary(self) -> None:
        for moment in (
            at(8, 0),
            at(9, 14, 59),
            at(9, 15),
            at(13, 59, 59),
            at(14, 0),
            at(15, 15),
            at(15, 30),
            at(15, 30, 1),
            at(20, 0),
        ):
            with self.subTest(moment=moment.isoformat()):
                self.assertEqual(
                    start_allowed(moment), start_refusal_reason(moment) is None
                )


class EodSquareoffTests(unittest.TestCase):
    def test_not_due_before_fourteen_fifty(self) -> None:
        self.assertFalse(eod_squareoff_due(at(14, 49, 59)))

    def test_due_from_fourteen_fifty_exactly_and_onward(self) -> None:
        self.assertTrue(eod_squareoff_due(at(14, 50)))
        self.assertTrue(eod_squareoff_due(at(15, 0)))

    def test_due_stays_true_past_the_close(self) -> None:
        # A loop still alive past 15:30 must still consider square-off due, not
        # wrap around to "not yet".
        self.assertTrue(eod_squareoff_due(at(15, 45)))

    def test_buffer_before_broker_mis_cutoff(self) -> None:
        # Zerodha stops accepting new MIS orders at 15:12; ours must fire well
        # before that so squareoff_all can submit and confirm every exit.
        self.assertTrue(eod_squareoff_due(at(14, 50)))
        self.assertFalse(eod_squareoff_due(at(14, 40)))


class ProtectionRetryCutoffTests(unittest.TestCase):
    def test_retry_allowed_before_fifteen_oh_five(self) -> None:
        self.assertTrue(protection_retry_allowed(at(15, 4, 59)))

    def test_retry_blocked_from_fifteen_oh_five_exactly_and_onward(self) -> None:
        self.assertFalse(protection_retry_allowed(at(15, 5)))
        self.assertFalse(protection_retry_allowed(at(15, 12)))
        self.assertFalse(protection_retry_allowed(at(15, 30)))


class TimezoneHandlingTests(unittest.TestCase):
    def test_utc_input_is_converted_to_ist(self) -> None:
        # 08:30 UTC == 14:00 IST, so entries must be blocked.
        utc_moment = datetime(2026, 9, 22, 8, 30, tzinfo=timezone.utc)
        self.assertEqual(ist_time(utc_moment).hour, 14)
        self.assertFalse(new_entries_allowed(utc_moment))

    def test_utc_input_inside_the_window_is_allowed(self) -> None:
        # 04:00 UTC == 09:30 IST.
        utc_moment = datetime(2026, 9, 22, 4, 0, tzinfo=timezone.utc)
        self.assertTrue(new_entries_allowed(utc_moment))

    def test_naive_input_is_treated_as_already_ist(self) -> None:
        naive = datetime(2026, 9, 22, 10, 0)
        self.assertTrue(market_session_open(naive))
        self.assertEqual(to_ist(naive).tzinfo, IST)

    def test_none_uses_current_time_without_raising(self) -> None:
        # Value depends on when the suite runs; only the type contract matters.
        for fn in (
            market_session_open,
            new_entries_allowed,
            start_allowed,
            eod_squareoff_due,
            protection_retry_allowed,
        ):
            with self.subTest(fn=fn.__name__):
                self.assertIsInstance(fn(), bool)


if __name__ == "__main__":
    unittest.main()
