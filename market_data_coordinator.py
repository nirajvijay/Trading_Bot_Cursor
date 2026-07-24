"""
Market-data coordinator: ordered post-candle fan-out.

1. 1m candle writer (fatal on unrecoverable persistence errors)
2. Optional live 5m builder + writer (5m write fatal on conflict)
3. 1m strategy consumers (isolated, non-fatal) — e.g. spike detector
4. 5m strategy consumers (isolated, non-fatal) — e.g. pullback engine

Order matches ops: 1m persist → 5m persist → spike → pullback.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, List, Optional, Protocol, Sequence

from candle_aggregation import CompletedFiveMinuteCandle, CompletedOneMinuteCandle
from candle_emission import CandleEmissionError
from live_five_minute_candle_builder import LiveFiveMinuteCandleBuilder
from live_five_minute_candle_writer import LiveFiveMinuteCandleWriter
from live_one_minute_candle_writer import (
    LiveOneMinuteCandleWriter,
    is_unrecoverable_persistence_error,
)

logger = logging.getLogger(__name__)

CandleConsumer = Callable[[CompletedOneMinuteCandle], None]
FiveMinuteConsumer = Callable[[CompletedFiveMinuteCandle], None]


class Closeable(Protocol):
    def close(self) -> None: ...


@dataclass(frozen=True)
class CoordinatorMetrics:
    candles_dispatched: int
    five_minute_dispatched: int
    strategy_consumer_failures: int


class MarketDataCoordinator:
    """
    Composition seam between market-data production and strategy consumers.

    May import both market-data and strategy modules; market-data libraries
    must not import this coordinator's strategy dependents.
    """

    def __init__(
        self,
        *,
        candle_writer: LiveOneMinuteCandleWriter,
        strategy_consumers: Sequence[CandleConsumer] = (),
        five_minute_builder: Optional[LiveFiveMinuteCandleBuilder] = None,
        five_minute_writer: Optional[LiveFiveMinuteCandleWriter] = None,
        five_minute_consumers: Sequence[FiveMinuteConsumer] = (),
        closeables: Sequence[Closeable] = (),
    ) -> None:
        self._candle_writer = candle_writer
        self._strategy_consumers: List[CandleConsumer] = list(strategy_consumers)
        self._five_minute_builder = five_minute_builder
        self._five_minute_writer = five_minute_writer
        self._five_minute_consumers: List[FiveMinuteConsumer] = list(
            five_minute_consumers
        )
        self._closeables: List[Closeable] = list(closeables)
        self._candles_dispatched = 0
        self._five_minute_dispatched = 0
        self._strategy_consumer_failures = 0

    @property
    def candle_writer(self) -> LiveOneMinuteCandleWriter:
        return self._candle_writer

    @property
    def metrics(self) -> CoordinatorMetrics:
        return CoordinatorMetrics(
            candles_dispatched=self._candles_dispatched,
            five_minute_dispatched=self._five_minute_dispatched,
            strategy_consumer_failures=self._strategy_consumer_failures,
        )

    def add_strategy_consumer(self, consumer: CandleConsumer) -> None:
        self._strategy_consumers.append(consumer)

    def add_five_minute_consumer(self, consumer: FiveMinuteConsumer) -> None:
        self._five_minute_consumers.append(consumer)

    def on_completed_candle(self, candle: CompletedOneMinuteCandle) -> None:
        self._candles_dispatched += 1

        try:
            self._candle_writer.on_candle(candle)
        except Exception as exc:
            if is_unrecoverable_persistence_error(exc):
                raise CandleEmissionError(candle, exc) from exc
            raise

        completed_5m: Optional[CompletedFiveMinuteCandle] = None
        if self._five_minute_builder is not None:
            completed_5m = self._five_minute_builder.on_one_minute(candle)

        if completed_5m is not None:
            self._write_five_minute(completed_5m)

        for index, consumer in enumerate(self._strategy_consumers):
            try:
                consumer(candle)
            except Exception:
                self._strategy_consumer_failures += 1
                logger.exception(
                    "strategy consumer %d failed for token=%s time=%s",
                    index,
                    candle.instrument_token,
                    candle.candle_time,
                )

        if completed_5m is not None:
            self._dispatch_five_minute_consumers(completed_5m)

    def _write_five_minute(self, candle: CompletedFiveMinuteCandle) -> None:
        self._five_minute_dispatched += 1

        if self._five_minute_writer is not None:
            try:
                self._five_minute_writer.on_candle(candle)
            except Exception as exc:
                if is_unrecoverable_persistence_error(exc):
                    raise RuntimeError(
                        "fatal 5m candle persistence for token=%s time=%s: %s"
                        % (candle.instrument_token, candle.candle_time, exc)
                    ) from exc
                raise

    def _dispatch_five_minute_consumers(self, candle: CompletedFiveMinuteCandle) -> None:
        for index, consumer in enumerate(self._five_minute_consumers):
            try:
                consumer(candle)
            except Exception:
                self._strategy_consumer_failures += 1
                logger.exception(
                    "5m strategy consumer %d failed for token=%s time=%s",
                    index,
                    candle.instrument_token,
                    candle.candle_time,
                )

    def close(self) -> None:
        """Close strategy resources first, then candle writers."""
        for closeable in self._closeables:
            try:
                closeable.close()
            except Exception:
                logger.exception("closeable.close() failed")
        if self._five_minute_writer is not None:
            try:
                self._five_minute_writer.close()
            except Exception:
                logger.exception("five_minute_writer.close() failed")
        self._candle_writer.close()
