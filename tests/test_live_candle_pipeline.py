from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from candle_aggregation import CompletedOneMinuteCandle
from candle_emission import CandleEmissionError
from live_candle_pipeline import LiveCandlePipeline
from live_one_minute_candle_writer import CandleConflictError, LiveOneMinuteCandleWriter
from market_data_coordinator import MarketDataCoordinator
from tick_event import IST

_IST = ZoneInfo(IST)
_TOKEN = 738561
_SYMBOL = "RELIANCE"


def _ist(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    second: int = 0,
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=_IST)


def _completed(
    *,
    candle_time: datetime,
    close: float = 103.0,
) -> CompletedOneMinuteCandle:
    return CompletedOneMinuteCandle(
        instrument_token=_TOKEN,
        candle_time=candle_time,
        open=100.0,
        high=105.0,
        low=99.0,
        close=close,
        volume=500,
        tick_count=10,
        volume_reliable=True,
        completion_reason="minute_transition",
        has_full_minute_coverage=True,
        is_partial=False,
    )


class CoordinatorDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.writer = LiveOneMinuteCandleWriter(
            db_path=Path(self._tmpdir.name) / "pipeline.db",
            token_to_symbol={_TOKEN: _SYMBOL},
        )

    def tearDown(self) -> None:
        try:
            self.writer.close()
        except Exception:
            pass
        self._tmpdir.cleanup()

    def test_writer_runs_before_strategy_consumers(self) -> None:
        order: list[str] = []

        def strategy(candle: CompletedOneMinuteCandle) -> None:
            order.append("strategy")
            self.assertEqual(self.writer.metrics.candles_inserted, 1)

        original = self.writer.on_candle

        def tracked_writer(candle: CompletedOneMinuteCandle) -> None:
            order.append("writer")
            original(candle)

        self.writer.on_candle = tracked_writer  # type: ignore[method-assign]
        coordinator = MarketDataCoordinator(
            candle_writer=self.writer,
            strategy_consumers=[strategy],
        )
        coordinator.on_completed_candle(
            _completed(candle_time=_ist(2026, 7, 22, 10, 30, 0))
        )
        self.assertEqual(order, ["writer", "strategy"])
        self.assertEqual(coordinator.metrics.candles_dispatched, 1)

    def test_strategy_failure_does_not_abort_or_fail_writer(self) -> None:
        def boom(candle: CompletedOneMinuteCandle) -> None:
            raise RuntimeError("strategy boom")

        coordinator = MarketDataCoordinator(
            candle_writer=self.writer,
            strategy_consumers=[boom],
        )
        coordinator.on_completed_candle(
            _completed(candle_time=_ist(2026, 7, 22, 10, 30, 0))
        )
        self.assertEqual(self.writer.metrics.candles_inserted, 1)
        self.assertEqual(coordinator.metrics.strategy_consumer_failures, 1)

    def test_unrecoverable_writer_error_is_emission_error(self) -> None:
        candle_time = _ist(2026, 7, 22, 10, 30, 0)
        candle = _completed(candle_time=candle_time, close=103.0)
        self.writer.on_candle(candle)
        coordinator = MarketDataCoordinator(candle_writer=self.writer)
        conflicting = _completed(candle_time=candle_time, close=104.0)
        with self.assertRaises(CandleEmissionError) as ctx:
            coordinator.on_completed_candle(conflicting)
        self.assertIsInstance(ctx.exception.cause, CandleConflictError)

    def test_strategy_not_called_when_writer_fails(self) -> None:
        called = {"n": 0}

        def strategy(candle: CompletedOneMinuteCandle) -> None:
            called["n"] += 1

        candle_time = _ist(2026, 7, 22, 10, 30, 0)
        self.writer.on_candle(_completed(candle_time=candle_time, close=103.0))
        coordinator = MarketDataCoordinator(
            candle_writer=self.writer,
            strategy_consumers=[strategy],
        )
        with self.assertRaises(CandleEmissionError):
            coordinator.on_completed_candle(
                _completed(candle_time=candle_time, close=104.0)
            )
        self.assertEqual(called["n"], 0)


class PipelinePersistCallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.writer = LiveOneMinuteCandleWriter(
            db_path=Path(self._tmpdir.name) / "pipeline.db",
            token_to_symbol={_TOKEN: _SYMBOL},
        )
        self.coordinator = MarketDataCoordinator(candle_writer=self.writer)
        self.pipeline = LiveCandlePipeline(coordinator=self.coordinator)

    def tearDown(self) -> None:
        try:
            self.writer.close()
        except Exception:
            pass
        self._tmpdir.cleanup()

    def test_builder_callback_wraps_conflict_as_emission_error(self) -> None:
        candle_time = _ist(2026, 7, 22, 10, 30, 0)
        candle = _completed(candle_time=candle_time, close=103.0)
        self.writer.on_candle(candle)
        conflicting = _completed(candle_time=candle_time, close=104.0)
        with self.assertRaises(CandleEmissionError) as ctx:
            self.pipeline.builder._on_candle(conflicting)
        self.assertIs(ctx.exception.candle, conflicting)
        self.assertIsInstance(ctx.exception.cause, CandleConflictError)

    def test_non_fatal_writer_errors_pass_through(self) -> None:
        candle = _completed(candle_time=_ist(2026, 7, 22, 10, 31, 0))

        def failing_writer(candle_arg: CompletedOneMinuteCandle) -> None:
            raise RuntimeError("callback failed")

        self.pipeline.writer.on_candle = failing_writer  # type: ignore[method-assign]
        with self.assertRaises(RuntimeError) as ctx:
            self.pipeline.builder._on_candle(candle)
        self.assertEqual(str(ctx.exception), "callback failed")


class PipelineShutdownTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.writer = LiveOneMinuteCandleWriter(
            db_path=Path(self._tmpdir.name) / "pipeline.db",
            token_to_symbol={_TOKEN: _SYMBOL},
        )
        self.coordinator = MarketDataCoordinator(candle_writer=self.writer)
        self.pipeline = LiveCandlePipeline(coordinator=self.coordinator)
        self.receiver = MagicMock()
        self.receiver.fatal_error = None
        self.pipeline.attach_receiver(self.receiver)

    def tearDown(self) -> None:
        try:
            self.writer.close()
        except Exception:
            pass
        self._tmpdir.cleanup()

    def test_shutdown_skips_flush_on_fatal(self) -> None:
        from candle_emission import CandleEmissionError as CEE

        cause = RuntimeError("persist failed")
        candle = _completed(candle_time=_ist(2026, 7, 22, 10, 30, 0))
        self.pipeline.builder._fatal_error = CEE(candle, cause)
        with patch.object(self.pipeline.builder, "flush") as flush_mock:
            self.pipeline.shutdown()
        flush_mock.assert_not_called()
        self.receiver.stop.assert_called_once()

    def test_shutdown_calls_flush_on_graceful_exit(self) -> None:
        with patch.object(self.pipeline.builder, "flush") as flush_mock:
            self.pipeline.shutdown()
        flush_mock.assert_called_once()
        self.receiver.stop.assert_called_once()

    def test_shutdown_order(self) -> None:
        events: list[str] = []

        def stop() -> None:
            events.append("stop")

        def flush() -> None:
            events.append("flush")

        def close() -> None:
            events.append("close")

        closeable = MagicMock()
        closeable.close.side_effect = lambda: events.append("strategy_close")
        self.coordinator._closeables = [closeable]

        self.receiver.stop.side_effect = stop
        with patch.object(self.pipeline.builder, "flush", side_effect=flush):
            with patch.object(self.pipeline.writer, "close", side_effect=close):
                self.pipeline.shutdown()
        self.assertEqual(events, ["stop", "flush", "strategy_close", "close"])


if __name__ == "__main__":
    unittest.main()
