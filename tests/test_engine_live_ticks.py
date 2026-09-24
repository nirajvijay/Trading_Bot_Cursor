"""The engine's own live price feed: lifecycle, subscriptions, health."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from typing import Any, List

from engine_live_ticks import (
    REASON_DISCONNECTED,
    REASON_GAVE_UP,
    REASON_PAPER,
    LiveTickFeed,
    NullTickFeed,
)

T0 = datetime(2026, 9, 22, 5, 0, tzinfo=timezone.utc)


class FakeTicker:
    MODE_LTP = "ltp"

    def __init__(self, api_key: str, access_token: str) -> None:
        self.api_key = api_key
        self.access_token = access_token
        self.calls: List[tuple] = []
        self.connected_with: Any = None
        self.closed = 0

    def connect(self, threaded: bool = False) -> None:
        self.connected_with = threaded

    def subscribe(self, tokens):
        self.calls.append(("subscribe", sorted(tokens)))

    def unsubscribe(self, tokens):
        self.calls.append(("unsubscribe", sorted(tokens)))

    def set_mode(self, mode, tokens):
        self.calls.append(("set_mode", mode, sorted(tokens)))

    def close(self) -> None:
        self.closed += 1


class FeedTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.now = T0
        self.tickers: List[FakeTicker] = []

        def factory(key, token):
            ticker = FakeTicker(key, token)
            self.tickers.append(ticker)
            return ticker

        self.feed = LiveTickFeed(
            api_key="k",
            access_token="t",
            ticker_factory=factory,
            call_in_io_thread=lambda fn, *args: fn(*args),
            now_fn=lambda: self.now,
            host_clock_ok=lambda: True,
        )

    @property
    def ticker(self) -> FakeTicker:
        return self.tickers[-1]

    def connect(self) -> None:
        self.feed.start()
        self.feed._on_connect(self.ticker)


class LifecycleTests(FeedTestCase):
    def test_start_connects_once_in_a_background_thread(self) -> None:
        self.assertTrue(self.feed.start())
        self.assertTrue(self.feed.start())
        self.assertEqual(len(self.tickers), 1)
        self.assertTrue(self.ticker.connected_with)
        self.assertEqual((self.ticker.api_key, self.ticker.access_token), ("k", "t"))

    def test_not_connected_until_kite_says_so(self) -> None:
        self.feed.start()
        self.assertFalse(self.feed.health().connected)
        self.feed._on_connect(self.ticker)
        self.assertTrue(self.feed.health().connected)
        self.assertIsNone(self.feed.health().reason)

    def test_a_failed_start_is_recorded_not_raised(self) -> None:
        def broken(_k, _t):
            raise RuntimeError("no network")

        feed = LiveTickFeed(
            api_key="k", access_token="t", ticker_factory=broken,
            host_clock_ok=lambda: True,
        )
        self.assertFalse(feed.start())
        health = feed.health()
        self.assertFalse(health.connected)
        self.assertIn("ws_start_failed", str(health.reason))

    def test_stop_closes_once_and_is_idempotent(self) -> None:
        self.connect()
        self.feed.stop()
        self.feed.stop()
        self.assertEqual(self.ticker.closed, 1)
        self.assertFalse(self.feed.health().connected)

    def test_stop_before_start_is_harmless(self) -> None:
        self.feed.stop()


class SubscriptionTests(FeedTestCase):
    def test_sync_subscribes_new_tokens_in_ltp_mode(self) -> None:
        self.connect()
        self.feed.sync({11, 22})
        self.assertIn(("subscribe", [11, 22]), self.ticker.calls)
        self.assertIn(("set_mode", "ltp", [11, 22]), self.ticker.calls)

    def test_sync_only_sends_the_difference(self) -> None:
        self.connect()
        self.feed.sync({11, 22})
        self.ticker.calls.clear()
        self.feed.sync({22, 33})
        self.assertEqual(
            self.ticker.calls,
            [("unsubscribe", [11]), ("subscribe", [33]), ("set_mode", "ltp", [33])],
        )

    def test_an_unchanged_set_sends_nothing(self) -> None:
        self.connect()
        self.feed.sync({11})
        self.ticker.calls.clear()
        self.feed.sync({11})
        self.assertEqual(self.ticker.calls, [])

    def test_dropping_everything_keeps_the_connection(self) -> None:
        self.connect()
        self.feed.sync({11})
        self.feed.sync(set())
        self.assertIn(("unsubscribe", [11]), self.ticker.calls)
        self.assertTrue(self.feed.health().connected)
        self.assertEqual(self.ticker.closed, 0)

    def test_wishes_made_before_connecting_are_sent_on_connect(self) -> None:
        self.feed.start()
        self.feed.sync({11, 22})
        self.assertEqual(self.ticker.calls, [])
        self.feed._on_connect(self.ticker)
        self.assertIn(("subscribe", [11, 22]), self.ticker.calls)

    def test_a_reconnect_resubscribes_the_current_set(self) -> None:
        self.connect()
        self.feed.sync({11, 22})
        self.feed._on_close(self.ticker, 1006, "network")
        self.feed.sync({22})  # a trade closed while disconnected
        self.ticker.calls.clear()
        self.feed._on_connect(self.ticker)
        self.assertEqual(self.ticker.calls, [("subscribe", [22]), ("set_mode", "ltp", [22])])


class TickAndHealthTests(FeedTestCase):
    def test_ticks_are_kept_per_token_with_receive_time(self) -> None:
        self.connect()
        self.feed.sync({11})
        self.feed._on_ticks(self.ticker, [{"instrument_token": 11, "last_price": 112.5}])
        tick = self.feed.latest(11)
        assert tick is not None
        self.assertEqual((tick.last_price, tick.received_at), (112.5, T0))
        self.assertEqual(self.feed.health().last_tick_at, T0)

    def test_ticks_for_unsubscribed_or_bad_data_are_ignored(self) -> None:
        self.connect()
        self.feed.sync({11})
        self.feed._on_ticks(
            self.ticker,
            [
                {"instrument_token": 99, "last_price": 50.0},
                {"instrument_token": 11, "last_price": None},
                {"instrument_token": "junk"},
            ],
        )
        self.assertIsNone(self.feed.latest(99))
        self.assertIsNone(self.feed.latest(11))

    def test_an_unsubscribed_tokens_last_tick_is_forgotten(self) -> None:
        self.connect()
        self.feed.sync({11})
        self.feed._on_ticks(self.ticker, [{"instrument_token": 11, "last_price": 112.5}])
        self.feed.sync(set())
        self.assertIsNone(self.feed.latest(11))

    def test_disconnect_marks_the_feed_down_and_drops_old_ticks(self) -> None:
        self.connect()
        self.feed.sync({11})
        self.feed._on_ticks(self.ticker, [{"instrument_token": 11, "last_price": 112.5}])
        self.feed._on_error(self.ticker, 1006, "reset")
        health = self.feed.health()
        self.assertFalse(health.connected)
        self.assertEqual(health.reason, REASON_DISCONNECTED)
        self.assertIsNone(self.feed.latest(11))

    def test_giving_up_is_reported_and_never_raised(self) -> None:
        self.connect()
        self.feed._on_noreconnect(self.ticker)
        self.assertEqual(self.feed.health().reason, REASON_GAVE_UP)

    def test_a_reconnect_attempt_counts_as_down(self) -> None:
        self.connect()
        self.feed._on_reconnect(self.ticker, 1)
        self.assertFalse(self.feed.health().connected)


class NullFeedTests(unittest.TestCase):
    def test_paper_feed_never_has_anything(self) -> None:
        feed = NullTickFeed()
        self.assertFalse(feed.start())
        feed.sync({11})
        self.assertIsNone(feed.latest(11))
        health = feed.health()
        self.assertFalse(health.live)
        self.assertEqual(health.reason, REASON_PAPER)
        feed.stop()


if __name__ == "__main__":
    unittest.main()
