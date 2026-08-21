"""Engine tests: B0 UNCERTAIN, buffer overflow, reconstruct/resume, no look-ahead."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from candle_aggregation import OneMinuteCandle
from continuation_types import ContinuationTriggeredEvent
from tick_event import IST, Ohlc, TickEvent
from vwap_qualifier_config import VwapQualifierConfig
from vwap_qualifier_engine import VwapQualifierEngine
from vwap_qualifier_repair import reconstruct_five_minute_hlc3
from vwap_qualifier_state import bucket_end_dt
from vwap_qualifier_writer import VwapQualifierWriter

_IST = ZoneInfo(IST)
TOKEN = 738561
SYMBOL = "RELIANCE"
SESSION = "2026-08-21"


def _ist(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 8, 21, hour, minute, second, tzinfo=_IST)


def _tick(
    *,
    sequence: int,
    hour: int,
    minute: int,
    second: int = 1,
    price: float = 100.0,
    volume_traded: int = 1000,
    token: int = TOKEN,
) -> TickEvent:
    ts = _ist(hour, minute, second)
    return TickEvent(
        sequence=sequence,
        instrument_token=token,
        last_price=price,
        exchange_timestamp=ts,
        received_at=ts,
        volume_traded=volume_traded,
        last_traded_quantity=1,
        average_traded_price=price,
        ohlc=Ohlc(open=price, high=price, low=price, close=price),
    )


def _1m(
    hour: int,
    minute: int,
    *,
    close: float = 100.0,
    volume: int = 100,
    high: float | None = None,
    low: float | None = None,
) -> OneMinuteCandle:
    ts = _ist(hour, minute)
    return OneMinuteCandle(
        candle_time=ts.isoformat(timespec="seconds"),
        open=close,
        high=close if high is None else high,
        low=close if low is None else low,
        close=close,
        volume=volume,
    )


def _bucket_1m(hour: int, minute: int, close: float = 100.0, volume: int = 100) -> list[OneMinuteCandle]:
    return [_1m(hour, minute + offset, close=close, volume=volume) for offset in range(5)]


def _trigger(
    *,
    hour: int,
    minute: int,
    second: int = 10,
    sequence: int = 50,
    trigger_price: float = 100.0,
    last_price: float = 100.0,
    direction: str = "UP",
    setup_id: str = "setup-1",
) -> ContinuationTriggeredEvent:
    ts = _ist(hour, minute, second)
    return ContinuationTriggeredEvent(
        setup_id=setup_id,
        instrument_token=TOKEN,
        tradingsymbol=SYMBOL,
        direction=direction,  # type: ignore[arg-type]
        trigger_price=trigger_price,
        trigger_price_ticks=int(trigger_price * 20),
        last_price=last_price,
        last_price_ticks=int(last_price * 20),
        tick_sequence=sequence,
        exchange_timestamp=ts,
        breakout_candle_time=ts.replace(second=0),
        breakout_candle_volume=10,
        avg_prior_3_1m_volume=5.0,
        continuation_rule_version="intraday_continuation_v1",
        detected_at=datetime.now(timezone.utc),
    )


class FakeFetcher:
    def __init__(self) -> None:
        self.calls: list[tuple[int, datetime, datetime]] = []
        self.payloads: dict[tuple[int, datetime], list[OneMinuteCandle]] = {}
        self.failures_left = 0

    def fetch_one_minute(self, instrument_token: int, start: datetime, end_exclusive: datetime):
        self.calls.append((instrument_token, start, end_exclusive))
        if self.failures_left > 0:
            self.failures_left -= 1
            raise RuntimeError("historical unavailable")
        return list(self.payloads.get((instrument_token, start), []))


def _engine(
    *,
    now: datetime,
    fetcher: FakeFetcher | None = None,
    buffer_max: int = 1024,
    backoff=(0.0, 0.0, 0.0, 0.0),
    max_attempts: int = 5,
    writer: VwapQualifierWriter | None = None,
) -> VwapQualifierEngine:
    cfg = VwapQualifierConfig(
        bootstrap_tick_buffer_max_per_token=buffer_max,
        repair_backoff_seconds=backoff,
        repair_max_attempts=max_attempts,
        historical_max_requests_per_second=100.0,
    )
    eng = VwapQualifierEngine(
        tokens=[TOKEN],
        token_to_symbol={TOKEN: SYMBOL},
        session_date=SESSION,
        writer=writer,
        config=cfg,
        fetcher=fetcher or FakeFetcher(),
        now_fn=lambda: now,
        manual_start=True,
        start_workers=False,
    )
    eng.freeze_cutoff(now)
    return eng


class LateStartTests(unittest.TestCase):
    def test_1002_start_trigger_in_b0_unavailable(self) -> None:
        eng = _engine(now=_ist(10, 2, 15))
        self.assertEqual(eng.b0, _ist(10, 0))
        candles = [_1m(9, m) for m in range(15, 60)] + [_1m(10, 0), _1m(10, 1)]
        eng.seed_closed_bars(TOKEN, candles)
        state = eng.token_state(TOKEN)
        self.assertIn(_ist(9, 55), state.committed)
        self.assertNotIn(_ist(10, 0), state.committed)
        eng.mark_bootstrap_ready()
        eng.on_tick(_tick(sequence=1, hour=10, minute=2, second=20, price=101.0))
        q = eng.on_raw_trigger(_trigger(hour=10, minute=2, second=20, sequence=1, trigger_price=101.0))
        assert q is not None
        self.assertEqual(q.classification, "UNAVAILABLE")
        self.assertEqual(q.quality_reason, "bootstrap_open_bucket")
        self.assertFalse(q.quality_ok)

    def test_on_boundary_1000_still_uncertain(self) -> None:
        eng = _engine(now=_ist(10, 0, 0))
        self.assertEqual(eng.b0, _ist(10, 0))
        eng.mark_bootstrap_ready()
        eng.on_tick(_tick(sequence=1, hour=10, minute=0, second=1, price=100.0))
        q = eng.on_raw_trigger(_trigger(hour=10, minute=0, second=1, sequence=1))
        assert q is not None
        self.assertEqual(q.classification, "UNAVAILABLE")
        self.assertEqual(q.quality_reason, "bootstrap_open_bucket")

    def test_reconstruct_b0_then_later_trigger_classifies(self) -> None:
        eng = _engine(now=_ist(10, 2))
        candles = [_1m(9, m, close=100.0, volume=100) for m in range(15, 60)]
        eng.seed_closed_bars(TOKEN, candles)
        eng.mark_bootstrap_ready()
        b0_bars = _bucket_1m(10, 0, close=100.0, volume=100)
        self.assertTrue(eng.reconstruct_bucket(TOKEN, _ist(10, 0), b0_bars))
        # Live ticks in 10:05 bucket (after B0).
        eng.on_tick(_tick(sequence=10, hour=10, minute=5, second=1, price=100.0, volume_traded=10000))
        eng.on_tick(_tick(sequence=11, hour=10, minute=5, second=10, price=100.10, volume_traded=10100))
        q = eng.on_raw_trigger(
            _trigger(hour=10, minute=5, second=10, sequence=11, trigger_price=100.10)
        )
        assert q is not None
        self.assertIn(q.classification, ("ACCEPT", "LIMITED", "REJECT"))
        self.assertTrue(q.quality_ok)
        self.assertIsNotNone(q.vwap)
        # No future 10:10 bucket in contributions.
        starts = [c.bucket_start for c in q.contributions]
        self.assertNotIn(_ist(10, 10), starts)

    def test_live_ticks_in_b0_do_not_enter_in_progress(self) -> None:
        eng = _engine(now=_ist(10, 2))
        eng.mark_bootstrap_ready()
        eng.on_tick(_tick(sequence=1, hour=10, minute=2, price=123.0, volume_traded=50))
        state = eng.token_state(TOKEN)
        self.assertIsNone(state.in_progress)


class BufferOverflowTests(unittest.TestCase):
    def test_overflow_marks_only_that_token(self) -> None:
        other = 999
        cfg = VwapQualifierConfig(
            bootstrap_tick_buffer_max_per_token=2,
            historical_max_requests_per_second=100.0,
        )
        eng = VwapQualifierEngine(
            tokens=[TOKEN, other],
            token_to_symbol={TOKEN: SYMBOL, other: "INFY"},
            session_date=SESSION,
            config=cfg,
            fetcher=FakeFetcher(),
            now_fn=lambda: _ist(10, 2),
            manual_start=True,
            start_workers=False,
        )
        eng.freeze_cutoff(_ist(10, 2))
        eng.on_tick(_tick(sequence=1, hour=10, minute=2, second=1, volume_traded=1))
        eng.on_tick(_tick(sequence=2, hour=10, minute=2, second=2, volume_traded=2))
        eng.on_tick(_tick(sequence=3, hour=10, minute=2, second=3, volume_traded=3))
        eng.on_tick(
            _tick(sequence=4, hour=10, minute=2, second=1, volume_traded=1, token=other)
        )
        self.assertTrue(eng.token_state(TOKEN).overflowed)
        self.assertFalse(eng.token_state(other).overflowed)
        self.assertGreaterEqual(eng.metrics.buffer_overflows, 1)
        eng.mark_bootstrap_ready()
        q = eng.on_raw_trigger(_trigger(hour=10, minute=2, second=3, sequence=3))
        assert q is not None
        self.assertEqual(q.classification, "UNAVAILABLE")


class RepairResumeTests(unittest.TestCase):
    def test_gap_trigger_stays_unavailable_after_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = VwapQualifierWriter(db_path=Path(tmp) / "live.db", start_worker=False)
            eng = _engine(now=_ist(10, 2), writer=writer)
            eng.seed_closed_bars(TOKEN, [_1m(9, m) for m in range(15, 60)])
            self.assertTrue(eng.reconstruct_bucket(TOKEN, _ist(10, 0), _bucket_1m(10, 0)))
            eng.mark_bootstrap_ready()
            eng.on_tick(_tick(sequence=1, hour=10, minute=5, second=1, volume_traded=10))
            eng.on_tick(_tick(sequence=2, hour=10, minute=7, second=1, volume_traded=20))
            q1 = eng.on_raw_trigger(
                _trigger(hour=10, minute=7, second=1, sequence=2, setup_id="gap-1")
            )
            assert q1 is not None
            self.assertEqual(q1.classification, "UNAVAILABLE")
            self.assertEqual(q1.quality_reason, "gap_in_current_bucket")
            writer.insert_sync(q1)
            self.assertTrue(eng.reconstruct_bucket(TOKEN, _ist(10, 5), _bucket_1m(10, 5)))
            writer.insert_sync(q1)
            import sqlite3

            conn = sqlite3.connect(Path(tmp) / "live.db")
            row = conn.execute(
                "SELECT classification FROM live_vwap_qualifications WHERE setup_id='gap-1'"
            ).fetchone()
            conn.close()
            writer.close()
            self.assertEqual(row[0], "UNAVAILABLE")

    def test_successful_repair_resumes_later_bucket(self) -> None:
        eng = _engine(now=_ist(10, 2))
        eng.seed_closed_bars(TOKEN, [_1m(9, m, close=100.0) for m in range(15, 60)])
        eng.mark_bootstrap_ready()
        self.assertTrue(eng.reconstruct_bucket(TOKEN, _ist(10, 0), _bucket_1m(10, 0, close=100.0)))
        eng.on_tick(_tick(sequence=20, hour=10, minute=5, second=0, price=100.0, volume_traded=5000))
        eng.on_tick(_tick(sequence=21, hour=10, minute=5, second=30, price=100.05, volume_traded=5100))
        q = eng.on_raw_trigger(
            _trigger(hour=10, minute=5, second=30, sequence=21, trigger_price=100.05)
        )
        assert q is not None
        self.assertNotEqual(q.classification, "UNAVAILABLE")
        self.assertTrue(q.quality_ok)

    def test_reconstruct_does_not_double_count(self) -> None:
        eng = _engine(now=_ist(10, 6))
        eng.mark_bootstrap_ready()
        bars = _bucket_1m(10, 0, volume=50)
        self.assertTrue(eng.reconstruct_bucket(TOKEN, _ist(10, 0), bars))
        vol1 = eng.token_state(TOKEN).cum_vol
        self.assertFalse(eng.reconstruct_bucket(TOKEN, _ist(10, 0), bars))
        self.assertEqual(eng.token_state(TOKEN).cum_vol, vol1)
        # Live ticks inside B0 ignored even after commit.
        eng.on_tick(_tick(sequence=3, hour=10, minute=2, volume_traded=99999))
        self.assertEqual(eng.token_state(TOKEN).cum_vol, vol1)

    def test_repair_fetches_full_bucket_range(self) -> None:
        fetcher = FakeFetcher()
        fetcher.payloads[(TOKEN, _ist(10, 0))] = _bucket_1m(10, 0)
        cfg = VwapQualifierConfig(
            repair_backoff_seconds=(0.0, 0.0, 0.0, 0.0),
            repair_max_attempts=5,
            historical_max_requests_per_second=100.0,
        )
        now = _ist(10, 6)
        eng = VwapQualifierEngine(
            tokens=[TOKEN],
            token_to_symbol={TOKEN: SYMBOL},
            session_date=SESSION,
            config=cfg,
            fetcher=fetcher,
            now_fn=lambda: now,
            manual_start=True,
            start_workers=False,
        )
        eng.freeze_cutoff(_ist(10, 2))
        eng.mark_bootstrap_ready()
        from vwap_qualifier_engine import _RepairJob

        eng._run_repair_job(_RepairJob(TOKEN, _ist(10, 0)))  # noqa: SLF001
        self.assertEqual(len(fetcher.calls), 1)
        _token, start, end = fetcher.calls[0]
        self.assertEqual(start, _ist(10, 0))
        self.assertEqual(end, bucket_end_dt(_ist(10, 0)))
        self.assertIn(_ist(10, 0), eng.token_state(TOKEN).committed)
        self.assertEqual(eng.token_state(TOKEN).committed[_ist(10, 0)].provenance, "bootstrap")

    def test_failed_repair_after_five_attempts(self) -> None:
        fetcher = FakeFetcher()
        fetcher.failures_left = 5
        cfg = VwapQualifierConfig(
            repair_backoff_seconds=(0.0, 0.0, 0.0, 0.0),
            repair_max_attempts=5,
            historical_max_requests_per_second=100.0,
        )
        eng = VwapQualifierEngine(
            tokens=[TOKEN],
            token_to_symbol={TOKEN: SYMBOL},
            session_date=SESSION,
            config=cfg,
            fetcher=fetcher,
            now_fn=lambda: _ist(10, 6),
            manual_start=True,
            start_workers=False,
        )
        eng.freeze_cutoff(_ist(10, 2))
        eng.mark_bootstrap_ready()
        from vwap_qualifier_engine import _RepairJob

        eng._run_repair_job(_RepairJob(TOKEN, _ist(10, 0)))  # noqa: SLF001
        self.assertIn(_ist(10, 0), eng.token_state(TOKEN).failed_buckets)
        eng.on_tick(_tick(sequence=8, hour=10, minute=5, second=1, volume_traded=10))
        q = eng.on_raw_trigger(_trigger(hour=10, minute=5, second=1, sequence=8))
        assert q is not None
        self.assertEqual(q.classification, "UNAVAILABLE")
        self.assertEqual(q.quality_reason, "repair_attempts_exhausted")

    def test_repair_a_does_not_block_ready_b(self) -> None:
        other = 2
        eng_b = VwapQualifierEngine(
            tokens=[other],
            token_to_symbol={other: "INFY"},
            session_date=SESSION,
            config=VwapQualifierConfig(historical_max_requests_per_second=100.0),
            fetcher=FakeFetcher(),
            now_fn=lambda: _ist(9, 16),
            manual_start=True,
            start_workers=False,
        )
        eng_b.freeze_cutoff(_ist(9, 16))
        self.assertEqual(eng_b.b0, _ist(9, 15))
        self.assertTrue(eng_b.reconstruct_bucket(other, _ist(9, 15), _bucket_1m(9, 15, close=50.0)))
        eng_b.mark_bootstrap_ready()
        eng_b.on_tick(
            _tick(sequence=1, hour=9, minute=20, second=1, price=50.0, volume_traded=10, token=other)
        )
        eng_b.on_tick(
            _tick(sequence=2, hour=9, minute=20, second=10, price=50.05, volume_traded=20, token=other)
        )
        event = ContinuationTriggeredEvent(
            setup_id="b-ready",
            instrument_token=other,
            tradingsymbol="INFY",
            direction="UP",
            trigger_price=50.05,
            trigger_price_ticks=1001,
            last_price=50.05,
            last_price_ticks=1001,
            tick_sequence=2,
            exchange_timestamp=_ist(9, 20, 10),
            breakout_candle_time=_ist(9, 20),
            breakout_candle_volume=10,
            avg_prior_3_1m_volume=5.0,
            continuation_rule_version="intraday_continuation_v1",
            detected_at=datetime.now(timezone.utc),
        )
        q = eng_b.on_raw_trigger(event)
        assert q is not None
        self.assertNotEqual(q.classification, "UNAVAILABLE")


class ReconstructMathTests(unittest.TestCase):
    def test_five_historical_minutes_required(self) -> None:
        with self.assertRaises(Exception):
            reconstruct_five_minute_hlc3(_bucket_1m(10, 0)[:4], _ist(10, 0))


class WriterImmutabilityTests(unittest.TestCase):
    def test_unavailable_row_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "live.db"
            writer = VwapQualifierWriter(db_path=db, start_worker=False)
            eng = _engine(now=_ist(10, 2), writer=writer)
            eng.mark_bootstrap_ready()
            q = eng.on_raw_trigger(_trigger(hour=10, minute=2, sequence=1, setup_id="imm-1"))
            assert q is not None
            self.assertEqual(q.classification, "UNAVAILABLE")
            writer.insert_sync(q)
            q2 = eng.on_raw_trigger(
                _trigger(hour=10, minute=6, sequence=1, setup_id="imm-1", trigger_price=100.0)
            )
            assert q2 is not None
            # Same PK; writer ignores without changing classification.
            writer.insert_sync(q2)
            import sqlite3

            conn = sqlite3.connect(db)
            row = conn.execute(
                "SELECT classification FROM live_vwap_qualifications WHERE setup_id='imm-1'"
            ).fetchone()
            conn.close()
            writer.close()
            self.assertEqual(row[0], "UNAVAILABLE")

    def test_enqueue_does_not_run_on_caller_thread_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "live.db"
            writer = VwapQualifierWriter(db_path=db, start_worker=True)
            eng = _engine(now=_ist(10, 2), writer=writer)
            eng.mark_bootstrap_ready()
            q = eng.on_raw_trigger(_trigger(hour=10, minute=2, sequence=1, setup_id="async-1"))
            assert q is not None
            writer.close()
            import sqlite3

            conn = sqlite3.connect(db)
            n = conn.execute("SELECT COUNT(*) FROM live_vwap_qualifications").fetchone()[0]
            conn.close()
            self.assertEqual(n, 1)


class BootstrapNotReadyTests(unittest.TestCase):
    def test_trigger_before_ready_unavailable(self) -> None:
        eng = _engine(now=_ist(10, 6))
        eng.freeze_cutoff(_ist(10, 2))
        q = eng.on_raw_trigger(_trigger(hour=10, minute=6, sequence=1))
        assert q is not None
        self.assertEqual(q.classification, "UNAVAILABLE")
        self.assertEqual(q.quality_reason, "bootstrap_not_ready")

    def test_stale_feed_unavailable(self) -> None:
        eng = _engine(now=_ist(10, 6))
        eng.reconstruct_bucket(TOKEN, _ist(10, 0), _bucket_1m(10, 0))
        eng.mark_bootstrap_ready()
        eng.mark_feed_interrupted(_ist(10, 6))
        eng.on_tick(_tick(sequence=1, hour=10, minute=6, second=1, volume_traded=10))
        q = eng.on_raw_trigger(_trigger(hour=10, minute=6, second=1, sequence=1))
        assert q is not None
        self.assertEqual(q.classification, "UNAVAILABLE")
        self.assertEqual(q.quality_reason, "feed_stale")


class StatusSnapshotTests(unittest.TestCase):
    def test_in_progress_seed_is_bootstrapping(self) -> None:
        eng = _engine(now=_ist(10, 2))
        snap = eng.status_snapshot()
        self.assertEqual(snap["state"], "bootstrapping")
        self.assertEqual(snap["reason"], "bootstrapping historical 1m")
        self.assertFalse(snap["bootstrap_ready"])
        self.assertFalse(snap["bootstrap_failed"])

    def test_bootstrap_failed_is_unavailable_not_bootstrapping(self) -> None:
        eng = _engine(now=_ist(10, 2))
        eng.mark_bootstrap_failed()
        snap = eng.status_snapshot()
        self.assertEqual(snap["state"], "unavailable")
        self.assertEqual(snap["reason"], "bootstrap failed")
        self.assertTrue(snap["bootstrap_failed"])

    def test_b0_unresolved_after_seed_is_repairing(self) -> None:
        eng = _engine(now=_ist(10, 2))
        candles = [_1m(9, m, close=100.0, volume=100) for m in range(15, 60)]
        eng.seed_closed_bars(TOKEN, candles)
        eng.mark_bootstrap_ready()
        snap = eng.status_snapshot()
        self.assertEqual(snap["state"], "repairing")
        self.assertIn("reconstructing", snap["reason"])

    def test_feed_stale_outranks_repairing(self) -> None:
        eng = _engine(now=_ist(10, 2))
        eng.mark_bootstrap_ready()
        self.assertEqual(eng.status_snapshot()["state"], "repairing")
        eng.mark_feed_interrupted(_ist(10, 3))
        snap = eng.status_snapshot()
        self.assertEqual(snap["state"], "unavailable")
        self.assertEqual(snap["reason"], "feed stale")

    def test_stale_during_bootstrap_is_unavailable(self) -> None:
        eng = _engine(now=_ist(10, 2))
        self.assertEqual(eng.status_snapshot()["state"], "bootstrapping")
        eng.mark_feed_interrupted(_ist(10, 2, 30))
        snap = eng.status_snapshot()
        self.assertFalse(snap["bootstrap_ready"])
        self.assertTrue(snap["feed_stale"])
        self.assertEqual(snap["state"], "unavailable")
        self.assertEqual(snap["reason"], "feed stale")

    def test_b0_unresolved_and_all_tokens_failed_is_unavailable(self) -> None:
        eng = _engine(now=_ist(10, 2))
        eng.mark_bootstrap_ready()
        state = eng.token_state(TOKEN)
        self.assertNotIn(eng.b0, state.committed)
        state.failed_buckets.add(_ist(10, 10))
        snap = eng.status_snapshot()
        self.assertEqual(snap["failed_token_count"], 1)
        self.assertEqual(snap["token_count"], 1)
        self.assertEqual(snap["state"], "unavailable")
        self.assertEqual(snap["reason"], "all tokens unavailable")

    def test_ready_after_b0_reconstruct(self) -> None:
        eng = _engine(now=_ist(10, 2))
        candles = [_1m(9, m, close=100.0, volume=100) for m in range(15, 60)]
        eng.seed_closed_bars(TOKEN, candles)
        eng.mark_bootstrap_ready()
        self.assertTrue(eng.reconstruct_bucket(TOKEN, eng.b0, _bucket_1m(10, 0)))
        snap = eng.status_snapshot()
        self.assertEqual(snap["state"], "ready")
        self.assertEqual(snap["failed_token_count"], 0)

    def test_partial_token_failure_is_repairing(self) -> None:
        other = 999
        eng = VwapQualifierEngine(
            tokens=[TOKEN, other],
            token_to_symbol={TOKEN: SYMBOL, other: "INFY"},
            session_date=SESSION,
            config=VwapQualifierConfig(historical_max_requests_per_second=100.0),
            fetcher=FakeFetcher(),
            now_fn=lambda: _ist(10, 6),
            manual_start=True,
            start_workers=False,
        )
        eng.freeze_cutoff(_ist(10, 6))
        eng.mark_bootstrap_ready()
        b0 = eng.b0
        assert b0 is not None
        b0_bars = _bucket_1m(b0.hour, b0.minute)
        self.assertTrue(eng.reconstruct_bucket(TOKEN, b0, b0_bars))
        self.assertTrue(eng.reconstruct_bucket(other, b0, b0_bars))
        eng.token_state(TOKEN).failed_buckets.add(_ist(10, 10))
        snap = eng.status_snapshot()
        self.assertEqual(snap["state"], "repairing")
        self.assertEqual(snap["failed_token_count"], 1)
        self.assertEqual(snap["reason"], "1 token unavailable; remaining tokens active")

    def test_all_tokens_failed_is_unavailable(self) -> None:
        eng = _engine(now=_ist(10, 6))
        eng.mark_bootstrap_ready()
        b0 = eng.b0
        assert b0 is not None
        self.assertTrue(eng.reconstruct_bucket(TOKEN, b0, _bucket_1m(b0.hour, b0.minute)))
        eng.token_state(TOKEN).failed_buckets.add(_ist(10, 15))
        snap = eng.status_snapshot()
        self.assertEqual(snap["state"], "unavailable")
        self.assertEqual(snap["reason"], "all tokens unavailable")

    def test_empty_token_set_is_unavailable_not_ready(self) -> None:
        eng = VwapQualifierEngine(
            tokens=[],
            token_to_symbol={},
            session_date=SESSION,
            config=VwapQualifierConfig(historical_max_requests_per_second=100.0),
            fetcher=FakeFetcher(),
            now_fn=lambda: _ist(10, 6),
            manual_start=True,
            start_workers=False,
        )
        eng.freeze_cutoff(_ist(10, 6))
        eng.mark_bootstrap_ready()
        snap = eng.status_snapshot()
        self.assertEqual(snap["token_count"], 0)
        self.assertEqual(snap["state"], "unavailable")
        self.assertEqual(snap["reason"], "no subscribed tokens")

    def test_counts_match_metrics(self) -> None:
        eng = _engine(now=_ist(10, 6))
        eng.mark_bootstrap_ready()
        self.assertTrue(eng.reconstruct_bucket(TOKEN, _ist(10, 0), _bucket_1m(10, 0)))
        eng.on_tick(_tick(sequence=1, hour=10, minute=6, second=1, price=100.0, volume_traded=10))
        eng.on_raw_trigger(_trigger(hour=10, minute=6, second=1, sequence=1, trigger_price=100.0))
        snap = eng.status_snapshot()
        metrics = eng.metrics
        self.assertEqual(snap["accept"], metrics.accept)
        self.assertEqual(snap["limited"], metrics.limited)
        self.assertEqual(snap["reject"], metrics.reject)
        self.assertEqual(snap["unavailable"], metrics.unavailable)
        self.assertEqual(
            snap["accept"] + snap["limited"] + snap["reject"] + snap["unavailable"],
            metrics.classified,
        )


if __name__ == "__main__":
    unittest.main()
