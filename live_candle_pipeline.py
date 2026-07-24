"""
Live candle pipeline: TickReceiver -> OneMinuteCandleBuilder -> coordinator.

Owns tick/build lifecycle and fatal candle-emission surface.
Post-candle fan-out (writer + strategy) belongs to MarketDataCoordinator.

Official boundary-tick precedence (live and replay must match):
  For each TickEvent T:
    1) OneMinuteCandleBuilder.on_tick(T)
         → may complete 1m/5m and run coordinator strategy consumers
         → pullback structural terminals take precedence
    2) optional tick strategy consumers (e.g. ContinuationEngine.on_tick)
"""

from __future__ import annotations

import logging
from typing import Callable, List, Optional, Sequence

from candle_emission import FailedEmission
from market_data_coordinator import MarketDataCoordinator
from one_minute_candle_builder import OneMinuteCandleBuilder
from tick_event import TickEvent
from tick_receiver import TickReceiver

logger = logging.getLogger(__name__)

TickConsumer = Callable[[TickEvent], None]


class LiveCandlePipeline:
    def __init__(
        self,
        *,
        coordinator: MarketDataCoordinator,
        tick_consumers: Sequence[TickConsumer] = (),
    ) -> None:
        self._coordinator = coordinator
        self._receiver: Optional[TickReceiver] = None
        self._tick_consumers: List[TickConsumer] = list(tick_consumers)
        self._builder = OneMinuteCandleBuilder(on_candle=self._coordinator.on_completed_candle)
        self._tick_consumer_failures = 0

    def attach_receiver(self, receiver: TickReceiver) -> None:
        self._receiver = receiver

    def add_tick_consumer(self, consumer: TickConsumer) -> None:
        self._tick_consumers.append(consumer)

    def on_tick(self, tick: TickEvent) -> None:
        """
        Fan-out a tick with locked precedence: builder (and thus coordinator)
        fully completes before any tick strategy consumer runs.
        """
        self._builder.on_tick(tick)
        for index, consumer in enumerate(self._tick_consumers):
            try:
                consumer(tick)
            except Exception:  # noqa: BLE001
                self._tick_consumer_failures += 1
                logger.exception(
                    "tick strategy consumer %d failed token=%s seq=%s",
                    index,
                    tick.instrument_token,
                    tick.sequence,
                )

    @property
    def tick_consumer_failures(self) -> int:
        return self._tick_consumer_failures

    @property
    def coordinator(self) -> MarketDataCoordinator:
        return self._coordinator

    @property
    def receiver(self) -> TickReceiver:
        if self._receiver is None:
            raise RuntimeError("receiver not attached")
        return self._receiver

    @property
    def builder(self) -> OneMinuteCandleBuilder:
        return self._builder

    @property
    def writer(self):
        """Backward-compatible access to the candle writer via the coordinator."""
        return self._coordinator.candle_writer

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

    def run(self) -> None:
        """Block until shutdown; re-raise fatal errors in the caller thread."""
        self.receiver.start()

    def shutdown(self, *, flush: Optional[bool] = None) -> None:
        self.receiver.stop()
        should_flush = flush if flush is not None else not self.is_fatal
        if should_flush:
            self._builder.flush()
        self._coordinator.close()
