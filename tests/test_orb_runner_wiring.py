"""The ORB detector inside the observation runner: additive, never in the way."""
from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import live_observation_runner as runner
from live_candle_pipeline import LiveCandlePipeline
from orb_detector import STATE_COLLECTING
from tests.test_orb_baselines import sessions_before

DAY = "2026-09-25"
SYMBOLS = {1: "AAA", 2: "BBB"}


def build_history(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE candles (instrument_token INTEGER NOT NULL, tradingsymbol TEXT NOT NULL, "
        "candle_time TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, "
        "close REAL NOT NULL, volume INTEGER NOT NULL, PRIMARY KEY (instrument_token, candle_time))"
    )
    for day in sessions_before(DAY, 15):
        for hm in ("09:15", "09:16", "09:17", "09:18", "09:19", "15:29"):
            conn.execute("INSERT INTO candles VALUES (1, 'AAA', ?, 100, 110, 100, 105, 200)",
                         (f"{day}T{hm}:00+05:30",))
    conn.commit()
    conn.close()


class StartOrbDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.hist = self.root / "hist.db"
        build_history(self.hist)

    def start(self, **overrides):
        kwargs = dict(db_path=self.root / "live.db", historical_db=self.hist, session_date=DAY,
                      token_to_symbol=SYMBOLS, tick_sizes={1: 0.05, 2: 0.05})
        kwargs.update(overrides)
        out = io.StringIO()
        with redirect_stdout(out):
            writer, detector = runner.start_orb_detector(**kwargs)
        if writer is not None:
            self.addCleanup(writer.close)
        return writer, detector, out.getvalue()

    def test_starts_and_records_baselines(self) -> None:
        writer, detector, printed = self.start()
        self.assertIsNotNone(detector)
        self.assertEqual(detector.state, STATE_COLLECTING)
        self.assertIn("ORB baselines ready: 1 / 2 (history as of 2026-09-24)", printed)
        conn = sqlite3.connect(self.root / "live.db")
        rows = dict(conn.execute("SELECT tradingsymbol, ready FROM live_orb_baselines").fetchall())
        status = conn.execute("SELECT state FROM live_orb_status").fetchall()
        conn.close()
        self.assertEqual(rows, {"AAA": 1, "BBB": 0})
        self.assertEqual(status, [(STATE_COLLECTING,)])

    def test_no_history_still_starts_with_nothing_ready(self) -> None:
        writer, detector, printed = self.start(historical_db=self.root / "missing.db")
        self.assertIsNotNone(detector)
        self.assertIn("ORB baselines ready: 0 / 2", printed)

    def test_a_failure_leaves_observation_running_without_orb(self) -> None:
        blocked = self.root / "a-directory"
        blocked.mkdir()
        with self.assertLogs(runner.logger, level="ERROR"):
            writer, detector, printed = self.start(db_path=blocked)
        self.assertEqual((writer, detector), (None, None))
        self.assertIn("ORB detector: OFF", printed)

    def test_any_surprise_is_contained(self) -> None:
        with mock.patch.object(runner, "OrbDetector", side_effect=RuntimeError("boom")):
            with self.assertLogs(runner.logger, level="ERROR"):
                writer, detector, printed = self.start()
        self.assertEqual((writer, detector), (None, None))
        # The writer it had opened is closed again.
        self.assertIn("ORB detector: OFF", printed)


class PipelineIsolationTests(unittest.TestCase):
    def test_a_raising_tick_consumer_cannot_stop_the_ones_before_it(self) -> None:
        # ORB is registered after continuation; the pipeline isolates each one.
        seen = []
        coordinator = mock.Mock()
        pipeline = LiveCandlePipeline(
            coordinator=coordinator,
            tick_consumers=[lambda tick: seen.append("continuation"),
                            lambda tick: (_ for _ in ()).throw(RuntimeError("orb broke"))],
        )
        pipeline._builder = mock.Mock()  # noqa: SLF001 - no candles needed here
        tick = mock.Mock(instrument_token=1, sequence=1)
        with self.assertLogs("live_candle_pipeline", level="ERROR"):
            pipeline.on_tick(tick)
            pipeline.on_tick(tick)
        self.assertEqual(seen, ["continuation", "continuation"])
        self.assertEqual(pipeline.tick_consumer_failures, 2)


if __name__ == "__main__":
    unittest.main()
