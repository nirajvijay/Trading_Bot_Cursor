"""Tests for live 5-minute candle builder, aggregation helpers, and writer."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from candle_aggregation import (
    CompletedOneMinuteCandle,
    floor_five_minute_bucket_start,
)
from live_five_minute_candle_builder import LiveFiveMinuteCandleBuilder
from live_five_minute_candle_writer import LiveFiveMinuteCandleWriter
from live_one_minute_candle_writer import CandleConflictError
from tick_event import IST

_IST = ZoneInfo(IST)
TOKEN = 101
SYMBOL = "TEST"


def _1m(
    minute: int,
    *,
    open_: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.5,
    volume: int = 100,
    partial: bool = False,
) -> CompletedOneMinuteCandle:
    hour, mins = divmod(minute, 60)
    return CompletedOneMinuteCandle(
        instrument_token=TOKEN,
        candle_time=datetime(2026, 7, 22, hour, mins, 0, tzinfo=_IST),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        tick_count=5,
        volume_reliable=True,
        completion_reason="minute_transition",
        has_full_minute_coverage=not partial,
        is_partial=partial,
    )


class FloorBucketTests(unittest.TestCase):
    def test_floor_1032_to_1030(self) -> None:
        self.assertEqual(floor_five_minute_bucket_start(10 * 60 + 32), 10 * 60 + 30)

    def test_aligned_unchanged(self) -> None:
        self.assertEqual(floor_five_minute_bucket_start(10 * 60 + 30), 10 * 60 + 30)

    def test_outside_session(self) -> None:
        self.assertIsNone(floor_five_minute_bucket_start(9 * 60))


class LiveFiveMinuteBuilderTests(unittest.TestCase):
    def test_emits_only_after_five_consecutive(self) -> None:
        emitted = []
        builder = LiveFiveMinuteCandleBuilder(on_five_minute=emitted.append)
        start = 10 * 60 + 30
        for offset in range(4):
            self.assertIsNone(builder.on_one_minute(_1m(start + offset)))
        bar = builder.on_one_minute(_1m(start + 4, close=102.0, volume=50))
        self.assertIsNotNone(bar)
        assert bar is not None
        self.assertEqual(bar.open, 100.0)
        self.assertEqual(bar.close, 102.0)
        self.assertEqual(bar.volume, 100 * 4 + 50)
        self.assertEqual(bar.constituent_count, 5)
        self.assertEqual(len(emitted), 1)

    def test_incomplete_bucket_not_emitted(self) -> None:
        builder = LiveFiveMinuteCandleBuilder()
        start = 10 * 60 + 30
        for offset in (0, 1, 2, 4):  # missing minute +3
            builder.on_one_minute(_1m(start + offset))
        # Force next bucket
        result = builder.on_one_minute(_1m(start + 5))
        self.assertIsNone(result)
        self.assertEqual(builder.buckets_incomplete, 1)

    def test_mid_session_partial_first_bucket_discarded(self) -> None:
        """Join mid-bucket then complete the next full bucket only."""
        builder = LiveFiveMinuteCandleBuilder()
        # Join at :32,:33,:34 of 10:30 bucket — incomplete
        for minute in (10 * 60 + 32, 10 * 60 + 33, 10 * 60 + 34):
            self.assertIsNone(builder.on_one_minute(_1m(minute)))
        # Next bucket 10:35–10:39 must be fully present to emit
        for offset in range(4):
            self.assertIsNone(builder.on_one_minute(_1m(10 * 60 + 35 + offset)))
        bar = builder.on_one_minute(_1m(10 * 60 + 39))
        self.assertIsNotNone(bar)
        self.assertEqual(builder.buckets_incomplete, 1)
        self.assertEqual(builder.buckets_emitted, 1)

    def test_partial_constituent_flag_rolled_up(self) -> None:
        builder = LiveFiveMinuteCandleBuilder()
        start = 9 * 60 + 15
        for offset in range(4):
            builder.on_one_minute(_1m(start + offset))
        bar = builder.on_one_minute(_1m(start + 4, partial=True))
        self.assertIsNotNone(bar)
        assert bar is not None
        self.assertTrue(bar.any_partial)


class LiveFiveMinuteWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "live.db"
        self.writer = LiveFiveMinuteCandleWriter(
            db_path=self.db,
            token_to_symbol={TOKEN: SYMBOL},
        )

    def tearDown(self) -> None:
        self.writer.close()
        self._tmp.cleanup()

    def test_insert_and_duplicate_ignore(self) -> None:
        builder = LiveFiveMinuteCandleBuilder()
        start = 10 * 60 + 30
        bar = None
        for offset in range(5):
            bar = builder.on_one_minute(_1m(start + offset))
        assert bar is not None
        self.writer.on_candle(bar)
        self.writer.on_candle(bar)
        self.assertEqual(self.writer.metrics.candles_inserted, 1)
        self.assertEqual(self.writer.metrics.duplicates_ignored, 1)

    def test_conflict_raises(self) -> None:
        builder = LiveFiveMinuteCandleBuilder()
        start = 10 * 60 + 30
        for offset in range(4):
            builder.on_one_minute(_1m(start + offset))
        bar = builder.on_one_minute(_1m(start + 4, close=101.0))
        assert bar is not None
        self.writer.on_candle(bar)

        builder2 = LiveFiveMinuteCandleBuilder()
        for offset in range(4):
            builder2.on_one_minute(_1m(start + offset))
        bar2 = builder2.on_one_minute(_1m(start + 4, close=109.0))
        assert bar2 is not None
        with self.assertRaises(CandleConflictError):
            self.writer.on_candle(bar2)


if __name__ == "__main__":
    unittest.main()
