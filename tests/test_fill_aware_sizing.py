"""Fill-aware sizing (2026-10-22) and the entry slippage it is judged by.

Live funnel entries filled a median 3.4 bps (worst 22.8) worse than the
trigger, most of it already in the last price when the signal fired; ORB filled
at the trigger. So the share count is worked out from the worse of the trigger
and that last price, plus 3 bps for the funnel and nothing for ORB. Only the
share count changes: stops, orders, timing and which trades are taken do not.
"""
from __future__ import annotations

import dataclasses
import unittest

import arb_metrics
from api.queries.performance import fetch_performance
from engine_arb import ARB_EXTRA_KEY, sizing_price
from engine_store import SqlitePositionStore
from engine_types import ExecutionState, Position, TriggerCandidate
from tests.test_engine_arb_orb import OrbEngineTestCase
from tests.test_execution_api import ExecutionApiTestCase, _candidate_row
from trading_engine_types import STRATEGY_FUNNEL, STRATEGY_ORB


class SizingPriceTests(unittest.TestCase):
    def price(self, direction, trigger, last, strategy):
        return sizing_price(direction=direction, trigger_price=trigger, last_price=last, strategy=strategy)

    def test_orb_at_the_trigger_is_exactly_the_trigger(self) -> None:
        self.assertEqual(self.price("UP", 110.0, 110.0, STRATEGY_ORB), 110.0)
        self.assertEqual(self.price("DOWN", 548.75, 548.75, STRATEGY_ORB), 548.75)

    def test_orb_takes_the_worse_price_and_no_buffer(self) -> None:
        self.assertEqual(self.price("UP", 110.0, 110.5, STRATEGY_ORB), 110.5)
        self.assertEqual(self.price("UP", 110.0, 109.5, STRATEGY_ORB), 110.0)    # better: ignored
        self.assertEqual(self.price("DOWN", 110.0, 109.5, STRATEGY_ORB), 109.5)
        self.assertEqual(self.price("DOWN", 110.0, 110.5, STRATEGY_ORB), 110.0)

    def test_funnel_takes_the_worse_price_plus_3_bps(self) -> None:
        self.assertAlmostEqual(self.price("UP", 831.0, 832.55, STRATEGY_FUNNEL), 832.55 * 1.0003)
        self.assertAlmostEqual(self.price("UP", 831.0, 830.0, STRATEGY_FUNNEL), 831.0 * 1.0003)
        self.assertAlmostEqual(self.price("DOWN", 9164.5, 9160.0, STRATEGY_FUNNEL), 9160.0 * 0.9997)

    def test_a_missing_or_broken_last_price_falls_back_to_the_trigger(self) -> None:
        for last in (None, 0.0, -1.0, float("nan"), "x"):
            self.assertEqual(self.price("UP", 110.0, last, STRATEGY_ORB), 110.0)
            self.assertAlmostEqual(self.price("UP", 110.0, last, STRATEGY_FUNNEL), 110.0 * 1.0003)


class OrbSizingTests(OrbEngineTestCase):
    def test_an_orb_at_its_trigger_is_sized_exactly_as_before(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.orb("o1", "AAA"))   # last price = trigger = 110
        stored = self.store.get("o1")
        self.assertEqual((stored.qty, stored.extra["trigger_based_qty"]), (1000, 1000))
        self.assertEqual(stored.extra["sizing_price"], 110.0)
        self.assertEqual(stored.stop_price, 107.5)

    def test_an_orb_whose_price_jumped_buys_fewer_shares_same_stop(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.orb("o1", "AAA", last_price=110.5))
        stored = self.store.get("o1")
        self.assertEqual((stored.qty, stored.extra["trigger_based_qty"]), (833, 1000))  # 2500 / 3.0
        self.assertEqual(stored.stop_price, 107.5)
        self.assertEqual(stored.extra["risk_cap_rupees"], 2500)
        self.assertEqual(self.arb("o1")["sizing_price"], 110.5)


class FunnelSizingTests(OrbEngineTestCase):
    def test_a_funnel_trade_is_sized_from_the_last_price_plus_3_bps(self) -> None:
        engine = self.engine()
        candidate = dataclasses.replace(self.trigger("s1", "AAA", score=2), last_price=110.2)
        self.run_triggers(engine, candidate)
        stored = self.store.get("s1")
        # Stop 106.95 (one tick under the 107 swing low); budget 4,500.
        self.assertEqual(stored.qty, int(4500 // (110.2 * 1.0003 - 106.95)))     # 1371
        self.assertEqual(stored.extra["trigger_based_qty"], 1475)
        self.assertEqual(stored.stop_price, 106.95)
        self.assertEqual(stored.state, ExecutionState.PROTECTED)


class NeverSkipsATradeTests(OrbEngineTestCase):
    scale = 0.05

    def test_a_trade_the_trigger_gives_one_share_still_gets_one(self) -> None:
        # Score 0 at micro: Rs 50. Stop 4950.10 (one tick under 4950.15):
        # 49.90 a share at the trigger (1 share), 51.40 at 5000 * 1.0003 (0).
        self.broker.last_prices["AAA"] = 5000.0
        engine = self.engine()
        self.run_triggers(engine, self.trigger("s1", "AAA", price=5000.0, swing_low=4950.15))
        stored = self.store.get("s1")
        self.assertEqual((stored.qty, stored.extra["trigger_based_qty"]), (1, 1))
        self.assertFalse(self.arb("s1")["one_share_floor"])
        self.assertEqual(stored.state, ExecutionState.PROTECTED)


def _trade(trade_id, *, strategy, score=1, trigger, last, fill, qty, direction="UP", level=None,
           signal_ts="2026-10-21T10:01:39+05:30", created="2026-10-21T04:31:40.992003+00:00",
           filled="2026-10-21T04:31:41.467787+00:00") -> Position:
    row = dict(_candidate_row(trade_id, trade_id.upper()), session_date="2026-10-21", direction=direction,
               trigger_price=trigger, last_price=last, strategy=strategy, trigger_exchange_ts=signal_ts,
               created_at=created)
    if level is not None:
        row["orb_level"] = level
    arb = {"strategy": strategy, "risk_scale": 0.05, "base_risk": 125.0, "risk_used": 125.0}
    if strategy == STRATEGY_FUNNEL:
        arb["score"] = score
    return Position(
        trade_id=trade_id, candidate=TriggerCandidate(**row), state=ExecutionState.CLOSED, qty=qty,
        entry_price=fill, stop_price=trigger - 2, realised_pnl=10.0,
        extra={ARB_EXTRA_KEY: arb, "initial_stop_price": trigger - 2, "close_reason": "eod_squareoff",
               "entry_filled_at": filled, "sizing_price": last, "trigger_based_qty": qty + 3},
    )


class SlippageRecordingTests(ExecutionApiTestCase):
    def test_the_split_adds_up_and_costs_rupees(self) -> None:
        candidate = {"direction": "UP", "trigger_price": 831.0, "last_price": 832.55, "strategy": "FUNNEL"}
        split = arb_metrics.entry_slippage_split(candidate, 832.896875)
        self.assertEqual(split["at_signal"], 18.65)
        self.assertAlmostEqual(split["at_signal"] + split["after_signal"],
                               arb_metrics.entry_slippage_bps(candidate, 832.896875), places=6)
        self.assertEqual(arb_metrics.entry_slippage_rupees(candidate, 832.896875, 16), 30.35)
        short = {"direction": "DOWN", "trigger_price": 9164.5, "last_price": 9164.5, "strategy": "FUNNEL"}
        self.assertEqual(arb_metrics.entry_slippage_rupees(short, 9165.0, 1), -0.5)   # sold higher: better
        self.assertIsNone(arb_metrics.entry_slippage_split({**candidate, "last_price": None}, 832.9))

    def test_timing_from_signal_to_engine_to_fill(self) -> None:
        timing = arb_metrics.entry_timing(
            {"trigger_exchange_ts": "2026-10-21T10:01:39+05:30"},
            "2026-10-21T04:31:40.992003+00:00", "2026-10-21T04:31:41.467787+00:00")
        self.assertEqual(timing, {"signal_to_engine_s": 1.99, "engine_to_fill_s": 0.48})
        self.assertEqual(arb_metrics.entry_timing({}, None, "x"),
                         {"signal_to_engine_s": None, "engine_to_fill_s": None})

    def test_the_performance_tab_groups_it_by_source(self) -> None:
        store = SqlitePositionStore(self.db)
        try:
            store.save(_trade("cg", strategy="FUNNEL", score=1, trigger=831.0, last=832.55, fill=832.896875, qty=16))
            store.save(_trade("wi", strategy="FUNNEL", score=3, trigger=189.33, last=189.34, fill=189.40, qty=306))
            store.save(_trade("hz", strategy="ORB", trigger=548.75, last=548.75, fill=548.65, qty=26,
                              direction="DOWN", level=548.8))
        finally:
            store.close()
        slip = fetch_performance(self.db)["slippage"]
        rows = {r["source"]: r for r in slip["by_source"]}
        self.assertEqual(sorted(rows), ["F1", "F3", "ORB"])
        self.assertEqual((rows["F1"]["trades"], rows["F1"]["worst"], rows["F1"]["rupees"]), (1, 22.83, 30.35))
        self.assertEqual(rows["F1"]["at_signal_mean"], 18.65)
        self.assertEqual(rows["F3"]["rupees"], 21.42)
        self.assertEqual((rows["ORB"]["mean"], rows["ORB"]["rupees"]), (2.73, 3.9))   # vs the 548.80 level
        # The store stamps created_at itself, so only the shape is checked here;
        # the timing maths is test_timing_from_signal_to_engine_to_fill.
        self.assertIn("engine_to_fill_s", rows["F1"])
        self.assertAlmostEqual(slip["entry_rupees"], 30.35 + 21.42 + 3.9, places=2)
        recent = slip["recent"]
        self.assertEqual([r["symbol"] for r in recent], ["HZ", "WI", "CG"])         # newest first
        self.assertEqual((recent[2]["qty"], recent[2]["trigger_based_qty"]), (16, 19))


if __name__ == "__main__":
    unittest.main()
