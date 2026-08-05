"""Tests for session coverage queries."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from api.queries.radar import fetch_coverage

SESSION_DATE = "2026-08-04"
RULE_VERSION = "v1"


def _create_live_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
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


def _insert_arm_and_decision(
    conn: sqlite3.Connection,
    setup_id: str,
    symbol: str,
    decision_type: str,
    session_date: str = SESSION_DATE,
) -> None:
    conn.execute(
        """
        INSERT INTO live_continuation_arms (
            setup_id, continuation_rule_version, instrument_token, tradingsymbol,
            session_date, direction, pullback_swing_high, pullback_swing_low,
            tick_size, buffer_ticks, trigger_price, trigger_price_ticks,
            pullback_type, ready_5m_candle_time, armed_at, payload_json
        ) VALUES (?, ?, ?, ?, ?, 'UP', 100, 90, 0.05, 1, 101, 2020, NULL, NULL, '2026-08-04T10:00:00', '{}')
        """,
        (setup_id, RULE_VERSION, 1000, symbol, session_date),
    )
    conn.execute(
        """
        INSERT INTO live_continuation_decisions (
            setup_id, continuation_rule_version, decision_type, reason,
            trigger_tick_sequence, trigger_exchange_ts, last_price, last_price_ticks,
            breakout_candle_time, breakout_candle_volume, avg_prior_3_1m_volume,
            volume_ok, volume_reliable, payload_json, created_at
        ) VALUES (?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, '{}', '2026-08-04T10:05:00')
        """,
        (setup_id, RULE_VERSION, decision_type),
    )


class FetchCoverageContinuationTests(unittest.TestCase):
    def test_continuation_successful_and_failed_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            live_db = Path(tmp) / "live.db"
            baselines_db = Path(tmp) / "baselines.db"
            conn = sqlite3.connect(live_db)
            try:
                _create_live_schema(conn)
                _insert_arm_and_decision(conn, "setup-1", "BAJFINANCE", "TRIGGERED")
                _insert_arm_and_decision(conn, "setup-2", "BAJAJ-AUTO", "REJECTED")
                _insert_arm_and_decision(conn, "setup-3", "RELIANCE", "TRIGGERED")
                _insert_arm_and_decision(conn, "setup-4", "OTHER", "DISARMED")
                _insert_arm_and_decision(
                    conn, "setup-5", "TCS", "TRIGGERED", session_date="2026-08-03"
                )
                conn.commit()
            finally:
                conn.close()

            coverage = fetch_coverage(live_db, baselines_db, SESSION_DATE)

        self.assertEqual(coverage["continuation_successful"], 2)
        self.assertEqual(coverage["continuation_failed"], 1)
        self.assertEqual(coverage["continuation_decisions"], 4)


if __name__ == "__main__":
    unittest.main()
