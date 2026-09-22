"""Tests for the standalone VWAP pipeline health check."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vwap_health_check import check, write_health

SESSION_DATE = "2026-09-23"


def _prod_created_at(age_seconds: int) -> str:
    """Timestamp in the exact format intraday_continuation_writer writes.

    Production stores ISO-8601 with a 'T' separator and a '+00:00' offset
    (e.g. 2026-08-07T08:43:14+00:00), which does NOT sort against SQLite's own
    space-separated datetime() output. Fixtures must use this format or they
    silently stop guarding the grace-window filter.
    """
    return (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat(
        timespec="seconds"
    )


def _make_live_db(path: Path, *, triggered_old: int, triggered_fresh: int, qualified: int) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE live_continuation_arms (
                setup_id TEXT, continuation_rule_version TEXT, session_date TEXT
            );
            CREATE TABLE live_continuation_decisions (
                setup_id TEXT, continuation_rule_version TEXT,
                decision_type TEXT, created_at TEXT
            );
            CREATE TABLE live_vwap_qualifications (
                setup_id TEXT, session_date TEXT
            );
            """
        )
        for i in range(triggered_old):
            setup_id = f"old-{i}"
            conn.execute(
                "INSERT INTO live_continuation_arms VALUES (?, 'v1', ?)",
                (setup_id, SESSION_DATE),
            )
            conn.execute(
                "INSERT INTO live_continuation_decisions VALUES (?, 'v1', 'TRIGGERED', ?)",
                (setup_id, _prod_created_at(600)),
            )
        for i in range(triggered_fresh):
            setup_id = f"fresh-{i}"
            conn.execute(
                "INSERT INTO live_continuation_arms VALUES (?, 'v1', ?)",
                (setup_id, SESSION_DATE),
            )
            conn.execute(
                "INSERT INTO live_continuation_decisions VALUES (?, 'v1', 'TRIGGERED', ?)",
                (setup_id, _prod_created_at(0)),
            )
        for i in range(qualified):
            conn.execute(
                "INSERT INTO live_vwap_qualifications VALUES (?, ?)",
                (f"old-{i}", SESSION_DATE),
            )
        conn.commit()
    finally:
        conn.close()


def _make_status_file(path: Path, *, callback_failures: int, persist_failures: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "session_date": SESSION_DATE,
                "vwap_qualifier": {
                    "callback_failures": callback_failures,
                    "persist_failures": persist_failures,
                },
            }
        ),
        encoding="utf-8",
    )


class VwapHealthCheckTests(unittest.TestCase):
    def test_ok_when_every_old_trigger_has_a_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "live.db"
            status_path = root / "runner_status.json"
            _make_live_db(db_path, triggered_old=3, triggered_fresh=1, qualified=3)
            _make_status_file(status_path, callback_failures=0, persist_failures=0)

            result = check(live_db=db_path, status_file=status_path, session_date=SESSION_DATE)

            self.assertTrue(result.ok)
            self.assertEqual(result.stuck_count, 0)
            # The fresh trigger is inside the grace window and must not count.
            self.assertEqual(result.triggered_count, 3)

    def test_alarms_when_triggers_have_no_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "live.db"
            status_path = root / "runner_status.json"
            _make_live_db(db_path, triggered_old=4, triggered_fresh=0, qualified=0)
            _make_status_file(status_path, callback_failures=0, persist_failures=0)

            result = check(live_db=db_path, status_file=status_path, session_date=SESSION_DATE)

            self.assertFalse(result.ok)
            self.assertEqual(result.stuck_count, 4)
            self.assertIn("no VWAP verdict", result.reason or "")

    def test_alarms_on_callback_failures_even_if_counts_line_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "live.db"
            status_path = root / "runner_status.json"
            _make_live_db(db_path, triggered_old=2, triggered_fresh=0, qualified=2)
            _make_status_file(status_path, callback_failures=5, persist_failures=0)

            result = check(live_db=db_path, status_file=status_path, session_date=SESSION_DATE)

            self.assertFalse(result.ok)
            self.assertEqual(result.callback_failures, 5)

    def test_missing_db_reports_zero_counts_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "does-not-exist.db"
            status_path = root / "runner_status.json"

            result = check(live_db=db_path, status_file=status_path, session_date=SESSION_DATE)

            self.assertEqual(result.triggered_count, 0)
            self.assertEqual(result.qualified_count, 0)
            self.assertTrue(result.ok)

    def test_write_health_persists_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "live.db"
            status_path = root / "runner_status.json"
            out_path = root / "vwap_health.json"
            _make_live_db(db_path, triggered_old=1, triggered_fresh=0, qualified=1)
            _make_status_file(status_path, callback_failures=0, persist_failures=0)

            result = check(live_db=db_path, status_file=status_path, session_date=SESSION_DATE)
            write_health(result, out_path=out_path)

            written = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertTrue(written["ok"])
            self.assertEqual(written["session_date"], SESSION_DATE)


if __name__ == "__main__":
    unittest.main()
