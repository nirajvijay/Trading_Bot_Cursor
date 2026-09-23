"""The structural stop: from market structure, never from the fill."""

from __future__ import annotations

import unittest

from engine_stop import structural_stop_price


class StructuralStopTests(unittest.TestCase):
    def test_long_stop_sits_below_the_swing_low(self) -> None:
        stop = structural_stop_price(
            direction="UP", swing_high=113.0, swing_low=107.0, tick_size=0.05, buffer_ticks=1
        )
        self.assertAlmostEqual(stop, 106.95, places=4)

    def test_short_stop_sits_above_the_swing_high(self) -> None:
        stop = structural_stop_price(
            direction="DOWN", swing_high=113.0, swing_low=107.0, tick_size=0.05, buffer_ticks=1
        )
        self.assertAlmostEqual(stop, 113.05, places=4)

    def test_the_buffer_scales_with_ticks(self) -> None:
        stop = structural_stop_price(
            direction="UP", swing_high=113.0, swing_low=107.0, tick_size=0.05, buffer_ticks=4
        )
        self.assertAlmostEqual(stop, 106.80, places=4)

    def test_a_zero_buffer_sits_exactly_at_the_swing(self) -> None:
        stop = structural_stop_price(
            direction="UP", swing_high=113.0, swing_low=107.0, tick_size=0.05, buffer_ticks=0
        )
        self.assertAlmostEqual(stop, 107.0, places=4)

    def test_the_result_lands_on_a_tick_boundary(self) -> None:
        stop = structural_stop_price(
            direction="UP", swing_high=113.0, swing_low=107.10, tick_size=0.05, buffer_ticks=1
        )
        assert stop is not None
        self.assertAlmostEqual((stop / 0.05) % 1, 0.0, places=6)

    def test_an_off_tick_swing_is_refused_rather_than_silently_rounded(self) -> None:
        # 107.03 is not a multiple of the 0.05 tick. Rounding it would invent a
        # stop price the exchange would reject; returning None makes the caller
        # skip the trade instead.
        self.assertIsNone(
            structural_stop_price(
                direction="UP",
                swing_high=113.0,
                swing_low=107.03,
                tick_size=0.05,
                buffer_ticks=1,
            )
        )

    def test_a_missing_swing_yields_no_stop(self) -> None:
        self.assertIsNone(
            structural_stop_price(
                direction="UP", swing_high=113.0, swing_low=None, tick_size=0.05, buffer_ticks=1
            )
        )
        self.assertIsNone(
            structural_stop_price(
                direction="DOWN", swing_high=None, swing_low=107.0, tick_size=0.05, buffer_ticks=1
            )
        )

    def test_a_non_positive_tick_size_yields_no_stop(self) -> None:
        for tick in (0.0, -0.05):
            with self.subTest(tick=tick):
                self.assertIsNone(
                    structural_stop_price(
                        direction="UP",
                        swing_high=113.0,
                        swing_low=107.0,
                        tick_size=tick,
                        buffer_ticks=1,
                    )
                )

    def test_an_unknown_direction_yields_no_stop(self) -> None:
        self.assertIsNone(
            structural_stop_price(
                direction="SIDEWAYS",
                swing_high=113.0,
                swing_low=107.0,
                tick_size=0.05,
                buffer_ticks=1,
            )
        )

    def test_a_buffer_that_would_go_below_zero_yields_no_stop(self) -> None:
        self.assertIsNone(
            structural_stop_price(
                direction="UP",
                swing_high=1.0,
                swing_low=0.05,
                tick_size=0.05,
                buffer_ticks=50,
            )
        )


if __name__ == "__main__":
    unittest.main()
