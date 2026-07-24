"""Unit and state-machine tests for intraday pullback core."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from candle_aggregation import CompletedFiveMinuteCandle
from intraday_pullback_config import IntradayPullbackRuleConfig
from intraday_pullback_engine import IntradayPullbackEngine
from intraday_pullback_rules import evaluate_pullback_monitoring
from intraday_pullback_writer import IntradayPullbackWriter
from pullback_features import (
    classify_pullback_type,
    empty_sequence_after_impulse,
    retracement_percent,
    update_sequence,
)
from pullback_indicators import Ema20State, SessionVwapState
from pullback_types import PullbackFeatures, PullbackSequenceState
from spike_types import IntradaySpikeEvent, SpikeDecision, SpikeFeatures
from tick_event import IST

_IST = ZoneInfo(IST)
TOKEN = 42
SYMBOL = "RELIANCE"


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
        session_date="2026-07-22",
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
        session_date="2026-07-22",
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
        session_date="2026-07-22",
        constituent_count=5,
        all_volume_reliable=True,
        any_partial=False,
        all_full_coverage=True,
        tick_count=25,
    )


class RetracementAndTypeTests(unittest.TestCase):
    def test_bullish_retracement_percent(self) -> None:
        # impulse 100-110, pullback low 104 → 60%
        pct = retracement_percent(
            direction="UP",
            impulse_high=110,
            impulse_low=100,
            pullback_low=104,
            pullback_high=109,
        )
        self.assertAlmostEqual(pct or 0, 60.0)

    def test_bearish_retracement_percent(self) -> None:
        pct = retracement_percent(
            direction="DOWN",
            impulse_high=110,
            impulse_low=100,
            pullback_low=101,
            pullback_high=106,
        )
        self.assertAlmostEqual(pct or 0, 60.0)

    def test_ema_vs_shallow_classifier(self) -> None:
        self.assertEqual(
            classify_pullback_type(
                direction="UP", lowest_low=104.0, highest_high=109.0, ema20=105.0
            ),
            "EMA_PULLBACK",
        )
        self.assertEqual(
            classify_pullback_type(
                direction="UP", lowest_low=106.0, highest_high=109.0, ema20=105.0
            ),
            "SHALLOW_STRUCTURE_PULLBACK",
        )

    def test_inclusive_ready_boundaries(self) -> None:
        config = IntradayPullbackRuleConfig()
        seq = PullbackSequenceState(
            highest_high_since_impulse=109,
            lowest_low_since_impulse=107,  # 30% of 10-range from 110
            retracement_percent=30.0,
            deepest_retracement_percent=30.0,
            cumulative_pullback_volume=100,
            median_pullback_volume=50,
            number_of_opposing_candles=1,
            largest_opposing_body_ratio=0.4,
            last_close=107.5,
            pullback_candle_count=2,
            last_eval_5m_candle_time=None,
            spike_extreme_breached=False,
            spike_extreme_breached_at=None,
            ema20_value=100.0,
            ema20_interacted=False,
            volumes=(50, 50),
        )
        # Fix low for exact 30%: (110-107)/10*100 = 30
        features = PullbackFeatures(
            instrument_token=TOKEN,
            direction="UP",
            eval_5m_candle_time=datetime(2026, 7, 22, 10, 40, tzinfo=_IST),
            open=108,
            high=109,
            low=107,
            close=107.5,
            volume=50,
            impulse_5m_high=110,
            impulse_5m_low=100,
            impulse_range=10,
            spike_1m_high=109,
            spike_1m_low=100,
            retracement_percent=30.0,
            deepest_retracement_percent=30.0,
            ema20_value=100.0,
            ema20_interacted=False,
            vwap=None,
            spike_extreme_breached=False,
            impulse_close_break=False,
            pullback_candle_count=2,
        )
        d = evaluate_pullback_monitoring(features, seq, config)
        self.assertEqual(d.outcome, "pullback_ready")

        seq60 = PullbackSequenceState(**{**seq.__dict__, "deepest_retracement_percent": 60.0, "retracement_percent": 60.0, "lowest_low_since_impulse": 104.0})
        features60 = PullbackFeatures(**{**features.__dict__, "deepest_retracement_percent": 60.0, "retracement_percent": 60.0})
        self.assertEqual(
            evaluate_pullback_monitoring(features60, seq60, config).outcome,
            "pullback_ready",
        )


class EmaVwapTests(unittest.TestCase):
    def test_ema_seed_and_update(self) -> None:
        ema = Ema20State(period=5)
        ema.seed_from_closes([1, 2, 3, 4, 5], seed_session_date="2026-07-21")
        self.assertTrue(ema.available)
        prev = ema.ema
        assert prev is not None
        nxt = ema.update(6)
        self.assertIsNotNone(nxt)
        self.assertNotEqual(nxt, prev)

    def test_session_vwap(self) -> None:
        v = SessionVwapState()
        v.update(110, 100, 105, 100)
        self.assertAlmostEqual(v.vwap or 0, 105.0)


class PullbackEngineE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "live.db"
        self.writer = IntradayPullbackWriter(db_path=self.db)
        self.ready = []
        self.engine = IntradayPullbackEngine(
            writer=self.writer,
            on_pullback_ready=self.ready.append,
        )

    def tearDown(self) -> None:
        self.writer.close()
        self._tmp.cleanup()

    def _drive_bullish_ready(self) -> None:
        # Spike at 10:32 → impulse bucket 10:30
        self.engine.on_spike(_spike(minute=10 * 60 + 32, high=110, low=100, close=109))
        # Impulse 5m 10:30
        self.engine.on_five_minute_candle(
            _5m(hour=10, minute=30, open_=100, high=110, low=100, close=109)
        )
        # Pullback candles: need 2 bars with deepest retrace in 30-60%
        # Impulse range 10. Target low ~104 (60%) or 107 (30%)
        self.engine.on_five_minute_candle(
            _5m(hour=10, minute=35, open_=109, high=109.5, low=106, close=106.5)
        )
        # (110-106)/10 = 40% — still need candle 2
        self.engine.on_five_minute_candle(
            _5m(hour=10, minute=40, open_=106.5, high=107, low=105, close=105.5)
        )
        # deepest (110-105)/10 = 50%

    def test_bullish_shallow_or_ema_ready(self) -> None:
        self._drive_bullish_ready()
        self.assertEqual(len(self.ready), 1)
        rt = self.engine._active_by_token[TOKEN]
        self.assertEqual(rt.state, "CONTINUATION_MONITORING")
        self.assertIn(self.ready[0].pullback_type, ("EMA_PULLBACK", "SHALLOW_STRUCTURE_PULLBACK"))

    def test_one_active_setup_ignores_second_spike(self) -> None:
        self.engine.on_spike(_spike(minute=10 * 60 + 32))
        before = self.engine.metrics.setups_created
        self.engine.on_spike(_spike(minute=11 * 60 + 5, high=120, low=110, close=119))
        self.assertEqual(self.engine.metrics.setups_created, before)
        self.assertEqual(self.engine.metrics.spike_ignored_while_active, 1)

    def test_excessive_retracement_invalidates(self) -> None:
        self.engine.on_spike(_spike(minute=10 * 60 + 32, high=110, low=100, close=109))
        self.engine.on_five_minute_candle(
            _5m(hour=10, minute=30, open_=100, high=110, low=100, close=109)
        )
        self.engine.on_five_minute_candle(
            _5m(hour=10, minute=35, open_=109, high=109, low=102, close=103)
        )
        # (110-102)/10 = 80% > 60
        self.assertNotIn(TOKEN, self.engine._active_by_token)
        self.assertEqual(self.engine.metrics.invalidated, 1)

    def test_impulse_close_break_invalidates(self) -> None:
        self.engine.on_spike(_spike(minute=10 * 60 + 32, high=110, low=100, close=109))
        self.engine.on_five_minute_candle(
            _5m(hour=10, minute=30, open_=100, high=110, low=100, close=109)
        )
        self.engine.on_five_minute_candle(
            _5m(hour=10, minute=35, open_=109, high=109, low=98, close=99)
        )
        self.assertEqual(self.engine.metrics.invalidated, 1)

    def test_spike_extreme_breach_is_not_terminal(self) -> None:
        self.engine.on_spike(
            _spike(minute=10 * 60 + 32, high=110, low=105, close=109, open_=106)
        )
        self.engine.on_five_minute_candle(
            _5m(hour=10, minute=30, open_=100, high=110, low=100, close=109)
        )
        # Breach spike low 105 but stay above impulse low 100; retrace moderate
        self.engine.on_five_minute_candle(
            _5m(hour=10, minute=35, open_=109, high=109, low=104, close=106)
        )
        self.assertEqual(self.engine.metrics.spike_extreme_breach, 1)
        self.assertIn(TOKEN, self.engine._active_by_token)

    def test_expire_after_seven_without_ready(self) -> None:
        self.engine.on_spike(_spike(minute=10 * 60 + 32, high=110, low=100, close=109))
        self.engine.on_five_minute_candle(
            _5m(hour=10, minute=30, open_=100, high=110, low=100, close=109)
        )
        # Keep retracement shallow (<30%): low >= 107
        base = 10 * 60 + 35
        for i in range(7):
            m = base + i * 5
            hour, mins = divmod(m, 60)
            self.engine.on_five_minute_candle(
                _5m(hour=hour, minute=mins, open_=109, high=109.5, low=108, close=108.5)
            )
        self.assertEqual(self.engine.metrics.expired, 1)

    def test_trade_executed_terminals(self) -> None:
        self._drive_bullish_ready()
        setup_id = self.engine._active_by_token[TOKEN].setup.setup_id
        self.engine.on_trade_executed(setup_id, fill_id="fill-1")
        self.assertEqual(self.engine.metrics.traded, 1)
        self.assertNotIn(TOKEN, self.engine._active_by_token)

    def test_continuation_attempt_does_not_close(self) -> None:
        self._drive_bullish_ready()
        setup_id = self.engine._active_by_token[TOKEN].setup.setup_id
        self.engine.record_continuation_attempt(setup_id, detail={"reason": "no_trigger"})
        self.engine.record_continuation_attempt(setup_id, detail={"reason": "no_trigger"})
        self.assertEqual(self.engine.metrics.continuation_attempts, 2)
        self.assertEqual(
            self.engine._active_by_token[TOKEN].state, "CONTINUATION_MONITORING"
        )

    def test_new_spike_after_terminal(self) -> None:
        self.engine.on_spike(_spike(minute=10 * 60 + 32, high=110, low=100, close=109))
        self.engine.on_five_minute_candle(
            _5m(hour=10, minute=30, open_=100, high=110, low=100, close=109)
        )
        self.engine.on_five_minute_candle(
            _5m(hour=10, minute=35, open_=109, high=109, low=102, close=103)
        )
        self.assertEqual(self.engine.metrics.invalidated, 1)
        self.engine.on_spike(_spike(minute=11 * 60 + 2, high=120, low=110, close=119))
        self.assertEqual(self.engine.metrics.setups_created, 2)

    def test_writer_failure_degrades(self) -> None:
        self.writer.close()
        self.engine.on_spike(_spike())
        self.assertTrue(self.engine.degraded)
        self.assertGreaterEqual(self.engine.metrics.subsystem_degraded, 1)


class ConfigFreezeTests(unittest.TestCase):
    def test_config_frozen(self) -> None:
        cfg = IntradayPullbackRuleConfig()
        with self.assertRaises(Exception):
            cfg.minimum_retracement_percent = 10.0  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
