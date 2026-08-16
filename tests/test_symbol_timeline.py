"""Tests for per-symbol event timeline queries."""

from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from api.queries.symbol_timeline import fetch_symbol_timeline

SESSION_DATE = "2026-08-01"
SYMBOL = "RELIANCE"


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE live_intraday_spikes (
            instrument_token INTEGER NOT NULL,
            tradingsymbol TEXT NOT NULL,
            candle_time TEXT NOT NULL,
            session_date TEXT NOT NULL,
            rule_version TEXT NOT NULL,
            direction TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume INTEGER NOT NULL,
            tick_count INTEGER NOT NULL,
            volume_reliable INTEGER NOT NULL,
            minute_of_day INTEGER NOT NULL,
            absolute_return REAL NOT NULL,
            signed_return REAL NOT NULL,
            relative_volume_median REAL NOT NULL,
            relative_volume_trimmed REAL NOT NULL,
            abs_return_vs_baseline REAL NOT NULL,
            body_ratio REAL NOT NULL,
            close_location REAL NOT NULL,
            baseline_as_of_date TEXT NOT NULL,
            median_volume REAL NOT NULL,
            trimmed_mean_volume REAL NOT NULL,
            valid_session_count INTEGER NOT NULL,
            is_reliable INTEGER NOT NULL,
            detected_at TEXT NOT NULL,
            PRIMARY KEY (instrument_token, candle_time, rule_version)
        );

        CREATE TABLE live_pullback_setups (
            setup_id TEXT PRIMARY KEY,
            instrument_token INTEGER NOT NULL,
            tradingsymbol TEXT NOT NULL,
            session_date TEXT NOT NULL,
            direction TEXT NOT NULL,
            spike_candle_time TEXT NOT NULL,
            spike_rule_version TEXT NOT NULL,
            spike_open REAL NOT NULL,
            spike_high REAL NOT NULL,
            spike_low REAL NOT NULL,
            spike_close REAL NOT NULL,
            spike_volume INTEGER NOT NULL,
            impulse_5m_candle_time TEXT NOT NULL,
            pullback_rule_version TEXT NOT NULL,
            previous_session_close REAL,
            session_open REAL,
            gap_absolute REAL,
            gap_percent REAL,
            gap_direction TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE live_pullback_setup_events (
            setup_id TEXT NOT NULL,
            sequence_number INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            resulting_state TEXT NOT NULL,
            evaluation_candle_time TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (setup_id, sequence_number)
        );

        CREATE TABLE live_continuation_arms (
            setup_id TEXT NOT NULL,
            continuation_rule_version TEXT NOT NULL,
            instrument_token INTEGER NOT NULL,
            tradingsymbol TEXT NOT NULL,
            session_date TEXT NOT NULL,
            direction TEXT NOT NULL,
            pullback_swing_high REAL,
            pullback_swing_low REAL,
            tick_size REAL NOT NULL,
            buffer_ticks INTEGER NOT NULL,
            trigger_price REAL NOT NULL,
            trigger_price_ticks INTEGER NOT NULL,
            pullback_type TEXT,
            ready_5m_candle_time TEXT,
            armed_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (setup_id, continuation_rule_version)
        );

        CREATE TABLE live_continuation_decisions (
            setup_id TEXT NOT NULL,
            continuation_rule_version TEXT NOT NULL,
            decision_type TEXT NOT NULL,
            reason TEXT,
            trigger_tick_sequence INTEGER,
            trigger_exchange_ts TEXT,
            last_price REAL,
            last_price_ticks INTEGER,
            breakout_candle_time TEXT,
            breakout_candle_volume INTEGER,
            avg_prior_3_1m_volume REAL,
            volume_ok INTEGER,
            volume_reliable INTEGER,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (setup_id, continuation_rule_version)
        );
        """
    )


def _insert_fixture(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO live_intraday_spikes (
            instrument_token, tradingsymbol, candle_time, session_date, rule_version,
            direction, open, high, low, close, volume, tick_count, volume_reliable,
            minute_of_day, absolute_return, signed_return, relative_volume_median,
            relative_volume_trimmed, abs_return_vs_baseline, body_ratio, close_location,
            baseline_as_of_date, median_volume, trimmed_mean_volume, valid_session_count,
            is_reliable, detected_at
        ) VALUES (
            738561, ?, '2026-08-01T09:20:00+00:00', ?, 'v1',
            'UP', 2500, 2510, 2495, 2508, 10000, 100, 1,
            350, 0.01, 0.01, 1.5, 1.4, 1.2, 0.8, 0.9,
            '2026-07-31', 5000, 4800, 20, 1, '2026-08-01T09:20:05+00:00'
        )
        """,
        (SYMBOL, SESSION_DATE),
    )

    conn.execute(
        """
        INSERT INTO live_pullback_setups (
            setup_id, instrument_token, tradingsymbol, session_date, direction,
            spike_candle_time, spike_rule_version, spike_open, spike_high, spike_low,
            spike_close, spike_volume, impulse_5m_candle_time, pullback_rule_version,
            created_at
        ) VALUES (
            'setup-cancelled', 738561, ?, ?, 'UP',
            '2026-08-01T09:20:00+00:00', 'v1', 2500, 2510, 2495, 2508, 10000,
            '2026-08-01T09:20:00+00:00', 'v1', '2026-08-01T09:21:00+00:00'
        )
        """,
        (SYMBOL, SESSION_DATE),
    )
    conn.executemany(
        """
        INSERT INTO live_pullback_setup_events (
            setup_id, sequence_number, event_type, resulting_state,
            evaluation_candle_time, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, '{}', ?)
        """,
        [
            (
                "setup-cancelled",
                1,
                "SETUP_CREATED",
                "SPIKE_ACCEPTED",
                None,
                "2026-08-01T09:21:00+00:00",
            ),
            (
                "setup-cancelled",
                2,
                "CANCELLED",
                "CANCELLED",
                None,
                "2026-08-01T09:22:00+00:00",
            ),
        ],
    )

    conn.execute(
        """
        INSERT INTO live_pullback_setups (
            setup_id, instrument_token, tradingsymbol, session_date, direction,
            spike_candle_time, spike_rule_version, spike_open, spike_high, spike_low,
            spike_close, spike_volume, impulse_5m_candle_time, pullback_rule_version,
            created_at
        ) VALUES (
            'setup-triggered', 738561, ?, ?, 'UP',
            '2026-08-01T10:00:00+00:00', 'v1', 2520, 2530, 2515, 2528, 12000,
            '2026-08-01T10:00:00+00:00', 'v1', '2026-08-01T10:01:00+00:00'
        )
        """,
        (SYMBOL, SESSION_DATE),
    )
    conn.executemany(
        """
        INSERT INTO live_pullback_setup_events (
            setup_id, sequence_number, event_type, resulting_state,
            evaluation_candle_time, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, '{}', ?)
        """,
        [
            (
                "setup-triggered",
                1,
                "SETUP_CREATED",
                "SPIKE_ACCEPTED",
                None,
                "2026-08-01T10:01:00+00:00",
            ),
            (
                "setup-triggered",
                2,
                "PULLBACK_READY",
                "PULLBACK_READY",
                None,
                "2026-08-01T10:05:00+00:00",
            ),
            (
                "setup-triggered",
                3,
                "CONTINUATION_TRIGGERED",
                "CONTINUATION_TRIGGERED",
                None,
                "2026-08-01T10:10:00+00:00",
            ),
        ],
    )
    conn.execute(
        """
        INSERT INTO live_continuation_arms (
            setup_id, continuation_rule_version, instrument_token, tradingsymbol,
            session_date, direction, tick_size, buffer_ticks, trigger_price,
            trigger_price_ticks, armed_at, payload_json
        ) VALUES (
            'setup-triggered', 'v1', 738561, ?, ?, 'UP',
            0.05, 1, 2535.0, 50700, '2026-08-01T10:06:00+00:00', '{}'
        )
        """,
        (SYMBOL, SESSION_DATE),
    )
    conn.execute(
        """
        INSERT INTO live_continuation_decisions (
            setup_id, continuation_rule_version, decision_type, reason,
            payload_json, created_at
        ) VALUES (
            'setup-triggered', 'v1', 'TRIGGERED', 'Breakout confirmed', '{}',
            '2026-08-01T10:10:00+00:00'
        )
        """
    )
    conn.commit()


class SymbolTimelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        _create_schema(self.conn)
        _insert_fixture(self.conn)
        self.db_path = Path(self._temp_db())

    def tearDown(self) -> None:
        self.conn.close()
        if self.db_path.exists():
            self.db_path.unlink()

    def _temp_db(self) -> str:
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".db")
        import os

        os.close(fd)
        disk = sqlite3.connect(path)
        disk.row_factory = sqlite3.Row
        _create_schema(disk)
        _insert_fixture(disk)
        disk.close()
        return path

    def test_timeline_returns_chronological_setups(self) -> None:
        timeline = fetch_symbol_timeline(self.db_path, SESSION_DATE, SYMBOL)

        self.assertEqual(timeline.symbol, SYMBOL)
        self.assertEqual(len(timeline.spikes), 1)
        self.assertEqual(timeline.spikes[0].direction, "UP")
        self.assertEqual(len(timeline.setups), 2)

        cancelled, triggered = timeline.setups
        self.assertEqual(cancelled.setup_id, "setup-cancelled")
        self.assertEqual(cancelled.status, "cancelled")
        self.assertEqual(len(cancelled.events), 2)
        self.assertEqual(cancelled.events[-1].event_type, "CANCELLED")

        self.assertEqual(triggered.setup_id, "setup-triggered")
        self.assertEqual(triggered.status, "triggered")
        self.assertEqual(len(triggered.events), 3)
        self.assertIsNotNone(triggered.continuation)
        assert triggered.continuation is not None
        self.assertEqual(triggered.continuation.trigger_price, 2535.0)
        self.assertEqual(triggered.continuation.decision, "TRIGGERED")

    def test_empty_symbol_returns_empty_arrays(self) -> None:
        timeline = fetch_symbol_timeline(self.db_path, SESSION_DATE, "TCS")
        self.assertEqual(timeline.spikes, [])
        self.assertEqual(timeline.setups, [])


if __name__ == "__main__":
    unittest.main()
