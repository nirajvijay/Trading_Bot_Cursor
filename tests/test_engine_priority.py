"""Trigger ranking: tier first, then breakout strength, then FIFO."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from engine_priority import (
    VWAP_ENTRY_CLASSES,
    rank_candidates,
    volume_ratio,
)
from engine_types import TriggerCandidate

BASE = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)


def candidate(
    setup_id: str,
    *,
    classification: str | None = "ACCEPT",
    volume: int | None = 5_000,
    average: float | None = 1_000.0,
    age_seconds: float = 0.0,
) -> TriggerCandidate:
    return TriggerCandidate(
        setup_id=setup_id,
        continuation_rule_version="v1",
        session_date="2026-09-22",
        tradingsymbol=setup_id.upper(),
        instrument_token=1,
        direction="UP",
        trigger_price=110.0,
        pullback_swing_high=109.0,
        pullback_swing_low=107.0,
        tick_size=0.05,
        buffer_ticks=1,
        trigger_exchange_ts=BASE.isoformat(),
        created_at=(BASE + timedelta(seconds=age_seconds)).isoformat(),
        vwap_classification=classification,
        breakout_candle_volume=volume,
        avg_prior_3_1m_volume=average,
    )


def order(candidates) -> list[str]:
    return [c.setup_id for c in rank_candidates(candidates)]


class VolumeRatioTests(unittest.TestCase):
    def test_ratio_is_breakout_over_prior_average(self) -> None:
        self.assertEqual(volume_ratio(candidate("a", volume=5_000, average=1_000.0)), 5.0)

    def test_missing_either_number_yields_none(self) -> None:
        self.assertIsNone(volume_ratio(candidate("a", volume=None)))
        self.assertIsNone(volume_ratio(candidate("a", average=None)))

    def test_non_positive_average_yields_none_instead_of_dividing(self) -> None:
        self.assertIsNone(volume_ratio(candidate("a", average=0.0)))
        self.assertIsNone(volume_ratio(candidate("a", average=-5.0)))


class TierPrecedenceTests(unittest.TestCase):
    def test_accept_beats_limited_regardless_of_volume(self) -> None:
        # The whole point of rule 1: a weak ACCEPT still outranks a monster
        # LIMITED in the same window.
        weak_accept = candidate("accept", classification="ACCEPT", volume=1_100, average=1_000.0)
        strong_limited = candidate(
            "limited", classification="LIMITED", volume=50_000, average=1_000.0
        )
        self.assertEqual(order([strong_limited, weak_accept]), ["accept", "limited"])

    def test_accept_beats_limited_even_when_limited_arrived_first(self) -> None:
        early_limited = candidate("limited", classification="LIMITED", age_seconds=0.0)
        later_accept = candidate("accept", classification="ACCEPT", age_seconds=0.5)
        self.assertEqual(order([early_limited, later_accept]), ["accept", "limited"])

    def test_unknown_classifications_rank_after_both_tiers(self) -> None:
        result = order(
            [
                candidate("reject", classification="REJECT"),
                candidate("none", classification=None),
                candidate("limited", classification="LIMITED"),
                candidate("accept", classification="ACCEPT"),
            ]
        )
        self.assertEqual(result[:2], ["accept", "limited"])
        self.assertEqual(set(result[2:]), {"reject", "none"})

    def test_entry_classes_and_tier_ranks_cover_the_same_classifications(self) -> None:
        from engine_priority import TIER_RANK

        self.assertEqual(set(VWAP_ENTRY_CLASSES), set(TIER_RANK))


class BreakoutStrengthTests(unittest.TestCase):
    def test_within_a_tier_stronger_breakout_first(self) -> None:
        result = order(
            [
                candidate("weak", volume=2_000, average=1_000.0),
                candidate("strong", volume=9_000, average=1_000.0),
                candidate("medium", volume=5_000, average=1_000.0),
            ]
        )
        self.assertEqual(result, ["strong", "medium", "weak"])

    def test_ratio_not_raw_volume_decides(self) -> None:
        # Bigger absolute volume, but a much weaker move relative to its own
        # recent average, must lose.
        big_but_ordinary = candidate("big", volume=100_000, average=90_000.0)  # 1.1x
        small_but_explosive = candidate("small", volume=9_000, average=1_000.0)  # 9x
        self.assertEqual(order([big_but_ordinary, small_but_explosive]), ["small", "big"])

    def test_missing_volume_sorts_last_within_its_own_tier(self) -> None:
        result = order(
            [
                candidate("unknown", volume=None, average=None),
                candidate("weak", volume=1_100, average=1_000.0),
            ]
        )
        self.assertEqual(result, ["weak", "unknown"])

    def test_missing_volume_still_beats_a_lower_tier(self) -> None:
        # An ACCEPT with no volume data must not fall behind a LIMITED.
        result = order(
            [
                candidate("limited", classification="LIMITED", volume=50_000, average=1_000.0),
                candidate("accept_no_vol", classification="ACCEPT", volume=None, average=None),
            ]
        )
        self.assertEqual(result, ["accept_no_vol", "limited"])

    def test_zero_average_is_treated_as_unusable_not_as_infinite_strength(self) -> None:
        result = order(
            [
                candidate("divzero", volume=5_000, average=0.0),
                candidate("normal", volume=1_100, average=1_000.0),
            ]
        )
        self.assertEqual(result, ["normal", "divzero"])


class FifoTiebreakTests(unittest.TestCase):
    def test_equal_tier_and_strength_falls_back_to_earliest_first(self) -> None:
        result = order(
            [
                candidate("later", age_seconds=2.0),
                candidate("earliest", age_seconds=0.0),
                candidate("middle", age_seconds=1.0),
            ]
        )
        self.assertEqual(result, ["earliest", "middle", "later"])

    def test_unparseable_timestamp_sorts_last_without_raising(self) -> None:
        broken = candidate("broken")
        broken = TriggerCandidate(**{**broken.__dict__, "created_at": "not-a-date"})
        result = order([broken, candidate("fine", age_seconds=5.0)])
        self.assertEqual(result, ["fine", "broken"])

    def test_fully_tied_candidates_keep_their_input_order(self) -> None:
        a = candidate("a")
        b = candidate("b")
        self.assertEqual(order([a, b]), ["a", "b"])
        self.assertEqual(order([b, a]), ["b", "a"])


class PurityTests(unittest.TestCase):
    def test_ranking_does_not_mutate_the_input_sequence(self) -> None:
        given = [candidate("b", volume=1_000), candidate("a", volume=9_000)]
        rank_candidates(given)
        self.assertEqual([c.setup_id for c in given], ["b", "a"])

    def test_empty_and_single_inputs(self) -> None:
        self.assertEqual(rank_candidates([]), [])
        self.assertEqual(order([candidate("only")]), ["only"])

    def test_realistic_same_window_collision(self) -> None:
        # Two ACCEPTs and two LIMITEDs land inside the same second.
        result = order(
            [
                candidate("limited_strong", classification="LIMITED", volume=8_000, average=1_000.0),
                candidate("accept_weak", classification="ACCEPT", volume=1_200, average=1_000.0),
                candidate("limited_weak", classification="LIMITED", volume=1_100, average=1_000.0),
                candidate("accept_strong", classification="ACCEPT", volume=6_000, average=1_000.0),
            ]
        )
        self.assertEqual(
            result,
            ["accept_strong", "accept_weak", "limited_strong", "limited_weak"],
        )


if __name__ == "__main__":
    unittest.main()
