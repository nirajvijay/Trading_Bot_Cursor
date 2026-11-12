"""ARB marked-stock trails (engine_marked_trails): the rules table, the entry
check, the trail ratchet in every mode, the engine's order path, halts, and
the replay.

Engine tests use the GUC harness (FakeBroker, the real store, a fake clock;
the clock starts at 10:30 IST) with a small test book so the numbers are
easy to follow. Research parity is scripts/marked_trails_parity.py.
"""
from __future__ import annotations

import dataclasses
import json
import unittest

import engine_marked_trails as M
from arb_replay import EXIT_DAY_HALT, EXIT_MARKED_TRAIL, ReplayBar, ReplayCandidate, replay_day
from config.nifty100_symbols import NIFTY_100_SYMBOLS
from engine_arb import ARB_EXTRA_KEY, ArbSettings
from engine_config import arb_session_config
from engine_exit import CloseReason
from engine_lock import LockBar
from engine_trailing import EVENT_STOP_MOVED, EVENT_STOP_REPLACED, STOP_MODS_KEY
from engine_types import ExecutionState
from tests.test_engine_arb_core import DAY
from tests.test_engine_arb_guc import GucEngineTestCase

TEST_TABLE = {
    "generated": "test",
    "stocks": {
        # L (buy) = open * 1.02, L (sell) = open * 0.98.
        "AAA": {"avg_high_pct": 2.0, "avg_low_pct": 2.0,
                "buy": {"trail_pct": 1.0, "mode": "plain"}, "sell": {"trail_pct": 1.0, "mode": "plain"}},
        "BBB": {"avg_high_pct": 2.0, "avg_low_pct": 2.0, "buy": {"trail_pct": 1.0, "mode": "plain"}},
        "CCC": {"avg_high_pct": 2.0, "avg_low_pct": 2.0, "buy": {"trail_pct": 1.0, "mode": "plain"}},
        # A wide trail (like TRENT 4%) and a higher level.
        "DDD": {"avg_high_pct": 3.0, "avg_low_pct": 3.0, "buy": {"trail_pct": 4.0, "mode": "plain"},
                "below_actual": True},
    },
}
BOOK = M.MarkedTrailBook.from_dict(TEST_TABLE)
PLAIN = M.TrailRule(1.0)
TOKENS = {"AAA": 1, "BBB": 2, "CCC": 3, "DDD": 4, "EEE": 5}


def key(hhmm: str) -> str:
    return f"{DAY}T{hhmm}:00+05:30"


def lbar(hhmm: str, high: float, low: float, close: float) -> LockBar:
    return LockBar(key(hhmm), high, low, close)


# -----------------------------------------------------------------------------
# The rules table
# -----------------------------------------------------------------------------


class TableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.book = M.MarkedTrailBook.load()

    def test_the_shipped_table_has_49_buy_and_6_sell_rules(self) -> None:
        self.assertEqual(self.book.rule_counts, (49, 6))
        self.assertEqual(len(self.book.stocks), 53)
        self.assertEqual(self.book.generated, "2026-11-12")

    def test_every_marked_stock_is_in_the_live_universe(self) -> None:
        # A name mismatch would mean a marked stock silently never trades.
        missing = [s for s in self.book.stocks if s not in NIFTY_100_SYMBOLS]
        self.assertEqual(missing, [])
        for symbol in ("LTM", "DMART", "M&M", "BAJAJ-AUTO"):
            self.assertIn(symbol, self.book)

    def test_special_modes_and_fixed_levels(self) -> None:
        ltm, dmart, lodha = self.book.get("LTM"), self.book.get("DMART"), self.book.get("LODHA")
        self.assertEqual((ltm.sell.mode, ltm.sell.cap_pct, ltm.sell.trail_pct), (M.MODE_LEVEL_CAP, 0.5, 0.25))
        self.assertEqual((ltm.avg_high_pct, ltm.avg_low_pct), (1.17, 1.25))
        self.assertEqual((dmart.buy.mode, dmart.avg_high_pct, dmart.avg_low_pct), (M.MODE_LEVEL_FLOOR, 1.17, 1.30))
        self.assertEqual(lodha.buy.mode, M.MODE_LEVEL_FLOOR)
        self.assertEqual(lodha.sell.mode, M.MODE_PLAIN)
        self.assertEqual(self.book.get("TRENT").buy.trail_pct, 4.0)

    def test_below_actual_marks_can_be_dropped(self) -> None:
        flagged = sorted(s for s, m in self.book.stocks.items() if m.below_actual)
        self.assertEqual(flagged, ["HINDUNILVR", "HINDZINC"])
        dropped = self.book.without_below_actual()
        self.assertEqual(dropped.rule_counts, (47, 6))
        self.assertNotIn("HINDZINC", dropped)

    def test_a_malformed_table_is_refused(self) -> None:
        bad = [
            {},
            {"stocks": {}},
            {"stocks": {"X": {"avg_high_pct": 1, "avg_low_pct": 1}}},                          # no rule
            {"stocks": {"X": {"avg_high_pct": 0, "avg_low_pct": 1, "buy": {"trail_pct": 1}}}},
            {"stocks": {"X": {"avg_high_pct": 1, "avg_low_pct": 1, "buy": {"trail_pct": 1, "mode": "level_cap"}}}},
            {"stocks": {"X": {"avg_high_pct": 1, "avg_low_pct": 1, "sell": {"trail_pct": 1, "mode": "level_cap"}}}},
            {"stocks": {"X": {"avg_high_pct": 1, "avg_low_pct": 1, "buy": {"trail_pct": 25}}}},
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(M.MarkedTableError):
                M.MarkedTrailBook.from_dict(data)


# -----------------------------------------------------------------------------
# Rules 1 and 2
# -----------------------------------------------------------------------------


class EntryCheckTests(unittest.TestCase):
    def check(self, symbol="AAA", direction="UP", entry=110.0, day_open=108.0):
        return M.check_entry(BOOK, symbol=symbol, direction=direction, entry_price=entry, day_open=day_open)

    def test_an_unmarked_stock_is_skipped(self) -> None:
        self.assertEqual(self.check(symbol="EEE").reason, M.REASON_UNMARKED)

    def test_an_unknown_open_fails_closed(self) -> None:
        for day_open in (None, 0.0, float("nan"), -1.0):
            with self.subTest(day_open=day_open):
                self.assertEqual(self.check(day_open=day_open).reason, M.REASON_OPEN_UNKNOWN)

    def test_a_buy_is_taken_at_or_below_the_level(self) -> None:
        # L = 100 * 1.02 = 102
        self.assertTrue(self.check(entry=102.0, day_open=100.0).ok)
        self.assertTrue(self.check(entry=101.0, day_open=100.0).ok)
        result = self.check(entry=102.05, day_open=100.0)
        self.assertEqual(result.reason, M.REASON_OUTSIDE_LEVEL)
        self.assertAlmostEqual(result.plan["level"], 102.0)

    def test_a_sell_is_taken_at_or_above_the_level(self) -> None:
        self.assertTrue(self.check(direction="DOWN", entry=98.0, day_open=100.0).ok)
        self.assertEqual(self.check(direction="DOWN", entry=97.95, day_open=100.0).reason, M.REASON_OUTSIDE_LEVEL)

    def test_the_unmarked_side_of_a_marked_stock_trades_with_normal_exits(self) -> None:
        result = self.check(symbol="BBB", direction="DOWN", entry=99.0, day_open=100.0)
        self.assertTrue(result.ok)
        self.assertIsNone(result.plan["rule"])
        self.assertIsNone(M.rule_from_plan(result.plan))
        # ... still held to its level.
        self.assertFalse(self.check(symbol="BBB", direction="DOWN", entry=97.0, day_open=100.0).ok)

    def test_the_plan_carries_the_rule(self) -> None:
        plan = self.check().plan
        self.assertEqual(M.rule_from_plan(plan), PLAIN)
        self.assertEqual((plan["day_open"], plan["table_generated"]), (108.0, "test"))


# -----------------------------------------------------------------------------
# Rule 3: the ratchet
# -----------------------------------------------------------------------------


class TrailRatchetTests(unittest.TestCase):
    def fold(self, bars, direction="UP", level=102.0, rule=PLAIN, tick=None):
        return M.fold_trail(bars, direction=direction, level=level, rule=rule,
                            entry_minute=key("10:30"), tick_size=tick)

    def test_nothing_until_a_completed_bar_reaches_the_level(self) -> None:
        state = self.fold([lbar("10:31", 101.9, 101.0, 101.5)])
        self.assertFalse(state.touched)
        self.assertIsNone(state.target)

    def test_the_entry_minutes_bar_never_touches(self) -> None:
        self.assertFalse(self.fold([lbar("10:30", 105.0, 100.0, 101.0)]).touched)

    def test_plain_buy_trails_the_running_high_and_only_tightens(self) -> None:
        bars = [lbar("10:31", 103.0, 101.0, 102.5),   # touch: run 103 -> 101.97
                lbar("10:32", 105.0, 102.0, 104.0),   # run 105 -> 103.95
                lbar("10:33", 104.0, 103.0, 103.5)]   # lower high: stays
        state = self.fold(bars[:1])
        self.assertEqual(state.touched_minute, key("10:31"))
        self.assertAlmostEqual(state.target, 101.97)
        state = self.fold(bars)
        self.assertAlmostEqual(state.run, 105.0)
        self.assertAlmostEqual(state.target, 103.95)

    def test_the_run_starts_no_worse_than_the_level(self) -> None:
        # The touch bar's high is the level itself.
        state = self.fold([lbar("10:31", 102.0, 101.0, 101.5)])
        self.assertAlmostEqual(state.run, 102.0)

    def test_level_floor_keeps_a_buy_stop_at_or_above_the_level(self) -> None:
        rule = M.TrailRule(1.0, M.MODE_LEVEL_FLOOR)
        state = self.fold([lbar("10:31", 102.5, 101.0, 102.0)], rule=rule)
        self.assertAlmostEqual(state.target, 102.0)            # 102.5 * 0.99 = 101.475 < L
        state = self.fold([lbar("10:31", 102.5, 101.0, 102.0), lbar("10:32", 106.0, 102.0, 105.0)], rule=rule)
        self.assertAlmostEqual(state.target, 104.94)

    def test_plain_sell_trails_the_running_low(self) -> None:
        state = self.fold([lbar("10:31", 99.0, 97.0, 97.5), lbar("10:32", 97.5, 95.0, 96.0)],
                          direction="DOWN", level=98.0)
        self.assertAlmostEqual(state.run, 95.0)
        self.assertAlmostEqual(state.target, 95.95)

    def test_level_cap_starts_half_a_percent_above_the_level(self) -> None:
        # LTM sell: min(L * 1.005, low * 1.0025).
        rule = M.TrailRule(0.25, M.MODE_LEVEL_CAP, 0.5)
        state = self.fold([lbar("10:31", 99.0, 97.9, 98.2)], direction="DOWN", level=98.0, rule=rule)
        self.assertAlmostEqual(state.target, min(98.0 * 1.005, 97.9 * 1.0025))
        state = self.fold([lbar("10:31", 99.0, 98.0, 98.2)], direction="DOWN", level=98.0, rule=rule)
        self.assertAlmostEqual(state.target, 98.0 * 1.0025)
        far = self.fold([lbar("10:31", 99.0, 90.0, 91.0)], direction="DOWN", level=98.0, rule=rule)
        self.assertAlmostEqual(far.target, 90.0 * 1.0025)

    def test_tick_rounding_is_away_from_the_market(self) -> None:
        up = self.fold([lbar("10:31", 103.0, 101.0, 102.5)], tick=0.05)
        self.assertEqual(up.target, 101.95)                      # 101.97 down
        down = self.fold([lbar("10:31", 99.0, 97.0, 97.5)], direction="DOWN", level=98.0, tick=0.05)
        self.assertEqual(down.target, 98.0)                       # 97.97 up

    def test_fold_is_resumable(self) -> None:
        bars = [lbar("10:31", 103.0, 101.0, 102.5), lbar("10:32", 105.0, 102.0, 104.0)]
        part = self.fold(bars[:1])
        whole = M.fold_trail(bars, direction="UP", level=102.0, rule=PLAIN, entry_minute=key("10:30"), state=part)
        self.assertEqual(whole, self.fold(bars))


class ResolveTrailStopTests(unittest.TestCase):
    def resolve(self, current, target, *, switched=False, ltp=105.0, direction="UP"):
        return M.resolve_trail_stop(direction=direction, current=current, target=target, tick_size=0.05,
                                    switched=switched, ltp=ltp)

    def test_the_first_write_may_loosen(self) -> None:
        move = self.resolve(104.0, 101.0)
        self.assertEqual((move.action, move.target), ("move", 101.0))

    def test_after_the_switch_it_only_tightens(self) -> None:
        self.assertEqual(self.resolve(104.0, 101.0, switched=True).reason, "trail_cannot_loosen")
        self.assertEqual(self.resolve(101.0, 104.0, switched=True).action, "move")

    def test_unchanged_and_no_price(self) -> None:
        self.assertEqual(self.resolve(104.0, 104.01).reason, "unchanged")
        self.assertEqual(self.resolve(101.0, 104.0, ltp=None).reason, "no_price")

    def test_a_stop_at_or_through_the_market_flattens(self) -> None:
        move = self.resolve(101.0, 105.0, ltp=105.0)
        self.assertEqual((move.action, move.reason), ("flatten", M.EXIT_TRAIL_THROUGH_MARKET))
        self.assertEqual(self.resolve(99.0, 96.0, ltp=96.5, direction="DOWN").action, "flatten")


class WalkTrailTests(unittest.TestCase):
    @staticmethod
    def bars(*rows):
        return [ReplayBar(f"{DAY}T{hhmm}", o, h, l, c) for hhmm, o, h, l, c in rows]

    def walk(self, bars, **kw):
        args = dict(direction="UP", level=102.0, rule=PLAIN, entry_minute=f"{DAY}T10:30",
                    touch_until=f"{DAY}T14:50", touch_inclusive=False)
        args.update(kw)
        return M.walk_trail(bars, **args)

    def test_stop_hit_and_gap(self) -> None:
        b = self.bars(("10:31", 101, 103, 101, 102.5), ("10:32", 102.5, 102.6, 101.5, 101.8))
        ex = self.walk(b)
        self.assertEqual((ex.reason, ex.minute), ("trail", f"{DAY}T10:32"))
        self.assertAlmostEqual(ex.price, 101.97)
        gap = self.bars(("10:31", 101, 103, 101, 102.5), ("10:32", 101.0, 101.2, 100.5, 100.8))
        self.assertEqual(self.walk(gap).price, 101.0)           # gapped through: the open

    def test_touch_on_the_normal_exit_bar(self) -> None:
        b = self.bars(("10:31", 101, 103, 101, 102.5), ("10:32", 103, 104, 103, 104))
        self.assertIsNone(self.walk(b, touch_until=f"{DAY}T10:31"))                      # live
        self.assertIsNotNone(self.walk(b, touch_until=f"{DAY}T10:31", touch_inclusive=True))  # research

    def test_end_of_day(self) -> None:
        b = self.bars(("14:48", 101, 103, 101, 102.5), ("14:49", 102.5, 103, 102.4, 102.9),
                      ("14:50", 103.0, 103.5, 102.8, 103.2))
        live = self.walk(b)
        self.assertEqual((live.reason, live.price), ("eod", 103.0))                       # 14:50 open
        research = self.walk(b, research_eod=True, touch_inclusive=True)
        self.assertEqual((research.reason, research.price), ("eod", 103.2))                # 14:50 close


# -----------------------------------------------------------------------------
# The engine
# -----------------------------------------------------------------------------


class MarkedEngineTestCase(GucEngineTestCase):
    settings_overrides = {"marked_trails_enabled": True}

    def setUp(self) -> None:
        super().setUp()
        self.broker.last_prices["EEE"] = 110.0
        self.opens = {token: 108.0 for token in TOKENS.values()}   # L (buy) = 110.16
        self.context.day_open = lambda *, instrument_token, session_date: self.opens.get(int(instrument_token))

    def engine(self, **overrides):
        engine = super().engine(**overrides)
        engine._marked_book = BOOK
        engine._marked_error = None
        return engine

    def orb(self, setup_id, symbol, **kw):
        kw.setdefault("instrument_token", TOKENS[symbol])
        return super().orb(setup_id, symbol, **kw)

    def guc(self, setup_id, symbol, **kw):
        kw.setdefault("instrument_token", TOKENS[symbol])
        return super().guc(setup_id, symbol, **kw)

    def moves(self, trade_id):
        return [json.loads(r["payload_json"]) for r in self.store.list_events(trade_id)
                if r["event_type"] == EVENT_STOP_MOVED]


class EngineOffTests(MarkedEngineTestCase):
    settings_overrides = {}

    def test_off_by_default_nothing_is_filtered(self) -> None:
        self.assertFalse(ArbSettings().marked_trails_enabled)
        del self.context.day_open
        engine = GucEngineTestCase.engine(self)
        self.run_triggers(engine, self.orb("o1", "EEE"), self.guc("g1", "DDD", price=120.0, stop=117.5))
        self.assertEqual(self.store.get("o1").state, ExecutionState.PROTECTED)
        self.assertNotIn(M.MARKED_EXTRA_KEY, self.arb("o1"))
        self.assertIsNone(engine.arb_snapshot()["marked_trails"]["stocks"])


class EngineEntryTests(MarkedEngineTestCase):
    def test_an_unmarked_orb_stock_is_skipped_and_its_slot_stays_empty(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.orb("o1", "AAA"), self.orb("o2", "EEE"), self.orb("o3", "BBB"))
        self.assertEqual(self.skip_reason("o2"), M.REASON_UNMARKED)
        self.assertEqual(self.store.get("o1").state, ExecutionState.PROTECTED)
        self.assertEqual(self.store.get("o3").state, ExecutionState.PROTECTED)
        # Two entries, two stops: nothing else was bought in EEE's place.
        self.assertEqual(self.broker.market_place_count, 2)

    def test_an_unmarked_guc_stock_is_skipped(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.guc("g1", "EEE"))
        self.assertEqual(self.skip_reason("g1"), M.REASON_UNMARKED)
        self.assertEqual(self.broker.market_place_count, 0)

    def test_an_entry_beyond_the_level_is_skipped(self) -> None:
        self.opens[TOKENS["AAA"]] = 107.0            # L = 109.14 < 110
        engine = self.engine()
        self.run_triggers(engine, self.orb("o1", "AAA"), self.guc("g1", "AAA"))
        self.assertEqual(self.skip_reason("o1"), M.REASON_OUTSIDE_LEVEL)
        self.assertAlmostEqual(self.arb("o1")[M.MARKED_EXTRA_KEY]["level"], 109.14)

    def test_no_open_no_trade(self) -> None:
        self.opens.pop(TOKENS["AAA"])
        engine = self.engine()
        self.run_triggers(engine, self.orb("o1", "AAA"))
        self.assertEqual(self.skip_reason("o1"), M.REASON_OPEN_UNKNOWN)

    def test_an_unreadable_table_fails_closed(self) -> None:
        engine = self.engine()
        engine._marked_book, engine._marked_error = None, "cannot read"
        self.run_triggers(engine, self.orb("o1", "AAA"), self.guc("g1", "BBB"))
        self.assertEqual(self.skip_reason("o1"), M.REASON_TABLE_UNAVAILABLE)
        self.assertEqual(self.skip_reason("g1"), M.REASON_TABLE_UNAVAILABLE)

    def test_the_plan_is_stamped_at_entry(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.orb("o1", "AAA"))
        plan = self.arb("o1")[M.MARKED_EXTRA_KEY]
        self.assertEqual((plan["day_open"], plan["rule"]), (108.0, {"trail_pct": 1.0, "mode": "plain"}))
        self.assertAlmostEqual(plan["level"], 110.16)

    def test_the_session_config_and_snapshot_carry_the_switch(self) -> None:
        cfg = arb_session_config(risk_scale=0.5, marked_trails_enabled=True, marked_trails_drop_below_actual=True)
        self.assertTrue(cfg.arb.marked_trails_enabled and cfg.arb.marked_trails_drop_below_actual)
        snap = self.engine().arb_snapshot()["marked_trails"]
        self.assertEqual((snap["enabled"], snap["stocks"], snap["error"]), (True, 4, None))


class EngineTrailTests(MarkedEngineTestCase):
    """ORB AAA: entry 110, stop 107.5 (R 2.5), open 108 -> L 110.16, 1% trail."""

    def enter(self, engine, setup_id="o1", symbol="AAA", **kw):
        self.run_triggers(engine, self.orb(setup_id, symbol, **kw))
        stored = self.store.get(setup_id)
        self.assertEqual(stored.state, ExecutionState.PROTECTED)
        return stored

    def bar_tick(self, engine, token, *bars, ltp=None, symbol="AAA"):
        self.context.bars.setdefault(token, []).extend(bars)
        if ltp is not None:
            self.broker.last_prices[symbol] = ltp
        self.clock.advance(60)
        engine.tick()

    def test_untouched_trades_keep_the_lock_rule(self) -> None:
        engine = self.engine()
        self.enter(engine)
        self.bar_tick(engine, 1, lbar("10:31", 110.1, 109.0, 110.0), ltp=110.0)
        stored = self.store.get("o1")
        self.assertEqual(stored.stop_price, 107.5)
        self.assertNotIn(M.MARKED_SWITCHED_KEY, stored.extra)

    def test_the_touch_switches_the_stop_to_the_trail(self) -> None:
        engine = self.engine()
        self.enter(engine)
        self.bar_tick(engine, 1, lbar("10:31", 111.0, 110.1, 110.9), ltp=110.9)
        stored = self.store.get("o1")
        self.assertEqual(stored.stop_price, 109.85)                  # 111 * 0.99 = 109.89, down a tick
        self.assertEqual(stored.extra[M.MARKED_SWITCHED_KEY], key("10:31"))
        (move,) = self.moves("o1")
        self.assertEqual((move["rule"], move["switch"], move["touched"]), ("marked_trail", True, key("10:31")))
        self.assertEqual(self.broker.orders[stored.stop_order_id].trigger_price, 109.85)
        # A higher high tightens it; a lower one leaves it.
        self.bar_tick(engine, 1, lbar("10:32", 113.0, 111.0, 112.5), ltp=112.5)
        self.assertEqual(self.store.get("o1").stop_price, 111.85)   # 113 * 0.99 = 111.87
        self.bar_tick(engine, 1, lbar("10:33", 112.8, 112.0, 112.2), ltp=112.2)
        self.assertEqual(self.store.get("o1").stop_price, 111.85)

    def test_the_switch_may_loosen_past_the_lock_and_the_initial_stop(self) -> None:
        # DDD: L = 108 * 1.03 = 111.24, 4% trail. Stop 109 -> R 1, +1R 111.
        engine = self.engine()
        self.enter(engine, "o1", "DDD", stop=109.0)
        self.bar_tick(engine, 4, lbar("10:31", 111.1, 110.5, 111.0), ltp=111.0, symbol="DDD")
        self.assertEqual(self.store.get("o1").stop_price, 110.25)    # the lock rule: +0.25R
        self.bar_tick(engine, 4, lbar("10:32", 111.5, 111.0, 111.4), ltp=111.4, symbol="DDD")
        stored = self.store.get("o1")
        self.assertEqual(stored.stop_price, 107.0)                   # 111.5 * 0.96 = 107.04
        self.assertTrue(stored.extra[M.MARKED_SWITCHED_KEY])
        self.bar_tick(engine, 4, lbar("10:33", 115.0, 111.0, 114.5), ltp=114.5, symbol="DDD")
        self.assertEqual(self.store.get("o1").stop_price, 110.4)     # 115 * 0.96
        # The lock rule (now far past +2R) no longer moves it.
        self.bar_tick(engine, 4, lbar("10:34", 114.9, 114.0, 114.8), ltp=114.8, symbol="DDD")
        self.assertEqual(self.store.get("o1").stop_price, 110.4)

    def test_a_restart_keeps_the_trail_where_it_was(self) -> None:
        engine = self.engine()
        self.enter(engine, "o1", "DDD", stop=109.0)
        self.bar_tick(engine, 4, lbar("10:31", 111.1, 110.5, 111.0), lbar("10:32", 111.5, 111.0, 111.4),
                      lbar("10:33", 115.0, 111.0, 114.5), ltp=114.5, symbol="DDD")
        self.assertEqual(self.store.get("o1").stop_price, 110.4)
        restarted = self.engine()
        self.clock.advance(60)
        restarted.tick()
        self.assertEqual(self.store.get("o1").stop_price, 110.4)

    def test_a_trail_stop_through_the_market_is_sold_at_market(self) -> None:
        engine = self.engine()
        self.enter(engine)
        self.bar_tick(engine, 1, lbar("10:31", 111.0, 110.1, 110.9), ltp=109.5)   # target 109.85 > ltp
        stored = self.store.get("o1")
        self.assertIn(stored.state, (ExecutionState.EXIT_SUBMITTED, ExecutionState.CLOSED))
        self.assertEqual(stored.extra.get("exit_reason"), CloseReason.MARKED_TRAIL_THROUGH_MARKET.value)

    def test_at_the_modification_cap_the_stop_is_cancelled_and_replaced(self) -> None:
        engine = self.engine()
        stored = self.enter(engine)
        old = stored.stop_order_id
        stored.extra[STOP_MODS_KEY] = {str(old): 20}
        self.store.save(stored)
        self.bar_tick(engine, 1, lbar("10:31", 111.0, 110.1, 110.9), ltp=110.9)
        stored = self.store.get("o1")
        self.assertNotEqual(stored.stop_order_id, old)
        self.assertEqual(stored.stop_price, 109.85)
        self.assertTrue(any(r["event_type"] == EVENT_STOP_REPLACED for r in self.store.list_events("o1")))

    def test_the_unmarked_side_keeps_the_lock_rule(self) -> None:
        # BBB has no sell rule: a short within level trades with normal exits.
        engine = self.engine()
        self.run_triggers(engine, self.orb("o1", "BBB", direction="DOWN", price=106.0, stop=108.5))
        self.assertEqual(self.store.get("o1").state, ExecutionState.PROTECTED)
        self.assertIsNone(self.arb("o1")[M.MARKED_EXTRA_KEY]["rule"])
        self.bar_tick(engine, 2, lbar("10:31", 105.0, 102.0, 102.5), ltp=102.5, symbol="BBB")
        self.assertEqual(self.moves("o1")[0]["rule"], "lock")


class EngineHaltTests(MarkedEngineTestCase):
    def test_an_orb_halt_flattens_a_trailed_orb_trade_and_guc_keeps_trailing(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.guc("g1", "AAA"), self.orb("o1", "BBB"), self.orb("o2", "CCC"))
        # Both g1 (GUC) and o1 (ORB) touch their level and switch to the trail.
        self.context.bars[1] = [lbar("10:31", 111.0, 110.1, 110.9)]
        self.context.bars[2] = [lbar("10:31", 111.0, 110.1, 110.9)]
        self.broker.last_prices.update(AAA=110.9, BBB=110.9)
        self.clock.advance(60)
        engine.tick()
        for trade_id in ("g1", "o1"):
            self.assertTrue(self.store.get(trade_id).extra.get(M.MARKED_SWITCHED_KEY), trade_id)
        # ORB marks far below its hard day stop: the ORB sleeve is flattened,
        # trailed trade included (no exemption).
        self.broker.last_prices.update(BBB=104.0, CCC=104.0)
        self.clock.advance(1)
        engine.tick()
        self.clock.advance(1)
        engine.tick()
        self.assertEqual(engine.orb_halt_reason, CloseReason.HARD_DAY_STOP)
        for trade_id in ("o1", "o2"):
            stored = self.store.get(trade_id)
            self.assertEqual((stored.state, stored.extra["close_reason"]), (ExecutionState.CLOSED, "hard_day_stop"))
        # GUC is not in the ORB sleeve: still protected, still trailing.
        self.context.bars[1].append(lbar("10:32", 113.0, 111.0, 112.5))
        self.broker.last_prices["AAA"] = 112.5
        self.clock.advance(60)
        engine.tick()
        guc = self.store.get("g1")
        self.assertEqual((guc.state, guc.stop_price), (ExecutionState.PROTECTED, 111.85))


# -----------------------------------------------------------------------------
# The replay
# -----------------------------------------------------------------------------


class ReplayTests(unittest.TestCase):
    """AAA opens 100 (L 102), EEE unmarked. Scale 1, ORB risk 2,500."""

    SETTINGS = ArbSettings(funnel_enabled=False, marked_trails_enabled=True)

    @staticmethod
    def day_bars(rows):
        return [ReplayBar(f"{DAY}T{hhmm}", o, h, l, c) for hhmm, o, h, l, c in rows]

    def cand(self, symbol, minute="09:30", price=101.0, stop=100.0, direction="UP"):
        return ReplayCandidate(setup_id=f"{symbol}{minute}", strategy="ORB", symbol=symbol, direction=direction,
                               minute=f"{DAY}T{minute}", moment=f"{DAY}T{minute}:00+05:30",
                               trigger_price=price, stop=stop, tick_size=0.05)

    def replay(self, cands, bars, settings=None, book=BOOK):
        return replay_day(cands, bars, settings or self.SETTINGS, capital_rupees=500_000.0, marked_book=book)

    def base(self):
        rows = [("09:15", 100.0, 100.5, 99.8, 100.2), ("09:30", 100.9, 101.2, 100.8, 101.1),
                ("09:31", 101.1, 102.5, 101.0, 102.4),      # touches L = 102: run 102.5 -> 101.475
                ("09:32", 102.4, 103.0, 102.2, 102.9),      # run 103 -> 101.97 -> 101.95
                ("09:33", 102.0, 102.1, 101.5, 101.6)]      # trades through 101.95
        return {"AAA": self.day_bars(rows), "BBB": self.day_bars(rows)}

    def test_unmarked_and_out_of_level_trades_are_skipped_before_the_budget(self) -> None:
        bars = self.base()
        bars["EEE"] = bars["BBB"]
        day = self.replay([self.cand("EEE"), self.cand("AAA", price=102.1, stop=101.0)], bars)
        reasons = {t.candidate.symbol: t.reason for t in day.trades}
        self.assertEqual(reasons, {"EEE": M.REASON_UNMARKED, "AAA": M.REASON_OUTSIDE_LEVEL})

    def test_a_touched_trade_exits_on_its_trail(self) -> None:
        day = self.replay([self.cand("AAA")], self.base())
        (trade,) = [t for t in day.trades if t.entered]
        self.assertEqual((trade.exit_reason, trade.exit_minute, trade.exit_price),
                         (EXIT_MARKED_TRAIL, f"{DAY}T09:33", 101.95))

    def test_flag_off_is_the_old_replay(self) -> None:
        settings = dataclasses.replace(self.SETTINGS, marked_trails_enabled=False)
        bars = self.base()
        old = replay_day([self.cand("BBB"), self.cand("AAA")], bars, settings, capital_rupees=500_000.0)
        new = self.replay([self.cand("BBB"), self.cand("AAA")], bars, settings)
        self.assertEqual(old, new)

    def test_a_halt_flattens_a_trailed_trade(self) -> None:
        # AAA touches its level at 09:31 and is on its trail (stop 101.45);
        # CCC (marked, L 102) falls, and at 09:32 the day is below the hard
        # stop: AAA is closed at the halt minute, not left to trail.
        rows_a = [("09:15", 100.0, 100.5, 99.8, 100.2), ("09:30", 100.9, 101.2, 100.8, 101.1),
                  ("09:31", 101.1, 102.5, 101.0, 102.0), ("09:32", 102.0, 102.0, 101.6, 101.6),
                  ("09:33", 101.6, 103.0, 101.6, 102.9), ("09:34", 102.9, 103.6, 102.8, 103.5)]
        rows_c = [("09:15", 100.0, 100.5, 99.8, 100.2), ("09:30", 100.9, 101.2, 100.8, 101.1),
                  ("09:31", 101.0, 101.0, 99.0, 99.5), ("09:32", 99.5, 99.5, 96.5, 96.5),
                  ("09:33", 96.5, 97.0, 96.0, 96.2)]
        bars = {"AAA": self.day_bars(rows_a), "CCC": self.day_bars(rows_c)}
        cands = [self.cand("AAA"), self.cand("CCC", price=101.0, stop=95.0)]
        free = self.replay(cands, bars)
        aaa = next(t for t in free.trades if t.candidate.symbol == "AAA")
        self.assertIsNone(free.halt_reason)
        self.assertEqual(aaa.exit_reason, "eod")                     # trails to the end of the data
        day = self.replay(cands, bars, dataclasses.replace(self.SETTINGS, hard_day_stop=300.0))
        self.assertEqual((day.halt_reason, day.halt_minute), ("hard_day_stop", f"{DAY}T09:32"))
        aaa = next(t for t in day.trades if t.candidate.symbol == "AAA")
        self.assertEqual((aaa.exit_reason, aaa.exit_minute, aaa.exit_price), (EXIT_DAY_HALT, f"{DAY}T09:32", 101.6))


if __name__ == "__main__":
    unittest.main()
