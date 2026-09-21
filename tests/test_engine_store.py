"""SqlitePositionStore: round-trips, dedup, the working set, and atomicity."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from engine_store import (
    SqlitePositionStore,
    candidate_from_json,
    candidate_to_json,
    realised_loss_rupees,
)
from engine_types import ExecutionState, Position, TriggerCandidate


def candidate(setup_id: str = "s1", **overrides) -> TriggerCandidate:
    values = dict(
        setup_id=setup_id,
        continuation_rule_version="v1",
        session_date="2026-09-22",
        tradingsymbol="AAA",
        instrument_token=111,
        direction="UP",
        trigger_price=110.0,
        pullback_swing_high=109.0,
        pullback_swing_low=107.0,
        tick_size=0.05,
        buffer_ticks=1,
        trigger_exchange_ts="2026-09-22T10:01:16+00:00",
        created_at=datetime.now(timezone.utc).isoformat(),
        vwap_classification="ACCEPT",
    )
    values.update(overrides)
    return TriggerCandidate(**values)


def position(trade_id: str = "t1", **overrides) -> Position:
    values = dict(
        trade_id=trade_id,
        candidate=candidate(overrides.pop("setup_id", trade_id)),
        state=ExecutionState.PENDING_ENTRY,
    )
    values.update(overrides)
    return Position(**values)


class StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "engine.db"
        self.store = SqlitePositionStore(self.path)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()


class CandidateSerializationTests(unittest.TestCase):
    def test_round_trip_preserves_every_field(self) -> None:
        original = candidate()
        restored = candidate_from_json(candidate_to_json(original))
        self.assertEqual(restored, original)

    def test_none_valued_fields_survive(self) -> None:
        original = candidate(
            pullback_swing_high=None,
            trigger_exchange_ts=None,
            vwap_classification=None,
        )
        restored = candidate_from_json(candidate_to_json(original))
        self.assertEqual(restored, original)

    def test_unknown_keys_from_a_newer_build_are_dropped(self) -> None:
        raw = candidate_to_json(candidate())
        tampered = raw[:-1] + ',"a_field_from_the_future":42}'
        restored = candidate_from_json(tampered)
        self.assertEqual(restored.setup_id, "s1")

    def test_missing_keys_fall_back_to_defaults(self) -> None:
        # Simulates a row written before a defaulted field was added.
        restored = candidate_from_json(
            '{"setup_id":"s9","continuation_rule_version":"v1",'
            '"session_date":"2026-09-22","tradingsymbol":"BBB",'
            '"instrument_token":2,"direction":"DOWN","trigger_price":50.0,'
            '"pullback_swing_high":51.0,"pullback_swing_low":49.0,'
            '"tick_size":0.05,"buffer_ticks":1,"trigger_exchange_ts":null,'
            '"created_at":"2026-09-22T04:00:00+00:00"}'
        )
        self.assertEqual(restored.setup_id, "s9")
        self.assertIsNone(restored.vwap_classification)


class PositionRoundTripTests(StoreTestCase):
    def test_save_then_get_preserves_every_field(self) -> None:
        saved = position(
            "t1",
            state=ExecutionState.PROTECTED,
            qty=300,
            entry_price=110.25,
            stop_price=106.95,
            entry_order_id="eo1",
            stop_order_id="so1",
            exit_order_id=None,
            realised_pnl=None,
            risk_taken_rupees=990.0,
            is_live=True,
            run_id="run-7",
            extra={"skip_reason": None, "note": "hello"},
        )
        self.store.save(saved)
        loaded = self.store.get("t1")
        assert loaded is not None
        self.assertEqual(loaded.state, ExecutionState.PROTECTED)
        self.assertEqual(loaded.qty, 300)
        self.assertEqual(loaded.entry_price, 110.25)
        self.assertEqual(loaded.stop_price, 106.95)
        self.assertEqual(loaded.entry_order_id, "eo1")
        self.assertEqual(loaded.stop_order_id, "so1")
        self.assertIsNone(loaded.exit_order_id)
        self.assertEqual(loaded.risk_taken_rupees, 990.0)
        self.assertTrue(loaded.is_live)
        self.assertEqual(loaded.run_id, "run-7")
        self.assertEqual(loaded.extra["note"], "hello")
        self.assertEqual(loaded.candidate, saved.candidate)

    def test_every_state_round_trips(self) -> None:
        for state in ExecutionState:
            with self.subTest(state=state.value):
                self.store.save(position(f"t-{state.value}", state=state))
                loaded = self.store.get(f"t-{state.value}")
                assert loaded is not None
                self.assertEqual(loaded.state, state)

    def test_save_is_an_upsert_not_a_duplicate(self) -> None:
        pos = position("t1")
        self.store.save(pos)
        pos.state = ExecutionState.ENTRY_SUBMITTED
        pos.qty = 100
        self.store.save(pos)
        rows = self.store.list_positions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["state"], "entry_submitted")
        self.assertEqual(rows[0]["qty"], 100)

    def test_created_at_is_not_overwritten_by_later_saves(self) -> None:
        pos = position("t1")
        self.store.save(pos)
        first = self.store.list_positions()[0]["created_at"]
        pos.state = ExecutionState.ENTRY_SUBMITTED
        self.store.save(pos)
        row = self.store.list_positions()[0]
        self.assertEqual(row["created_at"], first)
        self.assertGreaterEqual(row["updated_at"], first)

    def test_denormalized_columns_come_from_the_candidate(self) -> None:
        self.store.save(
            Position(
                trade_id="t1",
                candidate=candidate(
                    "setup-9",
                    tradingsymbol="ZZZ",
                    direction="DOWN",
                    vwap_classification="LIMITED",
                ),
                state=ExecutionState.PENDING_ENTRY,
            )
        )
        row = self.store.list_positions()[0]
        self.assertEqual(row["setup_id"], "setup-9")
        self.assertEqual(row["tradingsymbol"], "ZZZ")
        self.assertEqual(row["direction"], "DOWN")
        self.assertEqual(row["vwap_classification"], "LIMITED")
        self.assertEqual(row["session_date"], "2026-09-22")

    def test_get_unknown_trade_returns_none(self) -> None:
        self.assertIsNone(self.store.get("nope"))


class WorkingSetTests(StoreTestCase):
    def test_open_positions_excludes_all_three_terminal_states(self) -> None:
        self.store.save(position("open1", state=ExecutionState.ENTERED))
        self.store.save(position("open2", state=ExecutionState.PROTECTED))
        self.store.save(position("gone1", state=ExecutionState.CLOSED))
        self.store.save(position("gone2", state=ExecutionState.REJECTED))
        self.store.save(position("gone3", state=ExecutionState.CANCELLED))
        ids = {p.trade_id for p in self.store.open_positions()}
        self.assertEqual(ids, {"open1", "open2"})

    def test_open_positions_includes_every_non_terminal_state(self) -> None:
        non_terminal = [
            ExecutionState.PENDING_ENTRY,
            ExecutionState.ENTRY_SUBMITTED,
            ExecutionState.ENTERED,
            ExecutionState.PROTECTED,
            ExecutionState.TRAILING,
            ExecutionState.EXIT_SUBMITTED,
        ]
        for state in non_terminal:
            self.store.save(position(f"t-{state.value}", state=state))
        self.assertEqual(len(self.store.open_positions()), len(non_terminal))

    def test_a_closed_position_leaves_the_working_set_permanently(self) -> None:
        pos = position("t1", state=ExecutionState.PROTECTED)
        self.store.save(pos)
        self.assertEqual(len(self.store.open_positions()), 1)
        pos.state = ExecutionState.CLOSED
        pos.realised_pnl = -250.0
        self.store.save(pos)
        self.assertEqual(self.store.open_positions(), [])

    def test_empty_store_has_no_open_positions(self) -> None:
        self.assertEqual(self.store.open_positions(), [])


class DedupTests(StoreTestCase):
    def test_exists_is_true_only_for_known_setups(self) -> None:
        self.store.save(position("t1", setup_id="setup-a"))
        self.assertTrue(self.store.exists("setup-a"))
        self.assertFalse(self.store.exists("setup-b"))

    def test_exists_is_true_even_for_a_skipped_setup(self) -> None:
        # A rejected setup must never be reconsidered.
        self.store.save(
            Position(
                trade_id="t1",
                candidate=candidate("setup-a"),
                state=ExecutionState.REJECTED,
                extra={"skip_reason": "vwap_reject"},
            )
        )
        self.assertTrue(self.store.exists("setup-a"))


class EventDiaryTests(StoreTestCase):
    def test_events_are_returned_in_the_order_they_happened(self) -> None:
        self.store.save(position("t1"))
        for name in ("entry_intent", "entry_submitted", "entry_filled", "protected"):
            self.store.append_event("t1", name, {"n": name})
        types = [row["event_type"] for row in self.store.list_events("t1")]
        self.assertEqual(
            types, ["entry_intent", "entry_submitted", "entry_filled", "protected"]
        )

    def test_events_are_append_only_never_rewritten(self) -> None:
        self.store.save(position("t1"))
        self.store.append_event("t1", "protected", {"stop": 106.95})
        self.store.append_event("t1", "protected", {"stop": 107.50})
        rows = self.store.list_events("t1")
        self.assertEqual(len(rows), 2)
        self.assertIn("106.95", rows[0]["payload_json"])
        self.assertIn("107.5", rows[1]["payload_json"])

    def test_events_are_scoped_per_trade(self) -> None:
        self.store.save(position("t1"))
        self.store.save(position("t2"))
        self.store.append_event("t1", "a")
        self.store.append_event("t2", "b")
        self.assertEqual([r["event_type"] for r in self.store.list_events("t1")], ["a"])
        self.assertEqual([r["event_type"] for r in self.store.list_events("t2")], ["b"])

    def test_payload_defaults_to_empty_object(self) -> None:
        self.store.save(position("t1"))
        self.store.append_event("t1", "stopped")
        self.assertEqual(self.store.list_events("t1")[0]["payload_json"], "{}")

    def test_unserializable_payload_values_degrade_to_strings(self) -> None:
        # A datetime in a payload must not take down a real trade's diary write.
        self.store.save(position("t1"))
        self.store.append_event("t1", "closed", {"at": datetime.now(timezone.utc)})
        self.assertIn("at", self.store.list_events("t1")[0]["payload_json"])


class AtomicityTests(StoreTestCase):
    def test_save_with_event_writes_both(self) -> None:
        self.store.save_with_event(
            position("t1", state=ExecutionState.PENDING_ENTRY),
            "entry_intent",
            {"qty": 300},
        )
        self.assertIsNotNone(self.store.get("t1"))
        self.assertEqual(
            [r["event_type"] for r in self.store.list_events("t1")], ["entry_intent"]
        )

    def test_a_failure_mid_transaction_writes_neither(self) -> None:
        original = self.store._insert_event

        def boom(*_args, **_kwargs):
            raise sqlite3.OperationalError("disk gave up")

        self.store._insert_event = boom  # type: ignore[method-assign]
        try:
            with self.assertRaises(sqlite3.OperationalError):
                self.store.save_with_event(position("t1"), "entry_intent", {"qty": 300})
        finally:
            self.store._insert_event = original  # type: ignore[method-assign]

        # Neither half landed: no state row claiming an intent that has no
        # diary entry to explain it.
        self.assertIsNone(self.store.get("t1"))
        self.assertEqual(self.store.list_events("t1"), [])

    def test_a_failure_does_not_roll_back_earlier_committed_work(self) -> None:
        self.store.save_with_event(position("t0"), "entry_intent")
        original = self.store._insert_event
        self.store._insert_event = lambda *a, **k: (_ for _ in ()).throw(  # type: ignore[method-assign]
            sqlite3.OperationalError("nope")
        )
        try:
            with self.assertRaises(sqlite3.OperationalError):
                self.store.save_with_event(position("t1"), "entry_intent")
        finally:
            self.store._insert_event = original  # type: ignore[method-assign]
        self.assertIsNotNone(self.store.get("t0"))


class DurabilityTests(StoreTestCase):
    def test_state_survives_closing_and_reopening_the_file(self) -> None:
        self.store.save(
            position(
                "t1",
                state=ExecutionState.PROTECTED,
                qty=300,
                entry_price=110.25,
                stop_price=106.95,
            )
        )
        self.store.append_event("t1", "protected", {"stop": 106.95})
        self.store.close()

        reopened = SqlitePositionStore(self.path)
        try:
            loaded = reopened.get("t1")
            assert loaded is not None
            self.assertEqual(loaded.state, ExecutionState.PROTECTED)
            self.assertEqual(loaded.entry_price, 110.25)
            self.assertEqual(len(reopened.list_events("t1")), 1)
        finally:
            reopened.close()

    def test_synchronous_is_full_not_a_faster_unsafe_setting(self) -> None:
        # Guards the one pragma the crash-safety design actually depends on.
        value = self.store._conn.execute("PRAGMA synchronous").fetchone()[0]
        self.assertEqual(int(value), 2)  # 2 == FULL

    def test_opening_an_existing_file_is_idempotent(self) -> None:
        self.store.save(position("t1"))
        again = SqlitePositionStore(self.path)
        try:
            self.assertIsNotNone(again.get("t1"))
        finally:
            again.close()


class DailyLossInputTests(StoreTestCase):
    def test_closed_today_filters_by_session_and_state(self) -> None:
        self.store.save(
            Position(
                trade_id="t1",
                candidate=candidate("a", session_date="2026-09-22"),
                state=ExecutionState.CLOSED,
                realised_pnl=-500.0,
            )
        )
        self.store.save(
            Position(
                trade_id="t2",
                candidate=candidate("b", session_date="2026-09-21"),
                state=ExecutionState.CLOSED,
                realised_pnl=-900.0,
            )
        )
        self.store.save(
            Position(
                trade_id="t3",
                candidate=candidate("c", session_date="2026-09-22"),
                state=ExecutionState.PROTECTED,
            )
        )
        closed = self.store.closed_today("2026-09-22")
        self.assertEqual([p.trade_id for p in closed], ["t1"])

    def test_realised_loss_counts_losses_only_and_returns_a_positive(self) -> None:
        positions = [
            position("a", realised_pnl=-500.0),
            position("b", realised_pnl=-250.0),
            position("c", realised_pnl=1_000.0),
            position("d", realised_pnl=None),
        ]
        self.assertEqual(realised_loss_rupees(positions), 750.0)

    def test_realised_loss_of_nothing_is_zero(self) -> None:
        self.assertEqual(realised_loss_rupees([]), 0.0)

    def test_profits_do_not_offset_losses(self) -> None:
        # A green trade must not buy back room under the daily cap.
        positions = [position("a", realised_pnl=-3_000.0), position("b", realised_pnl=5_000.0)]
        self.assertEqual(realised_loss_rupees(positions), 3_000.0)


class ListPositionsTests(StoreTestCase):
    def test_session_filter(self) -> None:
        self.store.save(
            Position(
                trade_id="t1",
                candidate=candidate("a", session_date="2026-09-22"),
                state=ExecutionState.CLOSED,
            )
        )
        self.store.save(
            Position(
                trade_id="t2",
                candidate=candidate("b", session_date="2026-09-21"),
                state=ExecutionState.CLOSED,
            )
        )
        self.assertEqual(len(self.store.list_positions("2026-09-22")), 1)
        self.assertEqual(len(self.store.list_positions()), 2)

    def test_rows_expose_timestamps_for_the_api(self) -> None:
        self.store.save(position("t1"))
        row = self.store.list_positions()[0]
        self.assertIn("created_at", row.keys())
        self.assertIn("updated_at", row.keys())


if __name__ == "__main__":
    unittest.main()
