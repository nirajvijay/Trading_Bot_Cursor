"""engine_charges: the research intraday charges formula, ported unchanged."""
from __future__ import annotations

import unittest

from engine_charges import estimate_intraday_charges, round_trip_charges


class EstimateTests(unittest.TestCase):
    def test_a_full_size_round_trip_hits_the_brokerage_cap(self) -> None:
        # Buy 1,000 at 100, sell at 101.
        buy, sell = 100_000.0, 101_000.0
        brokerage = 20.0 + 20.0
        stt = 25.0                      # round(0.025% x 101,000 = 25.25)
        exchange = 0.0000307 * 201_000
        sebi = 10e-7 * 201_000
        stamp = 3.0                     # round(0.003% x 100,000)
        gst = 0.18 * (brokerage + exchange + sebi)
        self.assertAlmostEqual(
            estimate_intraday_charges(buy, sell),
            brokerage + stt + exchange + sebi + stamp + gst,
            places=9,
        )

    def test_a_micro_round_trip_pays_percentage_brokerage(self) -> None:
        # Buy 10 at 100, sell at 101: brokerage 0.03% per leg, no cap.
        buy, sell = 1_000.0, 1_010.0
        brokerage = 0.0003 * buy + 0.0003 * sell
        exchange = 0.0000307 * 2_010
        sebi = 10e-7 * 2_010
        gst = 0.18 * (brokerage + exchange + sebi)
        # STT round(0.2525) = 0 and stamp round(0.03) = 0.
        self.assertAlmostEqual(
            estimate_intraday_charges(buy, sell), brokerage + exchange + sebi + gst, places=9
        )

    def test_negative_values_are_treated_as_zero(self) -> None:
        self.assertEqual(estimate_intraday_charges(-5.0, -5.0), 0.0)


class RoundTripTests(unittest.TestCase):
    def test_a_long_buys_at_entry_and_sells_at_exit(self) -> None:
        self.assertAlmostEqual(
            round_trip_charges(direction="UP", qty=1_000, entry=100.0, exit_price=101.0),
            estimate_intraday_charges(100_000.0, 101_000.0),
        )

    def test_a_short_sells_at_entry_and_buys_back_at_exit(self) -> None:
        self.assertAlmostEqual(
            round_trip_charges(direction="DOWN", qty=1_000, entry=101.0, exit_price=100.0),
            estimate_intraday_charges(100_000.0, 101_000.0),
        )

    def test_no_quantity_costs_nothing(self) -> None:
        self.assertEqual(round_trip_charges(direction="UP", qty=0, entry=100.0, exit_price=99.0), 0.0)


if __name__ == "__main__":
    unittest.main()
