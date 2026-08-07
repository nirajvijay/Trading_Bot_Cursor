"""Tests for pre-market checklist query logic."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from api.queries.checklist import (
    _build_baselines,
    _build_historical,
    _build_instruments,
    _build_kite_auth,
    fetch_premarket_checklist,
)
from config.nifty100_symbols import NIFTY_100_SYMBOLS
from universe_manifest import write_universe_manifest_atomic


def _init_instruments_db(path: Path, *, count: int = 100, with_tick: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE nifty50_instruments (
            tradingsymbol TEXT PRIMARY KEY,
            instrument_token INTEGER NOT NULL,
            exchange TEXT NOT NULL,
            collected_at TEXT NOT NULL,
            instrument_data TEXT NOT NULL,
            quote_data TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE collection_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collected_at TEXT NOT NULL,
            symbols_requested INTEGER NOT NULL,
            symbols_found INTEGER NOT NULL,
            symbols_quoted INTEGER NOT NULL,
            missing_symbols TEXT
        )
        """
    )
    now = datetime.now(timezone.utc).isoformat()
    for i, symbol in enumerate(NIFTY_100_SYMBOLS[:count]):
        tick = {"tick_size": 0.05} if with_tick else {}
        conn.execute(
            """
            INSERT INTO nifty50_instruments
            VALUES (?, ?, 'NSE', ?, ?, NULL)
            """,
            (symbol, 100000 + i, now, json.dumps(tick)),
        )
    conn.execute(
        """
        INSERT INTO collection_runs
        VALUES (1, ?, 100, ?, 100, NULL)
        """,
        (now, count),
    )
    conn.commit()
    conn.close()


def _init_historical_db(path: Path, *, session_date: str = "2026-07-31", symbol_count: int = 100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE candles (
            instrument_token INTEGER NOT NULL,
            tradingsymbol TEXT NOT NULL,
            candle_time TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER,
            PRIMARY KEY (instrument_token, candle_time)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE candles_5m (
            instrument_token INTEGER NOT NULL,
            tradingsymbol TEXT NOT NULL,
            candle_time TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER,
            PRIMARY KEY (instrument_token, candle_time)
        )
        """
    )
    for i, symbol in enumerate(NIFTY_100_SYMBOLS[:symbol_count]):
        token = 100000 + i
        conn.execute(
            """
            INSERT INTO candles VALUES (?, ?, ?, 1, 1, 1, 1, 100)
            """,
            (token, symbol, f"{session_date}T09:15:00+05:30"),
        )
        for minute in range(25):
            hh = 9 + (15 + minute) // 60
            mm = (15 + minute) % 60
            conn.execute(
                """
                INSERT INTO candles_5m VALUES (?, ?, ?, 1, 1, 1, 1, 100)
                """,
                (
                    token,
                    symbol,
                    f"{session_date}T{hh:02d}:{mm:02d}:00+05:30",
                ),
            )
    conn.commit()
    conn.close()


def _init_baselines_db(path: Path, *, as_of: str = "2026-07-31") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE baselines (
            instrument_token INTEGER NOT NULL,
            tradingsymbol TEXT NOT NULL,
            minute_of_day INTEGER NOT NULL,
            median_volume REAL,
            trimmed_mean_volume REAL,
            median_abs_return REAL,
            valid_session_count INTEGER,
            is_reliable INTEGER,
            baseline_as_of_date TEXT NOT NULL,
            PRIMARY KEY (instrument_token, minute_of_day, baseline_as_of_date)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE baseline_generation_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at TEXT NOT NULL,
            baseline_as_of_date TEXT NOT NULL
        )
        """
    )
    for i, symbol in enumerate(NIFTY_100_SYMBOLS):
        conn.execute(
            """
            INSERT INTO baselines VALUES (?, ?, 630, 1, 1, 0.001, 21, 1, ?)
            """,
            (100000 + i, symbol, as_of),
        )
    conn.execute(
        "INSERT INTO baseline_generation_runs VALUES (1, '2026-08-01T10:00:00', ?)",
        (as_of,),
    )
    conn.commit()
    conn.close()


def _write_manifest(local_dir: Path) -> None:
    local_dir.mkdir(parents=True, exist_ok=True)
    write_universe_manifest_atomic(local_dir / "universe_manifest.json")


_FAKE_COMPLETED = [f"2026-07-{d:02d}" for d in range(1, 22)]

class ChecklistQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    @patch("api.queries.checklist.read_auth_status")
    def test_kite_auth_warning_when_token_present(self, mock_auth) -> None:
        mock_auth.return_value = {
            "api_key_configured": True,
            "api_secret_configured": True,
            "access_token_present": True,
            "masked_access_token": "abcd...wxyz",
        }
        result = _build_kite_auth()
        self.assertEqual(result["status"], "warning")
        self.assertIn("Check Token", result["message"])

    @patch("api.queries.checklist.token_valid_for_today", return_value=True)
    @patch("api.queries.checklist.read_token_check")
    @patch("api.queries.checklist.read_auth_status")
    def test_kite_auth_ok_when_validated_today(self, mock_auth, mock_cache, _mock_valid) -> None:
        mock_auth.return_value = {
            "api_key_configured": True,
            "api_secret_configured": True,
            "access_token_present": True,
            "masked_access_token": "abcd...wxyz",
        }
        mock_cache.return_value = {
            "valid": True,
            "checked_at": "2026-08-03T09:00:00+05:30",
            "session_date": "2026-08-03",
            "user_id": "AB1234",
        }
        result = _build_kite_auth()
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["token_validated_today"])

    @patch("api.queries.checklist.read_auth_status")
    def test_kite_auth_failed_missing_credentials(self, mock_auth) -> None:
        mock_auth.return_value = {
            "api_key_configured": False,
            "api_secret_configured": False,
            "access_token_present": False,
        }
        result = _build_kite_auth()
        self.assertEqual(result["status"], "failed")

    def test_instruments_ok(self) -> None:
        db = self.root / "instruments.db"
        _init_instruments_db(db)
        result = _build_instruments(db)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["instruments_count"], 100)

    def test_instruments_stale_message_formats_date(self) -> None:
        db = self.root / "instruments.db"
        _init_instruments_db(db)
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        conn = sqlite3.connect(db)
        conn.execute("UPDATE nifty50_instruments SET collected_at = ?", (old,))
        conn.execute("UPDATE collection_runs SET collected_at = ?", (old,))
        conn.commit()
        conn.close()
        result = _build_instruments(db)
        self.assertEqual(result["status"], "warning")
        self.assertIn("IST", result["message"])
        self.assertIn("older than 7 days", result["message"])

    def test_instruments_needs_update_when_missing(self) -> None:
        db = self.root / "instruments.db"
        _init_instruments_db(db, count=98)
        result = _build_instruments(db)
        self.assertEqual(result["status"], "needs_update")

    @patch(
        "api.queries.checklist.discover_completed_sessions",
        return_value=_FAKE_COMPLETED,
    )
    def test_historical_ok(self, _mock_sessions) -> None:
        db = self.root / "historical.db"
        _init_historical_db(db)
        result = _build_historical(db, "2026-08-03")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["symbols_covered"], 100)
        self.assertEqual(result["expected_prior_session"], "2026-07-31")

    @patch(
        "api.queries.checklist.discover_completed_sessions",
        return_value=_FAKE_COMPLETED,
    )
    def test_historical_ok_when_latest_is_session_date(self, _mock_sessions) -> None:
        """Today's partial candles must not fail when prior session P is covered."""
        db = self.root / "historical.db"
        _init_historical_db(db, session_date="2026-07-31")
        conn = sqlite3.connect(db)
        for i, symbol in enumerate(NIFTY_100_SYMBOLS):
            conn.execute(
                """
                INSERT INTO candles VALUES (?, ?, ?, 1, 1, 1, 1, 100)
                """,
                (100000 + i, symbol, "2026-08-03T10:00:00+05:30"),
            )
        conn.commit()
        conn.close()
        result = _build_historical(db, "2026-08-03")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["latest_date"], "2026-08-03")
        self.assertEqual(result["expected_prior_session"], "2026-07-31")

    def test_baselines_needs_update_when_behind_prior_session(self) -> None:
        db = self.root / "baselines.db"
        _init_baselines_db(db, as_of="2026-07-28")
        result = _build_baselines(db, "2026-08-03")
        self.assertEqual(result["status"], "needs_update")
        self.assertIn("expected as-of 2026-07-31", result["message"])

    def test_full_checklist_aggregate(self) -> None:
        instruments = self.root / "instruments.db"
        historical = self.root / "historical.db"
        baselines = self.root / "baselines.db"
        live = self.root / "live.db"
        local = self.root / "local"
        _init_instruments_db(instruments)
        _init_historical_db(historical)
        _init_baselines_db(baselines, as_of="2026-07-31")
        _write_manifest(local)
        live.parent.mkdir(parents=True, exist_ok=True)
        sqlite3.connect(live).close()

        with patch("api.queries.checklist.read_auth_status") as mock_auth, patch(
            "api.queries.checklist.token_valid_for_today", return_value=True
        ), patch("api.queries.checklist.read_token_check") as mock_cache, patch(
            "api.queries.checklist.discover_completed_sessions",
            return_value=_FAKE_COMPLETED,
        ), patch("api.queries.checklist.config.LOCAL_DATA_DIR", local):
            mock_auth.return_value = {
                "api_key_configured": True,
                "api_secret_configured": True,
                "access_token_present": True,
                "masked_access_token": "abcd...wxyz",
            }
            mock_cache.return_value = {
                "valid": True,
                "checked_at": "2026-08-03T09:00:00+05:30",
                "session_date": "2026-08-03",
                "user_id": "AB1234",
            }
            result = fetch_premarket_checklist(
                live_db=live,
                instruments_db=instruments,
                historical_db=historical,
                baselines_db=baselines,
                session_date="2026-08-03",
            )
        self.assertIn("areas", result)
        self.assertEqual(result["areas"]["offline_checks"]["radar_row_count"], 100)
        self.assertEqual(result["areas"]["offline_checks"]["status"], "ok")
        self.assertEqual(result["overall_status"], "ok")

    def test_missing_manifest_blocks_overall_ok(self) -> None:
        instruments = self.root / "instruments.db"
        historical = self.root / "historical.db"
        baselines = self.root / "baselines.db"
        live = self.root / "live.db"
        local = self.root / "local"
        local.mkdir(parents=True, exist_ok=True)
        _init_instruments_db(instruments)
        _init_historical_db(historical)
        _init_baselines_db(baselines, as_of="2026-07-31")
        sqlite3.connect(live).close()

        with patch("api.queries.checklist.read_auth_status") as mock_auth, patch(
            "api.queries.checklist.token_valid_for_today", return_value=True
        ), patch("api.queries.checklist.read_token_check") as mock_cache, patch(
            "api.queries.checklist.discover_completed_sessions",
            return_value=_FAKE_COMPLETED,
        ), patch("api.queries.checklist.config.LOCAL_DATA_DIR", local):
            mock_auth.return_value = {
                "api_key_configured": True,
                "api_secret_configured": True,
                "access_token_present": True,
                "masked_access_token": "abcd...wxyz",
            }
            mock_cache.return_value = {
                "valid": True,
                "checked_at": "2026-08-03T09:00:00+05:30",
                "session_date": "2026-08-03",
                "user_id": "AB1234",
            }
            result = fetch_premarket_checklist(
                live_db=live,
                instruments_db=instruments,
                historical_db=historical,
                baselines_db=baselines,
                session_date="2026-08-03",
            )
        self.assertNotEqual(result["overall_status"], "ok")
        self.assertTrue(
            any("manifest" in b.lower() or "not initialized" in b.lower() for b in result["blockers"])
        )

    def test_offline_fails_when_historical_missing(self) -> None:
        from api.queries.checklist import _build_offline

        live = self.root / "live.db"
        instruments = self.root / "instruments.db"
        historical = self.root / "historical.db"
        baselines = self.root / "baselines.db"
        _init_instruments_db(instruments)
        sqlite3.connect(live).close()

        result = _build_offline(
            live,
            instruments,
            historical,
            baselines,
            "2026-08-03",
            {"historical_candles": "failed"},
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("historical", result["missing_databases"])

    def test_offline_warns_when_only_live_missing(self) -> None:
        from api.queries.checklist import _build_offline

        live = self.root / "live.db"  # deliberately not created
        instruments = self.root / "instruments.db"
        historical = self.root / "historical.db"
        baselines = self.root / "baselines.db"
        _init_instruments_db(instruments)
        _init_historical_db(historical)
        _init_baselines_db(baselines, as_of="2026-07-31")

        result = _build_offline(
            live,
            instruments,
            historical,
            baselines,
            "2026-08-03",
            {
                "instruments": "ok",
                "historical_candles": "ok",
                "baselines": "ok",
                "five_minute_candles": "ok",
            },
        )
        self.assertEqual(result["status"], "warning")
        self.assertEqual(result["missing_databases"], ["live"])
        self.assertIn("Start Observation", result["message"])

    def test_missing_live_does_not_block_overall_ok(self) -> None:
        instruments = self.root / "instruments.db"
        historical = self.root / "historical.db"
        baselines = self.root / "baselines.db"
        live = self.root / "live.db"  # missing on purpose
        local = self.root / "local"
        local.mkdir(parents=True, exist_ok=True)
        _init_instruments_db(instruments)
        _init_historical_db(historical)
        _init_baselines_db(baselines, as_of="2026-07-31")
        _write_manifest(local)

        with patch("api.queries.checklist.read_auth_status") as mock_auth, patch(
            "api.queries.checklist.token_valid_for_today", return_value=True
        ), patch("api.queries.checklist.read_token_check") as mock_cache, patch(
            "api.queries.checklist.discover_completed_sessions",
            return_value=_FAKE_COMPLETED,
        ), patch("api.queries.checklist.config.LOCAL_DATA_DIR", local):
            mock_auth.return_value = {
                "api_key_configured": True,
                "api_secret_configured": True,
                "access_token_present": True,
                "masked_access_token": "abcd...wxyz",
            }
            mock_cache.return_value = {
                "valid": True,
                "checked_at": "2026-08-03T09:00:00+05:30",
                "session_date": "2026-08-03",
                "user_id": "AB1234",
            }
            result = fetch_premarket_checklist(
                live_db=live,
                instruments_db=instruments,
                historical_db=historical,
                baselines_db=baselines,
                session_date="2026-08-03",
            )
        self.assertEqual(result["areas"]["offline_checks"]["status"], "warning")
        self.assertEqual(result["overall_status"], "ok")
        self.assertFalse(
            any("live" in b.lower() for b in result["blockers"])
        )

class ChecklistApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.instruments = self.root / "instruments.db"
        self.historical = self.root / "historical.db"
        self.baselines = self.root / "baselines.db"
        self.live = self.root / "live.db"
        _init_instruments_db(self.instruments)
        _init_historical_db(self.historical)
        _init_baselines_db(self.baselines, as_of="2026-07-31")
        sqlite3.connect(self.live).close()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_api_endpoint(self) -> None:
        from fastapi.testclient import TestClient

        from api import config as api_config
        from api.main import app
        from tests.auth_test_helpers import clear_auth_overrides, disable_web_auth_overrides

        api_config.LIVE_DB_PATH = self.live
        api_config.INSTRUMENTS_DB_PATH = self.instruments
        api_config.HISTORICAL_DB_PATH = self.historical
        api_config.BASELINES_DB_PATH = self.baselines
        api_config.LOCAL_DATA_DIR = self.root / "local"
        api_config.LOCAL_INSTRUMENTS_DB_PATH = self.instruments
        api_config.LOCAL_HISTORICAL_DB_PATH = self.historical
        api_config.LOCAL_BASELINES_DB_PATH = self.baselines

        disable_web_auth_overrides()
        try:
            with patch("api.queries.checklist.read_auth_status") as mock_auth:
                mock_auth.return_value = {
                    "api_key_configured": True,
                    "api_secret_configured": True,
                    "access_token_present": True,
                    "masked_access_token": "abcd...wxyz",
                }
                client = TestClient(app)
                res = client.get("/api/v1/premarket-checklist?session_date=2026-08-03")
        finally:
            app.dependency_overrides.clear()
            clear_auth_overrides()
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["session_date"], "2026-08-03")
        self.assertIn("areas", body)
        self.assertIn("kite_auth", body["areas"])


if __name__ == "__main__":
    unittest.main()
