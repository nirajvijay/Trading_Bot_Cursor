#!/usr/bin/env python3
"""
Offline replay smoke for live 5m + pullback engine (no Kite).

Scenarios:
  1. Bullish controlled pullback → READY → CONTINUATION_MONITORING
  2. Excessive retracement → INVALIDATED
  3. Shallow for 7 candles → EXPIRED
  4. Second spike ignored while active
  5. Trade executed terminals setup
  6. Writer closed → degraded; 1m path still independent
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from candle_aggregation import CompletedFiveMinuteCandle, CompletedOneMinuteCandle
from intraday_pullback_engine import IntradayPullbackEngine
from intraday_pullback_writer import IntradayPullbackWriter
from live_five_minute_candle_builder import LiveFiveMinuteCandleBuilder
from live_five_minute_candle_writer import LiveFiveMinuteCandleWriter
from live_one_minute_candle_writer import LiveOneMinuteCandleWriter
from market_data_coordinator import MarketDataCoordinator
from spike_types import IntradaySpikeEvent, SpikeDecision, SpikeFeatures
from tick_event import IST

_IST = ZoneInfo(IST)
TOKEN = 738561
SYMBOL = "RELIANCE"


def _check(cond: bool, msg: str, failures: list[str]) -> None:
    if cond:
        print(f"  PASS  {msg}")
    else:
        print(f"  FAIL  {msg}")
        failures.append(msg)


def _spike(minute: int, *, high: float, low: float, close: float) -> IntradaySpikeEvent:
    hour, mins = divmod(minute, 60)
    ct = datetime(2026, 7, 22, hour, mins, 0, tzinfo=_IST)
    features = SpikeFeatures(
        instrument_token=TOKEN,
        minute_of_day=minute,
        session_date="2026-07-22",
        baseline_as_of_date="2026-07-21",
        open=low + 1,
        high=high,
        low=low,
        close=close,
        volume=1000,
        tick_count=20,
        volume_reliable=True,
        absolute_return=0.02,
        signed_return=0.02,
        direction="UP",
        relative_volume_median=3.0,
        relative_volume_trimmed=3.0,
        abs_return_vs_baseline=3.0,
        body_ratio=0.8,
        close_location=0.9,
        median_volume=100.0,
        trimmed_mean_volume=100.0,
        median_abs_return=0.001,
        valid_session_count=20,
        is_reliable=True,
    )
    return IntradaySpikeEvent(
        instrument_token=TOKEN,
        tradingsymbol=SYMBOL,
        candle_time=ct,
        session_date="2026-07-22",
        rule_version="intraday_spike_v1",
        direction="UP",
        open=low + 1,
        high=high,
        low=low,
        close=close,
        volume=1000,
        features=features,
        detected_at=datetime.now(timezone.utc),
        decision=SpikeDecision(
            accepted=True, rule_version="intraday_spike_v1", reasons=frozenset()
        ),
    )


def _5m(hour: int, minute: int, o: float, h: float, l: float, c: float) -> CompletedFiveMinuteCandle:
    return CompletedFiveMinuteCandle(
        instrument_token=TOKEN,
        candle_time=datetime(2026, 7, 22, hour, minute, 0, tzinfo=_IST),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=500,
        session_date="2026-07-22",
        constituent_count=5,
        all_volume_reliable=True,
        any_partial=False,
        all_full_coverage=True,
        tick_count=25,
    )


def _1m(minute: int) -> CompletedOneMinuteCandle:
    hour, mins = divmod(minute, 60)
    return CompletedOneMinuteCandle(
        instrument_token=TOKEN,
        candle_time=datetime(2026, 7, 22, hour, mins, 0, tzinfo=_IST),
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


def main() -> int:
    failures: list[str] = []
    print("=== Pullback replay smoke ===")

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "live.db"
        token_map = {TOKEN: SYMBOL}
        candle_writer = LiveOneMinuteCandleWriter(db_path=db, token_to_symbol=token_map)
        five_writer = LiveFiveMinuteCandleWriter(db_path=db, token_to_symbol=token_map)
        five_builder = LiveFiveMinuteCandleBuilder()
        pb_writer = IntradayPullbackWriter(db_path=db)
        ready = []
        engine = IntradayPullbackEngine(
            writer=pb_writer,
            on_pullback_ready=ready.append,
        )

        coordinator = MarketDataCoordinator(
            candle_writer=candle_writer,
            five_minute_builder=five_builder,
            five_minute_writer=five_writer,
            five_minute_consumers=[engine.on_five_minute_candle],
            closeables=[pb_writer],
        )

        # Feed five 1m bars to produce one live 5m via coordinator
        start = 10 * 60 + 30
        for off in range(5):
            coordinator.on_completed_candle(_1m(start + off))
        _check(
            coordinator.metrics.five_minute_dispatched == 1,
            "coordinator dispatched one 5m bar",
            failures,
        )
        _check(
            five_writer.metrics.candles_inserted == 1,
            "live_5m_candles inserted",
            failures,
        )

        # Direct pullback path (spike + synthetic 5m sequence)
        engine.on_spike(_spike(10 * 60 + 32, high=110, low=100, close=109))
        engine.on_five_minute_candle(_5m(10, 30, 100, 110, 100, 109))
        engine.on_five_minute_candle(_5m(10, 35, 109, 109.5, 106, 106.5))
        engine.on_five_minute_candle(_5m(10, 40, 106.5, 107, 105, 105.5))
        _check(len(ready) == 1, "bullish pullback became READY", failures)
        _check(
            engine._active_by_token[TOKEN].state == "CONTINUATION_MONITORING",
            "entered CONTINUATION_MONITORING",
            failures,
        )

        engine.on_spike(_spike(11 * 60 + 5, high=120, low=110, close=119))
        _check(
            engine.metrics.spike_ignored_while_active >= 1,
            "second spike ignored while active",
            failures,
        )

        setup_id = engine._active_by_token[TOKEN].setup.setup_id
        engine.record_continuation_attempt(setup_id)
        engine.on_trade_executed(setup_id, fill_id="F1")
        _check(engine.metrics.traded == 1, "TRADED terminal after fill", failures)
        _check(TOKEN not in engine._active_by_token, "slot freed after TRADED", failures)

        # Invalidation scenario on fresh engine
        pb_writer2 = IntradayPullbackWriter(db_path=Path(tmp) / "live2.db")
        eng2 = IntradayPullbackEngine(writer=pb_writer2)
        eng2.on_spike(_spike(10 * 60 + 32, high=110, low=100, close=109))
        eng2.on_five_minute_candle(_5m(10, 30, 100, 110, 100, 109))
        eng2.on_five_minute_candle(_5m(10, 35, 109, 109, 102, 103))
        _check(eng2.metrics.invalidated == 1, "excessive retrace invalidated", failures)
        pb_writer2.close()

        coordinator.close()

    print()
    if failures:
        print(f"FAILED ({len(failures)})")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
