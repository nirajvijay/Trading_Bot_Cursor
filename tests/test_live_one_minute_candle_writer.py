from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from candle_aggregation import CompletedOneMinuteCandle
from live_one_minute_candle_writer import (
    CandleConflictError,
    LiveOneMinuteCandleWriter,
    init_db,
    is_retryable_sqlite_error,
    is_unrecoverable_persistence_error,
)
from tick_event import IST

_IST = ZoneInfo(IST)
_TOKEN = 738561
_SYMBOL = "RELIANCE"


def _ist(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    second: int = 0,
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=_IST)


def _completed(
    *,
    candle_time: datetime,
    completion_reason: str = "minute_transition",
    has_full_minute_coverage: bool = True,
    is_partial: bool = False,
    open_: float = 100.0,
    high: float = 105.0,
    low: float = 99.0,
    close: float = 103.0,
    volume: int = 500,
    tick_count: int = 10,
    volume_reliable: bool = True,
    instrument_token: int = _TOKEN,
) -> CompletedOneMinuteCandle:
    return CompletedOneMinuteCandle(
        instrument_token=instrument_token,
        candle_time=candle_time,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        tick_count=tick_count,
        volume_reliable=volume_reliable,
        completion_reason=completion_reason,  # type: ignore[arg-type]
        has_full_minute_coverage=has_full_minute_coverage,
        is_partial=is_partial,
    )


class WriterTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test_live.db"
        self.writer = LiveOneMinuteCandleWriter(
            db_path=self.db_path,
            token_to_symbol={_TOKEN: _SYMBOL, 408065: "INFY"},
        )

    def tearDown(self) -> None:
        self.writer.close()
        self._tmpdir.cleanup()

    def _fetch_row(self, candle_time: str) -> tuple:
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(
                """
                SELECT tradingsymbol, open, high, low, close, volume,
                       tick_count, volume_reliable, completion_reason,
                       has_full_minute_coverage, is_partial, inserted_at
                FROM live_1m_candles
                WHERE instrument_token = ? AND candle_time = ?
                """,
                (_TOKEN, candle_time),
            ).fetchone()
        finally:
            conn.close()

    def _count_rows(self) -> int:
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute("SELECT COUNT(*) FROM live_1m_candles").fetchone()[0]
        finally:
            conn.close()


class InitDbTests(unittest.TestCase):
    def test_init_db_creates_schema_and_wal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "live.db"
            conn = init_db(db_path)
            try:
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                self.assertEqual(mode.lower(), "wal")
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                self.assertIn("live_1m_candles", tables)
                indexes = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='index'"
                    ).fetchall()
                }
                self.assertIn("idx_live_1m_is_partial", indexes)
                self.assertNotIn("idx_live_1m_token", indexes)
            finally:
                conn.close()


class SingleInsertTests(WriterTestCase):
    def test_single_insert_persists_all_fields(self) -> None:
        candle_time = _ist(2026, 7, 22, 10, 30, 0)
        candle = _completed(candle_time=candle_time)
        self.writer.on_candle(candle)
        row = self._fetch_row(candle_time.isoformat(timespec="seconds"))
        self.assertIsNotNone(row)
        self.assertEqual(row[0], _SYMBOL)
        self.assertEqual(row[1], 100.0)
        self.assertEqual(row[7], 1)
        self.assertEqual(row[8], "minute_transition")
        self.assertEqual(row[9], 1)
        self.assertEqual(row[10], 0)
        self.assertTrue(row[11].endswith("+00:00"))


class MultipleStocksTests(WriterTestCase):
    def test_multiple_stocks_and_minutes(self) -> None:
        times = [
            (_TOKEN, _ist(2026, 7, 22, 10, 30, 0)),
            (_TOKEN, _ist(2026, 7, 22, 10, 31, 0)),
            (408065, _ist(2026, 7, 22, 10, 30, 0)),
            (408065, _ist(2026, 7, 22, 10, 31, 0)),
        ]
        for token, candle_time in times:
            self.writer.on_candle(_completed(candle_time=candle_time, instrument_token=token))
        self.assertEqual(self._count_rows(), 4)


class DuplicateInsertTests(WriterTestCase):
    def test_identical_duplicate_is_ignored(self) -> None:
        candle = _completed(candle_time=_ist(2026, 7, 22, 10, 30, 0))
        self.writer.on_candle(candle)
        self.writer.on_candle(candle)
        self.assertEqual(self._count_rows(), 1)
        self.assertEqual(self.writer.metrics.candles_inserted, 1)
        self.assertEqual(self.writer.metrics.duplicates_ignored, 1)


class ConflictingDuplicateTests(WriterTestCase):
    def test_different_ohlcv_raises_conflict(self) -> None:
        candle_time = _ist(2026, 7, 22, 10, 30, 0)
        self.writer.on_candle(_completed(candle_time=candle_time, close=103.0))
        with self.assertRaises(CandleConflictError):
            self.writer.on_candle(_completed(candle_time=candle_time, close=104.0))
        self.assertEqual(self.writer.metrics.conflicting_duplicates, 1)
        row = self._fetch_row(candle_time.isoformat(timespec="seconds"))
        self.assertEqual(row[4], 103.0)

    def test_different_is_partial_raises_conflict(self) -> None:
        candle_time = _ist(2026, 7, 22, 10, 30, 0)
        self.writer.on_candle(
            _completed(candle_time=candle_time, is_partial=False, has_full_minute_coverage=True)
        )
        with self.assertRaises(CandleConflictError):
            self.writer.on_candle(
                _completed(
                    candle_time=candle_time,
                    is_partial=True,
                    has_full_minute_coverage=False,
                )
            )


class ValidationTests(WriterTestCase):
    def test_unknown_token_rejected(self) -> None:
        candle = _completed(
            candle_time=_ist(2026, 7, 22, 10, 30, 0),
            instrument_token=999999,
        )
        with self.assertRaises(ValueError):
            self.writer.on_candle(candle)
        self.assertEqual(self.writer.metrics.validation_errors, 1)

    def test_partial_without_coverage_must_be_partial(self) -> None:
        with self.assertRaises(ValueError):
            self.writer.on_candle(
                _completed(
                    candle_time=_ist(2026, 7, 22, 10, 30, 0),
                    has_full_minute_coverage=False,
                    is_partial=False,
                )
            )


class LockRetryTests(unittest.TestCase):
    def test_is_lock_error_recognizes_busy_and_locked(self) -> None:
        from live_one_minute_candle_writer import (
            SQLITE_BUSY,
            SQLITE_LOCKED,
            _is_lock_error,
        )

        for code in (SQLITE_BUSY, SQLITE_LOCKED):
            exc = sqlite3.OperationalError("database is locked")
            exc.sqlite_errorcode = code
            self.assertTrue(_is_lock_error(exc))

        exc = sqlite3.OperationalError("disk I/O error")
        exc.sqlite_errorcode = 10
        self.assertFalse(_is_lock_error(exc))


class CloseTests(WriterTestCase):
    def test_close_is_idempotent(self) -> None:
        self.writer.close()
        self.writer.close()

    def test_on_candle_after_close_raises(self) -> None:
        self.writer.close()
        with self.assertRaises(RuntimeError):
            self.writer.on_candle(_completed(candle_time=_ist(2026, 7, 22, 10, 30, 0)))


class RestartSafetyTests(unittest.TestCase):
    def test_reopen_and_duplicate_insert(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "live.db"
            token_map = {_TOKEN: _SYMBOL}
            candle = _completed(candle_time=_ist(2026, 7, 22, 10, 30, 0))
            writer1 = LiveOneMinuteCandleWriter(db_path=db_path, token_to_symbol=token_map)
            writer1.on_candle(candle)
            writer1.close()

            writer2 = LiveOneMinuteCandleWriter(db_path=db_path, token_to_symbol=token_map)
            writer2.on_candle(candle)
            writer2.close()
            self.assertEqual(writer2.metrics.duplicates_ignored, 1)


class PartialCandleFilterTests(WriterTestCase):
    def test_strategy_safe_query(self) -> None:
        self.writer.on_candle(
            _completed(
                candle_time=_ist(2026, 7, 22, 10, 30, 0),
                is_partial=False,
                has_full_minute_coverage=True,
            )
        )
        self.writer.on_candle(
            _completed(
                candle_time=_ist(2026, 7, 22, 10, 31, 0),
                is_partial=True,
                has_full_minute_coverage=False,
                completion_reason="shutdown_flush",
            )
        )
        conn = sqlite3.connect(self.db_path)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM live_1m_candles WHERE is_partial = 0"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 1)


class CrossThreadWriterTests(unittest.TestCase):
    def test_worker_thread_then_main_thread_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "live.db"
            writer = LiveOneMinuteCandleWriter(
                db_path=db_path,
                token_to_symbol={_TOKEN: _SYMBOL},
            )
            worker_time = _ist(2026, 7, 22, 10, 30, 0)
            main_time = _ist(2026, 7, 22, 10, 31, 0)
            worker_error: list[BaseException] = []

            def worker_write() -> None:
                try:
                    writer.on_candle(_completed(candle_time=worker_time))
                except BaseException as exc:
                    worker_error.append(exc)

            thread = threading.Thread(target=worker_write)
            thread.start()
            thread.join()
            self.assertEqual(worker_error, [])

            writer.on_candle(_completed(candle_time=main_time))
            writer.close()

            conn = sqlite3.connect(db_path)
            try:
                rows = conn.execute(
                    """
                    SELECT candle_time
                    FROM live_1m_candles
                    WHERE instrument_token = ?
                    ORDER BY candle_time
                    """,
                    (_TOKEN,),
                ).fetchall()
            finally:
                conn.close()

            self.assertEqual(len(rows), 2)
            self.assertEqual(
                rows[0][0],
                worker_time.isoformat(timespec="seconds"),
            )
            self.assertEqual(
                rows[1][0],
                main_time.isoformat(timespec="seconds"),
            )


class ConcurrentWriterTests(unittest.TestCase):
    def test_concurrent_writes_serialize_without_pk_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "live.db"
            writer = LiveOneMinuteCandleWriter(
                db_path=db_path,
                token_to_symbol={_TOKEN: _SYMBOL, 408065: "INFY"},
            )
            candle_specs = [
                (_TOKEN, _ist(2026, 7, 22, 10, 30, 0)),
                (_TOKEN, _ist(2026, 7, 22, 10, 31, 0)),
                (408065, _ist(2026, 7, 22, 10, 30, 0)),
                (408065, _ist(2026, 7, 22, 10, 31, 0)),
                (_TOKEN, _ist(2026, 7, 22, 10, 32, 0)),
                (408065, _ist(2026, 7, 22, 10, 32, 0)),
            ]
            errors: list[BaseException] = []
            lock = threading.Lock()

            def write_candle(token: int, candle_time: datetime) -> None:
                try:
                    writer.on_candle(
                        _completed(
                            candle_time=candle_time,
                            instrument_token=token,
                        )
                    )
                except BaseException as exc:
                    with lock:
                        errors.append(exc)

            threads = [
                threading.Thread(
                    target=write_candle,
                    args=(token, candle_time),
                )
                for token, candle_time in candle_specs
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(errors, [])
            writer.close()

            conn = sqlite3.connect(db_path)
            try:
                total = conn.execute(
                    "SELECT COUNT(*) FROM live_1m_candles"
                ).fetchone()[0]
                distinct_pk = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM (
                        SELECT instrument_token, candle_time
                        FROM live_1m_candles
                        GROUP BY instrument_token, candle_time
                    )
                    """
                ).fetchone()[0]
            finally:
                conn.close()

            self.assertEqual(total, len(candle_specs))
            self.assertEqual(distinct_pk, len(candle_specs))
            self.assertEqual(writer.metrics.candles_inserted, len(candle_specs))
            self.assertEqual(writer.metrics.duplicates_ignored, 0)
            self.assertEqual(writer.metrics.conflicting_duplicates, 0)
            self.assertEqual(writer.metrics.write_failures, 0)


class PersistenceErrorClassificationTests(unittest.TestCase):
    def test_conflict_is_unrecoverable(self) -> None:
        self.assertTrue(is_unrecoverable_persistence_error(CandleConflictError("conflict")))

    def test_validation_error_is_unrecoverable(self) -> None:
        self.assertTrue(is_unrecoverable_persistence_error(ValueError("invalid")))

    def test_writer_closed_runtime_error_is_unrecoverable(self) -> None:
        self.assertTrue(is_unrecoverable_persistence_error(RuntimeError("writer is closed")))

    def test_generic_runtime_error_is_not_unrecoverable(self) -> None:
        self.assertFalse(is_unrecoverable_persistence_error(RuntimeError("callback failed")))

    def test_busy_sqlite_error_is_retryable(self) -> None:
        exc = sqlite3.OperationalError("database is locked")
        exc.sqlite_errorcode = 5
        self.assertTrue(is_retryable_sqlite_error(exc))
        self.assertTrue(is_unrecoverable_persistence_error(exc))


if __name__ == "__main__":
    unittest.main()
