"""Heartbeat, crash-vs-stopped detection, and the live P&L mark file."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from engine_status import (
    HEARTBEAT_STALE_SECONDS,
    EngineStatus,
    HeartbeatWriter,
    LiveMarkWriter,
    assess,
    heartbeat_age_seconds,
    process_alive,
    read_json,
    read_live_marks,
    write_json_atomic,
)

NOW = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)


def status(**overrides) -> EngineStatus:
    values = dict(run_id="run-1", session_date="2026-09-22")
    values.update(overrides)
    return EngineStatus(**values)


def heartbeat_at(offset_seconds: float, **extra) -> dict:
    data = {
        "run_id": "run-1",
        "session_date": "2026-09-22",
        "state": "running",
        "pid": os.getpid(),
        "updated_at": (NOW - timedelta(seconds=offset_seconds)).isoformat(),
    }
    data.update(extra)
    return data


class StatusTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()


class AtomicWriteTests(StatusTestCase):
    def test_a_written_file_reads_back_intact(self) -> None:
        path = self.dir / "hb.json"
        write_json_atomic(path, {"a": 1, "b": "two"})
        self.assertEqual(read_json(path), {"a": 1, "b": "two"})

    def test_no_temp_files_are_left_behind(self) -> None:
        path = self.dir / "hb.json"
        for _ in range(5):
            write_json_atomic(path, {"n": 1})
        self.assertEqual([p.name for p in self.dir.iterdir()], ["hb.json"])

    def test_a_rewrite_fully_replaces_the_previous_content(self) -> None:
        path = self.dir / "hb.json"
        write_json_atomic(path, {"long": "x" * 500})
        write_json_atomic(path, {"short": 1})
        self.assertEqual(read_json(path), {"short": 1})

    def test_missing_and_corrupt_files_read_as_none(self) -> None:
        self.assertIsNone(read_json(self.dir / "absent.json"))
        bad = self.dir / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        self.assertIsNone(read_json(bad))

    def test_a_non_object_json_file_reads_as_none(self) -> None:
        path = self.dir / "list.json"
        path.write_text("[1,2,3]", encoding="utf-8")
        self.assertIsNone(read_json(path))

    def test_parent_directories_are_created(self) -> None:
        path = self.dir / "nested" / "deep" / "hb.json"
        write_json_atomic(path, {"ok": True})
        self.assertTrue(path.exists())


class HeartbeatWriterTests(StatusTestCase):
    def test_writing_stamps_the_time_and_pid(self) -> None:
        path = self.dir / "hb.json"
        writer = HeartbeatWriter(path)
        st = status()
        writer.write(st)
        data = read_json(path)
        assert data is not None
        self.assertTrue(data["updated_at"])
        self.assertEqual(data["pid"], os.getpid())

    def test_every_tick_overwrites_the_same_file(self) -> None:
        path = self.dir / "hb.json"
        writer = HeartbeatWriter(path)
        st = status()
        for i in range(1, 4):
            st.tick_count = i
            writer.write(st)
        data = read_json(path)
        assert data is not None
        self.assertEqual(data["tick_count"], 3)

    def test_live_pnl_is_not_a_heartbeat_field(self) -> None:
        # Deliberately scoped: P&L has its own file so the heartbeat's job
        # stays exactly what it already is.
        path = self.dir / "hb.json"
        HeartbeatWriter(path).write(status())
        data = read_json(path)
        assert data is not None
        self.assertNotIn("live_pnl", data)
        self.assertNotIn("total_pnl", data)

    def test_the_stop_note_records_why(self) -> None:
        path = self.dir / "hb.json"
        writer = HeartbeatWriter(path)
        writer.write_stop_note(status(), "eod_squareoff")
        data = read_json(path)
        assert data is not None
        self.assertTrue(data["stopped_on_purpose"])
        self.assertEqual(data["stop_reason"], "eod_squareoff")
        self.assertEqual(data["state"], "stopped")
        self.assertFalse(data["entries_allowed"])


class HeartbeatAgeTests(unittest.TestCase):
    def test_age_is_measured_from_updated_at(self) -> None:
        self.assertAlmostEqual(
            heartbeat_age_seconds(heartbeat_at(3.0), now=NOW), 3.0, places=3
        )

    def test_a_missing_heartbeat_has_no_age(self) -> None:
        self.assertIsNone(heartbeat_age_seconds(None, now=NOW))
        self.assertIsNone(heartbeat_age_seconds({}, now=NOW))

    def test_an_unparseable_timestamp_has_no_age(self) -> None:
        self.assertIsNone(
            heartbeat_age_seconds({"updated_at": "whenever"}, now=NOW)
        )

    def test_a_naive_timestamp_is_read_as_utc(self) -> None:
        naive = {"updated_at": (NOW - timedelta(seconds=2)).replace(tzinfo=None).isoformat()}
        self.assertAlmostEqual(heartbeat_age_seconds(naive, now=NOW), 2.0, places=3)


class LivenessTests(unittest.TestCase):
    def test_a_fresh_heartbeat_is_running(self) -> None:
        verdict = assess(heartbeat_at(0.5), now=NOW)
        self.assertEqual(verdict.state, "running")
        self.assertTrue(verdict.running)
        self.assertFalse(verdict.crashed)

    def test_silence_without_a_note_is_a_crash(self) -> None:
        # A crashed process cannot report its own crash, so absence is the
        # only available signal.
        verdict = assess(heartbeat_at(HEARTBEAT_STALE_SECONDS + 5), now=NOW)
        self.assertTrue(verdict.crashed)
        self.assertEqual(verdict.reason, "heartbeat_silent_without_stop_note")

    def test_silence_with_a_note_is_healthy(self) -> None:
        # The whole reason the note exists: three normal stops a day must not
        # train a human to ignore crash alarms.
        verdict = assess(
            heartbeat_at(
                600.0, stopped_on_purpose=True, stop_reason="eod_squareoff", state="stopped"
            ),
            now=NOW,
        )
        self.assertEqual(verdict.state, "stopped")
        self.assertEqual(verdict.reason, "eod_squareoff")
        self.assertFalse(verdict.crashed)

    def test_each_intentional_stop_reason_reads_as_stopped(self) -> None:
        for reason in ("daily_loss_breach", "eod_squareoff", "kill_all"):
            with self.subTest(reason=reason):
                verdict = assess(
                    heartbeat_at(999.0, stopped_on_purpose=True, stop_reason=reason),
                    now=NOW,
                )
                self.assertEqual(verdict.state, "stopped")
                self.assertEqual(verdict.reason, reason)

    def test_a_stop_note_is_believed_even_if_recent(self) -> None:
        verdict = assess(
            heartbeat_at(0.2, stopped_on_purpose=True, stop_reason="kill_all"), now=NOW
        )
        self.assertEqual(verdict.state, "stopped")

    def test_no_heartbeat_at_all_is_absent_not_crashed(self) -> None:
        # Never started is different from died.
        verdict = assess(None, now=NOW)
        self.assertEqual(verdict.state, "absent")
        self.assertFalse(verdict.crashed)

    def test_exactly_at_the_stale_boundary_is_still_running(self) -> None:
        verdict = assess(heartbeat_at(HEARTBEAT_STALE_SECONDS), now=NOW)
        self.assertEqual(verdict.state, "running")

    def test_just_past_the_boundary_is_crashed(self) -> None:
        verdict = assess(heartbeat_at(HEARTBEAT_STALE_SECONDS + 0.01), now=NOW)
        self.assertTrue(verdict.crashed)

    def test_the_threshold_is_well_clear_of_the_tick_rhythm(self) -> None:
        from engine_runloop import TICK_INTERVAL_SECONDS

        self.assertGreater(HEARTBEAT_STALE_SECONDS, 5 * TICK_INTERVAL_SECONDS)

    def test_the_pid_is_carried_through(self) -> None:
        verdict = assess(heartbeat_at(0.5), now=NOW)
        self.assertEqual(verdict.pid, os.getpid())


class ProcessLivenessTests(unittest.TestCase):
    def test_our_own_process_is_alive(self) -> None:
        self.assertTrue(process_alive(os.getpid()))

    def test_a_missing_pid_is_not_alive(self) -> None:
        self.assertFalse(process_alive(None))
        self.assertFalse(process_alive(0))
        self.assertFalse(process_alive(-1))

    def test_an_almost_certainly_dead_pid_is_not_alive(self) -> None:
        # A stale record must never block a legitimate restart forever, which
        # is why liveness is probed rather than trusted.
        self.assertFalse(process_alive(999_999_998))


class LiveMarkTests(StatusTestCase):
    def test_marks_are_written_with_a_total_and_a_timestamp(self) -> None:
        path = self.dir / "marks.json"
        LiveMarkWriter(path).write({"t1": 600.0, "t2": -150.0})
        marks = read_live_marks(path)
        self.assertEqual(marks["pnl"], {"t1": 600.0, "t2": -150.0})
        self.assertAlmostEqual(marks["total_pnl"], 450.0)
        self.assertTrue(marks["as_of"])
        self.assertTrue(marks["complete"])

    def test_a_missing_mark_makes_the_snapshot_incomplete(self) -> None:
        # So the desk can show it as partial rather than as a confident total.
        path = self.dir / "marks.json"
        LiveMarkWriter(path).write({"t1": 600.0, "t2": None})
        marks = read_live_marks(path)
        self.assertFalse(marks["complete"])
        self.assertAlmostEqual(marks["total_pnl"], 600.0)

    def test_no_positions_writes_a_zero_total(self) -> None:
        path = self.dir / "marks.json"
        LiveMarkWriter(path).write({})
        marks = read_live_marks(path)
        self.assertEqual(marks["total_pnl"], 0.0)

    def test_an_absent_mark_file_reads_as_empty_not_an_error(self) -> None:
        marks = read_live_marks(self.dir / "never_written.json")
        self.assertEqual(marks["pnl"], {})
        self.assertIsNone(marks["as_of"])

    def test_marks_live_in_their_own_file_not_the_heartbeat(self) -> None:
        hb = self.dir / "hb.json"
        marks = self.dir / "marks.json"
        HeartbeatWriter(hb).write(status())
        LiveMarkWriter(marks).write({"t1": 100.0})
        self.assertNotIn("pnl", json.loads(hb.read_text()))
        self.assertIn("pnl", json.loads(marks.read_text()))

    def test_rewriting_marks_every_tick_is_cheap_and_clean(self) -> None:
        path = self.dir / "marks.json"
        writer = LiveMarkWriter(path)
        for i in range(20):
            writer.write({"t1": float(i)})
        self.assertEqual(read_live_marks(path)["pnl"], {"t1": 19.0})
        self.assertEqual(len(list(self.dir.iterdir())), 1)


    def test_sources_and_feed_state_round_trip(self) -> None:
        path = self.dir / "marks.json"
        LiveMarkWriter(path).write(
            {"t1": 600.0, "t2": -150.0},
            sources={"t1": "ws", "t2": "kite_rest"},
            reasons={"t1": None, "t2": "tick_stale"},
            feed={"state": "fallback", "reason": "tick_stale", "last_tick_at": "2026-09-22T05:00:00+00:00"},
        )
        marks = read_live_marks(path)
        self.assertEqual(marks["source"], {"t1": "ws", "t2": "kite_rest"})
        self.assertEqual(marks["reason"]["t2"], "tick_stale")
        self.assertEqual(marks["feed"]["state"], "fallback")

    def test_an_old_marks_file_without_the_new_fields_still_reads(self) -> None:
        path = self.dir / "marks.json"
        path.write_text(json.dumps({"as_of": "x", "pnl": {"t1": 1.0}, "total_pnl": 1.0, "complete": True}))
        marks = read_live_marks(path)
        self.assertEqual((marks["source"], marks["reason"], marks["feed"]), ({}, {}, {}))
        self.assertEqual(marks["pnl"], {"t1": 1.0})

if __name__ == "__main__":
    unittest.main()
