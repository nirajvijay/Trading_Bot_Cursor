"""
Market-data coordinator: ordered post-candle fan-out.

Candle writer runs first (fatal on unrecoverable persistence errors).
Strategy consumers run next and are isolated (non-fatal).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, List, Optional, Protocol, Sequence

from candle_aggregation import CompletedOneMinuteCandle
from candle_emission import CandleEmissionError
from live_one_minute_candle_writer import (
    LiveOneMinuteCandleWriter,
    is_unrecoverable_persistence_error,
)

logger = logging.getLogger(__name__)

CandleConsumer = Callable[[CompletedOneMinuteCandle], None]


class Closeable(Protocol):
    def close(self) -> None: ...


@dataclass(frozen=True)
class CoordinatorMetrics:
    candles_dispatched: int
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
        closeables: Sequence[Closeable] = (),
    ) -> None:
        self._candle_writer = candle_writer
        self._strategy_consumers: List[CandleConsumer] = list(strategy_consumers)
        self._closeables: List[Closeable] = list(closeables)
        self._candles_dispatched = 0
        self._strategy_consumer_failures = 0

    @property
    def candle_writer(self) -> LiveOneMinuteCandleWriter:
        return self._candle_writer

    @property
    def metrics(self) -> CoordinatorMetrics:
        return CoordinatorMetrics(
            candles_dispatched=self._candles_dispatched,
            strategy_consumer_failures=self._strategy_consumer_failures,
        )

    def add_strategy_consumer(self, consumer: CandleConsumer) -> None:
        self._strategy_consumers.append(consumer)

    def on_completed_candle(self, candle: CompletedOneMinuteCandle) -> None:
        self._candles_dispatched += 1

        try:
            self._candle_writer.on_candle(candle)
        except Exception as exc:
            if is_unrecoverable_persistence_error(exc):
                raise CandleEmissionError(candle, exc) from exc
            raise

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

    def close(self) -> None:
        """Close strategy resources first, then the candle writer."""
        for closeable in self._closeables:
            try:
                closeable.close()
            except Exception:
                logger.exception("closeable.close() failed")
        self._candle_writer.close()
