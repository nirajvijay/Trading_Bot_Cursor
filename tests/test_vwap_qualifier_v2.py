"""Integration tests for vwap_qualifier_v2 classify + persist."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from candle_aggregation import CompletedOneMinuteCandle
from continuation_types import ContinuationArmedEvent, ContinuationTriggeredEvent
from vwap_historical_scheduler import VwapHistoricalScheduler
from vwap_qualifier_v2 import VwapQualifierV2
from vwap_qualifier_v2_writer import VwapQualifierV2Writer
from vwap_session_cache import SessionVwapCache, session_open_dt

IST = ZoneInfo("Asia/Kolkata")
TOKEN = 738561


def _trigger_event(
    *,
    setup_id: str = "setup-1",
    trigger_price: float = 100.0,
    trigger_ts: datetime,
) -> ContinuationTriggeredEvent:
    return ContinuationTriggeredEvent(
        setup_id=setup_id,
        instrument_token=TOKEN,
        tradingsymbol="RELIANCE",
        direction="UP",
        trigger_price=trigger_price,
        trigger_price_ticks=10000,
        last_price=trigger_price,
        last_price_ticks=10000,
        tick_sequence=1,
        exchange_timestamp=trigger_ts,
        breakout_candle_time=trigger_ts - timedelta(minutes=1),
        breakout_candle_volume=1000,
        avg_prior_3_1m_volume=500.0,
        continuation_rule_version="continuation_v1",
        detected_at=trigger_ts,
    )


def _fill_session_minutes(cache: SessionVwapCache, through: datetime) -> None:
    m = session_open_dt(cache.session_date)
    end = through.replace(second=0, microsecond=0)
    while m <= end:
        cache.on_live_candle(
            CompletedOneMinuteCandle(
                instrument_token=TOKEN,
                candle_time=m,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=1000,
                tick_count=10,
                volume_reliable=True,
                completion_reason="minute_transition",
                has_full_minute_coverage=True,
                is_partial=False,
            )
        )
        m += timedelta(minutes=1)


class VwapQualifierV2IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "live.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _engine(self) -> VwapQualifierV2:
        writer = VwapQualifierV2Writer(db_path=self.db)
        cache = SessionVwapCache("2026-09-01")
        fetcher = MagicMock()
        fetcher.fetch_one_minute.return_value = []
        scheduler = VwapHistoricalScheduler(
            cache=cache,
            session_date="2026-09-01",
            fetcher=fetcher,
        )
        return VwapQualifierV2(
            session_date="2026-09-01",
            token_to_symbol={TOKEN: "RELIANCE"},
            writer=writer,
            scheduler=scheduler,
            start_scheduler=False,
        )

    def test_accept_classify_and_persist(self) -> None:
        engine = self._engine()
        engine.on_armed(
            ContinuationArmedEvent(
                setup_id="setup-1",
                instrument_token=TOKEN,
                tradingsymbol="RELIANCE",
                session_date="2026-09-01",
                direction="UP",
                pullback_swing_high=None,
                pullback_swing_low=98.0,
                tick_size=0.05,
                buffer_ticks=1,
                trigger_price=100.0,
                trigger_price_ticks=2000,
                continuation_rule_version="continuation_v1",
                ready_5m_candle_time=None,
                armed_at=datetime(2026, 9, 1, 10, 0, tzinfo=IST),
            )
        )
        engine.registry.set_cache_state(TOKEN, "ready")
        trigger_ts = datetime(2026, 9, 1, 10, 6, 30, tzinfo=IST)
        _fill_session_minutes(engine.cache, trigger_ts - timedelta(minutes=1))
        result = engine.on_triggered(_trigger_event(trigger_price=100.0, trigger_ts=trigger_ts))
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.classification, "ACCEPT")
        self.assertEqual(result.risk_cap_inr, 900.0)
        conn = sqlite3.connect(self.db)
        row = conn.execute(
            "SELECT classification FROM live_vwap_qualifications WHERE setup_id=?",
            ("setup-1",),
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "ACCEPT")

    def test_warmup_concurrency_live_wins(self) -> None:
        cache = SessionVwapCache("2026-09-01")
        m = session_open_dt("2026-09-01")
        cache.apply_historical_minutes(TOKEN, [(m, 100, 101, 99, 100.0, 500)])
        cache.on_live_candle(
            CompletedOneMinuteCandle(
                instrument_token=TOKEN,
                candle_time=m,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=1000,
                tick_count=10,
                volume_reliable=True,
                completion_reason="minute_transition",
                has_full_minute_coverage=True,
                is_partial=False,
            )
        )
        snap = cache.build_snapshot(
            TOKEN,
            requested_cutoff=m + timedelta(minutes=2),
            cache_state="ready",
            feed_stale=False,
        )
        self.assertEqual(snap.live_minute_count, 1)
        self.assertGreater(snap.cum_volume, 500)


if __name__ == "__main__":
    unittest.main()
