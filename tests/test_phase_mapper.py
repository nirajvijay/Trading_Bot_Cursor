"""Tests for UI phase mapping."""

import unittest

from api.lib.phase_mapper import ALLOWED_PHASES, map_to_ui_phase
from api.queries.radar import _pullback_label


class PhaseMapperTests(unittest.TestCase):
    def test_idle(self) -> None:
        self.assertEqual(
            map_to_ui_phase(has_spike=False, setup_state=None, continuation_decision=None),
            "IDLE",
        )

    def test_spike_detected(self) -> None:
        self.assertEqual(
            map_to_ui_phase(has_spike=True, setup_state=None, continuation_decision=None),
            "SPIKE_DETECTED",
        )
        self.assertEqual(
            map_to_ui_phase(
                has_spike=True,
                setup_state="IMPULSE_MONITORING",
                continuation_decision=None,
            ),
            "SPIKE_DETECTED",
        )

    def test_pullback_active(self) -> None:
        self.assertEqual(
            map_to_ui_phase(
                has_spike=True,
                setup_state="PULLBACK_MONITORING",
                continuation_decision=None,
            ),
            "PULLBACK_ACTIVE",
        )

    def test_pullback_ready(self) -> None:
        self.assertEqual(
            map_to_ui_phase(
                has_spike=True,
                setup_state="PULLBACK_READY",
                continuation_decision=None,
            ),
            "PULLBACK_READY",
        )

    def test_continuation_armed(self) -> None:
        self.assertEqual(
            map_to_ui_phase(
                has_spike=True,
                setup_state="CONTINUATION_MONITORING",
                continuation_decision=None,
            ),
            "CONTINUATION_ARMED",
        )

    def test_triggered(self) -> None:
        self.assertEqual(
            map_to_ui_phase(
                has_spike=True,
                setup_state="CONTINUATION_TRIGGERED",
                continuation_decision="TRIGGERED",
            ),
            "TRIGGERED",
        )

    def test_rejected(self) -> None:
        self.assertEqual(
            map_to_ui_phase(
                has_spike=True,
                setup_state="CONTINUATION_REJECTED",
                continuation_decision="REJECTED",
            ),
            "REJECTED",
        )

    def test_disarmed_internal_states(self) -> None:
        for state in ("EXPIRED", "INVALIDATED", "SESSION_CLOSED", "CANCELLED"):
            self.assertEqual(
                map_to_ui_phase(has_spike=True, setup_state=state, continuation_decision=None),
                "DISARMED",
            )

    def test_allowed_phase_count(self) -> None:
        self.assertEqual(len(ALLOWED_PHASES), 8)


class PullbackLabelTests(unittest.TestCase):
    def test_early_spike_stages_distinct_from_pullback(self) -> None:
        self.assertEqual(_pullback_label("SPIKE_ACCEPTED"), "Setup")
        self.assertEqual(_pullback_label("IMPULSE_MONITORING"), "Impulse")
        self.assertEqual(_pullback_label("PULLBACK_MONITORING"), "Watching")

    def test_ready_and_empty(self) -> None:
        self.assertEqual(_pullback_label("PULLBACK_READY"), "Ready")
        self.assertEqual(_pullback_label(None), "-")


if __name__ == "__main__":
    unittest.main()
