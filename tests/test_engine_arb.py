"""engine_arb: settings, funnel filters and score, budget, positions, day controls."""
from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, time

import engine_clock
from engine_arb import (
    REASON_AFTER_CUTOFF,
    REASON_BELOW_MIN_RISK,
    REASON_NO_STOP,
    REASON_NO_TRIGGER_TIME,
    REASON_NOT_FIRST,
    REASON_SCORE0_LIMIT,
    REASON_STOP_TOO_TIGHT,
    REASON_VOLUME,
    ArbSettings,
    DayLockTracker,
    DayMtm,
    FunnelFeatures,
    InvalidArbSettings,
    allowed_risk,
    apply_one_share_floor,
    classify_funnel,
    closed_net_pnl,
    count_losers,
    day_mtm,
    hard_stop_hit,
    is_loser,
    open_mark,
    position_live_risk,
    validate_settings,
)
from engine_charges import round_trip_charges
from engine_types import ExecutionState, Position, TriggerCandidate

FULL = ArbSettings()
MICRO = ArbSettings(risk_scale=0.05)


def features(**overrides) -> FunnelFeatures:
    values = dict(
        trigger_time_ist=time(11, 0),
        volume_ratio=3.0,
        stop_pct=0.5,
        is_first_trigger_today=True,
        align_market_pct=0.1,
        align_sector_pct=0.1,
    )
    values.update(overrides)
    return FunnelFeatures(**values)


def position(
    state: ExecutionState,
    *,
    symbol: str = "AAA",
    direction: str = "UP",
    qty: int = 100,
    entry=100.0,
    stop=98.0,
    realised=None,
    extra=None,
    risk_taken=None,
) -> Position:
    candidate = TriggerCandidate(
        setup_id=f"{symbol}-{state.value}",
        continuation_rule_version="v1",
        session_date="2026-09-22",
        tradingsymbol=symbol,
        instrument_token=1,
        direction=direction,
        trigger_price=100.0,
        pullback_swing_high=None,
        pullback_swing_low=None,
        tick_size=0.05,
        buffer_ticks=1,
        trigger_exchange_ts=None,
        created_at="2026-09-22T05:00:00+00:00",
    )
    return Position(
        trade_id=candidate.setup_id,
        candidate=candidate,
        state=state,
        qty=qty,
        entry_price=entry,
        stop_price=stop,
        realised_pnl=realised,
        risk_taken_rupees=risk_taken,
        extra=dict(extra or {}),
    )


class SettingsTests(unittest.TestCase):
    def test_full_size_numbers(self) -> None:
        self.assertEqual(FULL.orb_risk_rupees(), 2500)
        self.assertEqual([FULL.funnel_risk_rupees(s) for s in range(4)], [1000, 2500, 4500, 6000])
        self.assertEqual(FULL.funnel_risk_rupees(7), 6000)  # 3+ is capped at score 3
        self.assertEqual(FULL.open_risk_cap_rupees(), 6000)
        self.assertEqual(FULL.hard_day_stop_rupees(), 5000)
        self.assertEqual(FULL.closed_loss_backstop_rupees(), 6000)
        self.assertFalse(FULL.one_share_floor_active)

    def test_micro_scales_every_rupee_figure_and_nothing_else(self) -> None:
        self.assertAlmostEqual(MICRO.orb_risk_rupees(), 125)
        self.assertEqual(
            [round(MICRO.funnel_risk_rupees(s), 6) for s in range(4)], [50, 125, 225, 300]
        )
        self.assertAlmostEqual(MICRO.open_risk_cap_rupees(), 300)
        self.assertAlmostEqual(MICRO.min_risk_rupees(), 25)
        self.assertAlmostEqual(MICRO.hard_day_stop_rupees(), 250)
        self.assertAlmostEqual(MICRO.day_lock_arm_rupees(), 150)
        self.assertAlmostEqual(MICRO.closed_loss_backstop_rupees(), 300)
        self.assertEqual(MICRO.day_lock_keep, 0.40)
        self.assertEqual(MICRO.funnel_cutoff, time(13, 0))
        self.assertTrue(MICRO.one_share_floor_active)
        self.assertFalse(ArbSettings(risk_scale=0.25).one_share_floor_active)

    def test_defaults_and_every_stage_validate(self) -> None:
        for scale in (0.05, 0.25, 0.5, 1.0):
            validate_settings(ArbSettings(risk_scale=scale))

    def test_unsafe_settings_are_refused(self) -> None:
        bad = [
            dict(risk_scale=0.1),
            dict(risk_scale=2.0),
            dict(day_lock_keep=0.45),
            dict(day_lock_keep=0.30),
            dict(hard_day_stop=6000.0),
            dict(open_risk_cap=5000.0),
            dict(min_risk=1000.0),
            dict(cut_mult=0.0),
            dict(cut_mult=1.5),
            dict(funnel_risks=(1000.0, 2500.0, 4500.0)),
            dict(funnel_risks=(1000.0, 0.0, 4500.0, 6000.0)),
            dict(min_volume_ratio=float("nan")),
            dict(one_share_floor_mult=0.5),
        ]
        for overrides in bad:
            with self.subTest(**{k: str(v) for k, v in overrides.items()}):
                with self.assertRaises(InvalidArbSettings):
                    validate_settings(replace(FULL, **overrides))


class FunnelTests(unittest.TestCase):
    def verdict(self, score0_taken: int = 0, **overrides):
        return classify_funnel(features(**overrides), FULL, score0_taken_today=score0_taken)

    def test_a_plain_trigger_passes_with_score_zero(self) -> None:
        verdict = self.verdict()
        self.assertTrue(verdict.ok)
        self.assertEqual(verdict.score, 0)
        self.assertEqual(verdict.base_risk_rupees, 1000)

    def test_each_filter_names_its_reason(self) -> None:
        cases = [
            (dict(trigger_time_ist=None), REASON_NO_TRIGGER_TIME),
            (dict(trigger_time_ist=time(13, 0)), REASON_AFTER_CUTOFF),
            (dict(volume_ratio=None), REASON_VOLUME),
            (dict(volume_ratio=1.99), REASON_VOLUME),
            (dict(stop_pct=None), REASON_NO_STOP),
            (dict(stop_pct=0.2499), REASON_STOP_TOO_TIGHT),
            (dict(is_first_trigger_today=False), REASON_NOT_FIRST),
        ]
        for overrides, reason in cases:
            with self.subTest(reason=reason):
                verdict = self.verdict(**overrides)
                self.assertFalse(verdict.ok)
                self.assertEqual(verdict.reason, reason)

    def test_boundaries_are_inclusive_where_the_research_was(self) -> None:
        self.assertTrue(self.verdict(trigger_time_ist=time(12, 59, 59)).ok)
        self.assertTrue(self.verdict(volume_ratio=2.0).ok)
        self.assertTrue(self.verdict(stop_pct=0.25).ok)

    def test_score_parts(self) -> None:
        # Market against the trade and sector with it: 2 points.
        v = self.verdict(align_market_pct=-0.21, align_sector_pct=0.01)
        self.assertEqual((v.score, v.base_risk_rupees), (2, 4500))
        # Sector with the trade counts only when the market is against it.
        self.assertEqual(self.verdict(align_market_pct=0.5, align_sector_pct=0.9).score, 0)
        # Exactly -0.2 is not "below -0.2".
        self.assertEqual(self.verdict(align_market_pct=-0.2, align_sector_pct=0.9).score, 0)
        self.assertEqual(self.verdict(align_market_pct=None, align_sector_pct=0.9).score, 0)
        self.assertEqual(self.verdict(align_market_pct=-0.5, align_sector_pct=None).score, 1)
        self.assertEqual(self.verdict(volume_ratio=6.0).score, 1)
        self.assertEqual(self.verdict(trigger_time_ist=time(10, 29, 59)).score, 1)
        self.assertEqual(self.verdict(trigger_time_ist=time(10, 30)).score, 0)

    def test_score_is_capped_at_three(self) -> None:
        v = self.verdict(
            align_market_pct=-1.0,
            align_sector_pct=1.0,
            volume_ratio=9.0,
            trigger_time_ist=time(9, 40),
        )
        self.assertEqual((v.score, v.base_risk_rupees), (3, 6000))
        self.assertEqual(sum(v.score_parts.values()), 4)

    def test_only_two_score_zero_trades_a_day(self) -> None:
        self.assertTrue(self.verdict(score0_taken=1).ok)
        verdict = self.verdict(score0_taken=2)
        self.assertEqual(verdict.reason, REASON_SCORE0_LIMIT)
        # A higher score is never limited.
        self.assertTrue(self.verdict(score0_taken=2, volume_ratio=7.0).ok)


class BudgetTests(unittest.TestCase):
    def test_a_fresh_day_takes_the_base_risk(self) -> None:
        d = allowed_risk(FULL, base_rupees=4500, losers_today=0, day_mtm_rupees=0, live_risk_rupees=0)
        self.assertEqual((d.risk_rupees, d.cut, d.room_rupees), (4500, False, 6000))

    def test_half_size_after_the_first_loser(self) -> None:
        d = allowed_risk(FULL, base_rupees=4500, losers_today=1, day_mtm_rupees=-2000, live_risk_rupees=0)
        self.assertEqual((d.risk_rupees, d.cut), (2250, True))

    def test_open_risk_is_capped_and_a_red_day_does_not_shrink_the_cap(self) -> None:
        d = allowed_risk(FULL, base_rupees=6000, losers_today=0, day_mtm_rupees=-3000, live_risk_rupees=4000)
        self.assertEqual(d.risk_rupees, 2000)

    def test_house_money_widens_the_cap(self) -> None:
        d = allowed_risk(FULL, base_rupees=6000, losers_today=0, day_mtm_rupees=3000, live_risk_rupees=4000)
        self.assertEqual(d.risk_rupees, 5000)

    def test_below_min_risk_is_skipped(self) -> None:
        d = allowed_risk(FULL, base_rupees=6000, losers_today=0, day_mtm_rupees=0, live_risk_rupees=5501)
        self.assertEqual((d.risk_rupees, d.reason), (0.0, REASON_BELOW_MIN_RISK))
        d = allowed_risk(FULL, base_rupees=6000, losers_today=0, day_mtm_rupees=0, live_risk_rupees=5500)
        self.assertEqual(d.risk_rupees, 500)

    def test_micro_budget_is_scaled(self) -> None:
        d = allowed_risk(
            MICRO, base_rupees=MICRO.funnel_risk_rupees(3), losers_today=1,
            day_mtm_rupees=0, live_risk_rupees=0,
        )
        self.assertAlmostEqual(d.risk_rupees, 150)


class OneShareFloorTests(unittest.TestCase):
    def test_micro_buys_one_share_when_it_is_close_enough(self) -> None:
        self.assertEqual(apply_one_share_floor(MICRO, risk_rupees=50, risk_per_share=60), (60, True))
        self.assertEqual(apply_one_share_floor(MICRO, risk_rupees=50, risk_per_share=62.5), (62.5, True))

    def test_micro_skips_a_share_that_risks_too_much(self) -> None:
        self.assertEqual(apply_one_share_floor(MICRO, risk_rupees=50, risk_per_share=62.51), (50, False))

    def test_no_floor_when_a_share_already_fits(self) -> None:
        self.assertEqual(apply_one_share_floor(MICRO, risk_rupees=50, risk_per_share=40), (50, False))

    def test_no_floor_above_the_micro_stage(self) -> None:
        stage = ArbSettings(risk_scale=0.25)
        self.assertEqual(apply_one_share_floor(stage, risk_rupees=250, risk_per_share=260), (250, False))


class PositionTests(unittest.TestCase):
    def test_pending_entry_counts_its_cap(self) -> None:
        p = position(ExecutionState.ENTRY_SUBMITTED, entry=None, extra={"risk_cap_rupees": 2500})
        self.assertEqual(position_live_risk(p), 2500)

    def test_holding_counts_distance_to_the_current_stop(self) -> None:
        self.assertEqual(position_live_risk(position(ExecutionState.PROTECTED)), 200)
        short = position(ExecutionState.TRAILING, direction="DOWN", entry=100.0, stop=101.5)
        self.assertEqual(position_live_risk(short), 150)

    def test_a_stop_at_or_past_entry_frees_the_risk(self) -> None:
        self.assertEqual(position_live_risk(position(ExecutionState.TRAILING, stop=100.0)), 0)
        self.assertEqual(position_live_risk(position(ExecutionState.TRAILING, stop=100.5)), 0)
        short = position(ExecutionState.TRAILING, direction="DOWN", stop=99.5)
        self.assertEqual(position_live_risk(short), 0)

    def test_no_stop_falls_back_to_the_risk_taken(self) -> None:
        p = position(ExecutionState.ENTERED, stop=None, risk_taken=180.0)
        self.assertEqual(position_live_risk(p), 180)

    def test_closed_and_rejected_hold_no_risk(self) -> None:
        self.assertEqual(position_live_risk(position(ExecutionState.CLOSED)), 0)
        self.assertEqual(position_live_risk(position(ExecutionState.REJECTED)), 0)

    def test_net_pnl_takes_charges_off_the_gross(self) -> None:
        p = position(ExecutionState.CLOSED, realised=150.0)  # exit at 101.5
        expected = 150.0 - round_trip_charges(direction="UP", qty=100, entry=100.0, exit_price=101.5)
        self.assertAlmostEqual(closed_net_pnl(p), expected)

    def test_a_small_gross_win_that_charges_eat_is_a_loser(self) -> None:
        self.assertTrue(is_loser(position(ExecutionState.CLOSED, realised=1.0)))
        self.assertFalse(is_loser(position(ExecutionState.CLOSED, realised=500.0)))

    def test_a_close_without_a_figure_counts_as_a_loser(self) -> None:
        self.assertTrue(is_loser(position(ExecutionState.CLOSED, realised=None)))
        self.assertFalse(is_loser(position(ExecutionState.PROTECTED, realised=None)))

    def test_count_losers(self) -> None:
        closed = [
            position(ExecutionState.CLOSED, realised=-100.0),
            position(ExecutionState.CLOSED, realised=900.0),
            position(ExecutionState.CLOSED, realised=-5.0),
        ]
        self.assertEqual(count_losers(closed), 2)


class DayMtmTests(unittest.TestCase):
    def test_closed_plus_open_marked_at_the_fresh_ltp(self) -> None:
        closed = [position(ExecutionState.CLOSED, symbol="C", realised=500.0)]
        held = position(ExecutionState.PROTECTED, symbol="H")
        day = day_mtm(closed, [held], {"H": 101.0})
        self.assertTrue(day.complete)
        self.assertAlmostEqual(day.mtm, round(closed_net_pnl(closed[0]) + open_mark(held, 101.0), 2))

    def test_a_stale_position_counts_at_its_stop(self) -> None:
        held = position(ExecutionState.PROTECTED, symbol="H")  # stop 98, 100 shares
        day = day_mtm([], [held], {})
        self.assertFalse(day.complete)
        self.assertEqual(day.stale_symbols, ("H",))
        self.assertAlmostEqual(day.mtm, round(open_mark(held, 98.0), 2))
        self.assertLess(day.mtm, -200)

    def test_pending_entries_have_no_mark(self) -> None:
        pending = position(ExecutionState.ENTRY_SUBMITTED, entry=None)
        self.assertEqual(day_mtm([], [pending], {}).mtm, 0.0)
        self.assertTrue(day_mtm([], [pending], {}).complete)

    def test_unpriced_closes_are_counted_not_guessed(self) -> None:
        day = day_mtm([position(ExecutionState.CLOSED, realised=None)], [], {})
        self.assertEqual((day.mtm, day.unpriced_closes), (0.0, 1))


def minute(hh: int, mm: int) -> datetime:
    return datetime(2026, 9, 22, hh, mm, tzinfo=engine_clock.IST)


def mtm(value: float, complete: bool = True) -> DayMtm:
    return DayMtm(mtm=value, closed_net=0.0, open_net=value, stale_symbols=() if complete else ("X",))


class DayControlTests(unittest.TestCase):
    def test_hard_stop_is_inclusive(self) -> None:
        self.assertTrue(hard_stop_hit(FULL, -5000.0))
        self.assertFalse(hard_stop_hit(FULL, -4999.99))
        self.assertTrue(hard_stop_hit(MICRO, -250.0))

    def test_lock_arms_at_3000_and_fires_at_40_percent_of_the_peak(self) -> None:
        lock = DayLockTracker(FULL)
        self.assertFalse(lock.observe(minute(10, 0), mtm(2999)))
        self.assertFalse(lock.armed)
        self.assertFalse(lock.observe(minute(10, 1), mtm(5000)))
        self.assertTrue(lock.armed)
        self.assertEqual(lock.lock_line, 2000)
        self.assertFalse(lock.observe(minute(10, 2), mtm(2000.01)))
        self.assertTrue(lock.observe(minute(10, 3), mtm(2000)))

    def test_one_sample_per_minute(self) -> None:
        lock = DayLockTracker(FULL)
        lock.observe(minute(10, 0), mtm(5000))
        # A later tick in the same minute is ignored, however low.
        self.assertFalse(lock.observe(minute(10, 0), mtm(100)))
        self.assertEqual(lock.peak, 5000)
        self.assertTrue(lock.observe(minute(10, 1), mtm(100)))

    def test_a_stale_minute_is_retried_on_the_next_tick(self) -> None:
        lock = DayLockTracker(FULL)
        lock.observe(minute(10, 0), mtm(5000))
        self.assertFalse(lock.observe(minute(10, 1), mtm(100, complete=False)))
        self.assertEqual(lock.sampled_minute, minute(10, 0))
        self.assertTrue(lock.observe(minute(10, 1), mtm(100)))

    def test_a_restored_peak_keeps_the_lock_armed(self) -> None:
        lock = DayLockTracker(FULL, peak=4000.0)
        self.assertTrue(lock.observe(minute(11, 0), mtm(1500)))

    def test_micro_lock_is_scaled(self) -> None:
        lock = DayLockTracker(MICRO)
        self.assertFalse(lock.observe(minute(10, 0), mtm(149)))
        self.assertFalse(lock.observe(minute(10, 1), mtm(200)))
        self.assertTrue(lock.observe(minute(10, 2), mtm(80)))


if __name__ == "__main__":
    unittest.main()
