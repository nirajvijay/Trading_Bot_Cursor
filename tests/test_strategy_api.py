"""The /strategy API: the ORB board and the funnel feed. Display only."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from api.queries import strategy as strategy_queries
from intraday_continuation_writer import IntradayContinuationWriter
from orb_detector import OrbDetector
from orb_types import OrbBaseline
from orb_writer import OrbWriter
from tests.auth_test_helpers import (
    clear_auth_overrides,
    disable_web_auth_overrides,
    make_test_client,
)
from tick_event import Ohlc, TickEvent

IST = ZoneInfo("Asia/Kolkata")
DAY = "2026-09-22"
BASE = "/api/v1/strategy"
SYMBOLS = {1: "AAA", 2: "BBB", 3: "CCC", 4: "DDD", 5: "EEE", 6: "FFF"}


class StrategyApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "live.db"
        self._saved = os.environ.get("LIVE_DB_PATH")
        os.environ["LIVE_DB_PATH"] = str(self.db)
        self.addCleanup(self._restore_env)
        strategy_queries._funnel_cache.clear()
        disable_web_auth_overrides()
        self.addCleanup(clear_auth_overrides)
        self.client = make_test_client()

    def _restore_env(self) -> None:
        if self._saved is None:
            os.environ.pop("LIVE_DB_PATH", None)
        else:
            os.environ["LIVE_DB_PATH"] = self._saved


class OrbBoardTests(StrategyApiTestCase):
    def run_day(self) -> None:
        """AAA 6x, BBB 5x (down), CCC 4x, DDD 3.2x, EEE 2.4x, FFF no baseline."""
        baselines = {
            token: OrbBaseline(token, symbol, True, atr14=20.0, avg_or_volume=1000.0,
                               history_as_of="2026-09-21")
            for token, symbol in SYMBOLS.items()
        }
        baselines[6] = OrbBaseline(6, "FFF", False, reason="stale_history",
                                   detail="latest candles 2026-09-17, expected 2026-09-21")
        writer = OrbWriter(self.db)
        self.addCleanup(writer.close)
        writer.save_baselines(DAY, "orb_v1", baselines.values())
        seen_at = {}
        detector = OrbDetector(
            session_date=DAY, token_to_symbol=SYMBOLS, tick_sizes={t: 0.05 for t in SYMBOLS},
            baselines=baselines, writer=writer, now_fn=lambda: seen_at["now"],
        )
        seq = 0

        def tick(token: int, hh: int, mm: int, ss: int, price: float, volume: int) -> None:
            nonlocal seq
            seq += 1
            when = datetime(2026, 9, 22, hh, mm, ss, tzinfo=IST)
            seen_at["now"] = when
            detector.on_tick(TickEvent(seq, token, price, when, when, volume, 1, price,
                                       Ohlc(open=1000.0, high=1012.0, low=998.0, close=990.0)))

        rvols = {1: 6.0, 2: 5.0, 3: 4.0, 4: 3.2, 5: 2.4, 6: 9.0}
        for token, rvol in rvols.items():
            close = 999.0 if token == 2 else 1010.0
            tick(token, 9, 8, 5, 1000.0, 500)
            tick(token, 9, 19, 59, close, 500 + int(rvol * 1000))
        for token in SYMBOLS:
            tick(token, 9, 20, 0, 999.0 if token == 2 else 1010.0, 10**6)
        tick(1, 9, 23, 11, 1012.5, 10**6)       # AAA triggers, half a rupee through
        tick(3, 9, 24, 0, 1030.0, 10**6)        # CCC: far through, missed

    def test_before_the_detector_has_ever_run(self) -> None:
        body = self.client.get(f"{BASE}/orb", params={"session_date": DAY}).json()
        self.assertEqual(body["session_date"], DAY)
        self.assertIsNone(body["status"])
        self.assertFalse(body["ranked"])
        self.assertEqual((body["arms"], body["baselines"]["total"]), ([], 0))
        self.assertEqual(body["rules"]["min_rvol"], 3.0)
        self.assertEqual(body["rules"]["trigger_end"], "11:01")

    def test_a_database_without_orb_tables_reads_as_empty(self) -> None:
        sqlite3.connect(self.db).close()
        response = self.client.get(f"{BASE}/orb", params={"session_date": DAY})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["arms"], [])

    def test_the_board_after_a_ranked_morning(self) -> None:
        self.run_day()
        body = self.client.get(f"{BASE}/orb", params={"session_date": DAY}).json()
        self.assertEqual(body["status"]["state"], "ranked")
        self.assertTrue(body["ranked"])
        self.assertEqual(body["ranked_at"], "2026-09-22T09:20:00+05:30")
        self.assertEqual(body["baselines"]["ready"], 5)
        self.assertEqual(body["baselines"]["total"], 6)
        self.assertEqual(body["baselines"]["as_of"], "2026-09-21")
        self.assertEqual(body["baselines"]["not_ready"][0]["symbol"], "FFF")

        arms = {a["symbol"]: a for a in body["arms"]}
        self.assertEqual(list(arms), ["AAA", "BBB", "CCC"])
        aaa = arms["AAA"]
        self.assertEqual((aaa["state"], aaa["rank"], aaa["rvol"], aaa["direction"]),
                         ("TRIGGERED", 1, 6.0, "UP"))
        self.assertEqual((aaa["level"], aaa["trigger_price"], aaa["planned_stop"]),
                         (1012.0, 1012.5, 1005.5))
        self.assertEqual(aaa["stop_at_level"], 1005.0)
        self.assertEqual(aaa["decided_at"], "2026-09-22T09:23:11+05:30")
        self.assertAlmostEqual(aaa["overshoot_bps"], 0.5 / 1012.0 * 1e4)
        self.assertEqual((arms["BBB"]["state"], arms["BBB"]["direction"], arms["BBB"]["level"]),
                         ("ARMED", "DOWN", 998.0))
        self.assertEqual(arms["BBB"]["stop_at_level"], 1005.0)
        self.assertIsNone(arms["BBB"]["trigger_price"])
        self.assertEqual((arms["CCC"]["state"], arms["CCC"]["reason"]),
                         ("MISSED", "overshoot_beyond_limit"))

        self.assertEqual([(s["symbol"], s["rank"]) for s in body["in_play_not_armed"]], [("DDD", 4)])
        self.assertEqual([s["symbol"] for s in body["near_misses"]], ["EEE"])
        self.assertEqual(body["unusable"], {"no_baseline": 1})
        self.assertEqual(body["valid_count"], 5)

    def test_another_day_is_empty(self) -> None:
        self.run_day()
        body = self.client.get(f"{BASE}/orb", params={"session_date": "2026-09-23"}).json()
        self.assertFalse(body["ranked"])
        self.assertEqual(body["arms"], [])

    def test_a_malformed_date_is_refused(self) -> None:
        self.assertEqual(self.client.get(f"{BASE}/orb", params={"session_date": "today"}).status_code, 422)
        self.assertEqual(self.client.get(f"{BASE}/funnel", params={"session_date": "x"}).status_code, 422)

    def test_no_date_means_today(self) -> None:
        response = self.client.get(f"{BASE}/orb")
        self.assertEqual(response.status_code, 200)
        self.assertRegex(response.json()["session_date"], r"^\d{4}-\d{2}-\d{2}$")


class FunnelFeedTests(StrategyApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        conn = sqlite3.connect(self.db)
        conn.execute(
            "CREATE TABLE live_1m_candles (tradingsymbol TEXT, candle_time TEXT, "
            "session_date TEXT, open REAL, close REAL)"
        )
        conn.commit()
        conn.close()
        self.writer = IntradayContinuationWriter(db_path=self.db)
        self.addCleanup(self.writer.close)

    def bar(self, symbol: str, hhmm: str, open_: float, close: float) -> None:
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO live_1m_candles VALUES (?, ?, ?, ?, ?)",
                         (symbol, f"{DAY}T{hhmm}:00+05:30", DAY, open_, close))

    def trigger(self, setup_id: str, symbol: str, hh: int, mm: int, *, volume: int = 3000,
                swing_low: float = 97.0, price: float = 100.0, session_date: str = DAY) -> None:
        when = datetime(2026, 9, 22, hh, mm, 30, tzinfo=IST)
        self.writer.insert_arm(
            setup_id=setup_id, continuation_rule_version="v1", instrument_token=1,
            tradingsymbol=symbol, session_date=session_date, direction="UP",
            pullback_swing_high=price - 0.05, pullback_swing_low=swing_low, tick_size=0.05,
            buffer_ticks=1, trigger_price=price, trigger_price_ticks=int(price * 20),
            pullback_type="ema", ready_5m_candle_time=None,
        )
        self.writer.insert_decision(
            setup_id=setup_id, continuation_rule_version="v1", decision_type="TRIGGERED",
            trigger_tick_sequence=1, trigger_exchange_ts=when, last_price=price,
            last_price_ticks=int(price * 20), breakout_candle_volume=volume,
            avg_prior_3_1m_volume=1000.0, volume_ok=True, volume_reliable=True, created_at=when,
        )

    def feed(self, day: str = DAY) -> dict:
        response = self.client.get(f"{BASE}/funnel", params={"session_date": day})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_no_triggers(self) -> None:
        self.assertEqual(self.feed(), {"session_date": DAY, "triggers": [], "pending": 0})

    def test_each_trigger_is_filtered_and_scored(self) -> None:
        # The market is down 0.5% by 10:11, against a long.
        for symbol in ("AAA", "BBB", "CCC"):
            self.bar(symbol, "09:15", 100.0, 100.0)
            self.bar(symbol, "10:11", 99.6, 99.5)
        self.trigger("t-good", "AAA", 10, 12, volume=7000)          # 7x volume, early, market against
        self.trigger("t-thin", "BBB", 10, 20, volume=1500)          # only 1.5x volume
        self.trigger("t-late", "CCC", 13, 5)                        # after 13:00
        self.trigger("t-tight", "DDD", 10, 40, swing_low=99.95)     # stop 0.1% away
        self.trigger("t-again", "AAA", 10, 50, volume=7000)         # AAA's second trigger
        rows = {r["setup_id"]: r for r in self.feed()["triggers"]}
        self.assertEqual(len(rows), 5)

        good = rows["t-good"]
        self.assertTrue(good["passes"])
        self.assertEqual(good["score"], 3)
        self.assertEqual(good["score_parts"], {"market_against": True, "sector_with": False,
                                               "volume_6x": True, "before_1030": True})
        self.assertEqual(good["base_risk"], 6000.0)
        self.assertEqual(good["volume_ratio"], 7.0)
        self.assertAlmostEqual(good["align_market_pct"], -0.5)
        self.assertEqual(good["trigger_time"], "2026-09-22T10:12:30+05:30")
        self.assertAlmostEqual(good["stop"], 96.95)

        self.assertEqual((rows["t-thin"]["passes"], rows["t-thin"]["reason"]), (False, "arb_volume_below_min"))
        self.assertEqual(rows["t-late"]["reason"], "arb_after_funnel_cutoff")
        self.assertEqual(rows["t-tight"]["reason"], "arb_stop_below_min_pct")
        self.assertEqual(rows["t-again"]["reason"], "arb_not_first_trigger_today")
        self.assertIsNone(rows["t-thin"]["base_risk"])
        self.assertEqual([r["setup_id"] for r in self.feed()["triggers"]],
                         ["t-good", "t-thin", "t-tight", "t-again", "t-late"])

    def test_only_the_asked_day(self) -> None:
        self.trigger("t-today", "AAA", 10, 12)
        self.trigger("t-other", "BBB", 10, 12, session_date="2026-09-21")
        self.assertEqual([r["setup_id"] for r in self.feed()["triggers"]], ["t-today"])
        self.assertEqual(self.feed("2026-09-23")["triggers"], [])

    def test_a_late_first_open_is_scored_over_several_polls(self) -> None:
        limit = strategy_queries.FUNNEL_MAX_NEW_PER_REQUEST
        for i in range(limit + 4):
            self.trigger(f"t{i:02d}", f"S{i:02d}", 10, 12)
        first = self.feed()
        self.assertEqual((len(first["triggers"]), first["pending"]), (limit, 4))
        second = self.feed()
        self.assertEqual((len(second["triggers"]), second["pending"]), (limit + 4, 0))

    def test_a_scored_trigger_is_not_recomputed(self) -> None:
        self.trigger("t1", "AAA", 10, 12)
        self.feed()
        calls = []
        original = strategy_queries._funnel_row
        strategy_queries._funnel_row = lambda *a, **k: calls.append(1) or original(*a, **k)
        try:
            self.feed()
        finally:
            strategy_queries._funnel_row = original
        self.assertEqual(calls, [])


class RouteGuardTests(unittest.TestCase):
    def test_both_routes_require_a_website_session(self) -> None:
        from api.auth.deps import require_web_session
        from api.main import app

        for path in (f"{BASE}/orb", f"{BASE}/funnel"):
            route = next(r for r in app.routes if getattr(r, "path", None) == path)
            deps = [d.call for d in route.dependant.dependencies]
            self.assertIn(require_web_session, deps, path)
            self.assertEqual(route.methods, {"GET"})


if __name__ == "__main__":
    unittest.main()
