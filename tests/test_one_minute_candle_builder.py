from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo

from candle_aggregation import CompletedOneMinuteCandle
from one_minute_candle_builder import OneMinuteCandleBuilder
from tick_event import IST, Ohlc, TickEvent

_IST = ZoneInfo(IST)
_TOKEN = 738561
_TOKEN_B = 408065


def _make_tick(
    *,
    sequence: int,
    price: float,
    exchange_timestamp: datetime,
    volume_traded: int = 0,
    instrument_token: int = _TOKEN,
) -> TickEvent:
    received_at = exchange_timestamp
    return TickEvent(
        sequence=sequence,
        instrument_token=instrument_token,
        last_price=price,
        exchange_timestamp=exchange_timestamp,
        received_at=received_at,
        volume_traded=volume_traded,
        last_traded_quantity=1,
        average_traded_price=price,
        ohlc=Ohlc(open=price, high=price, low=price, close=price),
    )


def _ist(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    second: int = 0,
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=_IST)


class BuilderTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.emitted: List[CompletedOneMinuteCandle] = []
        self.builder = OneMinuteCandleBuilder(on_candle=self.emitted.append)


class SessionFilterTests(BuilderTestCase):
    def test_ignore_pre_session_tick(self) -> None:
        self.builder.on_tick(
            _make_tick(
                sequence=1,
                price=100.0,
                exchange_timestamp=_ist(2026, 7, 22, 9, 14, 30),
                volume_traded=10,
            )
        )
        self.assertEqual(len(self.emitted), 0)
        self.assertEqual(self.builder.metrics.out_of_session_ticks, 1)

    def test_ignore_post_session_tick(self) -> None:
        self.builder.on_tick(
            _make_tick(
                sequence=1,
                price=100.0,
                exchange_timestamp=_ist(2026, 7, 22, 15, 30, 0),
                volume_traded=10,
            )
        )
        self.assertEqual(len(self.emitted), 0)
        self.assertEqual(self.builder.metrics.out_of_session_ticks, 1)

    def test_accept_session_boundary_ticks(self) -> None:
        self.builder.on_tick(
            _make_tick(
                sequence=1,
                price=100.0,
                exchange_timestamp=_ist(2026, 7, 22, 9, 15, 0),
                volume_traded=50,
            )
        )
        self.builder.flush()
        self.assertEqual(len(self.emitted), 1)
        self.assertEqual(self.emitted[0].candle_time, _ist(2026, 7, 22, 9, 15, 0))


class OhlcConstructionTests(BuilderTestCase):
    def test_multi_tick_ohlc_by_event_time(self) -> None:
        base = _ist(2026, 7, 22, 10, 0, 0)
        self.builder.on_tick(
            _make_tick(sequence=2, price=102.0, exchange_timestamp=base + timedelta(seconds=30), volume_traded=200)
        )
        self.builder.on_tick(
            _make_tick(sequence=1, price=100.0, exchange_timestamp=base + timedelta(seconds=5), volume_traded=100)
        )
        self.builder.on_tick(
            _make_tick(sequence=3, price=105.0, exchange_timestamp=base + timedelta(seconds=50), volume_traded=300)
        )
        self.builder.on_tick(
            _make_tick(sequence=4, price=98.0, exchange_timestamp=base + timedelta(seconds=20), volume_traded=150)
        )
        self.builder.flush()
        candle = self.emitted[0]
        self.assertEqual(candle.open, 100.0)
        self.assertEqual(candle.close, 105.0)
        self.assertEqual(candle.high, 105.0)
        self.assertEqual(candle.low, 98.0)


class OhlcTieBreakTests(BuilderTestCase):
    def test_equal_timestamp_sequence_tie_break(self) -> None:
        ts = _ist(2026, 7, 22, 10, 0, 10)
        self.builder.on_tick(_make_tick(sequence=5, price=101.0, exchange_timestamp=ts, volume_traded=100))
        self.builder.on_tick(_make_tick(sequence=2, price=100.0, exchange_timestamp=ts, volume_traded=110))
        self.builder.on_tick(_make_tick(sequence=8, price=103.0, exchange_timestamp=ts, volume_traded=120))
        self.builder.flush()
        candle = self.emitted[0]
        self.assertEqual(candle.open, 100.0)
        self.assertEqual(candle.close, 103.0)


class MinuteTransitionTests(BuilderTestCase):
    def test_emits_completed_bar_on_minute_change(self) -> None:
        self.builder.on_tick(
            _make_tick(
                sequence=1,
                price=100.0,
                exchange_timestamp=_ist(2026, 7, 22, 9, 15, 30),
                volume_traded=100,
            )
        )
        self.builder.on_tick(
            _make_tick(
                sequence=2,
                price=101.0,
                exchange_timestamp=_ist(2026, 7, 22, 9, 16, 5),
                volume_traded=150,
            )
        )
        self.assertEqual(len(self.emitted), 1)
        self.assertEqual(self.emitted[0].candle_time, _ist(2026, 7, 22, 9, 15, 0))
        self.assertEqual(self.emitted[0].volume, 100)


class MissingMinuteTests(BuilderTestCase):
    def test_no_gap_candles_on_minute_jump(self) -> None:
        self.builder.on_tick(
            _make_tick(
                sequence=1,
                price=100.0,
                exchange_timestamp=_ist(2026, 7, 22, 10, 4, 10),
                volume_traded=100,
            )
        )
        self.builder.on_tick(
            _make_tick(
                sequence=2,
                price=101.0,
                exchange_timestamp=_ist(2026, 7, 22, 10, 8, 10),
                volume_traded=200,
            )
        )
        self.assertEqual(len(self.emitted), 1)
        self.assertEqual(self.emitted[0].candle_time, _ist(2026, 7, 22, 10, 4, 0))
        self.builder.flush()
        self.assertEqual(len(self.emitted), 2)
        self.assertEqual(self.emitted[1].candle_time, _ist(2026, 7, 22, 10, 8, 0))


class VolumeBaselineTests(BuilderTestCase):
    def test_first_0915_bucket_counts_from_zero(self) -> None:
        self.builder.on_tick(
            _make_tick(
                sequence=1,
                price=100.0,
                exchange_timestamp=_ist(2026, 7, 22, 9, 15, 10),
                volume_traded=500,
            )
        )
        self.builder.flush()
        self.assertEqual(self.emitted[0].volume, 500)


class MidSessionStartupTests(BuilderTestCase):
    def test_first_tick_zero_incremental_volume(self) -> None:
        self.builder.on_tick(
            _make_tick(
                sequence=1,
                price=100.0,
                exchange_timestamp=_ist(2026, 7, 22, 10, 30, 0),
                volume_traded=10_000,
            )
        )
        self.builder.flush()
        self.assertEqual(self.emitted[0].volume, 0)

    def test_second_tick_after_mid_session_startup(self) -> None:
        self.builder.on_tick(
            _make_tick(
                sequence=1,
                price=100.0,
                exchange_timestamp=_ist(2026, 7, 22, 10, 30, 0),
                volume_traded=10_000,
            )
        )
        self.builder.on_tick(
            _make_tick(
                sequence=2,
                price=101.0,
                exchange_timestamp=_ist(2026, 7, 22, 10, 30, 30),
                volume_traded=10_500,
            )
        )
        self.builder.flush()
        self.assertEqual(self.emitted[0].volume, 500)


class OutOfOrderCumulativeTests(BuilderTestCase):
    def test_older_lower_cumulative_not_a_reset(self) -> None:
        base = _ist(2026, 7, 22, 9, 15, 0)
        self.builder.on_tick(
            _make_tick(sequence=1, price=100.0, exchange_timestamp=base + timedelta(seconds=10), volume_traded=200)
        )
        self.builder.on_tick(
            _make_tick(sequence=2, price=101.0, exchange_timestamp=base + timedelta(seconds=40), volume_traded=300)
        )
        self.builder.on_tick(
            _make_tick(sequence=3, price=99.0, exchange_timestamp=base + timedelta(seconds=20), volume_traded=150)
        )
        self.builder.flush()
        self.assertEqual(self.emitted[0].volume, 300)
        self.assertEqual(self.builder.metrics.cumulative_volume_decreases, 0)
        self.assertTrue(self.emitted[0].volume_reliable)


class GenuineResetTests(BuilderTestCase):
    def test_newer_lower_cumulative_is_reset(self) -> None:
        base = _ist(2026, 7, 22, 9, 15, 0)
        self.builder.on_tick(
            _make_tick(sequence=1, price=100.0, exchange_timestamp=base + timedelta(seconds=10), volume_traded=200)
        )
        self.builder.on_tick(
            _make_tick(sequence=2, price=101.0, exchange_timestamp=base + timedelta(seconds=40), volume_traded=50)
        )
        self.builder.flush()
        self.assertEqual(self.builder.metrics.cumulative_volume_decreases, 1)
        self.assertFalse(self.emitted[0].volume_reliable)


class SegmentedVolumeTests(BuilderTestCase):
    def test_volume_preserved_across_reset(self) -> None:
        base = _ist(2026, 7, 22, 9, 15, 0)
        self.builder.on_tick(
            _make_tick(sequence=1, price=100.0, exchange_timestamp=base + timedelta(seconds=5), volume_traded=100)
        )
        self.builder.on_tick(
            _make_tick(sequence=2, price=101.0, exchange_timestamp=base + timedelta(seconds=15), volume_traded=200)
        )
        self.builder.on_tick(
            _make_tick(sequence=3, price=102.0, exchange_timestamp=base + timedelta(seconds=45), volume_traded=50)
        )
        self.builder.on_tick(
            _make_tick(sequence=4, price=103.0, exchange_timestamp=base + timedelta(seconds=50), volume_traded=80)
        )
        self.builder.flush()
        self.assertEqual(self.emitted[0].volume, 230)


class CumulativeDecreaseTests(BuilderTestCase):
    def test_reset_candle_volume_reliable_false(self) -> None:
        base = _ist(2026, 7, 22, 9, 15, 0)
        self.builder.on_tick(
            _make_tick(sequence=1, price=100.0, exchange_timestamp=base + timedelta(seconds=10), volume_traded=500)
        )
        self.builder.on_tick(
            _make_tick(sequence=2, price=101.0, exchange_timestamp=base + timedelta(seconds=30), volume_traded=100)
        )
        self.builder.flush()
        self.assertFalse(self.emitted[0].volume_reliable)
        self.assertEqual(self.emitted[0].volume, 500)


class VolumeReliabilityTests(BuilderTestCase):
    def test_normal_candle_volume_reliable_true(self) -> None:
        self.builder.on_tick(
            _make_tick(
                sequence=1,
                price=100.0,
                exchange_timestamp=_ist(2026, 7, 22, 9, 15, 0),
                volume_traded=100,
            )
        )
        self.builder.on_tick(
            _make_tick(
                sequence=2,
                price=101.0,
                exchange_timestamp=_ist(2026, 7, 22, 9, 15, 30),
                volume_traded=200,
            )
        )
        self.builder.flush()
        self.assertTrue(self.emitted[0].volume_reliable)


class CallbackFailureTests(unittest.TestCase):
    def test_failure_preserves_state_and_retry_succeeds(self) -> None:
        emitted: List[CompletedOneMinuteCandle] = []
        fail_once = {"done": False}

        def flaky_callback(candle: CompletedOneMinuteCandle) -> None:
            if not fail_once["done"]:
                fail_once["done"] = True
                raise RuntimeError("callback failed")
            emitted.append(candle)

        builder = OneMinuteCandleBuilder(on_candle=flaky_callback)
        builder.on_tick(
            _make_tick(
                sequence=1,
                price=100.0,
                exchange_timestamp=_ist(2026, 7, 22, 10, 0, 0),
                volume_traded=100,
            )
        )
        with self.assertRaises(RuntimeError):
            builder.flush()
        self.assertEqual(len(emitted), 0)
        self.assertEqual(builder.metrics.candles_emitted, 0)
        self.assertIn(_TOKEN, builder._active)

        builder.flush()
        self.assertEqual(len(emitted), 1)
        self.assertEqual(builder.metrics.candles_emitted, 1)


class LastEmittedMinuteTests(BuilderTestCase):
    def test_blocks_reopening_emitted_minute(self) -> None:
        ts = _ist(2026, 7, 22, 10, 0, 0)
        self.builder.on_tick(_make_tick(sequence=1, price=100.0, exchange_timestamp=ts, volume_traded=100))
        self.builder.flush()
        self.builder.on_tick(
            _make_tick(
                sequence=2,
                price=101.0,
                exchange_timestamp=ts + timedelta(seconds=30),
                volume_traded=150,
            )
        )
        self.assertEqual(len(self.emitted), 1)
        self.assertEqual(self.builder.metrics.late_ticks_dropped, 1)


class FlushLateTickTests(BuilderTestCase):
    def test_flush_then_same_minute_tick_dropped(self) -> None:
        ts = _ist(2026, 7, 22, 10, 0, 15)
        self.builder.on_tick(_make_tick(sequence=1, price=100.0, exchange_timestamp=ts, volume_traded=100))
        self.builder.flush()
        self.builder.on_tick(
            _make_tick(sequence=2, price=101.0, exchange_timestamp=ts + timedelta(seconds=10), volume_traded=120)
        )
        self.assertEqual(len(self.emitted), 1)
        self.assertEqual(self.builder.metrics.late_ticks_dropped, 1)

    def test_late_tick_no_active_candle_dropped(self) -> None:
        ts = _ist(2026, 7, 22, 10, 0, 0)
        self.builder.on_tick(_make_tick(sequence=1, price=100.0, exchange_timestamp=ts, volume_traded=100))
        self.builder.flush()
        self.builder.on_tick(
            _make_tick(
                sequence=2,
                price=99.0,
                exchange_timestamp=_ist(2026, 7, 22, 10, 1, 0),
                volume_traded=150,
            )
        )
        self.builder.on_tick(
            _make_tick(
                sequence=3,
                price=98.0,
                exchange_timestamp=ts + timedelta(seconds=5),
                volume_traded=110,
            )
        )
        self.assertEqual(self.builder.metrics.late_ticks_dropped, 1)


class SequenceDedupTests(BuilderTestCase):
    def test_repeated_sequence_ignored(self) -> None:
        ts = _ist(2026, 7, 22, 10, 0, 0)
        tick = _make_tick(sequence=1, price=100.0, exchange_timestamp=ts, volume_traded=100)
        self.builder.on_tick(tick)
        self.builder.on_tick(tick)
        self.builder.flush()
        self.assertEqual(self.emitted[0].tick_count, 1)
        self.assertEqual(self.builder.metrics.duplicate_ticks_ignored, 1)

    def test_distinct_sequences_same_values_both_accepted(self) -> None:
        ts = _ist(2026, 7, 22, 10, 0, 0)
        self.builder.on_tick(_make_tick(sequence=1, price=100.0, exchange_timestamp=ts, volume_traded=100))
        self.builder.on_tick(_make_tick(sequence=2, price=100.0, exchange_timestamp=ts, volume_traded=100))
        self.builder.flush()
        self.assertEqual(self.emitted[0].tick_count, 2)


class OutOfOrderMinuteTests(BuilderTestCase):
    def test_late_minute_tick_dropped(self) -> None:
        self.builder.on_tick(
            _make_tick(
                sequence=1,
                price=100.0,
                exchange_timestamp=_ist(2026, 7, 22, 10, 1, 0),
                volume_traded=100,
            )
        )
        self.builder.on_tick(
            _make_tick(
                sequence=2,
                price=99.0,
                exchange_timestamp=_ist(2026, 7, 22, 10, 0, 30),
                volume_traded=50,
            )
        )
        self.assertEqual(self.builder.metrics.late_ticks_dropped, 1)


class MultiInstrumentTests(BuilderTestCase):
    def test_independent_candles_per_token(self) -> None:
        ts = _ist(2026, 7, 22, 10, 0, 0)
        self.builder.on_tick(
            _make_tick(sequence=1, price=100.0, exchange_timestamp=ts, volume_traded=100, instrument_token=_TOKEN)
        )
        self.builder.on_tick(
            _make_tick(
                sequence=1,
                price=200.0,
                exchange_timestamp=ts,
                volume_traded=500,
                instrument_token=_TOKEN_B,
            )
        )
        self.builder.flush()
        self.assertEqual(len(self.emitted), 2)
        tokens = {c.instrument_token for c in self.emitted}
        self.assertEqual(tokens, {_TOKEN, _TOKEN_B})


class FlushTests(BuilderTestCase):
    def test_flush_emits_in_progress_bar(self) -> None:
        self.builder.on_tick(
            _make_tick(
                sequence=1,
                price=100.0,
                exchange_timestamp=_ist(2026, 7, 22, 10, 0, 0),
                volume_traded=100,
            )
        )
        self.builder.flush()
        self.assertEqual(len(self.emitted), 1)

    def test_per_token_flush(self) -> None:
        ts = _ist(2026, 7, 22, 10, 0, 0)
        self.builder.on_tick(
            _make_tick(sequence=1, price=100.0, exchange_timestamp=ts, volume_traded=100, instrument_token=_TOKEN)
        )
        self.builder.on_tick(
            _make_tick(sequence=1, price=200.0, exchange_timestamp=ts, volume_traded=200, instrument_token=_TOKEN_B)
        )
        self.builder.flush(_TOKEN)
        self.assertEqual(len(self.emitted), 1)
        self.assertEqual(self.emitted[0].instrument_token, _TOKEN)
        self.assertIn(_TOKEN_B, self.builder._active)


class DayRolloverTests(BuilderTestCase):
    def test_session_date_change_finalizes_and_resets_volume(self) -> None:
        self.builder.on_tick(
            _make_tick(
                sequence=1,
                price=100.0,
                exchange_timestamp=_ist(2026, 7, 22, 15, 29, 0),
                volume_traded=1000,
            )
        )
        self.builder.on_tick(
            _make_tick(
                sequence=2,
                price=101.0,
                exchange_timestamp=_ist(2026, 7, 23, 9, 15, 0),
                volume_traded=50,
            )
        )
        self.assertEqual(len(self.emitted), 1)
        self.assertEqual(self.emitted[0].candle_time, _ist(2026, 7, 22, 15, 29, 0))
        self.builder.flush()
        self.assertEqual(len(self.emitted), 2)
        self.assertEqual(self.emitted[1].volume, 50)


class MetricsTests(BuilderTestCase):
    def test_all_counters(self) -> None:
        self.builder.on_tick(
            _make_tick(
                sequence=1,
                price=0.0,
                exchange_timestamp=_ist(2026, 7, 22, 9, 15, 0),
                volume_traded=0,
            )
        )
        self.builder.on_tick(
            _make_tick(
                sequence=2,
                price=100.0,
                exchange_timestamp=_ist(2026, 7, 22, 9, 14, 0),
                volume_traded=0,
            )
        )
        self.builder.on_tick(
            _make_tick(
                sequence=3,
                price=100.0,
                exchange_timestamp=_ist(2026, 7, 22, 9, 15, 10),
                volume_traded=200,
            )
        )
        self.builder.on_tick(
            _make_tick(
                sequence=3,
                price=100.0,
                exchange_timestamp=_ist(2026, 7, 22, 9, 15, 10),
                volume_traded=200,
            )
        )
        self.builder.on_tick(
            _make_tick(
                sequence=4,
                price=101.0,
                exchange_timestamp=_ist(2026, 7, 22, 9, 15, 40),
                volume_traded=50,
            )
        )
        self.builder.on_tick(
            _make_tick(
                sequence=5,
                price=102.0,
                exchange_timestamp=_ist(2026, 7, 22, 9, 16, 5),
                volume_traded=80,
            )
        )
        self.builder.on_tick(
            _make_tick(
                sequence=6,
                price=103.0,
                exchange_timestamp=_ist(2026, 7, 22, 9, 15, 50),
                volume_traded=60,
            )
        )
        self.builder.flush()
        metrics = self.builder.metrics
        self.assertEqual(metrics.out_of_session_ticks, 1)
        self.assertEqual(metrics.invalid_price_ticks, 1)
        self.assertEqual(metrics.duplicate_ticks_ignored, 1)
        self.assertEqual(metrics.cumulative_volume_decreases, 1)
        self.assertEqual(metrics.late_ticks_dropped, 1)
        self.assertEqual(metrics.candles_emitted, 2)


class CandleTimeTypeTests(BuilderTestCase):
    def test_candle_time_is_timezone_aware_datetime(self) -> None:
        self.builder.on_tick(
            _make_tick(
                sequence=1,
                price=100.0,
                exchange_timestamp=_ist(2026, 7, 22, 10, 30, 45),
                volume_traded=100,
            )
        )
        self.builder.flush()
        candle = self.emitted[0]
        self.assertEqual(candle.candle_time.tzinfo, _IST)
        self.assertEqual(candle.candle_time, _ist(2026, 7, 22, 10, 30, 0))
        bar = candle.to_one_minute_candle()
        self.assertIsInstance(bar.candle_time, str)


class InvalidPriceTests(BuilderTestCase):
    def test_invalid_price_incremented(self) -> None:
        self.builder.on_tick(
            _make_tick(
                sequence=1,
                price=-1.0,
                exchange_timestamp=_ist(2026, 7, 22, 10, 0, 0),
                volume_traded=100,
            )
        )
        self.assertEqual(self.builder.metrics.invalid_price_ticks, 1)
        self.assertEqual(len(self.emitted), 0)


if __name__ == "__main__":
    unittest.main()
