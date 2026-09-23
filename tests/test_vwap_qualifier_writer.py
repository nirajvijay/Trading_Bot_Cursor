"""VWAP qualifier writer: PriorityQueue wake, shutdown drain, PK migrate."""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from vwap_qualifier_types import VwapQualification
from vwap_qualifier_writer import (
    NEW_PK_COLUMNS,
    OLD_PK_COLUMNS,
    PRIORITY_NORMAL,
    PRIORITY_TRIGGER,
    VwapQualifierWriter,
    qualification_pk_columns,
)

_IST = ZoneInfo("Asia/Kolkata")
SESSION = "2026-08-21"
OLD_CREATE = """
CREATE TABLE live_vwap_qualifications (
    setup_id TEXT NOT NULL,
    continuation_rule_version TEXT NOT NULL,
    vwap_rule_version TEXT NOT NULL,
    instrument_token INTEGER NOT NULL,
    tradingsymbol TEXT NOT NULL,
    session_date TEXT NOT NULL,
    direction TEXT NOT NULL,
    trigger_price REAL NOT NULL,
    last_price REAL NOT NULL,
    trigger_tick_sequence INTEGER NOT NULL,
    trigger_exchange_ts TEXT NOT NULL,
    vwap REAL,
    gap REAL,
    classification TEXT NOT NULL,
    quality_ok INTEGER NOT NULL,
    quality_reason TEXT,
    vwap_provenance TEXT,
    bootstrap_cutoff_exchange_ts TEXT,
    completed_5m_count INTEGER NOT NULL,
    in_progress_bucket_start TEXT,
    in_progress_volume INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (setup_id, continuation_rule_version, vwap_rule_version)
);
"""


def _qual(
    *,
    setup_id: str = "s1",
    session_date: str = SESSION,
    classification: str = "ACCEPT",
    continuation_rule_version: str = "intraday_continuation_v1",
    vwap_rule_version: str = "vwap_qualifier_v1",
) -> VwapQualification:
    ts = datetime(2026, 8, 21, 10, 2, tzinfo=_IST)
    return VwapQualification(
        setup_id=setup_id,
        continuation_rule_version=continuation_rule_version,
        vwap_rule_version=vwap_rule_version,
        instrument_token=1,
        tradingsymbol="AAA",
        session_date=session_date,
        direction="UP",
        trigger_price=100.0,
        last_price=100.0,
        trigger_tick_sequence=1,
        trigger_exchange_ts=ts,
        vwap=100.0,
        gap=0.001,
        classification=classification,  # type: ignore[arg-type]
        quality_ok=True,
        quality_reason=None,
        vwap_provenance="live",
        bootstrap_cutoff_exchange_ts=None,
        completed_5m_count=1,
        in_progress_bucket_start=None,
        in_progress_volume=0,
        contributions=(),
        detected_at=datetime.now(timezone.utc),
    )


def _count(db: Path, setup_id: str | None = None) -> int:
    conn = sqlite3.connect(db)
    if setup_id is None:
        n = conn.execute("SELECT COUNT(*) FROM live_vwap_qualifications").fetchone()[0]
    else:
        n = conn.execute(
            "SELECT COUNT(*) FROM live_vwap_qualifications WHERE setup_id=?",
            (setup_id,),
        ).fetchone()[0]
    conn.close()
    return int(n)


class WriterPriorityTests(unittest.TestCase):
    def test_idle_enqueue_priority_commits_without_normal_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "live.db"
            writer = VwapQualifierWriter(db_path=db, start_worker=True)
            try:
                writer.enqueue_priority(_qual(setup_id="prio-idle"))
                deadline = time.monotonic() + 0.2
                while time.monotonic() < deadline and _count(db, "prio-idle") == 0:
                    time.sleep(0.01)
                self.assertEqual(_count(db, "prio-idle"), 1)
                self.assertEqual(_count(db), 1)
                self.assertLess(time.monotonic() + 0.2 - deadline, 0.25)
            finally:
                writer.close()

    def test_close_commits_queued_priority_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "live.db"
            writer = VwapQualifierWriter(db_path=db, start_worker=True)
            writer.enqueue_priority(_qual(setup_id="prio-close"))
            writer.close()
            self.assertEqual(_count(db, "prio-close"), 1)

    def test_priority_commits_before_queued_ordinary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "live.db"
            writer = VwapQualifierWriter(db_path=db, start_worker=True)
            order: list[str] = []
            hold = threading.Event()
            orig = writer._insert_row

            def gated(row: VwapQualification) -> bool:
                hold.wait(timeout=2.0)
                order.append(row.setup_id)
                return orig(row)

            writer._insert_row = gated  # type: ignore[method-assign]
            writer.enqueue(_qual(setup_id="normal-1"))
            time.sleep(0.05)
            writer.enqueue(_qual(setup_id="normal-2"))
            writer.enqueue_priority(_qual(setup_id="trigger-1"))
            hold.set()
            writer.close()
            self.assertIn("trigger-1", order)
            self.assertLess(order.index("trigger-1"), order.index("normal-2"))

    def test_enqueue_priority_tuple_beats_normal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "live.db"
            writer = VwapQualifierWriter(db_path=db, start_worker=False)
            writer.enqueue(_qual(setup_id="n"))
            writer.enqueue_priority(_qual(setup_id="p"))
            first = writer._queue.get_nowait()
            second = writer._queue.get_nowait()
            self.assertEqual(first[0], PRIORITY_TRIGGER)
            self.assertEqual(first[2].setup_id, "p")
            self.assertEqual(second[0], PRIORITY_NORMAL)
            self.assertEqual(second[2].setup_id, "n")
            writer.close()


class WriterPkMigrationTests(unittest.TestCase):
    def test_migrates_old_pk_before_worker_and_allows_two_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "live.db"
            conn = sqlite3.connect(db)
            conn.executescript(OLD_CREATE)
            conn.execute(
                """
                INSERT INTO live_vwap_qualifications (
                    setup_id, continuation_rule_version, vwap_rule_version,
                    instrument_token, tradingsymbol, session_date, direction,
                    trigger_price, last_price, trigger_tick_sequence, trigger_exchange_ts,
                    vwap, gap, classification, quality_ok, quality_reason, vwap_provenance,
                    bootstrap_cutoff_exchange_ts, completed_5m_count, in_progress_bucket_start,
                    in_progress_volume, payload_json, created_at
                ) VALUES ('same', 'v1', 'vwap_qualifier_v1', 1, 'AAA', '2026-08-17', 'UP',
                          100, 100, 1, 't', 100, 0.001, 'ACCEPT', 1, NULL, 'live',
                          NULL, 1, NULL, 0, '{}', 'c')
                """
            )
            conn.commit()
            self.assertEqual(qualification_pk_columns(conn), OLD_PK_COLUMNS)
            conn.close()
            writer = VwapQualifierWriter(db_path=db, start_worker=False)
            self.assertIsNone(writer._worker)
            self.assertEqual(qualification_pk_columns(writer._conn), NEW_PK_COLUMNS)
            writer.insert_sync(
                _qual(
                    setup_id="same",
                    session_date="2026-08-18",
                    continuation_rule_version="v1",
                )
            )
            writer.close()
            conn = sqlite3.connect(db)
            n = conn.execute(
                "SELECT COUNT(*) FROM live_vwap_qualifications WHERE setup_id='same'"
            ).fetchone()[0]
            dates = {
                r[0]
                for r in conn.execute(
                    "SELECT session_date FROM live_vwap_qualifications WHERE setup_id='same'"
                )
            }
            conn.close()
            self.assertEqual(n, 2)
            self.assertEqual(dates, {"2026-08-17", "2026-08-18"})

    def test_migration_failure_rolls_back_old_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "live.db"
            conn = sqlite3.connect(db)
            conn.executescript(OLD_CREATE)
            conn.execute(
                """
                INSERT INTO live_vwap_qualifications (
                    setup_id, continuation_rule_version, vwap_rule_version,
                    instrument_token, tradingsymbol, session_date, direction,
                    trigger_price, last_price, trigger_tick_sequence, trigger_exchange_ts,
                    vwap, gap, classification, quality_ok, quality_reason, vwap_provenance,
                    bootstrap_cutoff_exchange_ts, completed_5m_count, in_progress_bucket_start,
                    in_progress_volume, payload_json, created_at
                ) VALUES ('keep', 'v1', 'vwap_qualifier_v1', 1, 'AAA', '2026-08-17', 'UP',
                          100, 100, 1, 't', 100, 0.001, 'ACCEPT', 1, NULL, 'live',
                          NULL, 1, NULL, 0, '{}', 'c')
                """
            )
            conn.commit()
            conn.close()
            with patch.object(
                VwapQualifierWriter,
                "_copy_qualification_rows",
                side_effect=RuntimeError("copy failed"),
            ):
                with self.assertRaises(RuntimeError):
                    VwapQualifierWriter(db_path=db, start_worker=True)
            conn = sqlite3.connect(db)
            self.assertEqual(qualification_pk_columns(conn), OLD_PK_COLUMNS)
            n = conn.execute(
                "SELECT COUNT(*) FROM live_vwap_qualifications WHERE setup_id='keep'"
            ).fetchone()[0]
            conn.close()
            self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
