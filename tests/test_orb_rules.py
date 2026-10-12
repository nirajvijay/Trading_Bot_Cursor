"""ORB rules: the live opening range, the ranking, the stop and the breakout."""
from __future__ import annotations

import unittest
from datetime import datetime, time
from zoneinfo import ZoneInfo

from orb_config import OrbConfig
from orb_rules import (
    VERDICT_MISSED,
    VERDICT_NONE,
    VERDICT_TRIGGER,
    MinuteBar,
    PreopenVolume,
    TickSnapshot,
    evaluate_price,
    first_breakout_bar,
    make_arm,
    planned_stop,
    range_from_candles,
    range_from_snapshot,
    rank_ranges,
    stop_distance,
)
from orb_types import (
    NOT_SELECTED_RANK,
    NOT_SELECTED_RVOL,
    RANGE_BAD_OHLC,
    RANGE_BAD_VOLUME,
    RANGE_INCOMPLETE_CANDLES,
    RANGE_NO_BASELINE,
    RANGE_NO_DIRECTION,
    RANGE_NO_PREOPEN,
    RANGE_NO_TICKS,
    RANGE_NO_VOLUME,
    RANGE_OFF_GRID,
    RANGE_STALE_TICKS,
    OpeningRange,
    OrbBaseline,
)

IST = ZoneInfo("Asia/Kolkata")
DAY = "2026-09-22"
RANGE_END = datetime(2026, 9, 22, 9, 20, tzinfo=IST)


def at(hh: int, mm: int, ss: int = 0) -> datetime:
    return datetime(2026, 9, 22, hh, mm, ss, tzinfo=IST)


def baseline(atr: float = 20.0, average: float = 1000.0, ready: bool = True) -> OrbBaseline:
    return OrbBaseline(1, "AAA", ready, atr14=atr, avg_or_volume=average)


def snapshot(**overrides) -> TickSnapshot:
    values = dict(at=at(9, 19, 59), sequence=10, last_price=1010.0, day_open=1000.0,
                  day_high=1012.0, day_low=998.0, volume=4500)
    values.update(overrides)
    return TickSnapshot(**values)


def live_range(**overrides) -> OpeningRange:
    args = dict(
        instrument_token=1,
        tradingsymbol="AAA",
        baseline=baseline(),
        preopen=PreopenVolume(at=at(9, 8, 5), sequence=1, volume=500),
        snapshot=snapshot(),
        range_end=RANGE_END,
        tick_size=0.05,
    )
    args.update(overrides)
    return range_from_snapshot(**args)


def ranged(symbol: str, rvol: float, token: int, *, valid: bool = True) -> OpeningRange:
    return OpeningRange(
        instrument_token=token, tradingsymbol=symbol, valid=valid,
        reason=None if valid else RANGE_NO_DIRECTION, or_open=100.0, or_high=102.0,
        or_low=99.0, or_close=101.0, or_volume=int(rvol * 1000), direction="UP",
        level=102.0, atr14=4.0, avg_or_volume=1000.0, rvol=rvol,
    )


class LiveRangeTests(unittest.TestCase):
    def test_range_is_the_exchange_day_ohlc_and_volume_net_of_the_auction(self) -> None:
        built = live_range()
        self.assertTrue(built.valid)
        self.assertEqual((built.or_open, built.or_high, built.or_low, built.or_close),
                         (1000.0, 1012.0, 998.0, 1010.0))
        self.assertEqual(built.or_volume, 4000)       # 4500 less the 500 auction shares
        self.assertEqual(built.preopen_volume, 500)
        self.assertEqual(built.rvol, 4.0)
        self.assertEqual((built.direction, built.level), ("UP", 1012.0))

    def test_a_falling_range_arms_the_low(self) -> None:
        built = live_range(snapshot=snapshot(last_price=999.0))
        self.assertEqual((built.direction, built.level), ("DOWN", 998.0))

    def test_no_direction_when_it_closes_where_it_opened(self) -> None:
        built = live_range(snapshot=snapshot(last_price=1000.0))
        self.assertFalse(built.valid)
        self.assertEqual(built.reason, RANGE_NO_DIRECTION)
        self.assertEqual(built.rvol, 4.0)  # still recorded

    def test_reasons_a_live_range_is_unusable(self) -> None:
        cases = [
            (dict(baseline=None), RANGE_NO_BASELINE),
            (dict(baseline=baseline(ready=False)), RANGE_NO_BASELINE),
            (dict(preopen=None), RANGE_NO_PREOPEN),
            (dict(snapshot=None), RANGE_NO_TICKS),
            (dict(snapshot=snapshot(at=at(9, 19, 30))), RANGE_STALE_TICKS),
            (dict(snapshot=snapshot(day_open=0.0)), RANGE_BAD_OHLC),
            (dict(snapshot=snapshot(day_high=1005.0)), RANGE_BAD_OHLC),   # LTP above the day high
            (dict(snapshot=snapshot(day_low=1001.0)), RANGE_BAD_OHLC),    # open below the day low
            (dict(snapshot=snapshot(volume=400)), RANGE_BAD_VOLUME),      # below the auction volume
            (dict(snapshot=snapshot(volume=500)), RANGE_NO_VOLUME),
            (dict(snapshot=snapshot(day_high=1012.03)), RANGE_OFF_GRID),
        ]
        for overrides, reason in cases:
            with self.subTest(reason=reason, overrides=list(overrides)):
                built = live_range(**overrides)
                self.assertFalse(built.valid)
                self.assertEqual(built.reason, reason)

    def test_a_tick_twenty_seconds_before_the_boundary_is_still_fresh(self) -> None:
        self.assertTrue(live_range(snapshot=snapshot(at=at(9, 19, 40))).valid)


class CandleRangeTests(unittest.TestCase):
    BARS = [
        MinuteBar("09:15", 100.0, 101.0, 99.5, 100.5, 300),
        MinuteBar("09:16", 100.5, 102.0, 100.0, 101.5, 200),
        MinuteBar("09:17", 101.5, 101.8, 101.0, 101.2, 100),
        MinuteBar("09:18", 101.2, 101.6, 100.8, 101.0, 100),
        MinuteBar("09:19", 101.0, 101.9, 100.9, 101.7, 300),
        MinuteBar("09:20", 101.7, 105.0, 90.0, 104.0, 999),  # after the range
    ]

    def build(self, bars=None, **overrides):
        args = dict(instrument_token=1, tradingsymbol="AAA", baseline=baseline(average=250.0),
                    bars=self.BARS if bars is None else bars)
        args.update(overrides)
        return range_from_candles(**args)

    def test_first_five_bars_only(self) -> None:
        built = self.build()
        self.assertTrue(built.valid)
        self.assertEqual((built.or_open, built.or_high, built.or_low, built.or_close),
                         (100.0, 102.0, 99.5, 101.7))
        self.assertEqual((built.or_volume, built.rvol), (1000, 4.0))
        self.assertEqual((built.direction, built.level), ("UP", 102.0))

    def test_a_missing_opening_candle_makes_the_range_unusable(self) -> None:
        self.assertEqual(self.build(bars=self.BARS[1:]).reason, RANGE_INCOMPLETE_CANDLES)

    def test_no_baseline(self) -> None:
        self.assertEqual(self.build(baseline=None).reason, RANGE_NO_BASELINE)


class RankingTests(unittest.TestCase):
    def test_top_three_by_rvol_at_or_above_the_minimum(self) -> None:
        ranges = [ranged("A", 3.5, 1), ranged("B", 9.0, 2), ranged("C", 2.99, 3),
                  ranged("D", 4.0, 4), ranged("E", 3.0, 5), ranged("F", 8.0, 6, valid=False)]
        ranked = rank_ranges(ranges)
        self.assertEqual([(e.range.tradingsymbol, e.rank) for e in ranked if e.selected],
                         [("B", 1), ("D", 2), ("A", 3)])
        by_symbol = {e.range.tradingsymbol: e for e in ranked}
        self.assertEqual((by_symbol["E"].rank, by_symbol["E"].reason), (4, NOT_SELECTED_RANK))
        self.assertEqual((by_symbol["C"].rank, by_symbol["C"].reason), (None, NOT_SELECTED_RVOL))
        self.assertEqual((by_symbol["F"].rank, by_symbol["F"].reason), (None, RANGE_NO_DIRECTION))
        self.assertEqual([e.range.tradingsymbol for e in ranked], ["B", "D", "A", "E", "C", "F"])

    def test_exactly_the_minimum_counts(self) -> None:
        self.assertTrue(rank_ranges([ranged("A", 3.0, 1)])[0].selected)

    def test_nothing_in_play_arms_nothing(self) -> None:
        self.assertFalse(any(e.selected for e in rank_ranges([ranged("A", 2.5, 1)])))

    def test_equal_rvol_is_broken_by_symbol_so_the_result_is_stable(self) -> None:
        ranges = [ranged(s, 5.0, i) for i, s in enumerate(["D", "B", "A", "C"], 1)]
        self.assertEqual([e.range.tradingsymbol for e in rank_ranges(ranges) if e.selected],
                         ["A", "B", "C"])
        self.assertEqual([e.range.tradingsymbol for e in rank_ranges(ranges[::-1]) if e.selected],
                         ["A", "B", "C"])


class ArmAndStopTests(unittest.TestCase):
    def arm(self, **range_overrides):
        entry = rank_ranges([live_range(**range_overrides)])[0]
        return make_arm(entry, session_date=DAY, tick_size=0.05)

    def test_arm_carries_the_level_in_ticks_and_the_stop_distance(self) -> None:
        arm = self.arm()
        self.assertEqual(arm.setup_id, "1|2026-09-22|ORB|orb_v1")
        self.assertEqual((arm.direction, arm.level, arm.level_ticks), ("UP", 1012.0, 20240))
        self.assertAlmostEqual(arm.stop_distance, 7.0)
        self.assertEqual(stop_distance(20.0), 0.35 * 20.0)
        self.assertEqual((arm.rank, arm.rvol), (1, 4.0))

    def test_only_a_selected_range_can_be_armed(self) -> None:
        entry = rank_ranges([live_range(snapshot=snapshot(volume=600))])[0]  # RVOL 0.1
        with self.assertRaises(ValueError):
            make_arm(entry, session_date=DAY, tick_size=0.05)

    def test_stop_is_rounded_away_from_the_trade(self) -> None:
        # 1012 - 7.03 = 1004.97: a long's stop goes down to 1004.95.
        self.assertEqual(planned_stop(direction="UP", reference_price=1012.0, distance=7.03, tick_size=0.05), 1004.95)
        # 998 + 7.03 = 1005.03: a short's stop goes up to 1005.05.
        self.assertEqual(planned_stop(direction="DOWN", reference_price=998.0, distance=7.03, tick_size=0.05), 1005.05)
        # Already on the grid: unchanged.
        self.assertEqual(planned_stop(direction="UP", reference_price=1012.0, distance=7.0, tick_size=0.05), 1005.0)
        self.assertEqual(planned_stop(direction="DOWN", reference_price=998.0, distance=7.0, tick_size=0.05), 1005.0)
        # A one-paisa tick.
        self.assertEqual(planned_stop(direction="UP", reference_price=212.37, distance=1.234, tick_size=0.01), 211.13)

    def test_no_stop_from_bad_inputs(self) -> None:
        for kwargs in (
            dict(direction="UP", reference_price=5.0, distance=7.0, tick_size=0.05),     # below zero
            dict(direction="UP", reference_price=100.0, distance=0.0, tick_size=0.05),
            dict(direction="UP", reference_price=100.0, distance=1.0, tick_size=0.0),
            dict(direction="SIDEWAYS", reference_price=100.0, distance=1.0, tick_size=0.05),
            dict(direction="UP", reference_price=float("nan"), distance=1.0, tick_size=0.05),
            dict(direction="DOWN", reference_price=100.0, distance=float("inf"), tick_size=0.05),
        ):
            with self.subTest(kwargs=kwargs):
                self.assertIsNone(planned_stop(**kwargs))


class BreakoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.long = ArmAndStopTests().arm()                                    # level 1012, ATR 20
        self.short = ArmAndStopTests().arm(snapshot=snapshot(last_price=999.0))  # level 998

    def test_a_price_exactly_at_the_level_triggers(self) -> None:
        self.assertEqual(evaluate_price(self.long, 1012.0).kind, VERDICT_TRIGGER)
        self.assertEqual(evaluate_price(self.short, 998.0).kind, VERDICT_TRIGGER)

    def test_one_tick_short_of_the_level_does_not(self) -> None:
        self.assertEqual(evaluate_price(self.long, 1011.95).kind, VERDICT_NONE)
        self.assertEqual(evaluate_price(self.short, 998.05).kind, VERDICT_NONE)

    def test_overshoot_is_measured_from_the_level(self) -> None:
        verdict = evaluate_price(self.long, 1013.0)
        self.assertEqual(verdict.kind, VERDICT_TRIGGER)
        self.assertAlmostEqual(verdict.overshoot, 1.0)
        self.assertAlmostEqual(verdict.overshoot_atr, 0.05)

    def test_beyond_a_tenth_of_an_atr_is_a_missed_breakout(self) -> None:
        # ATR 20: the limit is 2.00 rupees beyond the level.
        self.assertEqual(evaluate_price(self.long, 1014.0).kind, VERDICT_TRIGGER)
        self.assertEqual(evaluate_price(self.long, 1014.05).kind, VERDICT_MISSED)
        self.assertEqual(evaluate_price(self.short, 996.0).kind, VERDICT_TRIGGER)
        self.assertEqual(evaluate_price(self.short, 995.95).kind, VERDICT_MISSED)

    def test_the_limit_is_configurable(self) -> None:
        loose = OrbConfig(max_overshoot_atr=0.5)
        self.assertEqual(evaluate_price(self.long, 1020.0, loose).kind, VERDICT_TRIGGER)

    def test_a_price_off_the_tick_grid_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            evaluate_price(self.long, 1012.03)


class CandleBreakoutTests(unittest.TestCase):
    def bars(self, *rows):
        return [MinuteBar(hm, o, h, l, o, 0) for hm, o, h, l in rows]

    def test_first_touch_enters_at_the_level(self) -> None:
        bars = self.bars(("09:19", 100, 200, 0), ("09:20", 100, 101, 99), ("09:21", 101, 102, 100.5),
                         ("09:22", 102, 104, 101))
        bar, entry = first_breakout_bar(bars, direction="UP", level=102.0)
        self.assertEqual((bar.time, entry), ("09:21", 102.0))  # the 09:19 bar is inside the range

    def test_a_bar_that_opens_beyond_the_level_enters_at_its_open(self) -> None:
        bar, entry = first_breakout_bar(self.bars(("09:20", 103, 104, 102.5)), direction="UP", level=102.0)
        self.assertEqual((bar.time, entry), ("09:20", 103))
        bar, entry = first_breakout_bar(self.bars(("09:20", 97, 97.5, 96)), direction="DOWN", level=98.0)
        self.assertEqual(entry, 97)

    def test_the_1100_bar_counts_and_the_1101_bar_does_not(self) -> None:
        hit = first_breakout_bar(self.bars(("11:00", 100, 102, 99)), direction="UP", level=102.0)
        self.assertEqual(hit[0].time, "11:00")
        self.assertIsNone(first_breakout_bar(self.bars(("11:01", 100, 102, 99)), direction="UP", level=102.0))

    def test_cutoff_follows_the_config(self) -> None:
        early = OrbConfig(trigger_end=time(10, 0))
        self.assertIsNone(
            first_breakout_bar(self.bars(("10:00", 100, 102, 99)), direction="UP", level=102.0, config=early)
        )


if __name__ == "__main__":
    unittest.main()
