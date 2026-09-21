"""Loop timing (drift) and the tiered per-step failure policy."""

from __future__ import annotations

import unittest
from typing import List

from engine_runloop import (
    AUTO_PAUSE_ON_ESCALATION,
    FAILURE_THRESHOLDS,
    STEP_COMMANDS,
    STEP_INGEST,
    STEP_PROTECTION,
    STEP_RECONCILE,
    STEP_SQUAREOFF,
    TICK_INTERVAL_SECONDS,
    StepFailureTracker,
    accumulated_drift_seconds,
    next_sleep_seconds,
    run_step,
)


class TickIntervalTests(unittest.TestCase):
    def test_the_interval_is_one_second(self) -> None:
        # Caps the unprotected window after a market fill at about a second.
        self.assertEqual(TICK_INTERVAL_SECONDS, 1.0)

    def test_the_vwap_wait_is_twice_the_tick_interval(self) -> None:
        from engine_core import VWAP_WAIT_SECONDS

        self.assertEqual(VWAP_WAIT_SECONDS, 2 * TICK_INTERVAL_SECONDS)


class DriftTests(unittest.TestCase):
    def test_a_fast_tick_sleeps_the_remainder(self) -> None:
        self.assertAlmostEqual(
            next_sleep_seconds(tick_started_at=0.0, tick_finished_at=0.3), 0.7
        )

    def test_an_overrunning_tick_starts_the_next_one_immediately(self) -> None:
        # Never negative, never "catch up by skipping".
        self.assertEqual(
            next_sleep_seconds(tick_started_at=0.0, tick_finished_at=1.4), 0.0
        )

    def test_a_tick_taking_exactly_the_interval_sleeps_nothing(self) -> None:
        self.assertEqual(
            next_sleep_seconds(tick_started_at=0.0, tick_finished_at=1.0), 0.0
        )

    def test_a_wildly_long_tick_still_returns_zero(self) -> None:
        self.assertEqual(
            next_sleep_seconds(tick_started_at=0.0, tick_finished_at=45.0), 0.0
        )

    def test_a_clock_going_backwards_does_not_produce_a_long_sleep(self) -> None:
        self.assertAlmostEqual(
            next_sleep_seconds(tick_started_at=5.0, tick_finished_at=4.0), 1.0
        )

    def test_the_documented_worked_example(self) -> None:
        # Loop starts at 0.0. Tick 1 takes 0.3s -> tick 2 on schedule at 1.0.
        # Tick 2 takes 1.4s, finishing at 2.4 -> tick 3 starts immediately,
        # 0.4s later than a clock-aligned schedule would have put it, and that
        # offset carries forward rather than a tick being skipped.
        self.assertAlmostEqual(
            next_sleep_seconds(tick_started_at=0.0, tick_finished_at=0.3), 0.7
        )
        self.assertEqual(
            next_sleep_seconds(tick_started_at=1.0, tick_finished_at=2.4), 0.0
        )
        self.assertAlmostEqual(
            accumulated_drift_seconds(started_at=0.0, now=2.4, ticks_completed=2), 0.4
        )

    def test_no_drift_when_every_tick_is_on_time(self) -> None:
        self.assertEqual(
            accumulated_drift_seconds(started_at=0.0, now=5.0, ticks_completed=5), 0.0
        )

    def test_drift_is_zero_before_any_tick_completes(self) -> None:
        self.assertEqual(
            accumulated_drift_seconds(started_at=0.0, now=99.0, ticks_completed=0), 0.0
        )

    def test_running_ahead_of_schedule_is_not_reported_as_drift(self) -> None:
        self.assertEqual(
            accumulated_drift_seconds(started_at=0.0, now=1.0, ticks_completed=5), 0.0
        )


class ThresholdTests(unittest.TestCase):
    def test_protection_has_the_least_patience(self) -> None:
        # Every second it stays broken is a real position with zero protection.
        self.assertEqual(FAILURE_THRESHOLDS[STEP_PROTECTION], 3)

    def test_reconciliation_is_next(self) -> None:
        self.assertEqual(FAILURE_THRESHOLDS[STEP_RECONCILE], 5)

    def test_ingest_is_the_most_patient(self) -> None:
        # A broken ingest risks a missed trade, not open risk.
        self.assertEqual(FAILURE_THRESHOLDS[STEP_INGEST], 10)

    def test_thresholds_are_ordered_by_exposure(self) -> None:
        self.assertLess(
            FAILURE_THRESHOLDS[STEP_PROTECTION], FAILURE_THRESHOLDS[STEP_RECONCILE]
        )
        self.assertLess(
            FAILURE_THRESHOLDS[STEP_RECONCILE], FAILURE_THRESHOLDS[STEP_INGEST]
        )

    def test_only_the_two_dangerous_steps_auto_pause_entries(self) -> None:
        self.assertEqual(
            set(AUTO_PAUSE_ON_ESCALATION), {STEP_PROTECTION, STEP_RECONCILE}
        )


class FailureTrackerTests(unittest.TestCase):
    def test_a_single_failure_does_not_escalate(self) -> None:
        tracker = StepFailureTracker()
        self.assertFalse(tracker.record_failure(STEP_PROTECTION, RuntimeError("blip")))
        self.assertFalse(tracker.any_escalated)

    def test_each_step_escalates_at_its_own_threshold(self) -> None:
        for step, threshold in FAILURE_THRESHOLDS.items():
            with self.subTest(step=step):
                tracker = StepFailureTracker()
                for i in range(1, threshold):
                    self.assertFalse(tracker.record_failure(step, RuntimeError("x")))
                self.assertTrue(tracker.record_failure(step, RuntimeError("x")))

    def test_a_success_clears_the_streak(self) -> None:
        # Failures must be consecutive; an intermittent blip is not a break.
        tracker = StepFailureTracker()
        tracker.record_failure(STEP_PROTECTION, RuntimeError("x"))
        tracker.record_failure(STEP_PROTECTION, RuntimeError("x"))
        tracker.record_success(STEP_PROTECTION)
        self.assertFalse(tracker.record_failure(STEP_PROTECTION, RuntimeError("x")))
        self.assertFalse(tracker.any_escalated)

    def test_a_success_clears_an_existing_escalation(self) -> None:
        tracker = StepFailureTracker()
        for _ in range(3):
            tracker.record_failure(STEP_PROTECTION, RuntimeError("x"))
        self.assertTrue(tracker.any_escalated)
        tracker.record_success(STEP_PROTECTION)
        self.assertFalse(tracker.any_escalated)

    def test_steps_are_counted_independently(self) -> None:
        tracker = StepFailureTracker()
        for _ in range(3):
            tracker.record_failure(STEP_INGEST, RuntimeError("x"))
        # Ingest's threshold is 10, so three failures there is not escalation,
        # and it must not spill into protection's much tighter count.
        self.assertFalse(tracker.any_escalated)
        self.assertEqual(tracker.consecutive[STEP_INGEST], 3)
        self.assertNotIn(STEP_PROTECTION, tracker.consecutive)

    def test_escalating_ingest_does_not_pause_entries(self) -> None:
        tracker = StepFailureTracker()
        for _ in range(FAILURE_THRESHOLDS[STEP_INGEST]):
            tracker.record_failure(STEP_INGEST, RuntimeError("x"))
        self.assertTrue(tracker.any_escalated)
        self.assertFalse(tracker.should_auto_pause())

    def test_escalating_protection_pauses_entries(self) -> None:
        tracker = StepFailureTracker()
        for _ in range(FAILURE_THRESHOLDS[STEP_PROTECTION]):
            tracker.record_failure(STEP_PROTECTION, RuntimeError("x"))
        self.assertTrue(tracker.should_auto_pause())

    def test_escalating_reconciliation_pauses_entries(self) -> None:
        tracker = StepFailureTracker()
        for _ in range(FAILURE_THRESHOLDS[STEP_RECONCILE]):
            tracker.record_failure(STEP_RECONCILE, RuntimeError("x"))
        self.assertTrue(tracker.should_auto_pause())

    def test_the_last_error_names_the_step(self) -> None:
        tracker = StepFailureTracker()
        tracker.record_failure(STEP_SQUAREOFF, RuntimeError("exchange down"))
        self.assertIn(STEP_SQUAREOFF, str(tracker.last_error))
        self.assertIn("exchange down", str(tracker.last_error))

    def test_an_exception_with_no_message_still_reports_something(self) -> None:
        tracker = StepFailureTracker()
        tracker.record_failure(STEP_INGEST, RuntimeError())
        self.assertIn("RuntimeError", str(tracker.last_error))

    def test_an_unknown_step_gets_the_default_threshold(self) -> None:
        tracker = StepFailureTracker()
        self.assertEqual(tracker.threshold_for("mystery_step"), 10)


class StepIsolationTests(unittest.TestCase):
    def test_a_failing_step_does_not_raise(self) -> None:
        tracker = StepFailureTracker()
        ok = run_step(
            STEP_INGEST,
            lambda: (_ for _ in ()).throw(RuntimeError("bad row")),
            tracker=tracker,
        )
        self.assertFalse(ok)

    def test_the_other_steps_still_run_after_one_fails(self) -> None:
        # The property that matters: a bug in the least dangerous step must
        # never silence the most dangerous one.
        tracker = StepFailureTracker()
        ran: List[str] = []
        run_step(
            STEP_INGEST,
            lambda: (_ for _ in ()).throw(RuntimeError("bad row")),
            tracker=tracker,
        )
        run_step(STEP_RECONCILE, lambda: ran.append("reconcile"), tracker=tracker)
        run_step(STEP_PROTECTION, lambda: ran.append("protection"), tracker=tracker)
        self.assertEqual(ran, ["reconcile", "protection"])

    def test_a_successful_step_reports_success(self) -> None:
        tracker = StepFailureTracker()
        self.assertTrue(run_step(STEP_INGEST, lambda: None, tracker=tracker))

    def test_the_escalation_callback_fires_once_at_the_threshold(self) -> None:
        tracker = StepFailureTracker()
        fired: List[str] = []

        def boom():
            raise RuntimeError("still broken")

        for _ in range(FAILURE_THRESHOLDS[STEP_PROTECTION]):
            run_step(
                STEP_PROTECTION,
                boom,
                tracker=tracker,
                on_escalate=lambda step, detail: fired.append(step),
            )
        self.assertEqual(fired, [STEP_PROTECTION])

    def test_the_callback_does_not_fire_before_the_threshold(self) -> None:
        tracker = StepFailureTracker()
        fired: List[str] = []
        run_step(
            STEP_PROTECTION,
            lambda: (_ for _ in ()).throw(RuntimeError("x")),
            tracker=tracker,
            on_escalate=lambda step, detail: fired.append(step),
        )
        self.assertEqual(fired, [])

    def test_a_raising_escalation_callback_is_not_swallowed_silently(self) -> None:
        # The callback surfaces the problem; if it is itself broken we would
        # rather know than have escalation quietly stop working.
        tracker = StepFailureTracker()

        def on_escalate(step, detail):
            raise ValueError("notifier broken")

        with self.assertRaises(ValueError):
            for _ in range(FAILURE_THRESHOLDS[STEP_PROTECTION]):
                run_step(
                    STEP_PROTECTION,
                    lambda: (_ for _ in ()).throw(RuntimeError("x")),
                    tracker=tracker,
                    on_escalate=on_escalate,
                )


if __name__ == "__main__":
    unittest.main()
