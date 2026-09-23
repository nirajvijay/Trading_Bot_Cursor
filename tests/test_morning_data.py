from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from api import config
from api.services import morning_data as data
from historical_collector import init_db
from nse_trading_calendar import IST


class MorningDataTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.hist = root / "history.db"
        self.inst = root / "instruments.db"
        self.stack.enter_context(patch.object(config, "HISTORICAL_DB_PATH", self.hist))
        self.stack.enter_context(patch.object(config, "INSTRUMENTS_DB_PATH", self.inst))
        self.stack.enter_context(patch.object(data, "NIFTY_100_SYMBOLS", ("TEST",)))
        self.day = "2026-09-23"
        self.sessions = data.required_sessions(self.day)
        with sqlite3.connect(self.inst) as conn:
            conn.execute("CREATE TABLE nifty50_instruments (tradingsymbol TEXT, instrument_token INTEGER)")
            conn.execute("INSERT INTO nifty50_instruments VALUES ('TEST', 1)")
        conn = init_db(self.hist)
        for day in self.sessions:
            self.seed(conn, day)
        conn.commit()
        conn.close()

    def tearDown(self):
        self.stack.close()

    def seed(self, conn, day):
        start = datetime.fromisoformat(day + "T09:15:00").replace(tzinfo=IST)
        conn.executemany("INSERT OR REPLACE INTO candles VALUES (1,'TEST',?,1,2,0,1,100)",
                         [((start + timedelta(minutes=i)).isoformat(),) for i in range(375)])

    def test_valid_history_has_no_network_work(self):
        self.assertEqual(data.repair_plan(self.day), [])

    def test_existing_completed_session_lookback_is_preserved(self):
        from nse_trading_calendar import prior_nse_trading_session
        with sqlite3.connect(self.hist) as conn:
            self.seed(conn, prior_nse_trading_session(self.sessions[-1]))
            conn.execute("DELETE FROM candles WHERE substr(candle_time,1,10)=?", (self.sessions[5],))
        self.assertEqual(data.repair_plan(self.day), [], '21 completed sessions can exclude an older incomplete day')

    def test_gap_in_prior_session_is_repaired_even_if_baseline_quality_passes(self):
        with sqlite3.connect(self.hist) as conn:
            conn.execute("DELETE FROM candles WHERE candle_time = ?", (self.sessions[0] + "T10:00:00+05:30",))
        pending = data.repair_plan(self.day)
        self.assertEqual([(stock.tradingsymbol, day) for stock, day in pending], [("TEST", self.sessions[0])])

    def test_severe_missing_history_requires_explicit_backfill(self):
        with sqlite3.connect(self.hist) as conn:
            for day in self.sessions[:4]:
                conn.execute("DELETE FROM candles WHERE substr(candle_time,1,10)=?", (day,))
        with self.assertRaisesRegex(ValueError, "backfill"):
            data.repair_plan(self.day)

    def test_bad_provider_result_rolls_back_replacement(self):
        stamp = self.sessions[0] + "T10:00:00+05:30"
        with sqlite3.connect(self.hist) as conn:
            conn.execute("DELETE FROM candles WHERE candle_time=?", (stamp,))
        with patch("login._get_kite"), patch.object(data, "fetch_candles_for_chunk", return_value=[]):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                data.repair_history(self.day)
        with sqlite3.connect(self.hist) as conn:
            count = conn.execute("SELECT count(*) FROM candles WHERE substr(candle_time,1,10)=?", (self.sessions[0],)).fetchone()[0]
        self.assertEqual(count, 374)

    def test_five_minute_repair_updates_corrected_prices(self):
        data.repair_five_minute(self.day)
        with sqlite3.connect(self.hist) as conn:
            conn.execute("UPDATE candles SET high=10 WHERE candle_time=?", (self.sessions[0] + "T09:16:00+05:30",))
        data.repair_five_minute(self.day)
        with sqlite3.connect(self.hist) as conn:
            high = conn.execute("SELECT high FROM candles_5m WHERE candle_time=?", (self.sessions[0] + "T09:15:00+05:30",)).fetchone()[0]
        self.assertEqual(high, 10)

    def test_incremental_repair_replaces_corrected_candles_and_invalidates_derived_rows(self):
        data.repair_five_minute(self.day)
        day = self.sessions[0]
        with sqlite3.connect(self.hist) as conn:
            conn.execute("DELETE FROM candles WHERE candle_time=?", (day + "T10:00:00+05:30",))
        start = datetime.fromisoformat(day + "T09:15:00").replace(tzinfo=IST)
        candles = [{"date": start + timedelta(minutes=i), "open": 1, "high": 5, "low": 0,
                    "close": 1, "volume": 100} for i in range(375)]
        with patch("login._get_kite"), patch.object(data, "fetch_candles_for_chunk", return_value=candles), patch.object(data.time, "sleep"):
            data.repair_history(self.day)
        self.assertEqual(data.repair_plan(self.day), [])
        with sqlite3.connect(self.hist) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM candles_5m WHERE substr(candle_time,1,10)=?", (day,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT high FROM candles WHERE candle_time=?", (day + "T09:15:00+05:30",)).fetchone()[0], 5)


if __name__ == "__main__":
    unittest.main()
