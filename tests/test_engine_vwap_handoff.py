"""fetch_triggered_with_vwap_since: trigger + VWAP verdict in one read."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from trading_engine_handoff import VWAP_RULE_VERSION, fetch_triggered_with_vwap_since

SCHEMA = """
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

VWAP_SCHEMA = """
CREATE TABLE live_vwap_qualifications (
    setup_id TEXT NOT NULL,
    continuation_rule_version TEXT NOT NULL,
    vwap_rule_version TEXT NOT NULL,
    session_date TEXT NOT NULL,
    classification TEXT NOT NULL,
    PRIMARY KEY (session_date, setup_id, continuation_rule_version, vwap_rule_version)
);
"""


def _insert_triggered(conn: sqlite3.Connection, setup_id: str, *, created_at: str) -> None:
    conn.execute(
        """
        INSERT INTO live_continuation_arms (
            setup_id, continuation_rule_version, instrument_token, tradingsymbol,
            session_date, direction, pullback_swing_high, pullback_swing_low,
            tick_size, buffer_ticks, trigger_price, trigger_price_ticks,
            pullback_type, ready_5m_candle_time, armed_at, payload_json
        ) VALUES (?, 'v1', 1, 'AAA', '2026-08-17', 'UP', 109, 100, 1, 1, 110, 110,
                  NULL, NULL, '2026-08-17T10:00:00', '{}')
        """,
        (setup_id,),
    )
    conn.execute(
        """
        INSERT INTO live_continuation_decisions (
            setup_id, continuation_rule_version, decision_type, reason,
            trigger_tick_sequence, trigger_exchange_ts, last_price, last_price_ticks,
            breakout_candle_time, breakout_candle_volume, avg_prior_3_1m_volume,
            volume_ok, volume_reliable, payload_json, created_at
        ) VALUES (?, 'v1', 'TRIGGERED', NULL, 1, '2026-08-17T10:01:16', 110.2, 110,
                  '2026-08-17T10:01:00', 100, 80, 1, 1, '{}', ?)
        """,
        (setup_id, created_at),
    )


def _insert_vwap(
    conn: sqlite3.Connection,
    setup_id: str,
    classification: str,
    *,
    vwap_rule_version: str = VWAP_RULE_VERSION,
) -> None:
    conn.execute(
        """
        INSERT INTO live_vwap_qualifications (
            setup_id, continuation_rule_version, vwap_rule_version, session_date, classification
        ) VALUES (?, 'v1', ?, '2026-08-17', ?)
        """,
        (setup_id, vwap_rule_version, classification),
    )


class TriggeredWithVwapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.live = Path(self.tmp.name) / "live.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _build(self, *, with_vwap_table: bool) -> sqlite3.Connection:
        conn = sqlite3.connect(self.live)
        conn.executescript(SCHEMA)
        if with_vwap_table:
            conn.executescript(VWAP_SCHEMA)
        return conn

    def test_accept_and_limited_and_reject_join_correctly(self) -> None:
        conn = self._build(with_vwap_table=True)
        _insert_triggered(conn, "accepted", created_at="2026-08-17T04:40:00+00:00")
        _insert_triggered(conn, "limited", created_at="2026-08-17T04:40:01+00:00")
        _insert_triggered(conn, "rejected", created_at="2026-08-17T04:40:02+00:00")
        _insert_vwap(conn, "accepted", "ACCEPT")
        _insert_vwap(conn, "limited", "LIMITED")
        _insert_vwap(conn, "rejected", "REJECT")
        conn.commit()
        conn.close()

        rows = fetch_triggered_with_vwap_since(
            self.live, created_at_gte="2026-08-17T00:00:00+00:00"
        )
        by_id = {r.setup_id: r.vwap_classification for r in rows}
        self.assertEqual(
            by_id, {"accepted": "ACCEPT", "limited": "LIMITED", "rejected": "REJECT"}
        )

    def test_missing_vwap_row_leaves_classification_none(self) -> None:
        conn = self._build(with_vwap_table=True)
        _insert_triggered(conn, "pending", created_at="2026-08-17T04:40:00+00:00")
        conn.commit()
        conn.close()

        rows = fetch_triggered_with_vwap_since(
            self.live, created_at_gte="2026-08-17T00:00:00+00:00"
        )
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].vwap_classification)

    def test_wrong_vwap_rule_version_filtered_out(self) -> None:
        conn = self._build(with_vwap_table=True)
        _insert_triggered(conn, "versioned", created_at="2026-08-17T04:40:00+00:00")
        _insert_vwap(conn, "versioned", "ACCEPT", vwap_rule_version="vwap_qualifier_v1")
        conn.commit()
        conn.close()

        rows = fetch_triggered_with_vwap_since(
            self.live, created_at_gte="2026-08-17T00:00:00+00:00"
        )
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].vwap_classification)

    def test_missing_vwap_table_falls_back_to_trigger_only(self) -> None:
        conn = self._build(with_vwap_table=False)
        _insert_triggered(conn, "no_vwap_table", created_at="2026-08-17T04:40:00+00:00")
        conn.commit()
        conn.close()

        rows = fetch_triggered_with_vwap_since(
            self.live, created_at_gte="2026-08-17T00:00:00+00:00"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].setup_id, "no_vwap_table")
        self.assertIsNone(rows[0].vwap_classification)


if __name__ == "__main__":
    unittest.main()
