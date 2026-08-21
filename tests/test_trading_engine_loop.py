"""Trading-engine loop sleep: 1s when idle, 250ms only while VWAP pending."""

from __future__ import annotations

import unittest

from live_trading_engine import next_loop_sleep_seconds
from trading_engine_cycle import VWAP_PENDING_RETRY_SECONDS


class LoopSleepTests(unittest.TestCase):
    def test_idle_waits_for_full_poll(self) -> None:
        sleep = next_loop_sleep_seconds(
            has_pending=False,
            poll_seconds=1.0,
            elapsed_since_full=0.0,
        )
        self.assertEqual(sleep, 1.0)
        sleep = next_loop_sleep_seconds(
            has_pending=False,
            poll_seconds=1.0,
            elapsed_since_full=0.4,
        )
        self.assertAlmostEqual(sleep, 0.6)

    def test_pending_retries_250ms_without_slowing_full_tick(self) -> None:
        sleep = next_loop_sleep_seconds(
            has_pending=True,
            poll_seconds=1.0,
            elapsed_since_full=0.0,
        )
        self.assertEqual(sleep, VWAP_PENDING_RETRY_SECONDS)
        sleep = next_loop_sleep_seconds(
            has_pending=True,
            poll_seconds=1.0,
            elapsed_since_full=0.9,
        )
        self.assertAlmostEqual(sleep, 0.1)
        sleep = next_loop_sleep_seconds(
            has_pending=True,
            poll_seconds=1.0,
            elapsed_since_full=1.0,
        )
        self.assertEqual(sleep, 0.0)


if __name__ == "__main__":
    unittest.main()
