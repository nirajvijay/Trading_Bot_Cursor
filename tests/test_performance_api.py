"""The Performance tab's numbers, the per-trade measurements behind them, and
the Strategy tab's engine decisions and market breadth."""
from __future__ import annotations

import os
import sqlite3
import unittest
from pathlib import Path

import arb_metrics
from api.queries.performance import fetch_performance
from api.queries.strategy import fetch_breadth, fetch_engine_decisions
from arb_daily_check import CHECK_VERSION, save_check
from engine_arb import ARB_EXTRA_KEY
from engine_store import SqlitePositionStore
from engine_types import ExecutionState, Position, TriggerCandidate
from live_one_minute_candle_writer import CREATE_LIVE_1M_CANDLES_TABLE_SQL
from tests.test_execution_api import ExecutionApiTestCase, _candidate_row

MICRO = 0.05


class MetricsTests(unittest.TestCase):
    def test_source_labels(self) -> None:
        self.assertEqual(arb_metrics.source_label({"strategy": "ORB"}), "ORB")
        self.assertEqual(arb_metrics.source_label({"strategy": "FUNNEL", "score": 0}), "F0")
        self.assertEqual(arb_metrics.source_label({"strategy": "FUNNEL", "score": 5}), "F3")
        self.assertIsNone(arb_metrics.source_label({"strategy": "FUNNEL"}))
        self.assertIsNone(arb_metrics.source_label(None))

    def test_the_reference_price_is_the_level_for_orb_and_the_trigger_otherwise(self) -> None:
        self.assertEqual(arb_metrics.signal_price({"strategy": "ORB", "orb_level": 109.9, "trigger_price": 110.0}), 109.9)
        self.assertEqual(arb_metrics.signal_price({"strategy": "ORB", "trigger_price": 110.0}), 110.0)
        self.assertEqual(arb_metrics.signal_price({"trigger_price": 110.0}), 110.0)
        self.assertIsNone(arb_metrics.signal_price({"trigger_price": 0}))

    def test_positive_slippage_is_always_worse(self) -> None:
        long = {"direction": "UP", "trigger_price": 100.0}
        short = {"direction": "DOWN", "trigger_price": 100.0}
        self.assertEqual(arb_metrics.entry_slippage_bps(long, 100.03), 3.0)     # paid more
        self.assertEqual(arb_metrics.entry_slippage_bps(long, 99.98), -2.0)
        self.assertEqual(arb_metrics.entry_slippage_bps(short, 99.97), 3.0)     # sold lower
        self.assertIsNone(arb_metrics.entry_slippage_bps(long, None))
        # A long's stop at 98 that filled at 97.9 is 10.2 bps worse; a short's at 102 filled at 102.1 too.
        self.assertEqual(arb_metrics.stop_exit_slippage_bps("UP", 98.0, 100.0, 10, -21.0), 10.2)
        self.assertEqual(arb_metrics.stop_exit_slippage_bps("DOWN", 102.0, 100.0, 10, -21.0), 9.8)

    def test_r_is_rupees_over_the_risk_at_entry(self) -> None:
        self.assertEqual(arb_metrics.r_gross(100.0, 98.0, 10, 30.0), 1.5)
        self.assertEqual(arb_metrics.r_gross(100.0, 102.0, 10, -20.0), -1.0)
        self.assertIsNone(arb_metrics.r_gross(100.0, 100.0, 10, 30.0))
        self.assertIsNone(arb_metrics.r_gross(100.0, 98.0, 0, 30.0))
        self.assertEqual(arb_metrics.exit_price("DOWN", 100.0, 10, 20.0), 98.0)


def _arb_trade(trade_id, day, *, symbol="AAA", strategy="FUNNEL", score=1, entry=100.0, stop=98.0,
               qty=10, pnl=20.0, fill=None, scale=MICRO, reason="stop_hit", direction="UP",
               state=ExecutionState.CLOSED) -> Position:
    row = dict(_candidate_row(trade_id, symbol), session_date=day, direction=direction,
               trigger_price=entry, strategy=strategy)
    arb = {"strategy": strategy, "risk_scale": scale, "base_risk": 125.0, "risk_used": 125.0}
    if strategy == "FUNNEL":
        arb["score"] = score
    return Position(
        trade_id=trade_id, candidate=TriggerCandidate(**row), state=state, qty=qty,
        entry_price=fill if fill is not None else entry, stop_price=stop, realised_pnl=pnl,
        extra={ARB_EXTRA_KEY: arb, "initial_stop_price": stop, "close_reason": reason},
    )


class PerformanceTests(ExecutionApiTestCase):
    def seed(self, *positions) -> None:
        store = SqlitePositionStore(self.db)
        try:
            for position in positions:
                store.save(position)
        finally:
            store.close()

    def summary(self, **kw):
        return fetch_performance(self.db, **kw)

    def test_before_any_arb_trade(self) -> None:
        body = self.client.get("/api/v1/performance/summary").json()
        self.assertEqual((body["risk_scale"], body["totals"]["trades"], body["days"]), (0.25, 0, []))
        self.assertEqual(body["stage"]["key"], "quarter")
        self.assertFalse(body["stage"]["all_gates_met"])
        self.assertEqual([s["source"] for s in body["sources"]], ["ORB", "F0", "F1", "F2", "F3"])
        self.assertEqual(body["reference"]["at_3bps"]["net_per_month"], 39532.0)

    def test_days_sources_and_totals(self) -> None:
        self.seed(
            _arb_trade("a", "2026-10-14", strategy="ORB", pnl=40.0),               # +2R
            _arb_trade("b", "2026-10-14", symbol="BBB", score=0, pnl=-20.0),        # -1R
            _arb_trade("c", "2026-10-15", symbol="CCC", score=3, pnl=-20.0),        # -1R
            _arb_trade("open", "2026-10-15", symbol="DDD", state=ExecutionState.PROTECTED, pnl=None),
            _arb_trade("full", "2026-10-15", symbol="EEE", scale=1.0, pnl=5000.0),  # another stage
        )
        store = SqlitePositionStore(self.db)
        store.save(Position(trade_id="legacy", candidate=TriggerCandidate(**_candidate_row("legacy")),
                            state=ExecutionState.CLOSED, qty=10, entry_price=100.0, realised_pnl=999.0))
        store.save_arb_day("2026-10-15", halted_reason="hard_day_stop", halted_at="2026-10-15T05:00:00+00:00",
                           halted_mtm=-251.0, peak_mtm=12.0)
        store.close()
        body = self.summary(risk_scale=MICRO)
        self.assertEqual(body["available_scales"], [MICRO, 1.0])
        totals = body["totals"]
        self.assertEqual((totals["trades"], totals["days"], totals["green_days"]), (3, 2, 1))
        self.assertEqual((totals["gross"], totals["r_gross"]), (0.0, 0.0))
        self.assertLess(totals["net"], 0)                      # charges on three round trips
        self.assertAlmostEqual(totals["net"], totals["gross"] - totals["charges"], places=2)
        self.assertEqual([d["session_date"] for d in body["days"]], ["2026-10-14", "2026-10-15"])
        self.assertEqual((body["days"][0]["r_gross"], body["days"][1]["halted_reason"]), (1.0, "hard_day_stop"))
        self.assertEqual(body["days"][1]["cumulative_net"], totals["net"])
        self.assertLess(totals["max_drawdown"], 0)
        by_source = {s["source"]: s for s in body["sources"]}
        self.assertEqual((by_source["ORB"]["trades"], by_source["ORB"]["r_gross"], by_source["ORB"]["avg_r"]), (1, 2.0, 2.0))
        self.assertEqual((by_source["F0"]["r_gross"], by_source["F3"]["r_gross"], by_source["F1"]["trades"]), (-1.0, -1.0, 0))
        self.assertEqual(body["months"][0]["month"], "2026-10")
        (halt,) = body["day_controls"]
        self.assertEqual((halt["reason"], halt["mtm"], halt["peak"]), ("hard_day_stop", -251.0, 12.0))
        # The latest stage traded is what is shown when none is asked for.
        self.assertEqual(self.summary()["risk_scale"], 1.0)

    def test_slippage_and_its_rolling_alert(self) -> None:
        self.seed(*[
            _arb_trade(f"t{i}", "2026-10-14", symbol=f"S{i}", fill=100.05, pnl=-20.5) for i in range(20)
        ])
        slip = self.summary()["slippage"]
        self.assertEqual((slip["entry"]["trades"], slip["entry"]["median"], slip["entry"]["mean"]), (20, 5.0, 5.0))
        self.assertEqual((slip["rolling_entry_mean"], slip["rolling_alert"], slip["rolling_demote"]), (5.0, True, False))
        self.assertEqual(slip["stop_exit"]["trades"], 20)
        gates = {g["key"]: g for g in self.summary()["stage"]["gates"]}
        self.assertFalse(gates["entry_slippage"]["ok"])        # median 5 bps is over the 3 bps gate

    def test_gates(self) -> None:
        days = [f"2026-10-{d:02d}" for d in range(1, 16)]
        self.seed(*[
            _arb_trade(f"{day}-{n}", day, symbol=f"S{n}", fill=100.01, pnl=30.0)
            for day in days for n in range(3)
        ])
        for day in days:
            save_check(self.db, {
                "summary": {"session_date": day, "risk_scale": MICRO, "candidates": 4, "matched": 4,
                            "match_rate": 1.0, "live_r": 4.5, "replay_r": 5.0, "halt_match": True,
                            "live_halt": None, "replay_halt": None, "live_entered": 3, "replay_entered": 3},
                "rows": [{"setup_id": "x", "match": True}, {"setup_id": "y", "match": False, "why": "risk_differs",
                                                           "symbol": "S1", "strategy": "FUNNEL", "direction": "UP",
                                                           "time": f"{day}T10:00:00+05:30",
                                                           "live": {"decision": "enter"}, "replay": {"decision": "enter"}}],
                "generated_at": "2026-10-16T00:00:00+00:00", "check_version": CHECK_VERSION,
            })
        body = self.summary(parity_date="2026-10-15")
        stage = body["stage"]
        self.assertEqual((stage["days"], stage["trades"], stage["next_key"]), (15, 45, "quarter"))
        self.assertEqual({g["key"]: g["ok"] for g in stage["gates"]},
                         {"time_in_stage": True, "safety_incidents": True, "parity": True,
                          "entry_slippage": True, "r_vs_replay": True})
        self.assertTrue(stage["all_gates_met"])
        self.assertEqual((body["parity"]["match_rate"], body["parity"]["candidates"]), (1.0, 60))
        self.assertEqual(body["parity"]["detail_date"], "2026-10-15")
        self.assertEqual([r["setup_id"] for r in body["parity"]["detail_rows"]], ["y"])

    def test_gates_that_fail_say_why(self) -> None:
        self.seed(
            _arb_trade("a", "2026-10-14", pnl=30.0),
            _arb_trade("b", "2026-10-14", symbol="BBB", pnl=-10.0, reason="unprotected_timeout"),
        )
        save_check(self.db, {
            "summary": {"session_date": "2026-10-14", "risk_scale": MICRO, "candidates": 10, "matched": 9,
                        "match_rate": 0.9, "live_r": 1.0, "replay_r": 4.0, "halt_match": False,
                        "live_halt": None, "replay_halt": {"reason": "day_lock", "minute": "2026-10-14T11:00"}},
            "rows": [], "generated_at": "x", "check_version": CHECK_VERSION,
        })
        stage = self.summary()["stage"]
        gates = {g["key"]: g for g in stage["gates"]}
        self.assertEqual({k: g["ok"] for k, g in gates.items()},
                         {"time_in_stage": False, "safety_incidents": False, "parity": False,
                          "entry_slippage": True, "r_vs_replay": False})
        self.assertIn("Safety exits: 1. Days whose halt differs from the replay: 1.", gates["safety_incidents"]["detail"])
        self.assertIn("9 of 10 (90%)", gates["parity"]["detail"])
        self.assertEqual(stage["incidents"][0]["close_reason"], "unprotected_timeout")
        self.assertFalse(stage["all_gates_met"])

    def test_a_traded_day_without_a_check_keeps_the_parity_gate_open(self) -> None:
        self.seed(_arb_trade("a", "2026-10-14"), _arb_trade("b", "2026-10-15", symbol="BBB"))
        save_check(self.db, {
            "summary": {"session_date": "2026-10-14", "risk_scale": MICRO, "candidates": 2, "matched": 2,
                        "match_rate": 1.0, "live_r": 1.0, "replay_r": 1.0, "halt_match": True},
            "rows": [], "generated_at": "x", "check_version": CHECK_VERSION,
        })
        body = self.summary()
        self.assertEqual(body["parity"]["unchecked_days"], ["2026-10-15"])
        self.assertFalse({g["key"]: g["ok"] for g in body["stage"]["gates"]}["parity"])

    def test_the_route_needs_a_website_session(self) -> None:
        from tests.auth_test_helpers import clear_auth_overrides, make_test_client

        clear_auth_overrides()
        self.assertIn(make_test_client().get("/api/v1/performance/summary").status_code, (401, 403))


class StrategyEngineColumnTests(ExecutionApiTestCase):
    def test_each_signal_shows_what_the_engine_did(self) -> None:
        store = SqlitePositionStore(self.db)
        store.save(_arb_trade("taken", "2026-10-14", strategy="ORB", state=ExecutionState.PROTECTED, pnl=None))
        skipped = _arb_trade("skipped", "2026-10-14", symbol="BBB", state=ExecutionState.REJECTED, pnl=None)
        skipped.extra["skip_reason"] = "arb_below_min_risk"
        store.save(skipped)
        store.close()
        decisions = fetch_engine_decisions(self.db, "2026-10-14")
        self.assertEqual((decisions["taken"]["decision"], decisions["taken"]["source"], decisions["taken"]["risk_used"]),
                         ("entered", "ORB", 125.0))
        self.assertEqual((decisions["skipped"]["decision"], decisions["skipped"]["reason"], decisions["skipped"]["risk_used"]),
                         ("skipped", "arb_below_min_risk", None))
        self.assertEqual(fetch_engine_decisions(self.db, "2026-10-15"), {})
        self.assertEqual(fetch_engine_decisions(Path(self.dir) / "none.db", "2026-10-14"), {})

    def test_the_orb_board_and_funnel_feed_carry_the_decision(self) -> None:
        from api.routers import strategy as router

        signals = [{"setup_id": "taken"}, {"setup_id": "unknown"}]
        store = SqlitePositionStore(self.db)
        store.save(_arb_trade("taken", "2026-10-14", pnl=None, state=ExecutionState.PROTECTED))
        store.close()
        router._attach_engine(signals, "2026-10-14")
        self.assertEqual(signals[0]["engine"]["decision"], "entered")
        self.assertIsNone(signals[1]["engine"])


class BreadthTests(unittest.TestCase):
    def test_market_and_sector_moves_from_the_open(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "live.db"
            conn = sqlite3.connect(db)
            conn.execute(CREATE_LIVE_1M_CANDLES_TABLE_SQL)
            rows = [("RELIANCE", "09:15", 100.0, 100.0), ("RELIANCE", "09:16", 100.0, 99.0),   # -1.0%
                    ("TCS", "09:15", 200.0, 200.0), ("TCS", "09:16", 200.0, 201.0),            # +0.5%
                    ("INFY", "09:15", 50.0, 50.0), ("INFY", "09:16", 50.0, 49.75)]             # -0.5%
            for i, (symbol, hhmm, o, c) in enumerate(rows):
                conn.execute(
                    "INSERT INTO live_1m_candles VALUES (?, ?, ?, '2026-10-14', ?, ?, ?, ?, 1, 1, 1, "
                    "'minute_transition', 1, 0, 'x')",
                    (hash(symbol) % 10000, symbol, f"2026-10-14T{hhmm}:00+05:30", o, max(o, c), min(o, c), c),
                )
            conn.commit()
            conn.close()
            body = fetch_breadth(db, "2026-10-14")
            self.assertEqual(body["stocks"], 3)
            self.assertAlmostEqual(body["market_pct"], (-1.0 + 0.5 - 0.5) / 3, places=4)
            self.assertEqual((body["threshold_pct"], body["scores_for"]), (0.2, "UP"))   # market down: longs score
            self.assertEqual(body["as_of"], "2026-10-14T09:16:00+05:30")
            sectors = {s["sector"]: s for s in body["sectors"]}
            self.assertEqual(sum(s["stocks"] for s in sectors.values()), 3)
            self.assertEqual(body["sectors"], sorted(body["sectors"], key=lambda s: -s["pct"]))
            empty = fetch_breadth(db, "2026-10-15")
            self.assertEqual((empty["market_pct"], empty["sectors"]), (None, []))
            self.assertIsNone(fetch_breadth(Path(tmp) / "none.db", "2026-10-14")["market_pct"])


if __name__ == "__main__":
    unittest.main()
