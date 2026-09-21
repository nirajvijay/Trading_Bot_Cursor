"""ExecutionEngine.handle_trigger: VWAP-verdict branching, ranking, gates.

Uses the real SqlitePositionStore rather than a fake: it is fast, and the
branching logic is only meaningful in terms of what actually gets persisted.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from engine_config import SessionRiskConfig
from engine_core import ExecutionEngine
from engine_entry import EntryResult
from engine_feed import FeedHealth
from engine_risk import RiskPolicy
from engine_sizing import RiskCappedSizing
from engine_store import SqlitePositionStore
from engine_types import ExecutionState, RiskLimits, TriggerCandidate
from trading_engine_broker import FakeBroker

# A moment comfortably inside the entry window (09:15-14:00 IST).
INSIDE_WINDOW = datetime(2026, 9, 22, 5, 0, tzinfo=timezone.utc)  # 10:30 IST


def _candidate(
    setup_id: str,
    *,
    vwap_classification,
    age_seconds: float = 0.0,
    symbol: str = "AAA",
    volume: int | None = None,
    average: float | None = None,
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
        trigger_exchange_ts="2026-09-22T10:01:16+00:00",
        created_at=(INSIDE_WINDOW - timedelta(seconds=age_seconds)).isoformat(),
        vwap_classification=vwap_classification,
        breakout_candle_volume=volume,
        avg_prior_3_1m_volume=average,
    )


class AlwaysHealthyFeed:
    def check(self, *, now=None) -> FeedHealth:
        return FeedHealth(healthy=True, age_seconds=0.5)


class StaleFeed:
    def check(self, *, now=None) -> FeedHealth:
        return FeedHealth(healthy=False, age_seconds=30.0, reason="feed_stale")


class EngineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SqlitePositionStore(Path(self._tmp.name) / "engine.db")
        self.broker = FakeBroker()
        self.broker.last_prices["AAA"] = 110.0
        self.broker.last_prices["BBB"] = 110.0

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def engine(self, *, candidates=None, feed=None, now=INSIDE_WINDOW) -> ExecutionEngine:
        return ExecutionEngine(
            broker=self.broker,
            store=self.store,
            feed_monitor=feed or AlwaysHealthyFeed(),
            risk_policy=RiskPolicy(
                RiskLimits(
                    per_trade_cap_rupees=900.0,
                    per_trade_cap_vwap_limited_rupees=450.0,
                    daily_loss_cap_rupees=3000.0,
                )
            ),
            sizing_policy=RiskCappedSizing(),
            candidate_source=lambda: list(candidates or []),
            session_config=SessionRiskConfig(),
            now_fn=lambda: now,
        )

    def saved(self):
        return self.store.list_positions()


class SkipBranchTests(EngineTestCase):
    def test_reject_classification_skips_immediately(self) -> None:
        self.engine().handle_trigger(_candidate("rejected", vwap_classification="REJECT"))
        rows = self.saved()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["state"], ExecutionState.REJECTED.value)
        self.assertIn("vwap_reject", rows[0]["extra_json"])

    def test_literal_unavailable_classification_skips_immediately(self) -> None:
        self.engine().handle_trigger(_candidate("unavail", vwap_classification="UNAVAILABLE"))
        self.assertIn("vwap_unavailable", self.saved()[0]["extra_json"])

    def test_none_classification_within_wait_window_defers_without_storing(self) -> None:
        self.engine().handle_trigger(
            _candidate("pending", vwap_classification=None, age_seconds=0.5)
        )
        self.assertEqual(self.saved(), [])

    def test_none_classification_past_wait_window_skips_as_unavailable(self) -> None:
        self.engine().handle_trigger(
            _candidate("stale_pending", vwap_classification=None, age_seconds=5.0)
        )
        rows = self.saved()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["state"], ExecutionState.REJECTED.value)
        self.assertIn("vwap_unavailable", rows[0]["extra_json"])

    def test_a_skip_writes_a_diary_entry_too(self) -> None:
        self.engine().handle_trigger(_candidate("rejected", vwap_classification="REJECT"))
        events = [r["event_type"] for r in self.store.list_events("rejected")]
        self.assertEqual(events, ["skipped"])


class EntryBranchTests(EngineTestCase):
    def test_accept_routes_to_the_entry_path_with_the_normal_cap(self) -> None:
        outcome = self.engine().handle_trigger(
            _candidate("accepted", vwap_classification="ACCEPT")
        )
        assert outcome is not None and outcome.position is not None
        self.assertEqual(outcome.result, EntryResult.FILLED)
        # 900 cap, risk/share 3.05 -> 295 shares.
        self.assertEqual(outcome.position.qty, 295)
        self.assertEqual(outcome.position.extra["risk_cap_rupees"], 900.0)

    def test_limited_routes_to_the_entry_path_with_the_smaller_cap(self) -> None:
        outcome = self.engine().handle_trigger(
            _candidate("limited", vwap_classification="LIMITED")
        )
        assert outcome is not None and outcome.position is not None
        self.assertEqual(outcome.position.extra["risk_cap_rupees"], 450.0)
        self.assertEqual(outcome.position.qty, 147)

    def test_an_entered_position_is_persisted(self) -> None:
        self.engine().handle_trigger(_candidate("accepted", vwap_classification="ACCEPT"))
        stored = self.store.get("accepted")
        assert stored is not None
        self.assertEqual(stored.state, ExecutionState.ENTERED)
        self.assertEqual(stored.entry_price, 110.0)


class PauseAndStopTests(EngineTestCase):
    def test_paused_entries_skip_everything_including_accept(self) -> None:
        engine = self.engine()
        engine.entries_paused = True
        engine.handle_trigger(_candidate("accepted", vwap_classification="ACCEPT"))
        engine.handle_trigger(_candidate("rejected", vwap_classification="REJECT"))
        self.assertEqual(self.saved(), [])

    def test_stopped_entries_also_skip_everything(self) -> None:
        engine = self.engine()
        engine.entries_stopped = True
        engine.handle_trigger(_candidate("accepted", vwap_classification="ACCEPT"))
        self.assertEqual(self.saved(), [])

    def test_stop_and_pause_are_independent_flags(self) -> None:
        engine = self.engine()
        engine.entries_stopped = True
        self.assertFalse(engine.entries_paused)


class EntryGateTests(EngineTestCase):
    def test_past_the_two_pm_cutoff_no_entry_is_taken(self) -> None:
        after_cutoff = datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc)  # 14:30 IST
        outcome = self.engine(now=after_cutoff).handle_trigger(
            _candidate("late", vwap_classification="ACCEPT")
        )
        assert outcome is not None
        self.assertEqual(outcome.result, EntryResult.SKIPPED)
        self.assertEqual(outcome.reason, "past_entry_cutoff")
        self.assertEqual(self.broker.market_place_count, 0)

    def test_a_stale_feed_blocks_the_entry_at_the_gate(self) -> None:
        outcome = self.engine(feed=StaleFeed()).handle_trigger(
            _candidate("stale", vwap_classification="ACCEPT")
        )
        assert outcome is not None
        self.assertEqual(outcome.reason, "feed_stale")

    def test_a_second_trigger_on_the_same_symbol_is_refused(self) -> None:
        engine = self.engine()
        engine.handle_trigger(_candidate("first", vwap_classification="ACCEPT"))
        outcome = engine.handle_trigger(
            _candidate("second", vwap_classification="ACCEPT", symbol="AAA")
        )
        assert outcome is not None
        self.assertEqual(outcome.reason, "symbol_already_open")

    def test_a_different_symbol_is_allowed_alongside(self) -> None:
        engine = self.engine()
        engine.handle_trigger(_candidate("first", vwap_classification="ACCEPT"))
        outcome = engine.handle_trigger(
            _candidate("second", vwap_classification="ACCEPT", symbol="BBB")
        )
        assert outcome is not None
        self.assertEqual(outcome.result, EntryResult.FILLED)


class IngestTests(EngineTestCase):
    def test_setups_already_known_to_the_store_are_not_re_handled(self) -> None:
        seen = _candidate("seen", vwap_classification="REJECT")
        engine = self.engine(candidates=[seen])
        engine.handle_trigger(seen)
        before = len(self.store.list_events("seen"))
        engine.ingest_triggers()
        self.assertEqual(len(self.store.list_events("seen")), before)

    def test_capital_shrinks_as_positions_are_opened(self) -> None:
        # Two symbols, both ACCEPT. The second sizes against less capital
        # because the first is now holding margin.
        engine = self.engine()
        engine.handle_trigger(_candidate("a", vwap_classification="ACCEPT", symbol="AAA"))
        outcome = engine.handle_trigger(
            _candidate("b", vwap_classification="ACCEPT", symbol="BBB")
        )
        assert outcome is not None and outcome.position is not None
        # Still risk-bound at this capital, so quantity is unchanged -- what
        # matters is that available capital was reduced, not zero.
        self.assertEqual(outcome.position.extra["binding_constraint"], "risk")
        self.assertLess(
            outcome.position.extra["capital_based_qty"],
            engine.session_config.buying_power_rupees / 110.0,
        )

    def test_accept_is_ingested_before_limited_in_the_same_batch(self) -> None:
        # Both are on the same symbol, so whichever is handled first wins the
        # slot and the other is refused -- which makes ordering observable.
        limited = _candidate("limited", vwap_classification="LIMITED", symbol="AAA")
        accept = _candidate("accept", vwap_classification="ACCEPT", symbol="AAA")
        engine = self.engine(candidates=[limited, accept])
        engine.ingest_triggers()
        accept_row = self.store.get("accept")
        limited_row = self.store.get("limited")
        assert accept_row is not None and limited_row is not None
        self.assertEqual(accept_row.state, ExecutionState.ENTERED)
        self.assertEqual(limited_row.extra.get("skip_reason"), "symbol_already_open")

    def test_stronger_breakout_wins_the_slot_within_a_tier(self) -> None:
        weak = _candidate(
            "weak", vwap_classification="ACCEPT", symbol="AAA", volume=1_100, average=1_000.0
        )
        strong = _candidate(
            "strong", vwap_classification="ACCEPT", symbol="AAA", volume=9_000, average=1_000.0
        )
        engine = self.engine(candidates=[weak, strong])
        engine.ingest_triggers()
        strong_row = self.store.get("strong")
        weak_row = self.store.get("weak")
        assert strong_row is not None and weak_row is not None
        self.assertEqual(strong_row.state, ExecutionState.ENTERED)
        self.assertEqual(weak_row.extra.get("skip_reason"), "symbol_already_open")


if __name__ == "__main__":
    unittest.main()
