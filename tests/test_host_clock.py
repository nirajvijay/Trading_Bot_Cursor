"""The host-timezone guard for KiteTicker's naive local tick timestamps."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from engine_live_ticks import LiveTickFeed
from host_clock import REASON_HOST_NOT_IST, host_is_ist
from tick_receiver import TickReceiver

# 2026-09-24 10:00:00 IST
EPOCH = datetime(2026, 9, 24, 4, 30, tzinfo=timezone.utc).timestamp()


def _local_clock(tz_name: str):
    """What datetime.fromtimestamp returns on a host set to tz_name."""
    tz = ZoneInfo(tz_name)
    return lambda epoch: datetime.fromtimestamp(epoch, tz).replace(tzinfo=None)


class HostIsIstTests(unittest.TestCase):
    def test_ist_host_passes(self) -> None:
        self.assertTrue(
            host_is_ist(EPOCH, local_fromtimestamp=_local_clock("Asia/Kolkata"))
        )

    def test_utc_host_fails(self) -> None:
        self.assertFalse(host_is_ist(EPOCH, local_fromtimestamp=_local_clock("UTC")))

    def test_same_offset_by_another_name_passes(self) -> None:
        # Asia/Calcutta is the legacy alias: identical wall clock, so ticks are right.
        self.assertTrue(
            host_is_ist(EPOCH, local_fromtimestamp=_local_clock("Asia/Calcutta"))
        )

    def test_nearby_offset_fails(self) -> None:
        # Nepal is +05:45: close is not correct.
        self.assertFalse(
            host_is_ist(EPOCH, local_fromtimestamp=_local_clock("Asia/Kathmandu"))
        )


class ReceiverRefusesOffIstTests(unittest.TestCase):
    def test_receiver_refuses_to_start_on_a_non_ist_host(self) -> None:
        with patch("tick_receiver.check_access_token") as check:
            with self.assertRaises(RuntimeError) as ctx:
                TickReceiver(on_tick=MagicMock(), host_clock_ok=lambda: False)
        self.assertIn("not IST", str(ctx.exception))
        # Refused before touching Kite at all.
        check.assert_not_called()


class LiveFeedOffIstTests(unittest.TestCase):
    def test_engine_feed_records_the_reason_and_never_connects(self) -> None:
        factory = MagicMock()
        feed = LiveTickFeed(
            api_key="k",
            access_token="t",
            ticker_factory=factory,
            host_clock_ok=lambda: False,
        )
        self.assertFalse(feed.start())
        factory.assert_not_called()
        health = feed.health()
        self.assertFalse(health.connected)
        self.assertEqual(health.reason, REASON_HOST_NOT_IST)


if __name__ == "__main__":
    unittest.main()
