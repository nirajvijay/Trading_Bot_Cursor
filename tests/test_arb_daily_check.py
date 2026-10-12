"""The post-market ARB check: live decisions against the rules replayed.

One end-to-end day (the real detector, handoff, engine and store; then the
check on the same databases), and the comparison rules on their own.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from arb_daily_check import (
    DailyCheckError,
    _compare,
    format_report,
    load_checks,
    run_check,
    save_check,
)
from engine_types import ExecutionState
from live_one_minute_candle_writer import CREATE_LIVE_1M_CANDLES_TABLE_SQL
from orb_detector import OrbDetector
from orb_types import OrbBaseline
from orb_writer import OrbWriter
from tests.test_engine_arb_core import DAY, ArbEngineTestCase
from tick_event import Ohlc, TickEvent
from trading_engine_handoff import fetch_arb_candidates_since

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parents[1]
SETUP = "11|2026-09-22|ORB|orb_v1"


class DayCheckTestCase(ArbEngineTestCase):
    """AAA breaks out of its opening range at 10:29:58: entry 110, stop 106.5."""

    def setUp(self) -> None:
        super().setUp()
        self.live_db = Path(self._tmp.name) / "live.db"
        self.engine_db = Path(self._tmp.name) / "engine.db"
        writer = OrbWriter(self.live_db, now_fn=lambda: self.clock.now - timedelta(seconds=2))
        self.addCleanup(writer.close)
        seen_at = {}
        detector = OrbDetector(
            session_date=DAY, token_to_symbol={11: "AAA"}, tick_sizes={11: 0.05},
            baselines={11: OrbBaseline(11, "AAA", True, atr14=10.0, avg_or_volume=1000.0)},
            writer=writer, now_fn=lambda: seen_at["now"],
        )

        def tick(seq, hh, mm, ss, price, volume):
            when = datetime(2026, 9, 22, hh, mm, ss, tzinfo=IST)
            seen_at["now"] = when
            detector.on_tick(TickEvent(seq, 11, price, when, when, volume, 1, price,
                                       Ohlc(open=108.0, high=110.0, low=107.5, close=107.0)))

        tick(1, 9, 8, 5, 108.0, 500)
        tick(2, 9, 19, 59, 109.5, 4500)
        tick(3, 9, 20, 0, 109.5, 4600)
        tick(4, 10, 29, 58, 110.0, 9000)
        conn = sqlite3.connect(self.live_db)
        conn.execute(CREATE_LIVE_1M_CANDLES_TABLE_SQL)
        conn.commit()
        conn.close()

    def bars(self, *rows) -> None:
        conn = sqlite3.connect(self.live_db)
        for hhmm, o, h, l, c in rows:
            conn.execute(
                "INSERT INTO live_1m_candles VALUES (11, 'AAA', ?, ?, ?, ?, ?, ?, 10, 5, 1, "
                "'minute_transition', 1, 0, 'x')",
                (f"{DAY}T{hhmm}:00+05:30", DAY, o, h, l, c),
            )
        conn.commit()
        conn.close()

    def trade(self):
        floor = (self.clock.now - timedelta(minutes=1)).isoformat()
        engine = self.engine(
            candidate_source=lambda: fetch_arb_candidates_since(self.live_db, created_at_gte=floor)
        )
        engine.tick()
        return engine

    def check(self, **kw):
        return run_check(live_db=self.live_db, engine_db=self.engine_db, session_date=DAY, **kw)


class EndToEndTests(DayCheckTestCase):
    def test_a_day_the_engine_traded_as_the_rules_say_matches(self) -> None:
        self.trade()
        stored = self.store.get(SETUP)
        self.assertEqual((stored.state, stored.qty), (ExecutionState.PROTECTED, 714))
        # 10:29 is the entry minute; 10:31 runs to +1R (113.5) and 10:33 falls
        # back through the locked stop at 110.85 (entry + 0.25 x 3.5, on the grid).
        self.bars(("10:29", 109.6, 110.1, 109.5, 110.0), ("10:30", 110.0, 111.0, 109.9, 110.8),
                  ("10:31", 110.8, 113.6, 110.7, 113.2), ("10:32", 113.2, 113.4, 112.0, 112.2),
                  ("10:33", 112.2, 112.3, 110.5, 110.6))
        result = self.check()
        s = result["summary"]
        self.assertEqual((s["candidates"], s["matched"], s["match_rate"]), (1, 1, 1.0))
        self.assertEqual((s["live_entered"], s["replay_entered"], s["risk_scale"]), (1, 1, 1.0))
        (row,) = result["rows"]
        self.assertTrue(row["match"])
        self.assertEqual(row["live"]["risk_used"], 2500.0)
        self.assertEqual(row["replay"]["risk_used"], 2500.0)
        self.assertEqual(row["replay"]["qty"], 714)
        self.assertEqual((row["replay"]["exit_reason"], row["replay"]["exit_minute"][11:]), ("lock", "10:33"))
        self.assertEqual(row["replay"]["exit_price"], 110.85)
        # The fill was at the level: no slippage against the backtest's assumption.
        self.assertEqual(row["live"]["entry_slippage_bps"], 0.0)
        self.assertEqual(s["entry_slippage_bps_median"], 0.0)
        self.assertIsNone(s["live_halt"])
        self.assertTrue(s["halt_match"])
        self.assertIn("Same decision and size: 1 of 1 (100%)", format_report(result))
        self.assertIn("No mismatches.", format_report(result))

    def test_a_signal_the_engine_never_took_is_a_mismatch(self) -> None:
        # ARB activity that day, but not this signal: the engine was not looking.
        self.save_closed("other", "ZZZ", -100.0)
        row = self.store.get("other")
        row.extra["arb"] = {"strategy": "FUNNEL", "score": 1, "risk_scale": 1.0, "capital": 500000.0}
        self.store.save(row)
        self.bars(("10:29", 109.6, 110.1, 109.5, 110.0), ("10:30", 110.0, 111.0, 109.9, 110.8))
        result = self.check()
        (row,) = result["rows"]
        self.assertEqual((row["match"], row["why"]), (False, "engine_did_not_see_it"))
        self.assertEqual(row["live"]["decision"], "not_seen")
        self.assertEqual(result["summary"]["match_rate"], 0.0)
        self.assertIn("engine_did_not_see_it", format_report(result))

    def test_a_day_without_arb_cannot_be_checked(self) -> None:
        with self.assertRaises(DailyCheckError):
            self.check()

    def test_a_missing_database_is_reported(self) -> None:
        self.trade()
        with self.assertRaises(DailyCheckError):
            run_check(live_db=Path(self._tmp.name) / "none.db", engine_db=self.engine_db, session_date=DAY)

    def test_the_result_is_stored_and_read_back_and_replaced(self) -> None:
        self.trade()
        self.bars(("10:29", 109.6, 110.1, 109.5, 110.0))
        result = self.check()
        save_check(self.engine_db, result)
        save_check(self.engine_db, result)
        (stored,) = load_checks(self.engine_db, with_rows_for=DAY)
        self.assertEqual(stored["summary"]["matched"], 1)
        self.assertEqual(stored["rows"][0]["setup_id"], SETUP)
        (brief,) = load_checks(self.engine_db)
        self.assertNotIn("rows", brief)
        # The engine's own tables are untouched.
        self.assertEqual(self.store.get(SETUP).qty, 714)

    def test_the_script_prints_the_report_and_writes_nothing_with_no_save(self) -> None:
        self.trade()
        self.bars(("10:29", 109.6, 110.1, 109.5, 110.0))
        done = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "arb_daily_check.py"), "--session-date", DAY,
             "--live-db", str(self.live_db), "--engine-db", str(self.engine_db), "--no-save"],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("ARB daily check, 2026-09-22 (stage 1)", done.stdout)
        self.assertEqual(load_checks(self.engine_db), [])
        bad = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "arb_daily_check.py"), "--session-date", "2026-09-23",
             "--live-db", str(self.live_db), "--engine-db", str(self.engine_db)],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        self.assertEqual(bad.returncode, 2)
        self.assertIn("Cannot check", bad.stderr)


class CheckFromTheDeskTests(DayCheckTestCase):
    """POST /performance/check: the same check, run and stored from the Performance tab."""

    def setUp(self) -> None:
        super().setUp()
        import os

        from tests.auth_test_helpers import (
            clear_auth_overrides,
            disable_web_auth_overrides,
            make_test_client,
        )

        saved = {k: os.environ.get(k) for k in ("EXECUTION_ENGINE_DB_PATH", "LIVE_DB_PATH")}
        os.environ["EXECUTION_ENGINE_DB_PATH"] = str(self.engine_db)
        os.environ["LIVE_DB_PATH"] = str(self.live_db)

        def restore() -> None:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        self.addCleanup(restore)
        disable_web_auth_overrides()
        self.addCleanup(clear_auth_overrides)
        self.client = make_test_client()

    def test_it_runs_stores_and_feeds_the_performance_tab(self) -> None:
        self.trade()
        self.bars(("10:29", 109.6, 110.1, 109.5, 110.0), ("10:30", 110.0, 111.0, 109.9, 110.8))
        done = self.client.post("/api/v1/performance/check", params={"session_date": DAY})
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual(done.json()["summary"]["matched"], 1)
        self.assertIn("ARB daily check, 2026-09-22", done.json()["report"])
        (stored,) = load_checks(self.engine_db)
        self.assertEqual(stored["session_date"], DAY)
        summary = self.client.get("/api/v1/performance/summary", params={"parity_date": DAY}).json()
        self.assertEqual((summary["parity"]["matched"], summary["parity"]["detail_date"]), (1, DAY))

    def test_a_day_that_cannot_be_checked_says_why_and_stores_nothing(self) -> None:
        refused = self.client.post("/api/v1/performance/check", params={"session_date": DAY})
        self.assertEqual(refused.status_code, 400)
        self.assertIn("no ARB activity", refused.json()["detail"])
        self.assertEqual(load_checks(self.engine_db), [])
        self.assertEqual(
            self.client.post("/api/v1/performance/check", params={"session_date": "22-09-2026"}).status_code, 422
        )


class CompareRuleTests(unittest.TestCase):
    ENTER = {"decision": "enter", "risk_used": 2500.0}

    def test_both_skipping_is_a_match_whatever_the_reasons(self) -> None:
        self.assertTrue(_compare({"decision": "skip", "reason": "a"}, {"decision": "skip", "reason": "b"}, 3.5)["match"])
        self.assertTrue(_compare({"decision": "not_seen"}, {"decision": "skip", "reason": "b"}, 3.5)["match"])

    def test_risk_within_one_share_is_a_match(self) -> None:
        self.assertTrue(_compare({"decision": "enter", "risk_used": 2497.0}, self.ENTER, 3.5)["match"])
        verdict = _compare({"decision": "enter", "risk_used": 1250.0}, self.ENTER, 3.5)
        self.assertEqual((verdict["match"], verdict["why"], verdict["risk_gap"]), (False, "risk_differs", 1250.0))

    def test_one_side_entering_is_a_mismatch(self) -> None:
        self.assertEqual(_compare(self.ENTER, {"decision": "skip"}, 3.5)["why"], "live_entered_replay_skipped")
        self.assertEqual(_compare({"decision": "skip"}, self.ENTER, 3.5)["why"], "live_skipped_replay_entered")

    def test_an_entry_with_no_recorded_risk_is_not_assumed_to_match(self) -> None:
        self.assertEqual(_compare({"decision": "enter", "risk_used": None}, self.ENTER, 3.5)["why"], "live_risk_unknown")


if __name__ == "__main__":
    unittest.main()
