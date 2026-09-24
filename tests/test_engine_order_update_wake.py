"""Kite order-update pushes wake the engine loop early, never faster than 0.5s,
and are never read as data."""

from __future__ import annotations

import threading
import unittest
from unittest import mock

import run_execution_engine as runner
from engine_live_ticks import LiveTickFeed, TickFeedHealth
from engine_runloop import MIN_TICK_GAP_SECONDS, LoopWake, wait_for_next_tick


class Clock:
    """A fake monotonic clock; sleeps and waits advance it."""

    def __init__(self) -> None:
        self.t = 100.0
        self.sleeps = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 6))
        self.t += seconds


class WaitForNextTickTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.tick_started = self.clock.t

    def wait(self, *, sleep_for: float, wake_after, min_gap=MIN_TICK_GAP_SECONDS) -> bool:
        """wake_after: seconds into the wait at which a push lands, or None."""

        def wait_for_wake(timeout: float) -> bool:
            if wake_after is None or wake_after >= timeout:
                self.clock.t += timeout
                return False
            self.clock.t += wake_after
            return True

        return wait_for_next_tick(
            sleep_for=sleep_for,
            tick_started_at=self.tick_started,
            wait_for_wake=wait_for_wake,
            sleep=self.clock.sleep,
            monotonic=self.clock.monotonic,
            min_gap=min_gap,
        )

    def elapsed(self) -> float:
        return round(self.clock.t - self.tick_started, 6)

    def test_no_push_keeps_the_one_second_cadence(self) -> None:
        self.clock.t += 0.2  # the tick took 0.2s
        self.assertFalse(self.wait(sleep_for=0.8, wake_after=None))
        self.assertEqual(self.elapsed(), 1.0)
        self.assertEqual(self.clock.sleeps, [])

    def test_an_early_push_is_held_to_the_minimum_gap(self) -> None:
        self.clock.t += 0.1
        self.assertTrue(self.wait(sleep_for=0.9, wake_after=0.05))
        self.assertEqual(self.elapsed(), MIN_TICK_GAP_SECONDS)

    def test_a_push_after_the_minimum_gap_ticks_at_once(self) -> None:
        self.clock.t += 0.1
        self.assertTrue(self.wait(sleep_for=0.9, wake_after=0.6))
        self.assertEqual(self.elapsed(), 0.7)
        self.assertEqual(self.clock.sleeps, [])

    def test_an_overrun_tick_starts_the_next_immediately(self) -> None:
        self.clock.t += 1.4
        called = []
        woke = wait_for_next_tick(
            sleep_for=0.0,
            tick_started_at=self.tick_started,
            wait_for_wake=lambda t: called.append(t) or True,
            sleep=self.clock.sleep,
            monotonic=self.clock.monotonic,
        )
        self.assertFalse(woke)
        self.assertEqual(called, [])

    def test_a_burst_never_exceeds_two_ticks_a_second(self) -> None:
        # A push is always pending: every wait returns at once.
        starts = []
        for _ in range(10):
            self.tick_started = self.clock.t
            starts.append(self.clock.t)
            self.clock.t += 0.01  # a fast tick
            self.wait(sleep_for=0.99, wake_after=0.0)
        gaps = [round(b - a, 6) for a, b in zip(starts, starts[1:])]
        self.assertTrue(all(g >= MIN_TICK_GAP_SECONDS for g in gaps), gaps)


class LoopWakeTests(unittest.TestCase):
    def test_notify_from_another_thread_ends_the_wait(self) -> None:
        wake = LoopWake()
        threading.Timer(0.01, wake.notify).start()
        self.assertTrue(wake.wait(2.0))

    def test_no_notify_times_out(self) -> None:
        self.assertFalse(LoopWake().wait(0.01))

    def test_a_push_during_a_tick_is_not_lost(self) -> None:
        wake = LoopWake()
        wake.clear()  # start of tick
        wake.notify()  # push lands mid-tick
        self.assertTrue(wake.wait(0.0))  # the next wait sees it

    def test_clear_at_tick_start_drops_pushes_the_tick_will_read_anyway(self) -> None:
        wake = LoopWake()
        wake.notify()
        wake.clear()
        self.assertFalse(wake.wait(0.0))


class Untouchable:
    """Any read of the push payload fails the test."""

    def __getattr__(self, name):
        raise AssertionError(f"order update payload was read: .{name}")

    def __getitem__(self, key):
        raise AssertionError(f"order update payload was read: [{key!r}]")

    def get(self, *_):
        raise AssertionError("order update payload was read: .get()")


class FakeTicker:
    MODE_LTP = "ltp"

    def __init__(self, *_args) -> None:
        self.on_order_update = None

    def connect(self, threaded: bool = False) -> None:
        pass


class FeedOrderUpdateTests(unittest.TestCase):
    def feed(self, wake=None) -> LiveTickFeed:
        self.ticker = None

        def factory(*args):
            self.ticker = FakeTicker(*args)
            return self.ticker

        feed = LiveTickFeed(
            api_key="k",
            access_token="t",
            ticker_factory=factory,
            host_clock_ok=lambda: True,
            on_order_update=wake,
        )
        feed.start()
        return feed

    def test_the_callback_is_registered_on_the_ticker(self) -> None:
        feed = self.feed()
        self.assertEqual(self.ticker.on_order_update, feed._on_order_update)

    def test_a_push_wakes_and_is_counted_without_reading_it(self) -> None:
        wake = mock.Mock()
        feed = self.feed(wake)
        self.ticker.on_order_update(self.ticker, Untouchable())
        self.ticker.on_order_update(self.ticker, Untouchable())
        self.assertEqual(wake.call_count, 2)
        self.assertEqual(feed.health().order_updates, 2)

    def test_a_failing_wake_never_raises_into_kiteticker(self) -> None:
        feed = self.feed(mock.Mock(side_effect=RuntimeError("boom")))
        feed._on_order_update(self.ticker, {})
        self.assertEqual(feed.health().order_updates, 1)

    def test_no_wake_wired_is_harmless(self) -> None:
        feed = self.feed()
        feed._on_order_update(self.ticker, {})
        self.assertEqual(feed.health().order_updates, 1)


class RunnerWiringTests(unittest.TestCase):
    def test_live_feed_is_wired_to_the_wake(self) -> None:
        wake = LoopWake()
        with mock.patch(
            "login._read_env_merged",
            return_value={"KITE_API_KEY": "key", "KITE_ACCESS_TOKEN": "tok"},
        ):
            feed = runner.build_tick_feed(live_orders=True, on_order_update=wake.notify)
        feed._on_order_update(None, {})
        self.assertTrue(wake.wait(0.0))

    def test_the_summary_carries_the_push_count(self) -> None:
        engine = mock.Mock(
            live_feed_health=TickFeedHealth(live=True, connected=True, order_updates=3),
            live_pnl_source={},
            live_pnl_reason={},
        )
        self.assertEqual(runner.live_mark_feed_summary(engine)["order_updates"], 3)


if __name__ == "__main__":
    unittest.main()
