from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from candle_aggregation import (
    CompletedOneMinuteCandle,
    ensure_ist,
    minute_of_day_from_datetime,
    minute_start_from_exchange_timestamp,
)
from tick_event import IST

_IST = ZoneInfo(IST)


class EnsureIstTests(unittest.TestCase):
    def test_naive_datetime_gets_ist(self) -> None:
        naive = datetime(2026, 7, 22, 10, 30, 45)
        result = ensure_ist(naive)
        self.assertEqual(result.tzinfo, _IST)
        self.assertEqual(result.hour, 10)
        self.assertEqual(result.minute, 30)

    def test_aware_datetime_converted_to_ist(self) -> None:
        utc = datetime(2026, 7, 22, 5, 0, 0, tzinfo=ZoneInfo("UTC"))
        result = ensure_ist(utc)
        self.assertEqual(result.tzinfo, _IST)
        self.assertEqual(result.hour, 10)
        self.assertEqual(result.minute, 30)


class MinuteHelpersTests(unittest.TestCase):
    def test_minute_of_day_from_datetime(self) -> None:
        dt = datetime(2026, 7, 22, 9, 15, 30, tzinfo=_IST)
        self.assertEqual(minute_of_day_from_datetime(dt), 9 * 60 + 15)

    def test_minute_start_floors_seconds(self) -> None:
        dt = datetime(2026, 7, 22, 10, 30, 45, 123456, tzinfo=_IST)
        result = minute_start_from_exchange_timestamp(dt)
        self.assertEqual(result, datetime(2026, 7, 22, 10, 30, 0, tzinfo=_IST))


class CompletedOneMinuteCandleTests(unittest.TestCase):
    def test_to_one_minute_candle_iso_conversion(self) -> None:
        candle_time = datetime(2026, 7, 22, 10, 30, 0, tzinfo=_IST)
        completed = CompletedOneMinuteCandle(
            instrument_token=738561,
            candle_time=candle_time,
            open=100.0,
            high=105.0,
            low=99.0,
            close=103.0,
            volume=500,
            tick_count=10,
            volume_reliable=True,
            completion_reason="minute_transition",
            has_full_minute_coverage=True,
            is_partial=False,
        )
        bar = completed.to_one_minute_candle()
        self.assertEqual(bar.candle_time, candle_time.isoformat(timespec="seconds"))
        self.assertEqual(bar.volume, 500)
        self.assertEqual(bar.close, 103.0)


if __name__ == "__main__":
    unittest.main()
