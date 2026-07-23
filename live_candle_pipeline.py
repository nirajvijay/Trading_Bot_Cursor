"""
Live candle pipeline: TickReceiver -> OneMinuteCandleBuilder -> coordinator.

Owns tick/build lifecycle and fatal candle-emission surface.
Post-candle fan-out (writer + strategy) belongs to MarketDataCoordinator.
"""

from __future__ import annotations

from typing import Optional

from candle_emission import FailedEmission
from market_data_coordinator import MarketDataCoordinator
from one_minute_candle_builder import OneMinuteCandleBuilder
from tick_receiver import TickReceiver


class LiveCandlePipeline:
    def __init__(self, *, coordinator: MarketDataCoordinator) -> None:
        self._coordinator = coordinator
        self._receiver: Optional[TickReceiver] = None
        self._builder = OneMinuteCandleBuilder(
            on_candle=self._coordinator.on_completed_candle
        )

    def attach_receiver(self, receiver: TickReceiver) -> None:
        self._receiver = receiver

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
