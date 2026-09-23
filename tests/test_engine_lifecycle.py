"""End-to-end lifecycle through ExecutionEngine.tick(), with a fake clock.

Covers the paths no single-module test can: trigger to protected in one tick,
a stop firing at the broker and being noticed, a breach closing everything and
auto-stopping, the 14:50 EOD square-off cutoff, and a step failing without
silencing the rest of the tick.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from engine_commands import CommandKind, CommandQueue
from engine_config import SessionRiskConfig
from engine_core import ExecutionEngine
from engine_exit import CloseReason
from engine_feed import FeedHealth
from engine_risk import RiskPolicy
from engine_runloop import FAILURE_THRESHOLDS, STEP_INGEST
from engine_sizing import RiskCappedSizing
from engine_store import SqlitePositionStore
from engine_types import ExecutionState, Position, RiskLimits, TriggerCandidate
from trading_engine_broker import FakeBroker

MORNING = datetime(2026, 9, 22, 5, 0, tzinfo=timezone.utc)      # 10:30 IST
AFTER_CUTOFF = datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc)  # 14:30 IST
EOD = datetime(2026, 9, 22, 9, 46, tzinfo=timezone.utc)          # 15:16 IST

LIMITS = RiskLimits(
    per_trade_cap_rupees=900.0,
    per_trade_cap_vwap_limited_rupees=450.0,
    daily_loss_cap_rupees=3000.0,
)


def candidate(
    setup_id: str,
    *,
    symbol: str = "AAA",
    classification: Optional[str] = "ACCEPT",
    created_at: datetime = MORNING,
) -> TriggerCandidate:
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
        trigger_exchange_ts=created_at.isoformat(),
        created_at=created_at.isoformat(),
        vwap_classification=classification,
        breakout_candle_volume=5_000,
        avg_prior_3_1m_volume=1_000.0,
    )


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class HealthyFeed:
    def check(self, *, now=None) -> FeedHealth:
        return FeedHealth(healthy=True, age_seconds=0.5)


class StaleFeed:
    def check(self, *, now=None) -> FeedHealth:
        return FeedHealth(healthy=False, age_seconds=30.0, reason="feed_stale")


class LifecycleTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "engine.db"
        self.store = SqlitePositionStore(self.path)
        self.queue = CommandQueue(self.path)
        self.broker = FakeBroker()
        for symbol in ("AAA", "BBB", "CCC"):
            self.broker.last_prices[symbol] = 110.0
        self.clock = Clock(MORNING)
        self.candidates: List[TriggerCandidate] = []

    def tearDown(self) -> None:
        self.queue.close()
        self.store.close()
        self._tmp.cleanup()

    def engine(self, *, feed=None) -> ExecutionEngine:
        return ExecutionEngine(
            broker=self.broker,
            store=self.store,
            feed_monitor=feed or HealthyFeed(),
            risk_policy=RiskPolicy(LIMITS),
            sizing_policy=RiskCappedSizing(),
            candidate_source=lambda: list(self.candidates),
            session_config=SessionRiskConfig(),
            session_date="2026-09-22",
            run_id="run-1",
            now_fn=self.clock,
            command_source=self.queue.take_pending,
        )

    def events(self, trade_id: str) -> List[str]:
        return [r["event_type"] for r in self.store.list_events(trade_id)]


class HappyPathTests(LifecycleTestCase):
    def test_a_trigger_becomes_a_protected_position_in_one_tick(self) -> None:
        self.candidates = [candidate("s1")]
        engine = self.engine()
        engine.tick()
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(stored.state, ExecutionState.PROTECTED)
        self.assertEqual(stored.qty, 295)
        self.assertEqual(stored.entry_price, 110.0)
        self.assertIsNotNone(stored.stop_order_id)

    def test_the_diary_records_the_full_path(self) -> None:
        self.candidates = [candidate("s1")]
        self.engine().tick()
        self.assertEqual(
            self.events("s1"),
            ["entry_intent", "entry_submitted", "entry_filled", "protected"],
        )

    def test_a_second_tick_does_not_re_enter_the_same_setup(self) -> None:
        self.candidates = [candidate("s1")]
        engine = self.engine()
        engine.tick()
        engine.tick()
        engine.tick()
        self.assertEqual(len(self.store.list_positions()), 1)
        self.assertEqual(self.broker.market_place_count, 1)

    def test_a_protected_position_stays_protected_across_ticks(self) -> None:
        self.candidates = [candidate("s1")]
        engine = self.engine()
        for _ in range(5):
            engine.tick()
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(stored.state, ExecutionState.PROTECTED)
        # No trailing: the stop never moves on its own.
        self.assertAlmostEqual(stored.stop_price, 106.95, places=4)

    def test_live_pnl_is_tracked_per_position_without_being_persisted(self) -> None:
        self.candidates = [candidate("s1")]
        engine = self.engine()
        engine.tick()
        self.broker.last_prices["AAA"] = 112.0
        engine.tick()
        self.assertIn("s1", engine.live_pnl)
        row = self.store.list_positions()[0]
        self.assertNotIn("live_pnl", row.keys())

    def test_two_symbols_can_be_held_at_once(self) -> None:
        self.candidates = [candidate("s1", symbol="AAA"), candidate("s2", symbol="BBB")]
        self.engine().tick()
        states = {p.trade_id: p.state for p in self.store.open_positions()}
        self.assertEqual(len(states), 2)
        self.assertTrue(all(s == ExecutionState.PROTECTED for s in states.values()))


class StopHitTests(LifecycleTestCase):
    def _open_one(self) -> ExecutionEngine:
        self.candidates = [candidate("s1")]
        engine = self.engine()
        engine.tick()
        self.candidates = []
        return engine

    def test_a_stop_firing_at_the_broker_is_noticed_and_closed(self) -> None:
        engine = self._open_one()
        stored = self.store.get("s1")
        assert stored is not None
        # Simulate the standing stop executing: the broker goes flat.
        self.broker.fill_sl(stored.stop_order_id, 106.50)
        engine.tick()
        closed = self.store.get("s1")
        assert closed is not None
        self.assertEqual(closed.state, ExecutionState.CLOSED)

    def test_realised_pnl_uses_the_real_fill_not_the_stop_level(self) -> None:
        engine = self._open_one()
        stored = self.store.get("s1")
        assert stored is not None
        self.broker.fill_sl(stored.stop_order_id, 105.00)
        engine.tick()
        closed = self.store.get("s1")
        assert closed is not None
        self.assertAlmostEqual(closed.realised_pnl, 295 * (105.00 - 110.0), places=2)

    def test_the_close_is_attributed_to_the_stop(self) -> None:
        engine = self._open_one()
        stored = self.store.get("s1")
        assert stored is not None
        self.broker.fill_sl(stored.stop_order_id, 106.50)
        engine.tick()
        closed = self.store.get("s1")
        assert closed is not None
        self.assertEqual(closed.extra["close_reason"], CloseReason.STOP_HIT.value)

    def test_a_closed_position_leaves_the_working_set(self) -> None:
        engine = self._open_one()
        stored = self.store.get("s1")
        assert stored is not None
        self.broker.fill_sl(stored.stop_order_id, 106.50)
        engine.tick()
        engine.tick()
        self.assertEqual(self.store.open_positions(), [])


class BreachTests(LifecycleTestCase):
    def _book_loss(self, trade_id: str, loss: float) -> None:
        self.store.save(
            Position(
                trade_id=trade_id,
                candidate=candidate(trade_id, symbol="ZZZ"),
                state=ExecutionState.CLOSED,
                qty=0,
                entry_price=110.0,
                realised_pnl=-loss,
            )
        )

    def test_under_the_cap_nothing_happens(self) -> None:
        self._book_loss("old1", 1_000.0)
        engine = self.engine()
        engine.tick()
        self.assertFalse(engine.breached)
        self.assertIsNone(engine.shutdown_reason)
        self.assertTrue(engine.entries_allowed)

    def test_at_the_cap_entries_pause_and_shutdown_begins(self) -> None:
        self._book_loss("old1", 3_000.0)
        engine = self.engine()
        engine.tick()
        self.assertTrue(engine.breached)
        self.assertEqual(engine.shutdown_reason, CloseReason.DAILY_LOSS_BREACH)
        self.assertFalse(engine.entries_allowed)

    def test_a_breach_closes_an_open_position_including_a_green_one(self) -> None:
        self.candidates = [candidate("s1")]
        engine = self.engine()
        engine.tick()
        self.broker.last_prices["AAA"] = 125.0  # deep in profit
        self._book_loss("old1", 3_000.0)
        self.candidates = []
        engine.tick()
        stored = self.store.get("s1")
        assert stored is not None
        self.assertIn(
            stored.state, (ExecutionState.EXIT_SUBMITTED, ExecutionState.CLOSED)
        )

    def test_a_breach_is_permanent_for_the_session(self) -> None:
        self._book_loss("old1", 3_000.0)
        engine = self.engine()
        engine.tick()
        for _ in range(5):
            engine.tick()
        self.assertTrue(engine.breached)
        self.assertTrue(engine.entries_paused)
        self.assertFalse(engine.entries_allowed)

    def test_auto_stop_only_once_everything_is_confirmed_closed(self) -> None:
        self.candidates = [candidate("s1")]
        engine = self.engine()
        engine.tick()
        self._book_loss("old1", 3_000.0)
        self.candidates = []
        engine.tick()
        self.assertFalse(engine.shutdown_complete)  # exit still in flight
        engine.tick()
        engine.tick()
        self.assertTrue(engine.shutdown_complete)
        self.assertEqual(self.store.open_positions(), [])

    def test_no_new_entries_are_taken_during_a_breach(self) -> None:
        self._book_loss("old1", 3_000.0)
        engine = self.engine()
        engine.tick()
        self.candidates = [candidate("new")]
        engine.tick()
        self.assertIsNone(self.store.get("new"))

    def test_the_breach_is_recorded_once_in_the_diary(self) -> None:
        self._book_loss("old1", 3_000.0)
        engine = self.engine()
        engine.tick()
        engine.tick()
        engine.tick()
        breaches = [
            r
            for r in self.store.list_events("__engine__")
            if r["event_type"] == "daily_loss_breached"
        ]
        self.assertEqual(len(breaches), 1)


class EodTests(LifecycleTestCase):
    def test_nothing_squares_off_before_the_cutoff(self) -> None:
        self.candidates = [candidate("s1")]
        engine = self.engine()
        engine.tick()
        self.clock.now = AFTER_CUTOFF
        self.candidates = []
        engine.tick()
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(stored.state, ExecutionState.PROTECTED)

    def test_the_cutoff_closes_everything_still_open(self) -> None:
        self.candidates = [candidate("s1")]
        engine = self.engine()
        engine.tick()
        self.clock.now = EOD
        self.candidates = []
        engine.tick()
        self.assertEqual(engine.shutdown_reason, CloseReason.EOD_SQUAREOFF)
        stored = self.store.get("s1")
        assert stored is not None
        self.assertIn(
            stored.state, (ExecutionState.EXIT_SUBMITTED, ExecutionState.CLOSED)
        )

    def test_entries_stop_at_two_pm_while_the_engine_keeps_running(self) -> None:
        self.clock.now = AFTER_CUTOFF
        self.candidates = [candidate("late", created_at=AFTER_CUTOFF)]
        engine = self.engine()
        engine.tick()
        stored = self.store.get("late")
        assert stored is not None
        self.assertEqual(stored.state, ExecutionState.REJECTED)
        self.assertEqual(stored.extra["skip_reason"], "past_entry_cutoff")
        self.assertIsNone(engine.shutdown_reason)

    def test_the_eod_close_is_attributed_to_the_cutoff(self) -> None:
        self.candidates = [candidate("s1")]
        engine = self.engine()
        engine.tick()
        self.clock.now = EOD
        self.candidates = []
        for _ in range(3):
            engine.tick()
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(
            stored.extra.get("close_reason"), CloseReason.EOD_SQUAREOFF.value
        )


class FeedHealthTests(LifecycleTestCase):
    def test_a_stale_feed_pauses_entries(self) -> None:
        self.candidates = [candidate("s1")]
        engine = self.engine(feed=StaleFeed())
        engine.tick()
        self.assertTrue(engine.entries_paused)
        self.assertEqual(engine.pause_reason, "feed_stale")
        self.assertIsNone(self.store.get("s1"))

    def test_a_recovered_feed_resumes_entries(self) -> None:
        engine = self.engine(feed=StaleFeed())
        engine.tick()
        self.assertTrue(engine.entries_paused)
        engine.feed_monitor = HealthyFeed()
        engine.tick()
        self.assertFalse(engine.entries_paused)

    def test_a_stale_feed_still_reconciles_existing_positions(self) -> None:
        # Pausing only ever affects new entries.
        self.candidates = [candidate("s1")]
        engine = self.engine()
        engine.tick()
        engine.feed_monitor = StaleFeed()
        self.candidates = []
        engine.tick()
        self.assertEqual(len(self.store.open_positions()), 1)


class StepIsolationTests(LifecycleTestCase):
    def test_a_broken_ingest_does_not_stop_positions_being_protected(self) -> None:
        # The load-bearing property: the least dangerous step must never
        # silence the most dangerous one.
        self.candidates = [candidate("s1")]
        engine = self.engine()
        engine.tick()  # s1 is now protected

        def boom():
            raise RuntimeError("malformed trigger row")

        engine.ingest_triggers = boom  # type: ignore[method-assign]
        # Break the stop at the broker so protection has real work to do.
        stored = self.store.get("s1")
        assert stored is not None
        self.broker.cancel_order(stored.stop_order_id)
        engine.tick()
        engine.tick()
        reloaded = self.store.get("s1")
        assert reloaded is not None
        # Protection noticed and re-placed despite ingest being broken.
        self.assertEqual(reloaded.state, ExecutionState.PROTECTED)
        self.assertGreaterEqual(engine.failures.consecutive.get(STEP_INGEST, 0), 1)

    def test_a_broken_ingest_escalates_at_its_own_threshold(self) -> None:
        engine = self.engine()
        engine.ingest_triggers = lambda: (_ for _ in ()).throw(RuntimeError("x"))  # type: ignore[method-assign]
        for _ in range(FAILURE_THRESHOLDS[STEP_INGEST]):
            engine.tick()
        self.assertIn(STEP_INGEST, engine.failures.escalated)
        # Ingest is not one of the entry-pausing steps.
        self.assertFalse(engine.failures.should_auto_pause())

    def test_an_escalation_is_recorded_in_the_diary(self) -> None:
        engine = self.engine()
        engine.ingest_triggers = lambda: (_ for _ in ()).throw(RuntimeError("x"))  # type: ignore[method-assign]
        for _ in range(FAILURE_THRESHOLDS[STEP_INGEST]):
            engine.tick()
        escalations = [
            r
            for r in self.store.list_events("__engine__")
            if r["event_type"] == "step_escalated"
        ]
        self.assertEqual(len(escalations), 1)

    def test_a_broker_outage_does_not_close_a_live_position(self) -> None:
        self.candidates = [candidate("s1")]
        engine = self.engine()
        engine.tick()
        self.candidates = []
        self.broker.positions_error = True  # positions book unreadable
        engine.tick()
        engine.tick()
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(stored.state, ExecutionState.PROTECTED)
        self.assertIsNone(stored.realised_pnl)


BEFORE_RETRY_CUTOFF = datetime(2026, 9, 22, 9, 20, tzinfo=timezone.utc)  # 14:50 IST
AFTER_RETRY_CUTOFF = datetime(2026, 9, 22, 9, 35, tzinfo=timezone.utc)   # 15:05 IST


class PartialEntryTestCase(LifecycleTestCase):
    """Entries that stay working so each test controls how they fill."""

    def setUp(self) -> None:
        super().setUp()
        self.broker.auto_fill_entry = False

    def _submit(self) -> ExecutionEngine:
        self.candidates = [candidate("s1")]
        engine = self.engine()
        engine.tick()
        self.candidates = []
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(stored.state, ExecutionState.ENTRY_SUBMITTED)
        return engine

    def _entry(self):
        stored = self.store.get("s1")
        assert stored is not None
        return self.broker.orders[str(stored.entry_order_id)]

    def _fill(self, filled: int, avg: float, status: str = "OPEN") -> None:
        entry = self._entry()
        entry.filled_quantity = filled
        entry.pending_quantity = 0 if status != "OPEN" else int(entry.quantity) - filled
        entry.average_price = avg
        entry.status = status


class EntryFillFinalTests(PartialEntryTestCase):
    """Steps 7-8 run once, on the final fill, never on the first chunk."""

    def test_a_full_fill_in_one_tick_is_applied_and_protected(self) -> None:
        engine = self._submit()
        self._fill(295, 110.25, status="COMPLETE")
        engine.tick()
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(stored.state, ExecutionState.PROTECTED)
        self.assertEqual((stored.qty, stored.entry_price), (295, 110.25))

    def test_a_fill_in_chunks_is_judged_once_on_the_final_fill(self) -> None:
        engine = self._submit()
        self._fill(100, 110.00)
        engine.tick()
        self._fill(200, 110.10)
        engine.tick()
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(stored.state, ExecutionState.ENTRY_SUBMITTED)
        self.assertNotIn("entry_filled", self.events("s1"))

        self._fill(295, 110.20, status="COMPLETE")
        engine.tick()
        engine.tick()
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(self.events("s1").count("entry_filled"), 1)
        self.assertEqual((stored.qty, stored.entry_price), (295, 110.20))
        self.assertAlmostEqual(stored.risk_taken_rupees, (110.20 - 106.95) * 295, places=4)
        self.assertEqual(stored.state, ExecutionState.PROTECTED)
        self.assertEqual(int(self.broker.orders[str(stored.stop_order_id)].quantity), 295)

    def test_partly_filled_then_cancelled_protects_the_partial_quantity(self) -> None:
        engine = self._submit()
        self._fill(120, 110.10)
        engine.tick()
        self._fill(120, 110.10, status="CANCELLED")
        engine.tick()
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(self.events("s1").count("entry_filled"), 1)
        self.assertEqual((stored.qty, stored.entry_price), (120, 110.10))
        self.assertEqual(stored.state, ExecutionState.PROTECTED)
        self.assertEqual(int(self.broker.orders[str(stored.stop_order_id)].quantity), 120)

    def test_cancelled_with_nothing_filled_is_still_a_failed_entry(self) -> None:
        engine = self._submit()
        self._fill(0, 0.0, status="CANCELLED")
        self._entry().average_price = None
        engine.tick()
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(stored.state, ExecutionState.CANCELLED)
        self.assertNotIn("entry_filled", self.events("s1"))


class PartialFillStallTests(PartialEntryTestCase):
    """An entry stuck partly filled past 10s is escalated once. Alert only."""

    KEY = "entry_stalled:s1"

    def test_no_escalation_within_ten_seconds(self) -> None:
        engine = self._submit()
        self._fill(100, 110.00)
        engine.tick()  # first sighting starts the clock
        self.clock.advance(9.9)
        engine.tick()
        self.assertNotIn("entry_partial_fill_stalled", self.events("s1"))
        self.assertNotIn(self.KEY, engine.failures.escalated)

    def test_escalates_exactly_once_after_ten_seconds(self) -> None:
        engine = self._submit()
        self._fill(100, 110.00)
        engine.tick()
        for _ in range(15):
            self.clock.advance(1.0)
            engine.tick()
        self.assertEqual(self.events("s1").count("entry_partial_fill_stalled"), 1)
        self.assertIn(self.KEY, engine.failures.escalated)
        self.assertEqual(
            sum(1 for n in engine.escalation_notices if n.startswith(self.KEY)), 1
        )
        # Alert only: nothing cancelled, no stop placed early.
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(stored.state, ExecutionState.ENTRY_SUBMITTED)
        self.assertIsNone(stored.stop_order_id)
        self.assertEqual(str(self._entry().status), "OPEN")

    def test_the_alert_clears_once_the_fill_is_final(self) -> None:
        engine = self._submit()
        self._fill(100, 110.00)
        engine.tick()
        self.clock.advance(11.0)
        engine.tick()
        self.assertIn(self.KEY, engine.failures.escalated)
        self._fill(295, 110.10, status="COMPLETE")
        engine.tick()
        self.assertNotIn(self.KEY, engine.failures.escalated)
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(stored.state, ExecutionState.PROTECTED)

    def test_no_escalation_when_the_order_completes_in_time(self) -> None:
        engine = self._submit()
        self._fill(100, 110.00)
        engine.tick()
        self.clock.advance(3.0)
        self._fill(295, 110.10, status="COMPLETE")
        engine.tick()
        self.clock.advance(20.0)
        engine.tick()
        self.assertNotIn("entry_partial_fill_stalled", self.events("s1"))
        self.assertNotIn(self.KEY, engine.failures.escalated)


class PartialEntrySquareoffTests(PartialEntryTestCase):
    """A partly filled entry caught by a square-off is flattened, never dropped."""

    def test_kill_all_flattens_the_filled_part_of_a_partial_entry(self) -> None:
        engine = self._submit()
        self._fill(120, 110.10)
        engine.tick()
        self.queue.enqueue(CommandKind.KILL_ALL)
        for _ in range(5):
            engine.tick()
        stored = self.store.get("s1")
        assert stored is not None
        events = self.events("s1")
        self.assertIn("entry_cancel_partial_fill", events)
        self.assertNotIn("cancelled", events)
        self.assertEqual(stored.state, ExecutionState.CLOSED)
        self.assertEqual(stored.qty, 120)
        self.assertEqual(self.broker.list_net_positions().get("AAA", 0), 0)
        self.assertTrue(engine.shutdown_complete)


class UnfilledEntryStallTests(PartialEntryTestCase):
    """An entry still OPEN at Kite with 0 filled past 10s: escalated once, alert only."""

    KEY = "entry_stalled:s1"

    def test_no_escalation_within_ten_seconds(self) -> None:
        engine = self._submit()
        engine.tick()  # first sighting of the still-working entry
        self.clock.advance(9.9)
        engine.tick()
        self.assertNotIn("entry_unfilled_stalled", self.events("s1"))
        self.assertNotIn(self.KEY, engine.failures.escalated)

    def test_escalates_exactly_once_and_cancels_nothing(self) -> None:
        engine = self._submit()
        for _ in range(15):
            self.clock.advance(1.0)
            engine.tick()
        self.assertEqual(self.events("s1").count("entry_unfilled_stalled"), 1)
        self.assertIn("0/295 filled", engine.failures.escalated[self.KEY])
        entry = self._entry()
        self.assertEqual(str(entry.status), "OPEN")
        self.assertEqual(int(entry.filled_quantity or 0), 0)
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(stored.state, ExecutionState.ENTRY_SUBMITTED)
        self.assertIsNone(stored.stop_order_id)

    def test_a_partial_fill_after_the_alert_is_escalated_on_its_own_clock(self) -> None:
        engine = self._submit()
        engine.tick()  # first sighting starts the clock
        self.clock.advance(11.0)
        engine.tick()
        self.assertEqual(self.events("s1").count("entry_unfilled_stalled"), 1)

        self._fill(100, 110.00)
        engine.tick()  # new reason: the clock restarts
        self.clock.advance(5.0)
        engine.tick()
        self.assertNotIn("entry_partial_fill_stalled", self.events("s1"))
        self.clock.advance(6.0)
        engine.tick()
        self.assertEqual(self.events("s1").count("entry_partial_fill_stalled"), 1)
        self.assertIn("100/295", engine.failures.escalated[self.KEY])

    def test_the_alert_clears_when_the_order_fills(self) -> None:
        engine = self._submit()
        engine.tick()  # first sighting starts the clock
        self.clock.advance(11.0)
        engine.tick()
        self.assertIn(self.KEY, engine.failures.escalated)
        self._fill(295, 110.05, status="COMPLETE")
        engine.tick()
        self.assertNotIn(self.KEY, engine.failures.escalated)
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(stored.state, ExecutionState.PROTECTED)

    def test_the_alert_clears_when_kite_cancels_it_unfilled(self) -> None:
        engine = self._submit()
        engine.tick()  # first sighting starts the clock
        self.clock.advance(11.0)
        engine.tick()
        self._fill(0, 0.0, status="CANCELLED")
        self._entry().average_price = None
        engine.tick()
        self.assertNotIn(self.KEY, engine.failures.escalated)
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(stored.state, ExecutionState.CANCELLED)


class ProtectionRetryCutoffTests(LifecycleTestCase):
    def test_stop_replacement_gives_up_past_fifteen_oh_five(self) -> None:
        self.candidates = [candidate("s1")]
        engine = self.engine()
        engine.tick()  # s1 is now protected
        stored = self.store.get("s1")
        assert stored is not None
        self.broker.cancel_order(stored.stop_order_id)  # stop lost at the broker
        self.broker.place_slm = lambda **_: (_ for _ in ()).throw(  # type: ignore[method-assign]
            RuntimeError("Intraday orders (MIS) are allowed only till 3:12 PM")
        )

        self.clock.now = BEFORE_RETRY_CUTOFF
        engine.tick()  # reconciliation notices the missing stop
        engine.tick()  # a retry is attempted and fails, before the cutoff
        before_failures = len(
            [e for e in self.events("s1") if e == "stop_place_failed"]
        )
        self.assertGreaterEqual(before_failures, 1)

        self.clock.now = AFTER_RETRY_CUTOFF
        engine.tick()
        engine.tick()
        after_failures = len(
            [e for e in self.events("s1") if e == "stop_place_failed"]
        )
        # No new attempts, and so no new failures, once past the cutoff.
        # (By 15:05 the 14:50 square-off has already flattened the position
        # anyway — this asserts the retry itself stopped, independent of that.)
        self.assertEqual(after_failures, before_failures)


class VwapWaitTests(LifecycleTestCase):
    def test_a_verdict_arriving_late_is_still_traded(self) -> None:
        # No poll/retry state: the same candidate simply reappears next tick.
        self.candidates = [candidate("s1", classification=None)]
        engine = self.engine()
        engine.tick()
        self.assertIsNone(self.store.get("s1"))
        self.candidates = [candidate("s1", classification="ACCEPT")]
        engine.tick()
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(stored.state, ExecutionState.PROTECTED)

    def test_a_verdict_that_never_arrives_is_skipped_after_the_ceiling(self) -> None:
        self.candidates = [candidate("s1", classification=None)]
        engine = self.engine()
        engine.tick()
        self.clock.advance(3.0)  # past the 2s ceiling
        engine.tick()
        stored = self.store.get("s1")
        assert stored is not None
        self.assertEqual(stored.extra["skip_reason"], "vwap_unavailable")


class KillAllLifecycleTests(LifecycleTestCase):
    def test_kill_all_closes_everything_then_completes(self) -> None:
        self.candidates = [candidate("s1", symbol="AAA"), candidate("s2", symbol="BBB")]
        engine = self.engine()
        engine.tick()
        self.candidates = []
        self.queue.enqueue(CommandKind.KILL_ALL)
        engine.tick()
        for _ in range(3):
            engine.tick()
        self.assertTrue(engine.shutdown_complete)
        self.assertEqual(self.store.open_positions(), [])
        for trade_id in ("s1", "s2"):
            stored = self.store.get(trade_id)
            assert stored is not None
            self.assertEqual(stored.state, ExecutionState.CLOSED)


if __name__ == "__main__":
    unittest.main()
