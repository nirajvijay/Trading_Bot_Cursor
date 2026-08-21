"""Qualifier failures must not change raw continuation TRIGGERED/REJECTED."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from live_candle_pipeline import LiveCandlePipeline
from live_one_minute_candle_writer import LiveOneMinuteCandleWriter
from market_data_coordinator import MarketDataCoordinator
from tick_event import IST, Ohlc, TickEvent

_IST = ZoneInfo(IST)
TOKEN = 738561
SYMBOL = "RELIANCE"


def _tick(sequence: int, hour: int, minute: int, second: int, price: float, volume: int) -> TickEvent:
    ts = datetime(2026, 8, 21, hour, minute, second, tzinfo=_IST)
    return TickEvent(
        sequence=sequence,
        instrument_token=TOKEN,
        last_price=price,
        exchange_timestamp=ts,
        received_at=ts,
        volume_traded=volume,
        last_traded_quantity=1,
        average_traded_price=price,
        ohlc=Ohlc(open=price, high=price, low=price, close=price),
    )


class QualifierIsolationTests(unittest.TestCase):
    def test_vwap_tick_exception_does_not_stop_continuation_consumer(self) -> None:
        order: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            writer = LiveOneMinuteCandleWriter(
                db_path=Path(tmp) / "live.db",
                token_to_symbol={TOKEN: SYMBOL},
            )
            coord = MarketDataCoordinator(candle_writer=writer)

            def boom(_tick: TickEvent) -> None:
                order.append("vwap")
                raise RuntimeError("vwap exploded")

            def continuation(tick: TickEvent) -> None:
                del tick
                order.append("continuation")

            pipeline = LiveCandlePipeline(
                coordinator=coord,
                tick_consumers=[boom, continuation],
            )
            pipeline.on_tick(_tick(1, 10, 5, 1, 100.0, 10))
            writer.close()
        self.assertEqual(order, ["vwap", "continuation"])
        self.assertEqual(pipeline.tick_consumer_failures, 1)

    def test_vwap_runs_before_continuation(self) -> None:
        order: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            writer = LiveOneMinuteCandleWriter(
                db_path=Path(tmp) / "live.db",
                token_to_symbol={TOKEN: SYMBOL},
            )
            coord = MarketDataCoordinator(candle_writer=writer)
            pipeline = LiveCandlePipeline(
                coordinator=coord,
                tick_consumers=[lambda t: order.append("vwap"), lambda t: order.append("cont")],
            )
            pipeline.on_tick(_tick(1, 10, 5, 1, 100.0, 10))
            writer.close()
        self.assertEqual(order, ["vwap", "cont"])


if __name__ == "__main__":
    unittest.main()
