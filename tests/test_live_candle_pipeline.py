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


class PipelinePersistCallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.writer = LiveOneMinuteCandleWriter(
            db_path=Path(self._tmpdir.name) / "pipeline.db",
            token_to_symbol={_TOKEN: _SYMBOL},
        )
        self.pipeline = LiveCandlePipeline(writer=self.writer)

    def tearDown(self) -> None:
        self.writer.close()
        self._tmpdir.cleanup()

    def test_persist_candle_wraps_conflict_as_emission_error(self) -> None:
        candle_time = _ist(2026, 7, 22, 10, 30, 0)
        candle = _completed(candle_time=candle_time, close=103.0)
        self.writer.on_candle(candle)
        conflicting = _completed(candle_time=candle_time, close=104.0)
        with self.assertRaises(CandleEmissionError) as ctx:
            self.pipeline._persist_candle(conflicting)
        self.assertIs(ctx.exception.candle, conflicting)
        self.assertIsInstance(ctx.exception.cause, CandleConflictError)

    def test_persist_candle_passes_through_non_fatal_errors(self) -> None:
        candle = _completed(candle_time=_ist(2026, 7, 22, 10, 31, 0))

        def failing_writer(candle_arg: CompletedOneMinuteCandle) -> None:
            raise RuntimeError("callback failed")

        self.pipeline._writer.on_candle = failing_writer  # type: ignore[method-assign]
        with self.assertRaises(RuntimeError) as ctx:
            self.pipeline._persist_candle(candle)
        self.assertEqual(str(ctx.exception), "callback failed")


class PipelineShutdownTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.writer = LiveOneMinuteCandleWriter(
            db_path=Path(self._tmpdir.name) / "pipeline.db",
            token_to_symbol={_TOKEN: _SYMBOL},
        )
        self.pipeline = LiveCandlePipeline(writer=self.writer)
        self.receiver = MagicMock()
        self.receiver.fatal_error = None
        self.pipeline.attach_receiver(self.receiver)

    def tearDown(self) -> None:
        self.writer.close()
        self._tmpdir.cleanup()

    def test_shutdown_skips_flush_on_fatal(self) -> None:
        cause = RuntimeError("persist failed")
        candle = _completed(candle_time=_ist(2026, 7, 22, 10, 30, 0))
        self.pipeline.builder._fatal_error = CandleEmissionError(candle, cause)
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

        self.receiver.stop.side_effect = stop
        with patch.object(self.pipeline.builder, "flush", side_effect=flush):
            with patch.object(self.pipeline.writer, "close", side_effect=close):
                self.pipeline.shutdown()
        self.assertEqual(events, ["stop", "flush", "close"])


if __name__ == "__main__":
    unittest.main()
