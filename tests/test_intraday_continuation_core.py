"""Unit and integration tests for the continuation trigger engine."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from candle_aggregation import CompletedFiveMinuteCandle, CompletedOneMinuteCandle
from continuation_features import (
    compute_trigger_ticks,
    price_reached_trigger,
    price_to_ticks,
    volume_confirms,
)
from continuation_tick_size import TickSizeMap, TickSizePreflightError, load_tick_size_map
from continuation_types import ContinuationTriggeredEvent
from intraday_continuation_config import IntradayContinuationRuleConfig
from intraday_continuation_engine import IntradayContinuationEngine
from intraday_continuation_writer import (
    ContinuationConflictError,
    IntradayContinuationWriter,
)
from intraday_pullback_engine import IntradayPullbackEngine
from intraday_pullback_writer import IntradayPullbackWriter
from pullback_features import empty_sequence_after_impulse, update_sequence
from spike_types import IntradaySpikeEvent, SpikeDecision, SpikeFeatures
from tick_event import IST, Ohlc, TickEvent

_IST = ZoneInfo(IST)
TOKEN = 42
SYMBOL = "RELIANCE"
SESSION = "2026-07-22"
TICK_SIZE = 0.05


def _tick(
    *,
    sequence: int,
    price: float,
    hour: int,
    minute: int,
    second: int = 30,
    volume_traded: int,
) -> TickEvent:
    ts = datetime(2026, 7, 22, hour, minute, second, tzinfo=_IST)
    return TickEvent(
        sequence=sequence,
        instrument_token=TOKEN,
        last_price=price,
        exchange_timestamp=ts,
        received_at=ts,
        volume_traded=volume_traded,
        last_traded_quantity=1,
        average_traded_price=price,
        ohlc=Ohlc(open=price, high=price, low=price, close=price),
    )


def _1m(
    *,
    hour: int,
    minute: int,
    volume: int,
    is_partial: bool = False,
    volume_reliable: bool = True,
) -> CompletedOneMinuteCandle:
    return CompletedOneMinuteCandle(
        instrument_token=TOKEN,
        candle_time=datetime(2026, 7, 22, hour, minute, 0, tzinfo=_IST),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=volume,
        tick_count=10,
        volume_reliable=volume_reliable,
        is_partial=is_partial,
        has_full_minute_coverage=True,
        completion_reason="minute_transition",
    )


def _spike(
    *,
    minute: int = 10 * 60 + 32,
    direction: str = "UP",
    high: float = 110.0,
    low: float = 100.0,
    open_: float = 101.0,
    close: float = 109.0,
) -> IntradaySpikeEvent:
    hour, mins = divmod(minute, 60)
    ct = datetime(2026, 7, 22, hour, mins, 0, tzinfo=_IST)
    features = SpikeFeatures(
        instrument_token=TOKEN,
        minute_of_day=minute,
        session_date=SESSION,
        baseline_as_of_date="2026-07-21",
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000,
        tick_count=20,
        volume_reliable=True,
        absolute_return=abs(close - open_) / open_,
        signed_return=(close - open_) / open_,
        direction=direction,  # type: ignore[arg-type]
        relative_volume_median=3.0,
        relative_volume_trimmed=3.0,
        abs_return_vs_baseline=3.0,
        body_ratio=0.8,
        close_location=0.9 if direction == "UP" else 0.1,
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
        session_date=SESSION,
        rule_version="intraday_spike_v1",
        direction=direction,  # type: ignore[arg-type]
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000,
        features=features,
        detected_at=datetime.now(timezone.utc),
        decision=SpikeDecision(
            accepted=True,
            rule_version="intraday_spike_v1",
            reasons=frozenset(),
        ),
    )


def _5m(
    *,
    hour: int,
    minute: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: int = 500,
) -> CompletedFiveMinuteCandle:
    return CompletedFiveMinuteCandle(
        instrument_token=TOKEN,
        candle_time=datetime(2026, 7, 22, hour, minute, 0, tzinfo=_IST),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        session_date=SESSION,
        constituent_count=5,
        all_volume_reliable=True,
        any_partial=False,
        all_full_coverage=True,
        tick_count=25,
    )


class TickNormalizedPriceTests(unittest.TestCase):
    def test_trigger_at_exactly_one_tick_above_swing(self) -> None:
        trigger_ticks, trigger_price = compute_trigger_ticks(
            direction="UP",
            swing_high=100.0,
            swing_low=None,
            tick_size=0.05,
            buffer_ticks=1,
        )
        self.assertEqual(trigger_ticks, price_to_ticks(100.05, 0.05))
        self.assertAlmostEqual(trigger_price, 100.05)
        self.assertTrue(
            price_reached_trigger(
                direction="UP",
                last_price=100.05,
                tick_size=0.05,
                trigger_price_ticks=trigger_ticks,
            )
        )
        self.assertFalse(
            price_reached_trigger(
                direction="UP",
                last_price=100.00,
                tick_size=0.05,
                trigger_price_ticks=trigger_ticks,
            )
        )

    def test_down_inclusive_reach(self) -> None:
        trigger_ticks, _ = compute_trigger_ticks(
            direction="DOWN",
            swing_high=None,
            swing_low=100.0,
            tick_size=0.05,
            buffer_ticks=1,
        )
        self.assertTrue(
            price_reached_trigger(
                direction="DOWN",
                last_price=99.95,
                tick_size=0.05,
                trigger_price_ticks=trigger_ticks,
            )
        )

    def test_volume_strict_greater(self) -> None:
        ok, avg, reason = volume_confirms(
            in_progress_volume=100,
            prior_volumes=(100, 100, 100),
            required_count=3,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "failed_breakout_volume_confirmation")
        ok2, _, reason2 = volume_confirms(
            in_progress_volume=101,
            prior_volumes=(100, 100, 100),
            required_count=3,
        )
        self.assertTrue(ok2)
        self.assertIsNone(reason2)

    def test_config_rejects_negative_buffer(self) -> None:
        with self.assertRaises(ValueError):
            IntradayContinuationRuleConfig(continuation_breakout_buffer_ticks=-1)


class SwingFreezeTests(unittest.TestCase):
    def test_swing_excludes_impulse_includes_ready_candle(self) -> None:
        seq = empty_sequence_after_impulse(110.0, 100.0)
        self.assertIsNone(seq.pullback_swing_high)
        self.assertIsNone(seq.pullback_swing_low)

        c1 = _5m(hour=10, minute=35, open_=109, high=109.0, low=104.0, close=105.0)
        seq = update_sequence(
            seq,
            direction="UP",
            candle=c1,
            impulse_high=110.0,
            impulse_low=100.0,
            spike_high=110.0,
            spike_low=100.0,
            ema20=None,
        )
        self.assertEqual(seq.pullback_swing_high, 109.0)
        self.assertEqual(seq.pullback_swing_low, 104.0)

        c2 = _5m(hour=10, minute=40, open_=105, high=108.5, low=104.5, close=106.0)
        seq = update_sequence(
            seq,
            direction="UP",
            candle=c2,
            impulse_high=110.0,
            impulse_low=100.0,
            spike_high=110.0,
            spike_low=100.0,
            ema20=None,
        )
        # READY-causing candle included; impulse 110 excluded.
        self.assertEqual(seq.pullback_swing_high, 109.0)
        self.assertEqual(seq.pullback_swing_low, 104.0)
        self.assertNotEqual(seq.pullback_swing_high, 110.0)


class ContinuationEngineIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "live.db"
        self.pb_writer = IntradayPullbackWriter(db_path=self.db)
        self.cont_writer = IntradayContinuationWriter(db_path=self.db)
        self.tick_sizes = TickSizeMap(by_token={TOKEN: TICK_SIZE}, by_symbol={SYMBOL: TICK_SIZE})
        self.triggered: list[ContinuationTriggeredEvent] = []
        self.rejected: list = []

        self.pullback = IntradayPullbackEngine(
            writer=self.pb_writer,
            on_pullback_ready=lambda c: self.continuation.on_pullback_ready(c),
            on_setup_terminal=lambda sid, reason: self.continuation.on_setup_terminal(
                sid, reason
            ),
        )
        self.continuation = IntradayContinuationEngine(
            writer=self.cont_writer,
            tick_sizes=self.tick_sizes,
            pullback_closer=self.pullback,
            on_triggered=self.triggered.append,
            on_rejected=self.rejected.append,
        )

    def tearDown(self) -> None:
        self.cont_writer.close()
        self.pb_writer.close()
        self._tmp.cleanup()

    def _drive_ready(self) -> str:
        self.pullback.on_spike(_spike(minute=10 * 60 + 32, high=110, low=100, close=109))
        self.pullback.on_five_minute_candle(
            _5m(hour=10, minute=30, open_=100, high=110, low=100, close=109)
        )
        self.pullback.on_five_minute_candle(
            _5m(hour=10, minute=35, open_=109, high=109, low=104, close=105)
        )
        self.pullback.on_five_minute_candle(
            _5m(hour=10, minute=40, open_=105, high=108, low=104, close=106)
        )
        setup_id = self.pullback.active_setup_id_for_token(TOKEN)
        # After READY auto-enters CONTINUATION_MONITORING — still active until close.
        self.assertIsNotNone(setup_id)
        assert setup_id is not None
        self.assertEqual(self.continuation.metrics.arms_created, 1)
        return setup_id

    def _seed_prior_volumes(self, volumes=(100, 100, 100)) -> None:
        for i, vol in enumerate(volumes):
            self.continuation.on_one_minute(
                _1m(hour=10, minute=40 + i, volume=vol)
            )

    def _seed_volume_baseline(self, *, hour: int, minute: int, baseline: int) -> None:
        # Establish last_valid in prior minute, then open new minute with reliable baseline.
        self.continuation.on_tick(
            _tick(
                sequence=1,
                price=100.0,
                hour=hour,
                minute=minute - 1 if minute > 0 else 0,
                second=50,
                volume_traded=baseline,
            )
        )
        self.continuation.on_tick(
            _tick(
                sequence=2,
                price=100.0,
                hour=hour,
                minute=minute,
                second=1,
                volume_traded=baseline,
            )
        )

    def test_reject_clears_active_and_allows_new_spike(self) -> None:
        setup_id = self._drive_ready()
        self._seed_prior_volumes((100, 100, 100))
        self._seed_volume_baseline(hour=10, minute=45, baseline=1000)

        # Reach trigger with weak in-progress volume (delta small).
        # swing high from pb candles = 109; trigger = 109.05
        self.continuation.on_tick(
            _tick(
                sequence=3,
                price=109.05,
                hour=10,
                minute=45,
                second=10,
                volume_traded=1050,  # in_progress=50 < avg 100
            )
        )
        self.assertEqual(len(self.rejected), 1)
        self.assertFalse(self.pullback.is_setup_active(setup_id))
        self.assertIsNone(self.pullback.active_setup_id_for_token(TOKEN))

        # New spike immediately allowed.
        before_ignored = self.pullback.metrics.spike_ignored_while_active
        self.pullback.on_spike(_spike(minute=11 * 60 + 2, high=120, low=110, close=119))
        self.assertEqual(self.pullback.metrics.spike_ignored_while_active, before_ignored)
        self.assertIsNotNone(self.pullback.active_setup_id_for_token(TOKEN))
        self.assertNotEqual(
            self.pullback.active_setup_id_for_token(TOKEN), setup_id
        )

    def test_trigger_clears_active(self) -> None:
        setup_id = self._drive_ready()
        self._seed_prior_volumes((100, 100, 100))
        self._seed_volume_baseline(hour=10, minute=45, baseline=1000)
        self.continuation.on_tick(
            _tick(
                sequence=3,
                price=109.05,
                hour=10,
                minute=45,
                second=10,
                volume_traded=1200,  # in_progress=200 > 100
            )
        )
        self.assertEqual(len(self.triggered), 1)
        self.assertFalse(self.pullback.is_setup_active(setup_id))
        self.assertIsNone(self.pullback.active_setup_id_for_token(TOKEN))

    def test_duplicate_close_idempotent(self) -> None:
        setup_id = self._drive_ready()
        self._seed_prior_volumes((100, 100, 100))
        self._seed_volume_baseline(hour=10, minute=45, baseline=1000)
        self.continuation.on_tick(
            _tick(
                sequence=3,
                price=109.05,
                hour=10,
                minute=45,
                second=10,
                volume_traded=1200,
            )
        )
        self.assertTrue(
            self.pullback.close_after_continuation_outcome(
                setup_id, "CONTINUATION_TRIGGERED"
            )
        )
        self.assertTrue(
            self.pullback.close_after_continuation_outcome(
                setup_id, "CONTINUATION_TRIGGERED"
            )
        )

    def test_unique_decision_pk(self) -> None:
        setup_id = self._drive_ready()
        self._seed_prior_volumes((100, 100, 100))
        self._seed_volume_baseline(hour=10, minute=45, baseline=1000)
        self.continuation.on_tick(
            _tick(
                sequence=3,
                price=109.05,
                hour=10,
                minute=45,
                second=10,
                volume_traded=1200,
            )
        )
        with self.assertRaises(ContinuationConflictError):
            self.cont_writer.insert_decision(
                setup_id=setup_id,
                continuation_rule_version="intraday_continuation_v1",
                decision_type="REJECTED",
                reason="failed_breakout_volume_confirmation",
            )

    def test_audit_write_fail_still_authoritative_and_clears_active(self) -> None:
        setup_id = self._drive_ready()
        self._seed_prior_volumes((100, 100, 100))
        self._seed_volume_baseline(hour=10, minute=45, baseline=1000)

        # Break audit persist after decision by closing pullback writer mid-flight
        # but keep in-memory close path: monkeypatch append_event to fail after clear.
        original_append = self.pb_writer.append_event
        calls = {"n": 0}

        def flaky_append(**kwargs):
            calls["n"] += 1
            # First call after clear is the audit — fail it.
            raise RuntimeError("simulated audit failure")

        self.pb_writer.append_event = flaky_append  # type: ignore[method-assign]
        self.continuation.on_tick(
            _tick(
                sequence=3,
                price=109.05,
                hour=10,
                minute=45,
                second=10,
                volume_traded=1200,
            )
        )
        self.pb_writer.append_event = original_append  # type: ignore[method-assign]

        self.assertEqual(len(self.triggered), 1)
        self.assertFalse(self.pullback.is_setup_active(setup_id))
        # Continuation decision row exists.
        conn = sqlite3.connect(self.db)
        row = conn.execute(
            "SELECT decision_type FROM live_continuation_decisions WHERE setup_id=?",
            (setup_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "TRIGGERED")


class TickSizeLoaderTests(unittest.TestCase):
    def test_missing_tick_size_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "inst.db"
            conn = sqlite3.connect(db)
            conn.execute(
                """
                CREATE TABLE nifty50_instruments (
                    tradingsymbol TEXT PRIMARY KEY,
                    instrument_token INTEGER NOT NULL,
                    exchange TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    instrument_data TEXT NOT NULL,
                    quote_data TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO nifty50_instruments VALUES (?,?,?,?,?,?)",
                ("FOO", 1, "NSE", "t", json.dumps({"tick_size": None}), None),
            )
            conn.commit()
            conn.close()
            with self.assertRaises(TickSizePreflightError):
                load_tick_size_map(db, required_tokens=[1])

    def test_loads_valid_tick_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "inst.db"
            conn = sqlite3.connect(db)
            conn.execute(
                """
                CREATE TABLE nifty50_instruments (
                    tradingsymbol TEXT PRIMARY KEY,
                    instrument_token INTEGER NOT NULL,
                    exchange TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    instrument_data TEXT NOT NULL,
                    quote_data TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO nifty50_instruments VALUES (?,?,?,?,?,?)",
                (
                    "RELIANCE",
                    TOKEN,
                    "NSE",
                    "t",
                    json.dumps({"tick_size": 0.05}),
                    None,
                ),
            )
            conn.commit()
            conn.close()
            m = load_tick_size_map(db, required_tokens=[TOKEN])
            self.assertEqual(m.get(TOKEN), 0.05)


class PipelineTickOrderTests(unittest.TestCase):
    def test_builder_before_tick_consumer(self) -> None:
        from live_candle_pipeline import LiveCandlePipeline
        from live_one_minute_candle_writer import LiveOneMinuteCandleWriter
        from market_data_coordinator import MarketDataCoordinator

        order: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "live.db"
            writer = LiveOneMinuteCandleWriter(
                db_path=db, token_to_symbol={TOKEN: SYMBOL}
            )

            def strat(c: CompletedOneMinuteCandle) -> None:
                order.append("strategy")

            coord = MarketDataCoordinator(
                candle_writer=writer, strategy_consumers=[strat]
            )

            def tick_consumer(t: TickEvent) -> None:
                order.append("tick_consumer")

            pipeline = LiveCandlePipeline(
                coordinator=coord, tick_consumers=[tick_consumer]
            )
            # Two ticks across minute boundary to complete first candle.
            pipeline.on_tick(
                _tick(sequence=1, price=100, hour=10, minute=0, second=10, volume_traded=10)
            )
            order.clear()
            pipeline.on_tick(
                _tick(sequence=2, price=101, hour=10, minute=1, second=1, volume_traded=20)
            )
            writer.close()
        # Completing tick: strategy (via coordinator) before tick_consumer.
        self.assertEqual(order, ["strategy", "tick_consumer"])


if __name__ == "__main__":
    unittest.main()
