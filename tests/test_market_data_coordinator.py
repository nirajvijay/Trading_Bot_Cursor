"""Phase 4: coordinator + pipeline composition with spike detector."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from baseline_generator import init_baselines_db
from baseline_store import BaselineStore
from candle_aggregation import CompletedOneMinuteCandle
from intraday_spike_detector import IntradaySpikeDetector
from intraday_spike_writer import IntradaySpikeWriter
from live_candle_pipeline import LiveCandlePipeline
from live_one_minute_candle_writer import LiveOneMinuteCandleWriter
from market_data_coordinator import MarketDataCoordinator
from tick_event import IST

_IST = ZoneInfo(IST)
_TOKEN = 738561
_SYMBOL = "RELIANCE"


def _passing_candle() -> CompletedOneMinuteCandle:
    return CompletedOneMinuteCandle(
        instrument_token=_TOKEN,
        candle_time=datetime(2026, 7, 23, 10, 30, 0, tzinfo=_IST),
        open=100.0,
        high=103.0,
        low=99.0,
        close=102.5,
        volume=20_000,
        tick_count=40,
        volume_reliable=True,
        completion_reason="minute_transition",
        has_full_minute_coverage=True,
        is_partial=False,
    )


class CoordinatorSpikeWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        root = Path(self._tmpdir.name)
        self.live_db = root / "live.db"
        self.baselines_db = root / "baselines.db"
        conn = init_baselines_db(self.baselines_db)
        conn.execute(
            """
            INSERT INTO baselines (
                instrument_token, tradingsymbol, minute_of_day,
                median_volume, trimmed_mean_volume, median_abs_return,
                valid_session_count, is_reliable, baseline_as_of_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (_TOKEN, _SYMBOL, 630, 5000.0, 4800.0, 0.0005, 21, 1, "2026-07-22"),
        )
        conn.commit()
        conn.close()

        self.token_map = {_TOKEN: _SYMBOL}
        self.candle_writer = LiveOneMinuteCandleWriter(
            db_path=self.live_db,
            token_to_symbol=self.token_map,
        )
        self.spike_writer = IntradaySpikeWriter(
            db_path=self.live_db,
            token_to_symbol=self.token_map,
        )
        self.store = BaselineStore.load("2026-07-23", db_path=self.baselines_db)
        self.detector = IntradaySpikeDetector(
            baseline_store=self.store,
            writer=self.spike_writer,
            token_to_symbol=self.token_map,
        )
        self.coordinator = MarketDataCoordinator(
            candle_writer=self.candle_writer,
            strategy_consumers=[self.detector.on_candle],
            closeables=[self.spike_writer],
        )
        self.pipeline = LiveCandlePipeline(coordinator=self.coordinator)

    def tearDown(self) -> None:
        try:
            self.pipeline.coordinator.close()
        except Exception:
            pass
        self._tmpdir.cleanup()

    def test_pipeline_dispatch_persists_candle_and_spike(self) -> None:
        self.pipeline.builder._on_candle(_passing_candle())
        self.assertEqual(self.candle_writer.metrics.candles_inserted, 1)
        self.assertEqual(self.detector.metrics.accepted_spikes, 1)
        self.assertEqual(self.spike_writer.metrics.spikes_inserted, 1)
        self.assertEqual(self.coordinator.metrics.candles_dispatched, 1)

    def test_one_minute_strategy_runs_before_five_minute_consumers(self) -> None:
        order: list[str] = []

        def on_1m(_candle: CompletedOneMinuteCandle) -> None:
            order.append("1m_strategy")

        def on_5m(_candle) -> None:  # type: ignore[no-untyped-def]
            order.append("5m_strategy")

        from live_five_minute_candle_builder import LiveFiveMinuteCandleBuilder

        builder = LiveFiveMinuteCandleBuilder()
        coord = MarketDataCoordinator(
            candle_writer=self.candle_writer,
            strategy_consumers=[on_1m],
            five_minute_builder=builder,
            five_minute_consumers=[on_5m],
        )
        start = 10 * 60 + 30
        for offset in range(5):
            hour, mins = divmod(start + offset, 60)
            candle = CompletedOneMinuteCandle(
                instrument_token=_TOKEN,
                candle_time=datetime(2026, 7, 23, hour, mins, 0, tzinfo=_IST),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                volume=100,
                tick_count=5,
                volume_reliable=True,
                completion_reason="minute_transition",
                has_full_minute_coverage=True,
                is_partial=False,
            )
            coord.on_completed_candle(candle)
        self.assertEqual(order.count("1m_strategy"), 5)
        self.assertEqual(order.count("5m_strategy"), 1)
        # Final 1m strategy must precede the 5m strategy consumer.
        last_1m = max(i for i, x in enumerate(order) if x == "1m_strategy")
        only_5m = order.index("5m_strategy")
        self.assertLess(last_1m, only_5m)


if __name__ == "__main__":
    unittest.main()
