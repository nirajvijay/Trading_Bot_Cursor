"""Auto trailing (the 0.5R schedule) and manual one-tick nudges.

The lifecycle fixture enters AAA long at 110.00 with the structural stop at
106.95, so R = 3.05 and one 0.5R step = 1.525.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import unittest

from engine_commands import CommandKind
from engine_runloop import STEP_TRAIL
from engine_trailing import (
    EVENT_STOP_ADOPTED,
    EVENT_STOP_MOVED,
    EVENT_STOP_REPLACED,
    INITIAL_STOP_KEY,
    REPLACE_AT_MODIFICATIONS,
    SOURCE_AUTO,
    SOURCE_KITE,
    SOURCE_MANUAL,
    STOP_MODS_KEY,
    STOP_MOVE_PENDING_KEY,
    TRAIL_ENABLED_KEY,
    adopted_source,
    nudge_target,
    resolve_stop,
    round_stop,
    schedule_stop,
)
from engine_types import ExecutionState
from tests.test_engine_lifecycle import LifecycleTestCase, candidate
from trading_engine_broker import KiteBroker

ENTRY = 110.0
INITIAL_STOP = 106.95
R = ENTRY - INITIAL_STOP  # 3.05


# ----------------------------------------------------------------------
# The pure rules
# ----------------------------------------------------------------------


class ScheduleTests(unittest.TestCase):
    def long(self, ltp: float):
        return schedule_stop(direction="UP", entry=ENTRY, r_pts=R, ltp=ltp, tick_size=0.05)

    def test_nothing_moves_below_half_r(self) -> None:
        self.assertIsNone(self.long(ENTRY + 0.5 * R - 0.05))
        self.assertIsNone(self.long(ENTRY - 1.0))

    def test_half_r_moves_the_stop_to_exactly_the_entry(self) -> None:
        self.assertEqual(self.long(ENTRY + 0.5 * R), ENTRY)

    def test_one_r_locks_in_half_r(self) -> None:
        # 110 + 1.525 = 111.525, rounded down to the grid for a long.
        self.assertEqual(self.long(ENTRY + 1.0 * R), 111.5)

    def test_the_gap_stays_one_step_at_any_height(self) -> None:
        # 3.2R of profit = 6 whole steps, so 5 steps (2.5R) are locked in.
        self.assertEqual(self.long(ENTRY + 3.2 * R), round_stop(ENTRY + 2.5 * R, 0.05, "UP"))

    def test_a_short_mirrors_the_long(self) -> None:
        entry, stop = 100.0, 102.0
        r = stop - entry
        at = lambda ltp: schedule_stop(
            direction="DOWN", entry=entry, r_pts=r, ltp=ltp, tick_size=0.05
        )
        self.assertIsNone(at(99.05))
        self.assertEqual(at(99.0), 100.0)
        self.assertEqual(at(98.0), 99.0)


class RoundingTests(unittest.TestCase):
    def test_a_long_stop_rounds_down_and_a_short_stop_rounds_up(self) -> None:
        self.assertEqual(round_stop(111.525, 0.05, "UP"), 111.5)
        self.assertEqual(round_stop(111.525, 0.05, "DOWN"), 111.55)

    def test_an_on_grid_price_is_left_alone(self) -> None:
        self.assertEqual(round_stop(111.5, 0.05, "UP"), 111.5)
        self.assertEqual(round_stop(111.5, 0.05, "DOWN"), 111.5)

    def test_each_instrument_uses_its_own_tick(self) -> None:
        self.assertEqual(round_stop(1234.37, 0.1, "UP"), 1234.3)
        self.assertEqual(round_stop(1234.37, 1.0, "DOWN"), 1235.0)

    def test_a_nudge_is_exactly_one_tick_of_that_instrument(self) -> None:
        self.assertEqual(nudge_target(106.95, 1, 0.05), 107.0)
        self.assertEqual(nudge_target(106.95, -1, 0.05), 106.9)
        self.assertEqual(nudge_target(1234.3, 1, 0.1), 1234.4)


class ResolveTests(unittest.TestCase):
    def resolve(self, candidate, *, current=108.0, auto_on=True, ltp=112.0, direction="UP"):
        return resolve_stop(
            direction=direction,
            current=current,
            candidate=candidate,
            floor=INITIAL_STOP,
            tick_size=0.05,
            auto_on=auto_on,
            ltp=ltp,
        )

    def test_auto_on_only_ever_tightens(self) -> None:
        self.assertEqual(self.resolve(108.05).target, 108.05)
        self.assertEqual(self.resolve(107.95).reason, "auto_trail_on_cannot_loosen")

    def test_auto_off_may_loosen_down_to_the_floor_but_not_past_it(self) -> None:
        self.assertEqual(self.resolve(107.95, auto_on=False).target, 107.95)
        self.assertEqual(self.resolve(INITIAL_STOP, auto_on=False).target, INITIAL_STOP)
        self.assertEqual(
            self.resolve(106.9, auto_on=False).reason, "beyond_initial_stop"
        )

    def test_a_stop_at_or_through_the_market_is_refused(self) -> None:
        self.assertEqual(self.resolve(112.0).reason, "at_or_through_market")
        self.assertEqual(self.resolve(112.05).reason, "at_or_through_market")

    def test_tightening_needs_a_price_from_this_tick(self) -> None:
        self.assertEqual(self.resolve(108.05, ltp=None).reason, "no_fresh_price")
        # Loosening never approaches the market, so it does not.
        self.assertEqual(self.resolve(107.95, auto_on=False, ltp=None).target, 107.95)

    def test_no_change_is_not_a_move(self) -> None:
        self.assertEqual(self.resolve(108.0).reason, "unchanged")


# ----------------------------------------------------------------------
# In the engine
# ----------------------------------------------------------------------


class TrailingTestCase(LifecycleTestCase):
    def open_one(self):
        self.candidates = [candidate("s1")]
        engine = self.engine()
        engine.tick()
        self.candidates = []
        stored = self.store.get("s1")
        assert stored is not None and stored.state == ExecutionState.PROTECTED
        return engine, stored

    def price(self, ltp: float) -> None:
        self.broker.last_prices["AAA"] = ltp

    def stored(self):
        pos = self.store.get("s1")
        assert pos is not None
        return pos

    def live_stops(self):
        return [
            o
            for o in self.broker.orders.values()
            if o.order_type in {"SL", "SL-M"} and o.status in {"TRIGGER PENDING", "OPEN"}
        ]

    def stop_events(self):
        return [
            (r["event_type"], json.loads(r["payload_json"]))
            for r in self.store.list_events("s1")
            if r["event_type"] in {EVENT_STOP_MOVED, EVENT_STOP_ADOPTED, EVENT_STOP_REPLACED}
        ]

    def command(self, kind: CommandKind, **payload) -> int:
        return self.queue.enqueue(kind, trade_id="s1", payload=payload)

    def command_status(self, command_id: int):
        conn = sqlite3.connect(self.path)
        try:
            return conn.execute(
                "SELECT status, result_json FROM engine_commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        finally:
            conn.close()


class ProtectionStartsTrailingTests(TrailingTestCase):
    def test_first_protection_records_the_floor_and_switches_auto_on(self) -> None:
        _, pos = self.open_one()
        self.assertEqual(pos.extra[INITIAL_STOP_KEY], INITIAL_STOP)
        self.assertIs(pos.extra[TRAIL_ENABLED_KEY], True)


class AutoTrailTests(TrailingTestCase):
    def test_half_r_moves_the_live_stop_to_breakeven(self) -> None:
        engine, _ = self.open_one()
        self.price(ENTRY + 0.5 * R)
        engine.tick()
        pos = self.stored()
        self.assertEqual(pos.stop_price, ENTRY)
        [stop] = self.live_stops()
        self.assertEqual(stop.trigger_price, ENTRY)
        self.assertEqual(stop.order_id, pos.stop_order_id)  # modified, not replaced
        [(event, payload)] = self.stop_events()
        self.assertEqual((event, payload["source"]), (EVENT_STOP_MOVED, SOURCE_AUTO))

    def test_the_stop_ratchets_up_and_never_back(self) -> None:
        engine, _ = self.open_one()
        self.price(ENTRY + 1.0 * R)
        engine.tick()
        self.assertEqual(self.stored().stop_price, 111.5)
        self.price(ENTRY + 0.6 * R)
        engine.tick()
        self.assertEqual(self.stored().stop_price, 111.5)

    def test_below_half_r_nothing_is_sent(self) -> None:
        engine, _ = self.open_one()
        self.price(ENTRY + 0.4 * R)
        engine.tick()
        self.assertEqual(self.broker.modify_count, 0)
        self.assertEqual(self.stored().stop_price, INITIAL_STOP)

    def test_a_repeated_price_does_not_modify_again(self) -> None:
        engine, _ = self.open_one()
        self.price(ENTRY + 0.5 * R)
        engine.tick()
        engine.tick()
        engine.tick()
        self.assertEqual(self.broker.modify_count, 1)

    def test_auto_off_leaves_the_stop_alone(self) -> None:
        engine, _ = self.open_one()
        self.command(CommandKind.SET_TRAIL, enabled=False)
        self.price(ENTRY + 2.0 * R)
        engine.tick()
        pos = self.stored()
        self.assertIs(pos.extra[TRAIL_ENABLED_KEY], False)
        self.assertEqual(pos.stop_price, INITIAL_STOP)

    def test_switching_auto_back_on_catches_up_to_the_schedule(self) -> None:
        engine, _ = self.open_one()
        self.command(CommandKind.SET_TRAIL, enabled=False)
        self.price(ENTRY + 1.0 * R)
        engine.tick()
        self.command(CommandKind.SET_TRAIL, enabled=True)
        engine.tick()
        self.assertEqual(self.stored().stop_price, 111.5)

    def test_no_price_this_tick_means_no_move(self) -> None:
        engine, _ = self.open_one()
        self.price(ENTRY + 1.0 * R)
        self.broker.positions_error = True
        engine.tick()
        self.assertEqual(self.broker.modify_count, 0)
        self.assertEqual(self.stored().stop_price, INITIAL_STOP)

    def test_a_failed_modify_keeps_the_stop_and_counts_as_a_step_failure(self) -> None:
        engine, _ = self.open_one()
        self.price(ENTRY + 0.5 * R)
        self.broker.modify_error = "Kite down"
        engine.tick()
        self.assertEqual(self.stored().stop_price, INITIAL_STOP)
        self.assertEqual(engine.failures.consecutive.get(STEP_TRAIL), 1)
        self.broker.modify_error = None
        engine.tick()
        self.assertEqual(self.stored().stop_price, ENTRY)
        self.assertNotIn(STEP_TRAIL, engine.failures.consecutive)

    def test_nothing_trails_once_square_off_has_begun(self) -> None:
        engine, _ = self.open_one()
        self.command(CommandKind.KILL_ALL)
        self.price(ENTRY + 2.0 * R)
        engine.tick()
        self.assertEqual(self.broker.modify_count, 0)


class ManualNudgeTests(TrailingTestCase):
    def test_up_one_tick_with_auto_on(self) -> None:
        engine, _ = self.open_one()
        cid = self.command(CommandKind.MOVE_STOP, ticks=1)
        engine.tick()
        self.assertEqual(self.command_status(cid)[0], "applied")
        self.assertEqual(self.stored().stop_price, 107.0)
        [(_, payload)] = self.stop_events()
        self.assertEqual(payload["source"], SOURCE_MANUAL)

    def test_down_is_refused_while_auto_is_on(self) -> None:
        engine, _ = self.open_one()
        self.command(CommandKind.MOVE_STOP, ticks=1)
        engine.tick()
        cid = self.command(CommandKind.MOVE_STOP, ticks=-1)
        engine.tick()
        status, result = self.command_status(cid)
        self.assertEqual(status, "rejected")
        self.assertIn("auto_trail_on_cannot_loosen", result)
        self.assertEqual(self.stored().stop_price, 107.0)

    def test_down_is_allowed_with_auto_off_but_never_past_the_floor(self) -> None:
        engine, _ = self.open_one()
        self.command(CommandKind.SET_TRAIL, enabled=False)
        self.command(CommandKind.MOVE_STOP, ticks=1)
        engine.tick()
        down = self.command(CommandKind.MOVE_STOP, ticks=-1)
        engine.tick()
        self.assertEqual(self.command_status(down)[0], "applied")
        self.assertEqual(self.stored().stop_price, INITIAL_STOP)
        past = self.command(CommandKind.MOVE_STOP, ticks=-1)
        engine.tick()
        status, result = self.command_status(past)
        self.assertEqual(status, "rejected")
        self.assertIn("beyond_initial_stop", result)

    def test_a_nudge_into_the_market_is_refused(self) -> None:
        engine, _ = self.open_one()
        self.price(INITIAL_STOP + 0.05)
        cid = self.command(CommandKind.MOVE_STOP, ticks=1)
        engine.tick()
        status, result = self.command_status(cid)
        self.assertEqual(status, "rejected")
        self.assertIn("at_or_through_market", result)

    def test_a_missing_tick_size_refuses_the_nudge(self) -> None:
        engine, pos = self.open_one()
        pos.candidate = dataclasses.replace(pos.candidate, tick_size=0)
        self.store.save(pos)
        cid = self.command(CommandKind.MOVE_STOP, ticks=1)
        engine.tick()
        status, result = self.command_status(cid)
        self.assertEqual(status, "rejected")
        self.assertIn("tick_size_unavailable", result)

    def test_more_than_one_tick_per_press_is_refused(self) -> None:
        engine, _ = self.open_one()
        cid = self.command(CommandKind.MOVE_STOP, ticks=5)
        engine.tick()
        self.assertEqual(self.command_status(cid)[0], "rejected")

    def test_the_more_favourable_of_nudge_and_schedule_wins(self) -> None:
        engine, _ = self.open_one()
        # The schedule wants breakeven (110.00); a nudge only reaches 107.00.
        self.price(ENTRY + 0.5 * R)
        self.command(CommandKind.MOVE_STOP, ticks=1)
        engine.tick()
        self.assertEqual(self.stored().stop_price, ENTRY)


class KiteEditTests(TrailingTestCase):
    def edit_in_kite(self, trigger: float) -> None:
        stop_id = self.stored().stop_order_id
        order = self.broker.orders[stop_id]
        self.broker.orders[stop_id] = type(order)(
            **{**order.__dict__, "trigger_price": trigger, "price": trigger}
        )

    def test_a_looser_kite_edit_is_adopted_even_with_auto_on(self) -> None:
        engine, _ = self.open_one()
        self.command(CommandKind.MOVE_STOP, ticks=1)
        engine.tick()
        self.edit_in_kite(106.5)
        engine.tick()
        pos = self.stored()
        self.assertEqual(pos.stop_price, 106.5)
        self.assertIn("stop_adopted_from_broker", pos.extra)
        event, payload = self.stop_events()[-1]
        self.assertEqual((event, payload["source"]), (EVENT_STOP_ADOPTED, SOURCE_KITE))

    def test_auto_carries_on_from_the_adopted_stop(self) -> None:
        engine, _ = self.open_one()
        self.edit_in_kite(106.5)
        engine.tick()
        self.price(ENTRY + 0.5 * R)
        engine.tick()
        self.assertEqual(self.stored().stop_price, ENTRY)

    def test_a_kite_edit_counts_against_the_modification_cap(self) -> None:
        engine, _ = self.open_one()
        self.edit_in_kite(108.0)
        engine.tick()
        pos = self.stored()
        self.assertEqual(pos.extra[STOP_MODS_KEY][pos.stop_order_id], 1)


class UnclearModifyTests(TrailingTestCase):
    def test_a_modify_that_landed_despite_an_error_is_credited_to_us(self) -> None:
        engine, _ = self.open_one()
        real = self.broker.modify_slm

        def lands_then_raises(*args, **kwargs):
            real(*args, **kwargs)
            raise RuntimeError("read timed out")

        self.broker.modify_slm = lands_then_raises  # type: ignore[method-assign]
        self.price(ENTRY + 0.5 * R)
        engine.tick()
        self.assertEqual(self.stored().stop_price, INITIAL_STOP)
        self.assertIn(STOP_MOVE_PENDING_KEY, self.stored().extra)
        self.broker.modify_slm = real  # type: ignore[method-assign]
        engine.tick()
        pos = self.stored()
        self.assertEqual(pos.stop_price, ENTRY)
        self.assertNotIn("stop_adopted_from_broker", pos.extra)
        self.assertNotIn(STOP_MOVE_PENDING_KEY, pos.extra)
        event, payload = self.stop_events()[-1]
        self.assertEqual((event, payload["source"]), (EVENT_STOP_ADOPTED, SOURCE_AUTO))

    def test_a_modify_that_never_landed_clears_its_marker(self) -> None:
        engine, _ = self.open_one()
        self.broker.stale_modify_trigger = True
        self.price(ENTRY + 0.5 * R)
        engine.tick()
        self.broker.stale_modify_trigger = False
        self.price(ENTRY)
        engine.tick()
        self.assertNotIn(STOP_MOVE_PENDING_KEY, self.stored().extra)

    def test_adopted_source_needs_the_same_order_and_price(self) -> None:
        _, pos = self.open_one()
        pos.extra[STOP_MOVE_PENDING_KEY] = {
            "order_id": pos.stop_order_id,
            "target": 110.0,
            "source": SOURCE_MANUAL,
        }
        self.assertEqual(adopted_source(pos, 110.0), SOURCE_MANUAL)
        self.assertEqual(adopted_source(pos, 109.0), SOURCE_KITE)
        pos.extra[STOP_MOVE_PENDING_KEY]["order_id"] = "other"
        self.assertEqual(adopted_source(pos, 110.0), SOURCE_KITE)


class ModificationCapTests(TrailingTestCase):
    def test_at_the_cap_the_stop_is_cancelled_and_replaced(self) -> None:
        engine, pos = self.open_one()
        old_id = pos.stop_order_id
        pos.extra[STOP_MODS_KEY] = {old_id: REPLACE_AT_MODIFICATIONS}
        self.store.save(pos)
        self.price(ENTRY + 0.5 * R)
        engine.tick()
        after = self.stored()
        self.assertEqual(after.state, ExecutionState.PROTECTED)
        self.assertNotEqual(after.stop_order_id, old_id)
        self.assertEqual(self.broker.orders[old_id].status, "CANCELLED")
        [stop] = self.live_stops()
        self.assertEqual(stop.order_id, after.stop_order_id)
        self.assertEqual(stop.trigger_price, ENTRY)
        self.assertEqual(after.extra[STOP_MODS_KEY].get(after.stop_order_id, 0), 0)
        self.assertIn(EVENT_STOP_REPLACED, [e for e, _ in self.stop_events()])

    def test_an_unconfirmed_cancel_never_places_a_second_stop(self) -> None:
        engine, pos = self.open_one()
        pos.extra[STOP_MODS_KEY] = {pos.stop_order_id: REPLACE_AT_MODIFICATIONS}
        self.store.save(pos)
        self.broker.cancel_noop = True
        self.price(ENTRY + 0.5 * R)
        engine.tick()
        self.assertEqual(len(self.live_stops()), 1)
        self.assertEqual(self.stored().stop_order_id, pos.stop_order_id)

    def test_kite_history_can_raise_the_count_near_the_cap(self) -> None:
        engine, pos = self.open_one()
        pos.extra[STOP_MODS_KEY] = {pos.stop_order_id: 12}
        self.store.save(pos)
        self.broker.modifications_by_order[pos.stop_order_id] = REPLACE_AT_MODIFICATIONS
        self.price(ENTRY + 0.5 * R)
        engine.tick()
        self.assertNotEqual(self.stored().stop_order_id, pos.stop_order_id)


class KiteModificationCountTests(unittest.TestCase):
    def test_counts_modify_rows_in_the_order_history(self) -> None:
        class Kite:
            def order_history(self, order_id):
                return [
                    {"status": "OPEN PENDING"},
                    {"status": "TRIGGER PENDING"},
                    {"status": "MODIFY VALIDATION PENDING"},
                    {"status": "MODIFIED"},
                    {"status": "TRIGGER PENDING"},
                    {"status": "MODIFY VALIDATION PENDING"},
                    {"status": "MODIFIED"},
                    {"status": "TRIGGER PENDING"},
                ]

        broker = KiteBroker(Kite(), live_orders_enabled=True)
        self.assertEqual(broker.order_modification_count("sl1"), 2)


if __name__ == "__main__":
    unittest.main()
