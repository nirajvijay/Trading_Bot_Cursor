"""ARB's lock-rule trailing: the pure rule, the bar reader, and the engine.

The rule's parity with the backtest is in tests/test_arb_replay_golden.py.
Here: its edges, the SQLite bar reader, and how the engine acts on it (when
it moves a stop, when it refuses, what a restart does).
"""
from __future__ import annotations

import dataclasses
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from engine_arb import position_live_risk
from engine_arb_context import ArbContextError, LiveArbContext
from engine_core import ENTRY_FILLED_AT_KEY
from engine_lock import (
    STAGE_GIVEBACK,
    STAGE_INITIAL,
    STAGE_LOCKED,
    LockBar,
    LockState,
    advance,
    fold,
    minute_key,
    stage_of_stop,
)
from engine_runloop import STEP_TRAIL
from engine_trailing import EVENT_STOP_MOVED, TRAIL_ENABLED_KEY
from live_one_minute_candle_writer import CREATE_LIVE_1M_CANDLES_TABLE_SQL
from tests.test_engine_arb_core import DAY, ArbEngineTestCase


def key(hhmm: str) -> str:
    return f"{DAY}T{hhmm}:00+05:30"


def bar(hhmm: str, high: float, low: float, close: float) -> LockBar:
    return LockBar(key(hhmm), high, low, close)


LONG = dict(direction="UP", entry=100.0, initial_stop=98.0, tick_size=0.05)   # R = 2
SHORT = dict(direction="DOWN", entry=100.0, initial_stop=102.0, tick_size=0.05)


class LockRuleTests(unittest.TestCase):
    def one(self, b: LockBar, state: LockState = LockState(), entry_bar: bool = False, **kw) -> LockState:
        return advance(state, b, entry_bar=entry_bar, **(kw or LONG))

    def test_below_one_r_there_is_no_target(self) -> None:
        state = self.one(bar("10:31", 101.95, 99.5, 101.0))
        self.assertIsNone(state.target)
        self.assertEqual(state.stage, STAGE_INITIAL)
        self.assertEqual(state.peak, 101.95)

    def test_at_exactly_one_r_the_stop_goes_to_a_quarter_r(self) -> None:
        state = self.one(bar("10:31", 102.0, 99.5, 101.5))
        self.assertEqual(state.target, 100.5)
        self.assertEqual(state.stage, STAGE_LOCKED)

    def test_one_r_is_judged_exactly_not_in_floating_point(self) -> None:
        # 100.1 - 98.5 is 1.5999999999999943 as a float; the peak is exactly +1R.
        state = advance(
            LockState(), bar("10:31", 101.7, 100.0, 101.2),
            direction="UP", entry=100.1, initial_stop=98.5, tick_size=0.05,
        )
        self.assertEqual(state.target, 100.5)

    def test_the_lock_level_is_rounded_away_from_the_market(self) -> None:
        # R = 3.05: +0.25R is 100.7625 -> 100.75 for a long, 99.25 for a short.
        up = advance(LockState(), bar("10:31", 103.05, 100.0, 102.0),
                     direction="UP", entry=100.0, initial_stop=96.95, tick_size=0.05)
        down = advance(LockState(), bar("10:31", 100.0, 96.95, 98.0),
                       direction="DOWN", entry=100.0, initial_stop=103.05, tick_size=0.05)
        self.assertEqual(up.target, 100.75)
        self.assertEqual(down.target, 99.25)

    def test_from_two_r_the_stop_keeps_a_quarter_of_the_peak_gain(self) -> None:
        state = self.one(bar("10:31", 106.0, 100.0, 105.5))   # +3R: 25% of 6 = 1.5
        self.assertEqual(state.target, 101.5)
        self.assertEqual(state.stage, STAGE_GIVEBACK)

    def test_the_stop_is_never_closer_than_one_tick_inside_the_close(self) -> None:
        state = self.one(bar("10:31", 102.0, 100.0, 100.2))   # level 100.5, close 100.2
        self.assertEqual(state.target, 100.15)

    def test_the_target_only_tightens(self) -> None:
        state = self.one(bar("10:31", 106.0, 100.0, 105.5))
        later = self.one(bar("10:32", 104.0, 101.6, 101.7), state)   # cap would be 101.65
        self.assertEqual(later.target, 101.5)
        self.assertEqual(later.peak, 106.0)
        self.assertEqual(later.stage, STAGE_GIVEBACK)

    def test_the_entry_bar_counts_only_its_close(self) -> None:
        early = self.one(bar("10:30", 105.0, 99.0, 101.0), entry_bar=True)
        self.assertIsNone(early.target)
        self.assertEqual(early.peak, 101.0)
        closed_high = self.one(bar("10:30", 105.0, 99.0, 102.5), entry_bar=True)
        self.assertEqual(closed_high.target, 100.5)

    def test_a_short_mirrors_a_long(self) -> None:
        state = advance(LockState(), bar("10:31", 100.0, 94.0, 94.5), **SHORT)   # +3R
        self.assertEqual(state.target, 98.5)
        self.assertEqual(state.peak, 94.0)
        capped = advance(LockState(), bar("10:31", 100.0, 98.0, 99.8), **SHORT)
        self.assertEqual(capped.target, 99.85)

    def test_fold_skips_bars_before_entry_and_bars_already_folded(self) -> None:
        bars = [bar("10:29", 110.0, 99.0, 105.0), bar("10:30", 101.0, 99.5, 100.5),
                bar("10:31", 102.2, 100.4, 102.0)]
        state = fold(bars, entry_minute=key("10:30"), **LONG)
        self.assertEqual((state.bars, state.target, state.last_minute), (2, 100.5, key("10:31")))
        again = fold(bars, entry_minute=key("10:30"), state=state, **LONG)
        self.assertEqual(again, state)

    def test_a_zero_r_never_arms(self) -> None:
        state = advance(LockState(), bar("10:31", 105.0, 100.0, 104.0),
                        direction="UP", entry=100.0, initial_stop=100.0, tick_size=0.05)
        self.assertIsNone(state.target)

    def test_stage_is_read_from_the_stop_kite_holds(self) -> None:
        kw = dict(direction="UP", entry=100.0, initial_stop=96.95, tick_size=0.05)
        self.assertEqual(stage_of_stop(stop=96.95, **kw), STAGE_INITIAL)
        self.assertEqual(stage_of_stop(stop=100.75, **kw), STAGE_LOCKED)
        self.assertEqual(stage_of_stop(stop=101.5, **kw), STAGE_GIVEBACK)
        self.assertIsNone(stage_of_stop(stop=None, **kw))
        short = dict(direction="DOWN", entry=100.0, initial_stop=103.05, tick_size=0.05)
        self.assertEqual(stage_of_stop(stop=99.25, **short), STAGE_LOCKED)
        self.assertEqual(stage_of_stop(stop=103.05, **short), STAGE_INITIAL)

    def test_minute_key_is_the_candle_time_text(self) -> None:
        moment = datetime(2026, 9, 22, 5, 0, 42, tzinfo=timezone.utc)
        self.assertEqual(minute_key(moment), "2026-09-22T10:30:00+05:30")


class LockBarReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "live.db"
        conn = sqlite3.connect(self.db)
        conn.execute(CREATE_LIVE_1M_CANDLES_TABLE_SQL)
        rows = [
            (7, "AAA", key("10:29"), 109.0, 110.0, 108.0, 109.5, 0),
            (7, "AAA", key("10:30"), 110.0, 111.0, 109.5, 110.5, 0),
            (7, "AAA", key("10:31"), 110.5, 113.5, 110.2, 113.0, 1),   # partial
            (7, "AAA", key("10:32"), 113.0, 114.0, 112.0, 113.2, 0),
            (8, "BBB", key("10:31"), 50.0, 55.0, 49.0, 54.0, 0),
        ]
        for token, symbol, minute, o, h, l, c, partial in rows:
            conn.execute(
                "INSERT INTO live_1m_candles VALUES (?, ?, ?, ?, ?, ?, ?, ?, 10, 5, 1, ?, ?, ?, ?)",
                (token, symbol, minute, DAY, o, h, l, c, "minute_transition",
                 0 if partial else 1, partial, "x"),
            )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_reads_this_instruments_complete_bars_from_the_entry_minute(self) -> None:
        bars = LiveArbContext(self.db).lock_bars(instrument_token=7, since_minute=key("10:30"))
        self.assertEqual([b.minute for b in bars], [key("10:30"), key("10:32")])
        self.assertEqual((bars[1].high, bars[1].low, bars[1].close), (114.0, 112.0, 113.2))

    def test_after_minute_returns_only_newer_bars(self) -> None:
        context = LiveArbContext(self.db)
        bars = context.lock_bars(instrument_token=7, since_minute=key("10:30"), after_minute=key("10:30"))
        self.assertEqual([b.minute for b in bars], [key("10:32")])
        self.assertEqual(
            context.lock_bars(instrument_token=7, since_minute=key("10:30"), after_minute=key("10:32")), []
        )

    def test_a_missing_database_is_an_error_not_an_empty_answer(self) -> None:
        with self.assertRaises(ArbContextError):
            LiveArbContext(Path(self._tmp.name) / "none.db").lock_bars(
                instrument_token=7, since_minute=key("10:30")
            )


class EngineLockTests(ArbEngineTestCase):
    """Entry at 110.00 with the stop at 106.95: R = 3.05, +1R = 113.05,
    +0.25R = 110.7625 -> 110.75 on the grid. The clock starts at 10:30."""

    def enter(self, engine, setup_id="s1", symbol="AAA", token=1, **kw):
        candidate = dataclasses.replace(self.trigger(setup_id, symbol, score=2, **kw), instrument_token=token)
        self.run_triggers(engine, candidate)
        stored = self.store.get(setup_id)
        self.assertEqual(stored.state.value, "protected")
        return stored

    def moves(self, trade_id="s1"):
        return [
            dict(row) for row in self.store.list_events(trade_id) if row["event_type"] == EVENT_STOP_MOVED
        ]

    def test_the_fill_time_is_stamped_at_entry(self) -> None:
        stored = self.enter(self.engine())
        self.assertEqual(stored.extra[ENTRY_FILLED_AT_KEY], self.clock.now.isoformat())
        self.assertEqual((stored.entry_price, stored.stop_price), (110.0, 106.95))

    def test_without_a_completed_bar_nothing_moves_however_far_price_runs(self) -> None:
        engine = self.engine()
        self.enter(engine)
        self.broker.last_prices["AAA"] = 117.0   # past +2R: the legacy ladder would have moved
        self.clock.advance(20)
        engine.tick()
        self.assertEqual(self.store.get("s1").stop_price, 106.95)
        self.assertEqual(self.moves(), [])

    def test_a_bar_that_reached_one_r_locks_a_quarter_r_and_frees_the_risk(self) -> None:
        engine = self.engine()
        self.enter(engine)
        self.assertGreater(position_live_risk(self.store.get("s1")), 0)
        self.context.bars[1] = [bar("10:31", 113.05, 110.2, 112.0)]
        self.broker.last_prices["AAA"] = 112.0
        self.clock.advance(90)
        engine.tick()
        stored = self.store.get("s1")
        self.assertEqual(stored.stop_price, 110.75)
        self.assertEqual(position_live_risk(stored), 0.0)
        (event,) = self.moves()
        import json
        payload = json.loads(event["payload_json"])
        self.assertEqual((payload["rule"], payload["stage"], payload["source"]), ("lock", "locked", "auto"))
        self.assertEqual((payload["peak"], payload["bar"]), (113.05, key("10:31")))
        # The freed risk lets a full-size trade in (see PR A D6).
        self.run_triggers(engine, dataclasses.replace(self.trigger("s2", "BBB", score=3), instrument_token=2))
        self.assertEqual(self.store.get("s2").extra["risk_cap_rupees"], 6000)

    def test_the_entry_minutes_high_does_not_count(self) -> None:
        engine = self.engine()
        self.enter(engine)
        self.context.bars[1] = [bar("10:30", 114.0, 109.0, 110.4)]
        self.broker.last_prices["AAA"] = 110.4
        self.clock.advance(60)
        engine.tick()
        self.assertEqual(self.store.get("s1").stop_price, 106.95)

    def test_two_r_keeps_a_quarter_of_the_peak_gain(self) -> None:
        engine = self.engine()
        self.enter(engine)
        self.context.bars[1] = [bar("10:31", 118.0, 110.0, 117.5)]   # +8: keep 2.00
        self.broker.last_prices["AAA"] = 117.5
        self.clock.advance(90)
        engine.tick()
        self.assertEqual(self.store.get("s1").stop_price, 112.0)

    def test_a_target_at_or_through_the_market_waits_for_the_price(self) -> None:
        engine = self.engine()
        self.enter(engine)
        self.context.bars[1] = [bar("10:31", 113.5, 110.0, 113.0)]
        self.broker.last_prices["AAA"] = 110.75    # exactly at the target
        self.clock.advance(90)
        engine.tick()
        self.assertEqual(self.store.get("s1").stop_price, 106.95)
        self.assertEqual(engine.failures.consecutive.get(STEP_TRAIL, 0), 0)
        self.broker.last_prices["AAA"] = 110.8
        self.clock.advance(1)
        engine.tick()
        self.assertEqual(self.store.get("s1").stop_price, 110.75)

    def test_no_price_this_tick_means_no_move(self) -> None:
        engine = self.engine()
        self.enter(engine)
        self.context.bars[1] = [bar("10:31", 113.5, 110.0, 113.0)]
        del self.broker.last_prices["AAA"]
        self.clock.advance(90)
        engine.tick()
        self.assertEqual(self.store.get("s1").stop_price, 106.95)

    def test_a_failed_bar_read_is_counted_and_other_stops_still_move(self) -> None:
        engine = self.engine()
        self.enter(engine)
        self.enter(engine, "s2", "BBB", token=2)
        self.context.bars[2] = [bar("10:31", 113.5, 110.0, 113.0)]
        self.broker.last_prices["BBB"] = 113.0
        real = self.context.lock_bars

        def flaky(*, instrument_token, since_minute, after_minute=None):
            if instrument_token == 1:
                raise ArbContextError("observation db read failed: locked")
            return real(instrument_token=instrument_token, since_minute=since_minute, after_minute=after_minute)

        self.context.lock_bars = flaky
        self.clock.advance(90)
        engine.tick()
        self.assertEqual(self.store.get("s1").stop_price, 106.95)
        self.assertEqual(self.store.get("s2").stop_price, 110.75)
        self.assertEqual(engine.failures.consecutive.get(STEP_TRAIL), 1)
        self.assertIn("lock bars", engine.failures.last_error)
        self.assertFalse(engine.entries_paused)

    def test_auto_trail_off_is_respected(self) -> None:
        engine = self.engine()
        stored = self.enter(engine)
        stored.extra[TRAIL_ENABLED_KEY] = False
        self.store.save(stored)
        self.context.bars[1] = [bar("10:31", 113.5, 110.0, 113.0)]
        self.broker.last_prices["AAA"] = 113.0
        self.clock.advance(90)
        engine.tick()
        self.assertEqual(self.store.get("s1").stop_price, 106.95)

    def test_a_restart_recomputes_the_same_stop_and_moves_nothing_twice(self) -> None:
        engine = self.engine()
        self.enter(engine)
        self.context.bars[1] = [bar("10:31", 113.5, 110.0, 113.0), bar("10:32", 113.2, 112.0, 112.4)]
        self.broker.last_prices["AAA"] = 112.4
        self.clock.advance(150)
        engine.tick()
        self.assertEqual(self.store.get("s1").stop_price, 110.75)
        restarted = self.engine()
        self.context.bar_reads.clear()
        self.clock.advance(1)
        restarted.tick()
        self.assertEqual(self.store.get("s1").stop_price, 110.75)
        self.assertEqual(len(self.moves()), 1)
        # Rebuilt from the entry minute, not from memory.
        self.assertEqual(self.context.bar_reads[0], (1, key("10:30"), None))

    def test_a_restart_before_the_move_still_makes_it(self) -> None:
        engine = self.engine()
        self.enter(engine)
        self.context.bars[1] = [bar("10:31", 113.5, 110.0, 113.0)]
        self.broker.last_prices["AAA"] = 113.0
        self.clock.advance(90)
        self.engine().tick()
        self.assertEqual(self.store.get("s1").stop_price, 110.75)

    def test_only_bars_newer_than_the_last_one_are_read(self) -> None:
        engine = self.engine()
        self.enter(engine)
        self.context.bars[1] = [bar("10:31", 111.0, 110.0, 110.5)]
        self.clock.advance(90)
        engine.tick()
        self.context.bar_reads.clear()
        self.clock.advance(1)
        engine.tick()
        self.assertEqual(self.context.bar_reads, [(1, key("10:30"), key("10:31"))])

    def test_a_short_locks_above_the_market(self) -> None:
        engine = self.engine()
        candidate = dataclasses.replace(
            self.trigger("s1", "AAA", score=2), direction="DOWN",
            pullback_swing_high=113.0, pullback_swing_low=107.0,
        )
        self.run_triggers(engine, candidate)
        stored = self.store.get("s1")
        self.assertEqual((stored.entry_price, stored.stop_price), (110.0, 113.05))
        self.context.bars[1] = [bar("10:31", 110.0, 106.95, 108.0)]
        self.broker.last_prices["AAA"] = 108.0
        self.clock.advance(90)
        engine.tick()
        self.assertEqual(self.store.get("s1").stop_price, 109.25)

    def test_a_stop_already_tighter_is_left_alone(self) -> None:
        engine = self.engine()
        stored = self.enter(engine)
        self.broker.last_prices["AAA"] = 114.0
        self.broker.modify_slm(stored.stop_order_id, 111.5)   # tightened by hand in Kite
        self.context.bars[1] = [bar("10:31", 113.5, 110.0, 113.0)]
        self.clock.advance(90)
        engine.tick()
        self.assertEqual(self.store.get("s1").stop_price, 111.5)
        self.assertEqual(self.moves(), [])


class ArbSnapshotTests(ArbEngineTestCase):
    """What the desk's ARB strip is fed (display only)."""

    def test_a_legacy_engine_has_no_snapshot(self) -> None:
        from engine_config import SessionRiskConfig
        from engine_risk import RiskPolicy

        legacy = SessionRiskConfig()
        engine = self.engine(session_config=legacy, risk_policy=RiskPolicy(legacy.to_risk_limits()),
                             arb_context=None)
        self.assertIsNone(engine.arb_snapshot())

    def test_the_budget_as_the_engine_sees_it(self) -> None:
        engine = self.engine()
        self.save_closed("lost", "ZZZ", -400.0)
        candidate = dataclasses.replace(self.trigger("s1", "AAA", score=2), instrument_token=1)
        self.run_triggers(engine, candidate)
        snap = engine.arb_snapshot()
        stored = self.store.get("s1")
        self.assertEqual(stored.extra["risk_cap_rupees"], 2250.0)        # 4500 halved after the loser
        self.assertEqual((snap["risk_scale"], snap["open_risk_cap"], snap["hard_day_stop"]), (1.0, 6000.0, 5000.0))
        self.assertEqual((snap["losers_today"], snap["half_size"], snap["score0_used"]), (1, True, 0))
        self.assertEqual(snap["live_risk"], round(position_live_risk(stored), 2))
        self.assertEqual(snap["room"], round(6000.0 - snap["live_risk"], 2))    # the day is red: no house money
        self.assertLess(snap["day_mtm"], -400.0)
        self.assertTrue(snap["day_check_ok"])
        self.assertEqual((snap["lock_armed"], snap["lock_line"], snap["halted_reason"]), (False, None, None))
        self.assertEqual((snap["orb_cutoff"], snap["funnel_cutoff"]), ("11:01", "13:00"))

    def test_a_halt_is_reported_with_when_and_at_what(self) -> None:
        engine = self.engine()
        candidate = dataclasses.replace(self.trigger("s1", "AAA", score=3), instrument_token=1)
        self.run_triggers(engine, candidate)
        self.broker.last_prices["AAA"] = 107.0      # about -1R on a 6,000 trade: past the hard stop
        self.clock.advance(5)
        engine.tick()
        snap = engine.arb_snapshot()
        self.assertEqual(snap["halted_reason"], "hard_day_stop")
        self.assertLessEqual(snap["halted_mtm"], -5000.0)
        self.assertIsNotNone(snap["halted_at"])

    def test_the_capital_and_stage_are_stamped_on_every_arb_trade(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, dataclasses.replace(self.trigger("s1", "AAA", score=2), instrument_token=1))
        info = self.arb("s1")
        self.assertEqual((info["capital"], info["risk_scale"]), (500_000.0, 1.0))


if __name__ == "__main__":
    unittest.main()
