"""Command queue and the engine's handling of each command."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from engine_commands import CommandKind, CommandQueue, CommandStatus
from engine_config import SessionRiskConfig
from engine_core import ExecutionEngine
from engine_exit import CloseReason
from engine_feed import FeedHealth
from engine_risk import RiskPolicy
from engine_sizing import RiskCappedSizing
from engine_store import SqlitePositionStore
from engine_types import ExecutionState, Position, RiskLimits, TriggerCandidate
from trading_engine_broker import FakeBroker

INSIDE_WINDOW = datetime(2026, 9, 22, 5, 0, tzinfo=timezone.utc)   # 10:30 IST
AFTER_CUTOFF = datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc)    # 14:30 IST


def candidate(setup_id: str = "s1", symbol: str = "AAA") -> TriggerCandidate:
    return TriggerCandidate(
        setup_id=setup_id,
        continuation_rule_version="v1",
        session_date="2026-09-22",
        tradingsymbol=symbol,
        instrument_token=1,
        direction="UP",
        trigger_price=110.0,
        pullback_swing_high=113.0,
        pullback_swing_low=107.0,
        tick_size=0.05,
        buffer_ticks=1,
        trigger_exchange_ts="2026-09-22T10:00:00+00:00",
        created_at=INSIDE_WINDOW.isoformat(),
        vwap_classification="ACCEPT",
    )


class HealthyFeed:
    def check(self, *, now=None) -> FeedHealth:
        return FeedHealth(healthy=True, age_seconds=0.5)


class QueueTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "engine.db"
        self.store = SqlitePositionStore(self.path)
        self.queue = CommandQueue(self.path)
        self.broker = FakeBroker()
        self.broker.last_prices["AAA"] = 110.0

    def tearDown(self) -> None:
        self.queue.close()
        self.store.close()
        self._tmp.cleanup()

    def engine(self, *, now=INSIDE_WINDOW) -> ExecutionEngine:
        return ExecutionEngine(
            broker=self.broker,
            store=self.store,
            feed_monitor=HealthyFeed(),
            risk_policy=RiskPolicy(
                RiskLimits(
                    per_trade_cap_rupees=900.0,
                    per_trade_cap_vwap_limited_rupees=450.0,
                    daily_loss_cap_rupees=3000.0,
                )
            ),
            sizing_policy=RiskCappedSizing(),
            candidate_source=lambda: [],
            session_config=SessionRiskConfig(),
            session_date="2026-09-22",
            now_fn=lambda: now,
            command_source=self.queue.take_pending,
        )

    def protected_position(self, trade_id: str = "s1", symbol: str = "AAA") -> Position:
        """A genuinely held, genuinely protected position.

        The entry has to actually fill at the broker, not just be asserted
        locally: FakeBroker derives net quantity from filled orders, so a
        position with only a stop order looks flat and reconciliation closes it
        on the first tick -- correctly.
        """
        self.broker.last_prices.setdefault(symbol, 110.0)
        entry = self.broker.place_market_mis(
            tradingsymbol=symbol, transaction_type="BUY", quantity=300, tag=trade_id
        )
        stop = self.broker.place_slm(
            tradingsymbol=symbol,
            transaction_type="SELL",
            quantity=300,
            trigger_price=106.95,
            tag=trade_id,
        )
        pos = Position(
            trade_id=trade_id,
            candidate=candidate(trade_id, symbol),
            state=ExecutionState.PROTECTED,
            qty=300,
            entry_price=110.0,
            stop_price=106.95,
            entry_order_id=entry.order_id,
            stop_order_id=stop.order_id,
        )
        self.store.save(pos)
        return pos


class QueueMechanicsTests(QueueTestCase):
    def test_a_command_starts_pending_and_is_readable(self) -> None:
        cid = self.queue.enqueue(CommandKind.STOP)
        pending = self.queue.pending()
        self.assertEqual([c.command_id for c in pending], [cid])
        self.assertEqual(pending[0].kind, CommandKind.STOP)

    def test_commands_come_back_oldest_first(self) -> None:
        first = self.queue.enqueue(CommandKind.STOP)
        second = self.queue.enqueue(CommandKind.START)
        self.assertEqual([c.command_id for c in self.queue.pending()], [first, second])

    def test_an_applied_command_leaves_the_pending_set(self) -> None:
        cid = self.queue.enqueue(CommandKind.STOP)
        self.queue.mark_applied(cid, {"ok": True})
        self.assertEqual(self.queue.pending(), [])
        record = self.queue.record(cid)
        assert record is not None
        self.assertEqual(record["status"], CommandStatus.APPLIED.value)
        self.assertEqual(record["result"], {"ok": True})

    def test_a_rejected_command_records_its_reason(self) -> None:
        cid = self.queue.enqueue(CommandKind.START)
        self.queue.reject(cid, "past_entry_cutoff")
        record = self.queue.record(cid)
        assert record is not None
        self.assertEqual(record["status"], CommandStatus.REJECTED.value)
        self.assertEqual(record["result"]["reason"], "past_entry_cutoff")

    def test_close_position_requires_a_trade_id(self) -> None:
        with self.assertRaises(ValueError):
            self.queue.enqueue(CommandKind.CLOSE_POSITION)

    def test_kill_all_needs_no_trade_id(self) -> None:
        self.assertGreater(self.queue.enqueue(CommandKind.KILL_ALL), 0)

    def test_an_unknown_kind_is_rejected_rather_than_wedging_the_queue(self) -> None:
        with self.queue._conn:
            self.queue._conn.execute(
                "INSERT INTO engine_commands (at, kind, payload_json, status) "
                "VALUES ('now', 'nonsense', '{}', 'pending')"
            )
        good = self.queue.enqueue(CommandKind.STOP)
        pending = self.queue.pending()
        # The bad row is rejected and the good one still comes through.
        self.assertEqual([c.command_id for c in pending], [good])

    def test_a_resolved_command_cannot_be_resolved_twice(self) -> None:
        cid = self.queue.enqueue(CommandKind.STOP)
        pending = self.queue.take_pending()[0]
        pending.applied({"first": True})
        pending.rejected("second")
        record = self.queue.record(cid)
        assert record is not None
        self.assertEqual(record["status"], CommandStatus.APPLIED.value)

    def test_a_read_only_queue_can_read_but_the_engine_writes(self) -> None:
        cid = self.queue.enqueue(CommandKind.STOP)
        reader = CommandQueue(self.path, read_only=True)
        try:
            record = reader.record(cid)
            assert record is not None
            self.assertEqual(record["kind"], "stop")
        finally:
            reader.close()


class StopStartTests(QueueTestCase):
    def test_stop_blocks_entries_without_stopping_the_loop(self) -> None:
        engine = self.engine()
        self.queue.enqueue(CommandKind.STOP)
        engine.tick()
        self.assertTrue(engine.entries_stopped)
        self.assertFalse(engine.entries_allowed)
        # Still running: no shutdown was triggered.
        self.assertIsNone(engine.shutdown_reason)

    def test_start_re_enables_entries(self) -> None:
        engine = self.engine()
        engine.entries_stopped = True
        self.queue.enqueue(CommandKind.START)
        engine.tick()
        self.assertFalse(engine.entries_stopped)
        self.assertTrue(engine.entries_allowed)

    def test_the_toggle_is_re_clickable(self) -> None:
        engine = self.engine()
        for kind, expected in (
            (CommandKind.STOP, True),
            (CommandKind.START, False),
            (CommandKind.STOP, True),
            (CommandKind.START, False),
        ):
            self.queue.enqueue(kind)
            engine.tick()
            self.assertEqual(engine.entries_stopped, expected)

    def test_start_is_refused_after_two_pm(self) -> None:
        engine = self.engine(now=AFTER_CUTOFF)
        engine.entries_stopped = True
        cid = self.queue.enqueue(CommandKind.START)
        engine.tick()
        self.assertTrue(engine.entries_stopped)
        record = self.queue.record(cid)
        assert record is not None
        self.assertEqual(record["result"]["reason"], "past_entry_cutoff")

    def test_the_loop_keeps_running_after_a_refused_start(self) -> None:
        engine = self.engine(now=AFTER_CUTOFF)
        self.queue.enqueue(CommandKind.START)
        engine.tick()
        self.assertIsNone(engine.shutdown_reason)

    def test_stop_leaves_reconciliation_running(self) -> None:
        pos = self.protected_position()
        engine = self.engine()
        self.queue.enqueue(CommandKind.STOP)
        engine.tick()
        # Position still tracked and still reconciled, not abandoned.
        self.assertEqual(len(self.store.open_positions()), 1)
        self.assertIsNotNone(engine.last_truth)
        self.assertEqual(pos.trade_id, "s1")

    def test_each_command_is_applied_exactly_once(self) -> None:
        engine = self.engine()
        self.queue.enqueue(CommandKind.STOP)
        engine.tick()
        engine.entries_stopped = False  # pretend something reset it
        engine.tick()
        # The command was consumed on the first tick; it must not re-apply.
        self.assertFalse(engine.entries_stopped)


class ClosePositionTests(QueueTestCase):
    def test_one_position_is_closed_and_the_engine_keeps_running(self) -> None:
        self.protected_position()
        engine = self.engine()
        self.queue.enqueue(CommandKind.CLOSE_POSITION, trade_id="s1")
        engine.tick()
        stored = self.store.get("s1")
        assert stored is not None
        self.assertIn(
            stored.state, (ExecutionState.EXIT_SUBMITTED, ExecutionState.CLOSED)
        )
        self.assertIsNone(engine.shutdown_reason)
        self.assertFalse(engine.entries_stopped)

    def test_it_is_tagged_manual_close(self) -> None:
        self.protected_position()
        engine = self.engine()
        self.queue.enqueue(CommandKind.CLOSE_POSITION, trade_id="s1")
        engine.tick()
        stored = self.store.get("s1")
        assert stored is not None
        recorded = stored.extra.get("exit_reason") or stored.extra.get("close_reason")
        self.assertEqual(recorded, CloseReason.MANUAL_CLOSE.value)

    def test_an_unknown_trade_is_rejected_not_crashed(self) -> None:
        engine = self.engine()
        cid = self.queue.enqueue(CommandKind.CLOSE_POSITION, trade_id="nope")
        engine.tick()
        record = self.queue.record(cid)
        assert record is not None
        self.assertEqual(record["status"], CommandStatus.REJECTED.value)
        self.assertEqual(record["result"]["reason"], "position_not_open")

    def test_closing_every_position_manually_does_not_auto_stop(self) -> None:
        # The explicit non-trigger: being flat is not a shutdown reason.
        self.protected_position("s1", "AAA")
        engine = self.engine()
        self.queue.enqueue(CommandKind.CLOSE_POSITION, trade_id="s1")
        engine.tick()
        engine.tick()
        self.assertIsNone(engine.shutdown_reason)


class KillAllTests(QueueTestCase):
    def test_kill_all_begins_the_shared_shutdown_sequence(self) -> None:
        self.protected_position()
        engine = self.engine()
        self.queue.enqueue(CommandKind.KILL_ALL)
        engine.tick()
        self.assertEqual(engine.shutdown_reason, CloseReason.KILL_ALL)
        self.assertFalse(engine.entries_allowed)

    def test_it_closes_everything(self) -> None:
        self.protected_position("s1", "AAA")
        self.broker.last_prices["BBB"] = 110.0
        self.protected_position("s2", "BBB")
        engine = self.engine()
        self.queue.enqueue(CommandKind.KILL_ALL)
        engine.tick()
        for trade_id in ("s1", "s2"):
            stored = self.store.get(trade_id)
            assert stored is not None
            self.assertIn(
                stored.state, (ExecutionState.EXIT_SUBMITTED, ExecutionState.CLOSED)
            )

    def test_shutdown_completes_only_once_everything_is_closed(self) -> None:
        self.protected_position()
        engine = self.engine()
        self.queue.enqueue(CommandKind.KILL_ALL)
        engine.tick()
        # The exit is in flight, not confirmed, so the session is not over yet.
        self.assertFalse(engine.shutdown_complete)
        # Next tick, reconciliation sees flat and finalizes.
        engine.tick()
        engine.tick()
        self.assertTrue(engine.shutdown_complete)
        self.assertEqual(self.store.open_positions(), [])

    def test_no_new_entries_are_taken_once_shutdown_began(self) -> None:
        engine = self.engine()
        self.queue.enqueue(CommandKind.KILL_ALL)
        engine.tick()
        outcome = engine.handle_trigger(candidate("new"))
        self.assertIsNone(outcome)


if __name__ == "__main__":
    unittest.main()
