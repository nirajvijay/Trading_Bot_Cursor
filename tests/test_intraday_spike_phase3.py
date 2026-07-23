"""Phase 3 tests: IntradaySpikeWriter + IntradaySpikeDetector."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from baseline_generator import init_baselines_db
from baseline_store import BaselineStore
from candle_aggregation import CompletedOneMinuteCandle
from intraday_spike_config import IntradaySpikeRuleConfig
from intraday_spike_detector import IntradaySpikeDetector
from intraday_spike_writer import (
    IntradaySpikeWriter,
    SpikeConflictError,
)
from live_one_minute_candle_writer import LiveOneMinuteCandleWriter
from spike_types import (
    IntradaySpikeEvent,
    SpikeDecision,
    SpikeFeatures,
)
from tick_event import IST

_IST = ZoneInfo(IST)
_TOKEN = 738561
_SYMBOL = "RELIANCE"


def _ist(hour: int, minute: int) -> datetime:
    return datetime(2026, 7, 23, hour, minute, 0, tzinfo=_IST)


def _candle(
    *,
    hour: int = 10,
    minute: int = 30,
    open_: float = 100.0,
    high: float = 102.0,
    low: float = 99.0,
    close: float = 101.8,
    volume: int = 20_000,
    is_partial: bool = False,
    has_full_minute_coverage: bool = True,
    volume_reliable: bool = True,
    completion_reason: str = "minute_transition",
) -> CompletedOneMinuteCandle:
    return CompletedOneMinuteCandle(
        instrument_token=_TOKEN,
        candle_time=_ist(hour, minute),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        tick_count=40,
        volume_reliable=volume_reliable,
        completion_reason=completion_reason,  # type: ignore[arg-type]
        has_full_minute_coverage=has_full_minute_coverage,
        is_partial=is_partial,
    )


def _features(**overrides) -> SpikeFeatures:
    base = dict(
        instrument_token=_TOKEN,
        minute_of_day=630,
        session_date="2026-07-23",
        baseline_as_of_date="2026-07-22",
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.8,
        volume=20_000,
        tick_count=40,
        volume_reliable=True,
        absolute_return=0.018,
        signed_return=0.018,
        direction="UP",
        relative_volume_median=4.0,
        relative_volume_trimmed=4.1,
        abs_return_vs_baseline=3.0,
        body_ratio=0.75,
        close_location=0.933,
        median_volume=5_000.0,
        trimmed_mean_volume=4_800.0,
        median_abs_return=0.006,
        valid_session_count=21,
        is_reliable=True,
    )
    base.update(overrides)
    return SpikeFeatures(**base)  # type: ignore[arg-type]


def _event(*, detected_at: datetime | None = None, **feature_overrides) -> IntradaySpikeEvent:
    features = _features(**feature_overrides)
    return IntradaySpikeEvent(
        instrument_token=_TOKEN,
        tradingsymbol=_SYMBOL,
        candle_time=_ist(10, 30),
        session_date=features.session_date,
        rule_version="intraday_spike_v1",
        direction=features.direction,
        open=features.open,
        high=features.high,
        low=features.low,
        close=features.close,
        volume=features.volume,
        features=features,
        detected_at=detected_at or datetime(2026, 7, 23, 5, 0, 0, tzinfo=timezone.utc),
        decision=SpikeDecision(
            accepted=True,
            rule_version="intraday_spike_v1",
            reasons=frozenset(),
        ),
    )


def _insert_baseline(
    conn: sqlite3.Connection,
    *,
    minute_of_day: int = 630,
    as_of: str = "2026-07-22",
    median_volume: float = 5_000.0,
    trimmed_mean_volume: float = 4_800.0,
    median_abs_return: float = 0.0005,
    is_reliable: int = 1,
) -> None:
    conn.execute(
        """
        INSERT INTO baselines (
            instrument_token, tradingsymbol, minute_of_day,
            median_volume, trimmed_mean_volume, median_abs_return,
            valid_session_count, is_reliable, baseline_as_of_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _TOKEN,
            _SYMBOL,
            minute_of_day,
            median_volume,
            trimmed_mean_volume,
            median_abs_return,
            21,
            is_reliable,
            as_of,
        ),
    )
    conn.commit()


class IntradaySpikeWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "live.db"
        self.writer = IntradaySpikeWriter(
            db_path=self.db_path,
            token_to_symbol={_TOKEN: _SYMBOL},
        )

    def tearDown(self) -> None:
        self.writer.close()
        self._tmpdir.cleanup()

    def test_inserts_full_feature_row(self) -> None:
        self.writer.on_spike(_event())
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            """
            SELECT relative_volume_median, relative_volume_trimmed, absolute_return,
                   body_ratio, close_location, baseline_as_of_date, rule_version
            FROM live_intraday_spikes
            """
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 4.0)
        self.assertEqual(row[1], 4.1)
        self.assertEqual(row[2], 0.018)
        self.assertEqual(row[3], 0.75)
        self.assertAlmostEqual(row[4], 0.933)
        self.assertEqual(row[5], "2026-07-22")
        self.assertEqual(row[6], "intraday_spike_v1")
        self.assertEqual(self.writer.metrics.spikes_inserted, 1)

    def test_duplicate_identical_payload_ignored(self) -> None:
        first = _event(detected_at=datetime(2026, 7, 23, 5, 0, 0, tzinfo=timezone.utc))
        second = _event(detected_at=datetime(2026, 7, 23, 6, 0, 0, tzinfo=timezone.utc))
        self.writer.on_spike(first)
        self.writer.on_spike(second)
        self.assertEqual(self.writer.metrics.spikes_inserted, 1)
        self.assertEqual(self.writer.metrics.duplicates_ignored, 1)
        conn = sqlite3.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM live_intraday_spikes").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_conflict_on_divergent_payload(self) -> None:
        self.writer.on_spike(_event(close=101.8, absolute_return=0.018))
        with self.assertRaises(SpikeConflictError):
            self.writer.on_spike(
                _event(close=101.9, absolute_return=0.019, signed_return=0.019)
            )
        self.assertEqual(self.writer.metrics.conflicting_duplicates, 1)

    def test_shares_db_file_with_candle_writer(self) -> None:
        candle_writer = LiveOneMinuteCandleWriter(
            db_path=self.db_path,
            token_to_symbol={_TOKEN: _SYMBOL},
        )
        try:
            candle = _candle(
                open_=100.0, high=103.0, low=99.0, close=102.5, volume=20_000
            )
            candle_writer.on_candle(candle)
            self.writer.on_spike(_event())
            conn = sqlite3.connect(self.db_path)
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            joined = conn.execute(
                """
                SELECT s.rule_version, c.volume
                FROM live_intraday_spikes s
                JOIN live_1m_candles c
                  ON c.instrument_token = s.instrument_token
                 AND c.candle_time = s.candle_time
                """
            ).fetchone()
            conn.close()
            self.assertIn("live_1m_candles", tables)
            self.assertIn("live_intraday_spikes", tables)
            self.assertEqual(joined[0], "intraday_spike_v1")
            self.assertEqual(joined[1], 20_000)
        finally:
            candle_writer.close()


class IntradaySpikeDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.live_db = Path(self._tmpdir.name) / "live.db"
        self.baselines_db = Path(self._tmpdir.name) / "baselines.db"
        self.baselines_conn = init_baselines_db(self.baselines_db)
        _insert_baseline(self.baselines_conn, minute_of_day=630)
        # Also seed 09:30 and 14:00 for window edge tests if needed
        _insert_baseline(self.baselines_conn, minute_of_day=570)
        _insert_baseline(self.baselines_conn, minute_of_day=840)
        self.store = BaselineStore.load("2026-07-23", db_path=self.baselines_db)
        self.writer = IntradaySpikeWriter(
            db_path=self.live_db,
            token_to_symbol={_TOKEN: _SYMBOL},
        )
        self.emitted: list[IntradaySpikeEvent] = []

    def tearDown(self) -> None:
        self.writer.close()
        self.baselines_conn.close()
        self._tmpdir.cleanup()

    def _detector(self, **kwargs) -> IntradaySpikeDetector:
        return IntradaySpikeDetector(
            baseline_store=self.store,
            writer=self.writer,
            token_to_symbol={_TOKEN: _SYMBOL},
            on_spike=self.emitted.append,
            **kwargs,
        )

    def test_accepts_and_persists_passing_candle(self) -> None:
        # open=100, close=102, high=103, low=99 → body=2/4=0.5 is too low;
        # use close=102.2, high=103, low=99 → body=2.2/4=0.55 still low;
        # close=102.5, high=103, low=99 → body=2.5/4=0.625, close_loc=3.5/4=0.875
        detector = self._detector()
        detector.on_candle(
            _candle(open_=100.0, high=103.0, low=99.0, close=102.5, volume=20_000)
        )
        snap = detector.metrics.snapshot()
        self.assertEqual(snap.candles_seen, 1)
        self.assertEqual(snap.eligible_candles, 1)
        self.assertEqual(snap.accepted_spikes, 1)
        self.assertEqual(snap.rejected_spikes, 0)
        self.assertEqual(len(self.emitted), 1)
        self.assertEqual(self.writer.metrics.spikes_inserted, 1)

    def test_skips_partial(self) -> None:
        detector = self._detector()
        detector.on_candle(
            _candle(is_partial=True, has_full_minute_coverage=False)
        )
        self.assertEqual(detector.metrics.partial_skipped, 1)
        self.assertEqual(detector.metrics.accepted_spikes, 0)
        self.assertEqual(len(self.emitted), 0)

    def test_baseline_miss(self) -> None:
        detector = self._detector()
        detector.on_candle(_candle(hour=11, minute=0))  # minute 660, no baseline
        self.assertEqual(detector.metrics.baseline_miss, 1)
        self.assertEqual(detector.metrics.accepted_spikes, 0)

    def test_baseline_unreliable(self) -> None:
        _insert_baseline(
            self.baselines_conn,
            minute_of_day=660,
            is_reliable=0,
        )
        store = BaselineStore.load("2026-07-23", db_path=self.baselines_db)
        detector = IntradaySpikeDetector(
            baseline_store=store,
            writer=self.writer,
            token_to_symbol={_TOKEN: _SYMBOL},
            on_spike=self.emitted.append,
        )
        detector.on_candle(_candle(hour=11, minute=0))
        self.assertEqual(detector.metrics.baseline_unreliable, 1)

    def test_rejected_by_rules(self) -> None:
        detector = self._detector()
        # Low volume fails relative volume rules
        detector.on_candle(_candle(volume=100, close=101.8))
        self.assertEqual(detector.metrics.rejected_spikes, 1)
        self.assertEqual(detector.metrics.accepted_spikes, 0)

    def _passing_candle(self, **kwargs) -> CompletedOneMinuteCandle:
        defaults = dict(
            open_=100.0, high=103.0, low=99.0, close=102.5, volume=20_000
        )
        defaults.update(kwargs)
        return _candle(**defaults)  # type: ignore[arg-type]

    def test_writer_failure_is_non_fatal(self) -> None:
        detector = self._detector()
        detector.on_candle(self._passing_candle())
        self.assertEqual(detector.metrics.accepted_spikes, 1)

        self.writer.close()
        _insert_baseline(self.baselines_conn, minute_of_day=631)
        store = BaselineStore.load("2026-07-23", db_path=self.baselines_db)
        detector2 = IntradaySpikeDetector(
            baseline_store=store,
            writer=self.writer,
            token_to_symbol={_TOKEN: _SYMBOL},
        )
        # Should not raise despite closed writer
        detector2.on_candle(self._passing_candle(hour=10, minute=31))
        self.assertEqual(detector2.metrics.writer_failures, 1)
        self.assertEqual(detector2.metrics.accepted_spikes, 0)

    def test_restart_replay_idempotent(self) -> None:
        detector = self._detector()
        candle = self._passing_candle()
        detector.on_candle(candle)
        detector.on_candle(candle)
        self.assertEqual(detector.metrics.accepted_spikes, 2)
        self.assertEqual(self.writer.metrics.spikes_inserted, 1)
        self.assertEqual(self.writer.metrics.duplicates_ignored, 1)

    def test_outside_window_rejected(self) -> None:
        _insert_baseline(self.baselines_conn, minute_of_day=555)  # 09:15
        store = BaselineStore.load("2026-07-23", db_path=self.baselines_db)
        detector = IntradaySpikeDetector(
            baseline_store=store,
            writer=self.writer,
            token_to_symbol={_TOKEN: _SYMBOL},
        )
        detector.on_candle(self._passing_candle(hour=9, minute=15))
        self.assertEqual(detector.metrics.rejected_spikes, 1)


if __name__ == "__main__":
    unittest.main()
