"""Closing safely when the broker and the engine disagree.

Replays of what happened live on 2026-09-24:

* HYUNDAI / LODHA: pressing Close met Kite's transient CANCEL PENDING, the
  close was refused, and the next tick re-placed the stop it had just
  cancelled. The close must instead finish on its own.
* A position closed directly in Kite left the engine's stop live.
* DRREDDY: a filled stop was re-placed, each replacement sold again, and the
  engine read the growing short as a long. The account must be flattened from
  Kite's signed quantity, never grown.
"""

from __future__ import annotations

import dataclasses
import sqlite3
import unittest

from engine_commands import CommandKind
from engine_core import (
    MAX_STOP_REPLACEMENTS,
    MISMATCH_KEY,
    STOP_REPLACEMENTS_KEY,
    STOP_TRIGGER_FILL_SECONDS,
    UNPROTECTED_FLATTEN_SECONDS,
)
from engine_entry import broker_tag_for
from engine_exit import EXIT_PENDING_KEY, ORPHAN_STOPS_KEY
from engine_types import ExecutionState
from tests.test_engine_lifecycle import LifecycleTestCase, candidate
from trading_engine_broker import KiteBroker
from trading_engine_types import PositionQuote

EXIT_TAG = f"{broker_tag_for('s1')}-x"


class SafetyExitTestCase(LifecycleTestCase):
    def open_one(self):
        self.candidates = [candidate("s1")]
        engine = self.engine()
        engine.tick()
        self.candidates = []
        stored = self.store.get("s1")
        assert stored is not None and stored.state == ExecutionState.PROTECTED
        return engine, stored

    def exit_orders(self):
        return [o for o in self.broker.orders.values() if o.tag == EXIT_TAG]

    def command_status(self, command_id: int) -> str:
        conn = sqlite3.connect(self.path)
        try:
            row = conn.execute(
                "SELECT status FROM engine_commands WHERE command_id = ?", (command_id,)
            ).fetchone()
        finally:
            conn.close()
        return row[0]

    def fail_stop_placement(self) -> None:
        def refuse(**_):
            raise RuntimeError("Trigger price for stoploss sell orders should be lower")

        self._real_place_slm = self.broker.place_slm
        self.broker.place_slm = refuse  # type: ignore[method-assign]

    def allow_stop_placement(self) -> None:
        self.broker.place_slm = self._real_place_slm  # type: ignore[method-assign]


# ----------------------------------------------------------------------
# Fix 1: KiteBroker waits for Kite to confirm the cancel
# ----------------------------------------------------------------------


class FakeKite:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.reads = 0

    def cancel_order(self, **_):
        return {"order_id": "sl1"}

    def orders(self):
        self.reads += 1
        status = self.statuses[min(self.reads, len(self.statuses)) - 1]
        return [
            {
                "order_id": "sl1",
                "status": status,
                "tradingsymbol": "AAA",
                "transaction_type": "SELL",
                "order_type": "SL-M",
                "quantity": 10,
                "filled_quantity": 0,
                "pending_quantity": 0 if status == "CANCELLED" else 10,
                "product": "MIS",
            }
        ]


class KiteCancelConfirmationTests(unittest.TestCase):
    def broker(self, statuses):
        kite = FakeKite(statuses)
        broker = KiteBroker(kite, live_orders_enabled=True)
        clock = {"t": 0.0}
        broker._sleep = lambda s: clock.__setitem__("t", clock["t"] + s)
        broker._monotonic = lambda: clock["t"]
        return broker, kite

    def test_cancel_pending_is_waited_out_until_kite_confirms(self) -> None:
        broker, kite = self.broker(["CANCEL PENDING", "CANCEL PENDING", "CANCELLED"])
        result = broker.cancel_order("sl1")
        self.assertEqual(result.status, "CANCELLED")
        self.assertEqual(kite.reads, 3)

    def test_a_cancel_that_never_confirms_is_reported_unconfirmed(self) -> None:
        broker, kite = self.broker(["CANCEL PENDING"])
        result = broker.cancel_order("sl1")
        self.assertEqual(result.status, "CANCEL PENDING")
        # ~1.5s of 0.25s re-reads, then give up rather than wait forever.
        self.assertLessEqual(kite.reads, 8)


# ----------------------------------------------------------------------
# Fix 2: a Close that meets CANCEL PENDING finishes on its own
# ----------------------------------------------------------------------


class CancelPendingCloseTests(SafetyExitTestCase):
    def test_the_close_completes_without_a_second_press(self) -> None:
        engine, stored = self.open_one()
        self.broker.cancel_pending_once = True
        command_id = self.queue.enqueue(CommandKind.CLOSE_POSITION, trade_id="s1")

        engine.tick()  # cancel sent; Kite answers CANCEL PENDING
        self.assertEqual(self.command_status(command_id), "applied")
        pending = self.store.get("s1")
        assert pending is not None
        self.assertIn(EXIT_PENDING_KEY, pending.extra)
        self.assertEqual(self.exit_orders(), [])

        engine.tick()  # Kite now shows CANCELLED: the exit goes out
        engine.tick()  # and the position is booked flat
        closed = self.store.get("s1")
        assert closed is not None
        self.assertEqual(closed.state, ExecutionState.CLOSED)
        self.assertEqual(closed.extra.get("close_reason"), "manual_close")
        self.assertEqual(len(self.exit_orders()), 1)
        self.assertEqual(self.broker.net_position_qty("AAA"), 0)
        self.assertIn("exit_pending", self.events("s1"))

    def test_no_stop_is_re_placed_while_the_exit_is_pending(self) -> None:
        engine, stored = self.open_one()
        placed_before = self.broker.slm_place_count
        self.broker.cancel_pending_once = True
        self.queue.enqueue(CommandKind.CLOSE_POSITION, trade_id="s1")
        for _ in range(4):
            engine.tick()
        self.assertEqual(self.broker.slm_place_count, placed_before)
        self.assertNotIn("stop_missing_at_broker", self.events("s1"))
        self.assertNotIn("protected", self.events("s1")[4:])

    def test_a_stop_still_cancel_pending_holds_the_exit_back(self) -> None:
        engine, stored = self.open_one()
        # Kite never gets past CANCEL PENDING: never send the exit meanwhile.
        self.broker.cancel_noop = True
        self.queue.enqueue(CommandKind.CLOSE_POSITION, trade_id="s1")
        for _ in range(3):
            engine.tick()
            self.clock.advance(1)
        self.assertEqual(self.exit_orders(), [])
        self.assertEqual(self.broker.slm_place_count, 1)


# ----------------------------------------------------------------------
# Fix 3: a close made in Kite cancels the engine's leftover stop
# ----------------------------------------------------------------------


class KiteSideCloseTests(SafetyExitTestCase):
    def test_the_leftover_stop_is_cancelled(self) -> None:
        engine, stored = self.open_one()
        self.broker.simulate_external_flatten("AAA", 111.0)
        engine.tick()
        closed = self.store.get("s1")
        assert closed is not None
        self.assertEqual(closed.state, ExecutionState.CLOSED)
        self.assertEqual(self.broker.orders[stored.stop_order_id].status, "CANCELLED")
        self.assertIn("stop_cancelled", self.events("s1"))

    def test_a_tagged_stop_whose_id_was_lost_is_cancelled_too(self) -> None:
        engine, stored = self.open_one()
        stop_id = stored.stop_order_id
        stored.stop_order_id = None
        self.store.save(stored)
        self.broker.simulate_external_flatten("AAA", 111.0)
        engine.tick()
        self.assertEqual(self.broker.orders[stop_id].status, "CANCELLED")

    def test_an_unconfirmed_cancel_is_retried_on_later_ticks(self) -> None:
        engine, stored = self.open_one()
        self.broker.simulate_external_flatten("AAA", 111.0)
        self.broker.cancel_noop = True
        engine.tick()
        closed = self.store.get("s1")
        assert closed is not None
        self.assertEqual(closed.state, ExecutionState.CLOSED)
        self.assertEqual(closed.extra[ORPHAN_STOPS_KEY], [stored.stop_order_id])
        self.assertEqual(closed.extra["manual_review"], "orphan_stop_unconfirmed")

        self.broker.cancel_noop = False
        engine.tick()
        cleared = self.store.get("s1")
        assert cleared is not None
        self.assertNotIn(ORPHAN_STOPS_KEY, cleared.extra)
        self.assertEqual(self.broker.orders[stored.stop_order_id].status, "CANCELLED")
        self.assertIn("orphan_stop_cleared", self.events("s1"))


# ----------------------------------------------------------------------
# Fix 4 and 5: DRREDDY -- never grow a short, flatten it from Kite's number
# ----------------------------------------------------------------------


class DirectionMismatchTests(SafetyExitTestCase):
    def go_short_by(self, stored, extra: int) -> None:
        """Sells beyond the long: Kite ends up net short by `extra`."""
        self.broker.inject_complete_order(
            tradingsymbol="AAA",
            transaction_type="SELL",
            quantity=int(stored.qty) + extra,
            price=106.9,
        )

    def test_a_confirmed_mismatch_is_bought_back_to_exactly_flat(self) -> None:
        engine, stored = self.open_one()
        self.go_short_by(stored, 10)
        engine.tick()  # seen once: not yet acted on
        self.assertEqual(self.exit_orders(), [])
        engine.tick()  # confirmed: stops cancelled, then BUY 10
        exits = self.exit_orders()
        self.assertEqual(len(exits), 1)
        self.assertEqual(exits[0].transaction_type, "BUY")
        self.assertEqual(int(exits[0].quantity), 10)
        self.assertEqual(self.broker.orders[stored.stop_order_id].status, "CANCELLED")
        events = self.events("s1")
        self.assertLess(events.index("stop_cancelled"), events.index("mismatch_flatten_submitted"))

        engine.tick()  # Kite shows 0: booked and logged
        self.assertEqual(self.broker.net_position_qty("AAA"), 0)
        closed = self.store.get("s1")
        assert closed is not None
        self.assertEqual(closed.state, ExecutionState.CLOSED)
        self.assertEqual(closed.extra["manual_review"], "direction_mismatch")
        self.assertIn("direction_mismatch", self.events("s1"))
        self.assertIn("mismatch_flattened", self.events("s1"))
        self.assertTrue(engine.entries_paused)
        self.assertIn("direction_mismatch:s1", engine.failures.escalated)

    def test_no_stop_or_quantity_is_adopted_during_a_mismatch(self) -> None:
        engine, stored = self.open_one()
        self.go_short_by(stored, 10)
        engine.tick()
        seen = self.store.get("s1")
        assert seen is not None
        self.assertEqual(seen.qty, stored.qty)
        self.assertEqual(self.broker.slm_place_count, 1)

    def test_a_one_tick_blip_does_nothing(self) -> None:
        engine, stored = self.open_one()
        self.broker.position_quotes["AAA"] = PositionQuote(quantity=-int(stored.qty))
        engine.tick()
        del self.broker.position_quotes["AAA"]
        engine.tick()
        engine.tick()
        after = self.store.get("s1")
        assert after is not None
        self.assertEqual(after.state, ExecutionState.PROTECTED)
        self.assertNotIn(MISMATCH_KEY, after.extra)
        self.assertEqual(self.exit_orders(), [])
        self.assertNotIn("direction_mismatch", self.events("s1"))
        self.assertFalse(engine.entries_paused)

    def test_a_filled_stop_is_never_replaced_while_positions_lag(self) -> None:
        engine, stored = self.open_one()
        filled = self.broker.fill_sl(stored.stop_order_id, 106.9)
        # A triggered stop no longer reads as a stop order, so the live-stop
        # lookup cannot see it: exactly how it looked "missing" on DRREDDY.
        self.broker.orders[stored.stop_order_id] = dataclasses.replace(
            filled, order_type="MARKET"
        )
        # Kite's positions view still shows the long for a moment.
        self.broker.position_quotes["AAA"] = PositionQuote(quantity=int(stored.qty))
        engine.tick()
        engine.tick()
        self.assertEqual(self.broker.slm_place_count, 1)
        self.assertNotIn("stop_missing_at_broker", self.events("s1"))
        del self.broker.position_quotes["AAA"]
        engine.tick()
        closed = self.store.get("s1")
        assert closed is not None
        self.assertEqual(closed.extra.get("close_reason"), "stop_hit")
        sells = [o for o in self.broker.orders.values() if o.transaction_type == "SELL"]
        self.assertEqual(len(sells), 1)


# ----------------------------------------------------------------------
# Triggered stop-limit: Kite shows it as a LIMIT under the same order id
# ----------------------------------------------------------------------


class TriggeredStopLimitTests(SafetyExitTestCase):
    def trigger(self, stored, *, filled: int = 0) -> None:
        """What Kite does when our SL triggers: same id, now a working LIMIT."""
        order = self.broker.orders[stored.stop_order_id]
        qty = int(order.quantity)
        self.broker.orders[stored.stop_order_id] = dataclasses.replace(
            order,
            order_type="LIMIT",
            status="OPEN",
            filled_quantity=filled,
            pending_quantity=qty - filled,
            average_price=106.95 if filled else None,
        )

    def test_a_triggered_unfilled_stop_is_not_replaced(self) -> None:
        engine, stored = self.open_one()
        self.trigger(stored)
        for _ in range(2):
            engine.tick()
            self.clock.advance(1)
        self.assertEqual(self.broker.slm_place_count, 1)
        self.assertNotIn("stop_missing_at_broker", self.events("s1"))
        self.assertEqual(self.exit_orders(), [])
        self.assertIn("stop_triggered", self.events("s1"))

    def test_unfilled_past_the_limit_is_cancelled_and_exited_once(self) -> None:
        engine, stored = self.open_one()
        self.trigger(stored)
        engine.tick()  # triggered: the clock starts
        self.clock.advance(STOP_TRIGGER_FILL_SECONDS + 1)
        engine.tick()  # still unfilled: cancel, then market out
        for _ in range(3):
            self.clock.advance(1)
            engine.tick()
        self.assertEqual(self.broker.orders[stored.stop_order_id].status, "CANCELLED")
        exits = self.exit_orders()
        self.assertEqual(len(exits), 1)
        self.assertEqual(exits[0].transaction_type, "SELL")
        self.assertEqual(int(exits[0].quantity), int(stored.qty))
        closed = self.store.get("s1")
        assert closed is not None
        self.assertEqual(closed.state, ExecutionState.CLOSED)
        self.assertEqual(closed.extra.get("close_reason"), "stop_triggered_unfilled")
        self.assertEqual(self.broker.net_position_qty("AAA"), 0)
        self.assertEqual(self.broker.slm_place_count, 1)

    def test_a_part_filled_stop_exits_only_what_is_left(self) -> None:
        engine, stored = self.open_one()
        self.trigger(stored, filled=100)
        engine.tick()
        self.clock.advance(STOP_TRIGGER_FILL_SECONDS + 1)
        engine.tick()
        engine.tick()
        exits = self.exit_orders()
        self.assertEqual(len(exits), 1)
        self.assertEqual(int(exits[0].quantity), int(stored.qty) - 100)
        self.assertEqual(self.broker.net_position_qty("AAA"), 0)
        closed = self.store.get("s1")
        assert closed is not None
        self.assertEqual(closed.state, ExecutionState.CLOSED)

    def test_a_part_filled_stop_never_over_sells_while_positions_lag(self) -> None:
        engine, stored = self.open_one()
        self.trigger(stored, filled=100)
        # Kite's positions book has not caught up with the 100 sold yet.
        self.broker.position_quotes["AAA"] = PositionQuote(quantity=int(stored.qty))
        engine.tick()
        self.clock.advance(STOP_TRIGGER_FILL_SECONDS + 1)
        engine.tick()
        del self.broker.position_quotes["AAA"]
        engine.tick()
        exits = self.exit_orders()
        self.assertEqual(len(exits), 1)
        self.assertEqual(int(exits[0].quantity), int(stored.qty) - 100)
        self.assertEqual(self.broker.net_position_qty("AAA"), 0)

    def test_a_stop_that_fills_in_time_is_left_to_close_normally(self) -> None:
        engine, stored = self.open_one()
        self.trigger(stored)
        engine.tick()
        self.clock.advance(1)
        self.broker.fill_sl(stored.stop_order_id, 106.95)
        engine.tick()
        closed = self.store.get("s1")
        assert closed is not None
        self.assertEqual(closed.extra.get("close_reason"), "stop_hit")
        self.assertEqual(self.exit_orders(), [])

    def test_a_kite_side_close_cancels_a_triggered_stop_too(self) -> None:
        engine, stored = self.open_one()
        self.trigger(stored)
        self.broker.simulate_external_flatten("AAA", 106.0)
        engine.tick()
        self.assertEqual(self.broker.orders[stored.stop_order_id].status, "CANCELLED")
        closed = self.store.get("s1")
        assert closed is not None
        self.assertEqual(closed.state, ExecutionState.CLOSED)


# ----------------------------------------------------------------------
# Fix 6: re-placement cap
# ----------------------------------------------------------------------


class StopReplacementCapTests(SafetyExitTestCase):
    def lose_stop(self, engine) -> None:
        stored = self.store.get("s1")
        assert stored is not None
        self.broker.cancel_order(stored.stop_order_id)
        engine.tick()

    def test_the_position_is_exited_once_after_three_re_placements(self) -> None:
        engine, stored = self.open_one()
        for _ in range(MAX_STOP_REPLACEMENTS):
            self.lose_stop(engine)
        replaced = self.store.get("s1")
        assert replaced is not None
        self.assertEqual(replaced.extra[STOP_REPLACEMENTS_KEY], MAX_STOP_REPLACEMENTS)
        self.assertEqual(replaced.state, ExecutionState.PROTECTED)
        self.assertEqual(self.exit_orders(), [])

        self.lose_stop(engine)  # one more: exit instead of a fourth stop
        for _ in range(3):
            engine.tick()
        self.assertEqual(len(self.exit_orders()), 1)
        self.assertEqual(self.exit_orders()[0].transaction_type, "SELL")
        self.assertEqual(self.broker.slm_place_count, 1 + MAX_STOP_REPLACEMENTS)
        closed = self.store.get("s1")
        assert closed is not None
        self.assertEqual(closed.state, ExecutionState.CLOSED)
        self.assertEqual(closed.extra["manual_review"], "stop_replacement_cap")
        self.assertIn("stop_replacement_cap_reached", self.events("s1"))
        self.assertTrue(engine.entries_paused)

    def test_failed_first_placements_do_not_count(self) -> None:
        self.fail_stop_placement()
        self.candidates = [candidate("s1")]
        engine = self.engine()
        engine.tick()
        self.candidates = []
        for _ in range(5):
            self.clock.advance(1)
            engine.tick()
        self.allow_stop_placement()
        self.clock.advance(1)
        engine.tick()
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(stored.state, ExecutionState.PROTECTED)
        self.assertNotIn(STOP_REPLACEMENTS_KEY, stored.extra)
        self.assertGreaterEqual(self.events("s1").count("stop_place_failed"), 5)


# ----------------------------------------------------------------------
# Safety net: a fill that never gets its first stop
# ----------------------------------------------------------------------


class UnprotectedTimeoutTests(SafetyExitTestCase):
    def test_unprotected_past_the_timeout_is_exited_exactly_once(self) -> None:
        self.fail_stop_placement()
        self.candidates = [candidate("s1")]
        engine = self.engine()
        engine.tick()
        self.candidates = []
        self.clock.advance(UNPROTECTED_FLATTEN_SECONDS / 2)
        engine.tick()
        self.assertEqual(self.exit_orders(), [])
        self.clock.advance(UNPROTECTED_FLATTEN_SECONDS / 2 + 1)
        engine.tick()
        for _ in range(3):
            self.clock.advance(1)
            engine.tick()
        self.assertEqual(len(self.exit_orders()), 1)
        closed = self.store.get("s1")
        assert closed is not None
        self.assertEqual(closed.state, ExecutionState.CLOSED)
        self.assertEqual(closed.extra["manual_review"], "unprotected_timeout")
        self.assertEqual(self.events("s1").count("auto_flatten_unprotected"), 1)
        self.assertIn(f"unprotected_timeout:s1", engine.failures.escalated)

    def test_protected_within_the_timeout_is_left_alone(self) -> None:
        self.fail_stop_placement()
        self.candidates = [candidate("s1")]
        engine = self.engine()
        engine.tick()
        self.candidates = []
        self.clock.advance(UNPROTECTED_FLATTEN_SECONDS - 4)
        engine.tick()
        self.allow_stop_placement()
        self.clock.advance(1)
        engine.tick()
        for _ in range(5):
            self.clock.advance(10)
            engine.tick()
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(stored.state, ExecutionState.PROTECTED)
        self.assertEqual(self.exit_orders(), [])
        self.assertNotIn("auto_flatten_unprotected", self.events("s1"))


if __name__ == "__main__":
    unittest.main()
