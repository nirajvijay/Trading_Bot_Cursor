"""The runner's live P&L feed wiring: LIVE gets a feed, PAPER never does."""

from __future__ import annotations

import unittest
from unittest import mock

import run_execution_engine as runner
from engine_live_ticks import LiveTickFeed, NullTickFeed, TickFeedHealth


class BuildTickFeedTests(unittest.TestCase):
    def test_paper_never_gets_a_feed(self) -> None:
        self.assertIsInstance(runner.build_tick_feed(live_orders=False), NullTickFeed)

    def test_live_gets_a_feed_with_the_kite_credentials(self) -> None:
        with mock.patch(
            "login._read_env_merged",
            return_value={"KITE_API_KEY": "key", "KITE_ACCESS_TOKEN": "tok"},
        ):
            feed = runner.build_tick_feed(live_orders=True)
        self.assertIsInstance(feed, LiveTickFeed)
        self.assertEqual((feed._api_key, feed._access_token), ("key", "tok"))


class FeedSummaryTests(unittest.TestCase):
    def _engine(self, health, sources, reasons=None):
        return mock.Mock(
            live_feed_health=health,
            live_pnl_source=sources,
            live_pnl_reason=reasons or {},
        )

    def test_paper_is_off(self) -> None:
        summary = runner.live_mark_feed_summary(
            self._engine(TickFeedHealth(live=False, connected=False, reason="paper_mode"), {})
        )
        self.assertEqual(summary["state"], "off")

    def test_a_stale_trade_makes_the_badge_fallback_with_its_reason(self) -> None:
        summary = runner.live_mark_feed_summary(
            self._engine(
                TickFeedHealth(live=True, connected=True),
                {"t1": "ws", "t2": "kite_rest"},
                {"t1": None, "t2": "tick_stale"},
            )
        )
        self.assertEqual((summary["state"], summary["reason"]), ("fallback", "tick_stale"))

    def test_all_live(self) -> None:
        summary = runner.live_mark_feed_summary(
            self._engine(TickFeedHealth(live=True, connected=True), {"t1": "ws"})
        )
        self.assertEqual(summary["state"], "live")


if __name__ == "__main__":
    unittest.main()
