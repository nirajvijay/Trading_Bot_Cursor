"""
Live candle pipeline: TickReceiver -> OneMinuteCandleBuilder -> LiveOneMinuteCandleWriter.

Owns wiring, persistence error classification at the callback boundary, and
deterministic shutdown ordering.
"""

from __future__ import annotations

from typing import Optional

from candle_aggregation import CompletedOneMinuteCandle
from candle_emission import CandleEmissionError, FailedEmission
from live_one_minute_candle_writer import (
    LiveOneMinuteCandleWriter,
    is_unrecoverable_persistence_error,
)
from one_minute_candle_builder import OneMinuteCandleBuilder
from tick_receiver import TickReceiver


class LiveCandlePipeline:
    def __init__(self, *, writer: LiveOneMinuteCandleWriter) -> None:
        self._writer = writer
        self._receiver: Optional[TickReceiver] = None
        self._builder = OneMinuteCandleBuilder(on_candle=self._persist_candle)

    def attach_receiver(self, receiver: TickReceiver) -> None:
        self._receiver = receiver

    @property
    def receiver(self) -> TickReceiver:
        if self._receiver is None:
            raise RuntimeError("receiver not attached")
        return self._receiver

    @property
    def builder(self) -> OneMinuteCandleBuilder:
        return self._builder

    @property
    def writer(self) -> LiveOneMinuteCandleWriter:
        return self._writer

    @property
    def fatal_error(self) -> Optional[BaseException]:
        if self._receiver is not None and self._receiver.fatal_error is not None:
            return self._receiver.fatal_error
        return self._builder.fatal_error

    @property
    def failed_emission(self) -> Optional[FailedEmission]:
        return self._builder.failed_emission

    @property
    def is_fatal(self) -> bool:
        return self.fatal_error is not None

    def _persist_candle(self, candle: CompletedOneMinuteCandle) -> None:
        try:
            self._writer.on_candle(candle)
        except Exception as exc:
            if is_unrecoverable_persistence_error(exc):
                raise CandleEmissionError(candle, exc) from exc
            raise

    def run(self) -> None:
        """Block until shutdown; re-raise fatal errors in the caller thread."""
        self.receiver.start()

    def shutdown(self, *, flush: Optional[bool] = None) -> None:
        self.receiver.stop()
        should_flush = flush if flush is not None else not self.is_fatal
        if should_flush:
            self._builder.flush()
        self._writer.close()
