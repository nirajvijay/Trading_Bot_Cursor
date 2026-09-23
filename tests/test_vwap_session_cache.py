"""Session VWAP cache and minute ledger tests."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from candle_aggregation import CompletedOneMinuteCandle
from vwap_session_cache import SessionVwapCache, session_open_dt

IST = ZoneInfo("Asia/Kolkata")


def _candle(
    token: int,
    minute: datetime,
    *,
    close: float = 100.0,
    volume: int = 1000,
) -> CompletedOneMinuteCandle:
    return CompletedOneMinuteCandle(
        instrument_token=token,
        candle_time=minute,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=volume,
        tick_count=10,
        volume_reliable=True,
        completion_reason="minute_transition",
        has_full_minute_coverage=True,
        is_partial=False,
    )


class SessionVwapCacheTests(unittest.TestCase):
    def test_live_replaces_historical(self) -> None:
        cache = SessionVwapCache("2026-09-01")
        token = 1
        m = session_open_dt("2026-09-01")
        cache.apply_historical_minutes(token, [(m, 100, 101, 99, 100.0, 1000)])
        cache.on_live_candle(_candle(token, m, close=105.0, volume=2000))
        snap = cache.build_snapshot(
            token,
            requested_cutoff=m + timedelta(minutes=2),
            cache_state="ready",
            feed_stale=False,
        )
        self.assertEqual(snap.live_minute_count, 1)
        self.assertEqual(snap.historical_minute_count, 0)

    def test_continuous_chain_required(self) -> None:
        cache = SessionVwapCache("2026-09-01")
        token = 2
        m0 = session_open_dt("2026-09-01")
        m1 = m0 + timedelta(minutes=1)
        cache.on_live_candle(_candle(token, m1, close=101.0))
        snap = cache.build_snapshot(
            token,
            requested_cutoff=m1 + timedelta(minutes=1),
            cache_state="ready",
            feed_stale=False,
        )
        self.assertFalse(snap.quality_ok)
        self.assertEqual(snap.quality_reason, "gap_in_minutes")


class ArmRegistryTests(unittest.TestCase):
    def test_ref_count_multi_setup(self) -> None:
        from vwap_arm_registry import VwapArmRegistry

        warmed: list[int] = []
        colds: list[int] = []
        reg = VwapArmRegistry(
            on_warmup=warmed.append,
            on_cold=colds.append,
        )
        reg.arm("s1", 10)
        reg.arm("s2", 10)
        self.assertEqual(len(warmed), 1)
        reg.disarm("s1", 10)
        self.assertEqual(colds, [])
        reg.disarm("s2", 10)
        self.assertEqual(colds, [10])


if __name__ == "__main__":
    unittest.main()
