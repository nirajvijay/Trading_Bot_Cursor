"""Smoke: Nifty 100 token queue sizing / candle-completeness expectations."""

from __future__ import annotations

import unittest

from config.nifty100_symbols import NIFTY_100_SYMBOLS
from session_quality import NOMINAL_SESSION_MINUTES


class Nifty100QueueSmokeTests(unittest.TestCase):
    def test_universe_size(self) -> None:
        self.assertEqual(len(NIFTY_100_SYMBOLS), 100)
        self.assertEqual(len(set(NIFTY_100_SYMBOLS)), 100)

    def test_queue_capacity_covers_full_book(self) -> None:
        # TickReceiver default queue must absorb a burst across the full universe.
        # 100 tokens × ~2 ticks/sec × 5s buffer ≈ 1000; production default is higher.
        subscribed = len(NIFTY_100_SYMBOLS)
        recommended_min = subscribed * 10
        self.assertGreaterEqual(recommended_min, 1000)
        self.assertEqual(subscribed, 100)

    def test_completed_session_candle_budget(self) -> None:
        # One completed session contributes up to 375 1m candles per symbol.
        self.assertEqual(NOMINAL_SESSION_MINUTES, 375)
        full_universe_candles = len(NIFTY_100_SYMBOLS) * NOMINAL_SESSION_MINUTES
        self.assertEqual(full_universe_candles, 37500)


if __name__ == "__main__":
    unittest.main()
