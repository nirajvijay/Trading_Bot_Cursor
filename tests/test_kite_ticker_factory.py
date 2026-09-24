"""Both feeds build the real KiteTicker with the SDK's maximum reconnect tries."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from kiteconnect import KiteTicker

from engine_live_ticks import LiveTickFeed
from kite_ticker_factory import KITE_RECONNECT_MAX_TRIES, make_kite_ticker


class FactoryTests(unittest.TestCase):
    def test_real_ticker_gets_300_tries_and_default_backoff(self) -> None:
        ticker = make_kite_ticker("k", "t")  # constructing never connects
        self.assertIsInstance(ticker, KiteTicker)
        self.assertEqual(KITE_RECONNECT_MAX_TRIES, 300)
        self.assertEqual(ticker.reconnect_max_tries, 300)
        self.assertEqual(ticker.reconnect_max_delay, KiteTicker.RECONNECT_MAX_DELAY)

    def test_300_is_within_the_sdk_ceiling(self) -> None:
        # Above the ceiling the SDK silently clamps; stay exactly at it.
        self.assertLessEqual(KITE_RECONNECT_MAX_TRIES, KiteTicker._maximum_reconnect_max_tries)


class DefaultWiringTests(unittest.TestCase):
    def test_engine_feed_uses_the_factory_by_default(self) -> None:
        with patch("kite_ticker_factory.make_kite_ticker", return_value=MagicMock()) as make:
            feed = LiveTickFeed(api_key="k", access_token="t", host_clock_ok=lambda: True)
            self.assertTrue(feed.start())
        make.assert_called_once_with("k", "t")

    def test_observation_receiver_uses_the_factory_by_default(self) -> None:
        import tick_receiver

        with patch.object(tick_receiver, "check_access_token", return_value=(True, "ok")), \
             patch.object(tick_receiver, "_require_env",
                          return_value={"KITE_API_KEY": "k", "KITE_ACCESS_TOKEN": "t"}), \
             patch.object(tick_receiver, "_get_kite", return_value=MagicMock()), \
             patch.object(tick_receiver, "load_nifty50_tokens",
                          return_value=[MagicMock(instrument_token=1, tradingsymbol="A")]):
            receiver = tick_receiver.TickReceiver(on_tick=MagicMock(), host_clock_ok=lambda: True)
        self.assertIs(receiver._ticker_factory, tick_receiver.make_kite_ticker)


if __name__ == "__main__":
    unittest.main()
