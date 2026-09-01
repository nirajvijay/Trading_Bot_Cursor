"""Handoff: poll TRIGGERED rows; stale / missing / duplicate handling."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from trading_engine_handoff import (
    VWAP_RULE_VERSION,
    VwapLookupError,
    fetch_triggered_since,
    fetch_vwap_classification,
)
from trading_engine_risk import size_new_trade
from trading_engine_store import TradingEngineStore
from trading_engine_types import DEFAULT_TOTAL_CAPITAL, TriggerCandidate

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


def _insert_triggered(
    conn: sqlite3.Connection,
    setup_id: str,
    *,
    created_at: str,
    swing_low: float | None = 100.0,
    swing_high: float | None = 109.0,
    decision: str = "TRIGGERED",
) -> None:
    conn.execute(
        """
        INSERT INTO live_continuation_arms (
            setup_id, continuation_rule_version, instrument_token, tradingsymbol,
            session_date, direction, pullback_swing_high, pullback_swing_low,
            tick_size, buffer_ticks, trigger_price, trigger_price_ticks,
            pullback_type, ready_5m_candle_time, armed_at, payload_json
        ) VALUES (?, 'v1', 1, 'AAA', '2026-08-17', 'UP', ?, ?, 1, 1, 110, 110,
                  NULL, NULL, '2026-08-17T10:00:00', '{}')
        """,
        (setup_id, swing_high, swing_low),
    )
    conn.execute(
        """
        INSERT INTO live_continuation_decisions (
            setup_id, continuation_rule_version, decision_type, reason,
            trigger_tick_sequence, trigger_exchange_ts, last_price, last_price_ticks,
            breakout_candle_time, breakout_candle_volume, avg_prior_3_1m_volume,
            volume_ok, volume_reliable, payload_json, created_at
        ) VALUES (?, 'v1', ?, NULL, 1, '2026-08-17T10:01:16', 110.2, 110,
                  '2026-08-17T10:01:00', 100, 80, 1, 1, '{}', ?)
        """,
        (setup_id, decision, created_at),
    )


class HandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.live = Path(self.tmp.name) / "live.db"
        conn = sqlite3.connect(self.live)
        conn.executescript(SCHEMA)
        _insert_triggered(conn, "fresh", created_at="2026-08-17T04:40:00+00:00")
        _insert_triggered(conn, "stale", created_at="2026-08-17T03:00:00+00:00")
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_stale_created_at_excluded(self) -> None:
        rows = fetch_triggered_since(
            self.live, created_at_gte="2026-08-17T04:30:00+00:00"
        )
        ids = [r.setup_id for r in rows]
        self.assertEqual(ids, ["fresh"])

    def test_duplicate_setup_not_inserted_twice(self) -> None:
        store = TradingEngineStore(Path(self.tmp.name) / "te.db")
        first = store.insert_candidate(
            setup_id="fresh",
            continuation_rule_version="v1",
            session_date="2026-08-17",
            symbol="AAA",
            instrument_token=1,
            direction="UP",
            entry_estimate=110,
            tick_size=1,
            trigger_time="t",
        )
        second = store.insert_candidate(
            setup_id="fresh",
            continuation_rule_version="v1",
            session_date="2026-08-17",
            symbol="AAA",
            instrument_token=1,
            direction="UP",
            entry_estimate=110,
            tick_size=1,
            trigger_time="t",
        )
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        store.close()

    def test_missing_swing_sizes_as_skipped(self) -> None:
        cand = TriggerCandidate(
            setup_id="x",
            continuation_rule_version="v1",
            session_date="2026-08-17",
            tradingsymbol="AAA",
            instrument_token=1,
            direction="UP",
            trigger_price=110,
            pullback_swing_high=109,
            pullback_swing_low=None,
            tick_size=1,
            buffer_ticks=1,
            trigger_exchange_ts="t",
            created_at="c",
        )
        decision = size_new_trade(cand, [], total_capital=DEFAULT_TOTAL_CAPITAL)
        self.assertEqual(decision.reason, "missing_stop")

    def test_vwap_classification_four_field_identity(self) -> None:
        conn = sqlite3.connect(self.live)
        conn.execute(
            """
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
                PRIMARY KEY (session_date, setup_id, continuation_rule_version, vwap_rule_version)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO live_vwap_qualifications (
                setup_id, continuation_rule_version, vwap_rule_version,
                instrument_token, tradingsymbol, session_date, direction,
                trigger_price, last_price, trigger_tick_sequence, trigger_exchange_ts,
                vwap, gap, classification, quality_ok, quality_reason, vwap_provenance,
                bootstrap_cutoff_exchange_ts, completed_5m_count, in_progress_bucket_start,
                in_progress_volume, payload_json, created_at
            ) VALUES ('fresh', 'v1', ?, 1, 'AAA', '2026-08-17', 'UP',
                      110, 110, 1, 't', 110, 0.001, 'ACCEPT', 1, NULL, 'live',
                      NULL, 1, NULL, 0, '{}', 'c')
            """,
            (VWAP_RULE_VERSION,),
        )
        conn.commit()
        conn.close()
        hit = fetch_vwap_classification(
            self.live,
            session_date="2026-08-17",
            setup_id="fresh",
            continuation_rule_version="v1",
        )
        self.assertEqual(hit, "ACCEPT")
        self.assertIsNone(
            fetch_vwap_classification(
                self.live,
                session_date="2026-08-16",
                setup_id="fresh",
                continuation_rule_version="v1",
            )
        )
        self.assertIsNone(
            fetch_vwap_classification(
                self.live,
                session_date="2026-08-17",
                setup_id="fresh",
                continuation_rule_version="other",
            )
        )
        self.assertIsNone(
            fetch_vwap_classification(
                self.live,
                session_date="2026-08-17",
                setup_id="fresh",
                continuation_rule_version="v1",
                vwap_rule_version="vwap_qualifier_v1",
            )
        )

    def test_vwap_missing_table_is_hard_error(self) -> None:
        with self.assertRaises(VwapLookupError):
            fetch_vwap_classification(
                self.live,
                session_date="2026-08-17",
                setup_id="fresh",
                continuation_rule_version="v1",
            )


if __name__ == "__main__":
    unittest.main()
