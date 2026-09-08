"""Risk, stop, capital, and P&L math for the trading engine."""

from __future__ import annotations

import unittest

from trading_engine_risk import (
    capital_snapshot,
    remaining_downside_risk,
    risk_snapshot,
    size_new_trade,
    structural_stop_price,
    trade_remaining_risk,
)
from trading_engine_types import (
    DAILY_LOSS_CAP,
    DEFAULT_TOTAL_CAPITAL,
    TradeRecord,
    TriggerCandidate,
)


def _candidate(**kwargs) -> TriggerCandidate:
    base = dict(
        setup_id="s1",
        continuation_rule_version="v1",
        session_date="2026-08-17",
        tradingsymbol="AAA",
        instrument_token=1,
        direction="UP",
        trigger_price=110.0,
        pullback_swing_high=109.0,
        pullback_swing_low=100.0,
        tick_size=1.0,
        buffer_ticks=1,
        trigger_exchange_ts="2026-08-17T10:01:16",
        created_at="2026-08-17T04:31:16+00:00",
    )
    base.update(kwargs)
    return TriggerCandidate(**base)


def _trade(**kwargs) -> TradeRecord:
    base = dict(
        trade_id="t1",
        setup_id="s1",
        continuation_rule_version="v1",
        broker_tag="te1",
        session_date="2026-08-17",
        symbol="AAA",
        instrument_token=1,
        direction="UP",
        qty=10,
        entry_estimate=110.0,
        entry_fill=110.0,
        initial_stop=99.0,
        current_stop=99.0,
        exit_fill=None,
        tick_size=1.0,
        notional=1100.0,
        margin_blocked=220.0,
        status="protected_open",
        skip_reason=None,
        reject_reason=None,
        close_reason=None,
        entry_order_id="e1",
        sl_order_id="s1",
        realised_pnl=0.0,
        open_pnl=0.0,
        closed_loss_contribution=0.0,
        trigger_time="10:01:16",
        entry_time="10:01:20",
        close_time=None,
        created_at="t",
        updated_at="t",
    )
    base.update(kwargs)
    return TradeRecord(**base)


class StructuralStopTests(unittest.TestCase):
    def test_long_stop_below_swing_low(self) -> None:
        stop = structural_stop_price(
            direction="UP",
            swing_high=109.0,
            swing_low=100.0,
            tick_size=1.0,
            buffer_ticks=1,
        )
        self.assertEqual(stop, 99.0)

    def test_short_stop_above_swing_high(self) -> None:
        stop = structural_stop_price(
            direction="DOWN",
            swing_high=200.0,
            swing_low=190.0,
            tick_size=1.0,
            buffer_ticks=1,
        )
        self.assertEqual(stop, 201.0)

    def test_missing_swing_returns_none(self) -> None:
        self.assertIsNone(
            structural_stop_price(
                direction="UP",
                swing_high=109.0,
                swing_low=None,
                tick_size=1.0,
                buffer_ticks=1,
            )
        )


class RemainingRiskTests(unittest.TestCase):
    def test_long_structural_risk(self) -> None:
        self.assertEqual(
            remaining_downside_risk(
                direction="UP", qty=10, entry=110.0, current_stop=99.0
            ),
            110.0,
        )

    def test_long_profit_lock_is_zero(self) -> None:
        self.assertEqual(
            remaining_downside_risk(
                direction="UP", qty=10, entry=110.0, current_stop=111.0
            ),
            0.0,
        )

    def test_short_profit_lock_is_zero(self) -> None:
        self.assertEqual(
            remaining_downside_risk(
                direction="DOWN", qty=5, entry=200.0, current_stop=199.0
            ),
            0.0,
        )

    def test_abs_entry_minus_stop_would_be_wrong(self) -> None:
        locked = remaining_downside_risk(
            direction="UP", qty=10, entry=110.0, current_stop=115.0
        )
        naive = 10 * abs(110.0 - 115.0)
        self.assertEqual(locked, 0.0)
        self.assertEqual(naive, 50.0)


class SizeAndCapsTests(unittest.TestCase):
    def test_qty_capped_at_900(self) -> None:
        # Early unit tests ignore default cost bps so qty math stays integer-clean.
        decision = size_new_trade(
            _candidate(), [], total_capital=DEFAULT_TOTAL_CAPITAL, cost_bps=0.0
        )
        self.assertTrue(decision.allow)
        self.assertEqual(decision.initial_stop, 99.0)
        self.assertEqual(decision.risk_per_share, 11.0)
        self.assertEqual(decision.qty, 81)  # floor(900/11)
        self.assertLessEqual(decision.proposed_risk, 900.0 + 1e-9)

    def test_limited_cap_uses_450_not_halved_accept_qty(self) -> None:
        from trading_engine_types import LIMITED_PER_TRADE_RISK_CAP

        accept = size_new_trade(_candidate(), [], total_capital=DEFAULT_TOTAL_CAPITAL)
        limited = size_new_trade(
            _candidate(),
            [],
            total_capital=DEFAULT_TOTAL_CAPITAL,
            per_trade_risk_cap=LIMITED_PER_TRADE_RISK_CAP,
        )
        self.assertTrue(limited.allow)
        self.assertEqual(limited.qty, 40)  # floor(450/11)
        self.assertLessEqual(limited.proposed_risk, 450.0 + 1e-9)
        self.assertGreater(accept.qty, limited.qty)

    def test_missing_stop_skipped(self) -> None:
        decision = size_new_trade(
            _candidate(pullback_swing_low=None),
            [],
            total_capital=DEFAULT_TOTAL_CAPITAL,
        )
        self.assertFalse(decision.allow)
        self.assertEqual(decision.reason, "missing_stop")
        self.assertEqual(decision.kind, "skipped")

    def test_unprotected_blocks_new_trades(self) -> None:
        open_unprotected = _trade(status="stop_pending")
        decision = size_new_trade(
            _candidate(setup_id="s2"),
            [open_unprotected],
            total_capital=DEFAULT_TOTAL_CAPITAL,
        )
        self.assertFalse(decision.allow)
        self.assertEqual(decision.reason, "unprotected_lockout")
        self.assertEqual(decision.kind, "rejected")

    def test_entry_submitting_consumes_risk_and_blocks(self) -> None:
        pending = _trade(
            status="entry_submitting",
            qty=81,
            intended_qty=81,
            remaining_entry_qty=81,
            entry_fill=None,
            current_stop=99.0,
        )
        snap = risk_snapshot([pending])
        self.assertGreater(snap.committed_risk, 0)
        self.assertEqual(snap.in_flight_count, 1)
        # Pending entry reserves a concurrency slot (distinct symbol).
        decision = size_new_trade(
            _candidate(setup_id="s2", tradingsymbol="BBB"),
            [pending],
            total_capital=DEFAULT_TOTAL_CAPITAL,
            max_concurrent_positions=1,
        )
        self.assertFalse(decision.allow)
        self.assertEqual(decision.reason, "concurrency_limit")

    def test_profit_does_not_increase_daily_cap(self) -> None:
        winner = _trade(
            status="closed",
            realised_pnl=500.0,
            closed_loss_contribution=0.0,
        )
        snap = risk_snapshot([winner])
        self.assertEqual(snap.closed_loss_today, 0.0)
        self.assertEqual(snap.remaining_daily, DAILY_LOSS_CAP)

    def test_closed_loss_reduces_remaining(self) -> None:
        loser = _trade(
            status="closed",
            realised_pnl=-400.0,
            closed_loss_contribution=400.0,
            qty=0,
            margin_blocked=0,
        )
        snap = risk_snapshot([loser])
        self.assertEqual(snap.closed_loss_today, 400.0)
        self.assertEqual(snap.remaining_daily, 2600.0)

    def test_insufficient_capital_skips(self) -> None:
        decision = size_new_trade(_candidate(), [], total_capital=1.0)
        self.assertFalse(decision.allow)
        self.assertEqual(decision.reason, "insufficient_capital")

    def test_margin_uses_demo_5x(self) -> None:
        decision = size_new_trade(_candidate(), [], total_capital=DEFAULT_TOTAL_CAPITAL)
        self.assertAlmostEqual(decision.margin_blocked, decision.notional / 5.0)

    def test_capital_snapshot_excludes_closed(self) -> None:
        open_t = _trade(status="protected_open", margin_blocked=1000)
        closed = _trade(trade_id="t2", status="closed", margin_blocked=0)
        cap = capital_snapshot([open_t, closed], total_capital=300000)
        self.assertEqual(cap.margin_used, 1000)
        self.assertEqual(cap.remaining_capital, 299000)

    def test_pending_states_count_in_remaining_risk(self) -> None:
        submitting = _trade(status="entry_submitting", trade_id="a", qty=10)
        filled = _trade(status="entry_filled", trade_id="b", qty=10, setup_id="s2")
        pending = _trade(status="stop_pending", trade_id="c", qty=10, setup_id="s3")
        protected = _trade(status="protected_open", trade_id="d", qty=10, setup_id="s4")
        snap = risk_snapshot([submitting, filled, pending, protected])
        expected = sum(
            trade_remaining_risk(t) for t in (submitting, filled, pending, protected)
        )
        self.assertEqual(snap.committed_risk, expected)
        self.assertEqual(snap.unprotected_count, 2)

    def test_costs_increase_reserved_and_reduce_qty(self) -> None:
        from trading_engine_risk import charge_per_share, slippage_per_share

        zero = size_new_trade(
            _candidate(),
            [],
            total_capital=DEFAULT_TOTAL_CAPITAL,
            charge_bps=0.0,
            slippage_bps=0.0,
        )
        with_cost = size_new_trade(
            _candidate(),
            [],
            total_capital=DEFAULT_TOTAL_CAPITAL,
            charge_bps=30.0,
            slippage_bps=20.0,
        )
        self.assertTrue(zero.allow and with_cost.allow)
        self.assertGreater(zero.qty, with_cost.qty)
        cps = charge_per_share(110.0, charge_bps=30.0) + slippage_per_share(
            110.0, slippage_bps=20.0
        )
        self.assertAlmostEqual(
            with_cost.proposed_risk, with_cost.qty * (11.0 + cps), places=6
        )

    def test_break_even_stop_still_reserves_charges_and_slippage(self) -> None:
        from trading_engine_risk import charge_per_share, slippage_per_share

        be = _trade(
            status="protected_open",
            qty=10,
            filled_qty=10,
            remaining_position_qty=10,
            protected_qty=10,
            entry_fill=110.0,
            current_stop=110.0,
            initial_stop=99.0,
            charge_bps=30.0,
            slippage_bps=20.0,
        )
        cps = charge_per_share(110.0, charge_bps=30.0) + slippage_per_share(
            110.0, slippage_bps=20.0
        )
        reserved = trade_remaining_risk(be)
        self.assertAlmostEqual(reserved, 10 * cps, places=6)
        self.assertGreater(reserved, 0.0)

    def test_losing_exit_with_fees_not_erased(self) -> None:
        """Confirmed adverse exit: price loss counts; fees stay reserved until fold."""
        from trading_engine_risk import (
            charge_per_share,
            exited_cost_reservation,
            fold_exit_costs_into_loss,
        )

        charge = 50.0
        cps = charge_per_share(110.0, charge_bps=charge)
        adverse = _trade(
            status="partial_exit",
            qty=10,
            filled_qty=10,
            exited_qty=4,
            remaining_position_qty=6,
            protected_qty=6,
            entry_fill=110.0,
            entry_value=1100.0,
            exit_value=396.0,  # 4 × 99
            exit_value_est=0.0,
            pnl_provisional=0,
            closed_loss_contribution=44.0,
            current_stop=99.0,
            charge_bps=charge,
            slippage_bps=0.0,
        )
        self.assertAlmostEqual(exited_cost_reservation(adverse), 4 * cps, places=6)
        folded = fold_exit_costs_into_loss(
            price_pnl=-44.0, qty=4, entry=110.0, charge_bps=charge
        )
        self.assertAlmostEqual(folded, 44.0 + 4 * cps, places=6)

    def test_gross_be_exit_still_reserves_fees(self) -> None:
        """Gross break-even exit: zero price loss, fees still reserved then folded."""
        from trading_engine_risk import (
            charge_per_share,
            exited_cost_reservation,
            fold_exit_costs_into_loss,
        )

        charge = 50.0
        cps = charge_per_share(110.0, charge_bps=charge)
        be_exit = _trade(
            status="partial_exit",
            qty=10,
            filled_qty=10,
            exited_qty=4,
            remaining_position_qty=6,
            protected_qty=6,
            entry_fill=110.0,
            entry_value=1100.0,
            exit_value=440.0,  # 4 × 110 gross BE
            exit_value_est=0.0,
            pnl_provisional=0,
            closed_loss_contribution=0.0,
            current_stop=99.0,
            charge_bps=charge,
            slippage_bps=20.0,
        )
        # Confirmed BE: reserve charges only (slippage already in fill prices → not re-added).
        self.assertAlmostEqual(exited_cost_reservation(be_exit), 4 * cps, places=6)
        open_cps = charge_per_share(110.0, charge_bps=charge) + (
            110.0 * 20.0 / 10_000.0
        )
        expected = 6 * 11.0 + 6 * open_cps + 4 * cps
        self.assertAlmostEqual(trade_remaining_risk(be_exit), expected, places=6)
        self.assertAlmostEqual(
            fold_exit_costs_into_loss(price_pnl=0.0, qty=4, entry=110.0, charge_bps=charge),
            4 * cps,
            places=6,
        )

    def test_confirmed_slippage_not_double_counted(self) -> None:
        """Estimated slippage reserved only while unpriced; confirmed fills embed it."""
        from trading_engine_risk import exited_cost_reservation, slippage_per_share

        slip = 50.0
        sps = slippage_per_share(110.0, slippage_bps=slip)
        unpriced = _trade(
            status="partial_exit",
            qty=10,
            filled_qty=10,
            exited_qty=4,
            remaining_position_qty=6,
            protected_qty=6,
            entry_fill=110.0,
            entry_value=1100.0,
            exit_value=0.0,
            exit_value_est=436.0,  # estimate only
            pnl_provisional=1,
            closed_loss_contribution=0.0,
            current_stop=99.0,
            charge_bps=0.0,
            slippage_bps=slip,
        )
        self.assertAlmostEqual(exited_cost_reservation(unpriced), 4 * sps, places=6)

        confirmed = _trade(
            status="partial_exit",
            qty=10,
            filled_qty=10,
            exited_qty=4,
            remaining_position_qty=6,
            protected_qty=6,
            entry_fill=110.0,
            entry_value=1100.0,
            exit_value=396.0,  # adverse — slippage in price P&L
            exit_value_est=0.0,
            pnl_provisional=0,
            closed_loss_contribution=44.0,
            current_stop=99.0,
            charge_bps=0.0,
            slippage_bps=slip,
        )
        self.assertAlmostEqual(exited_cost_reservation(confirmed), 0.0, places=6)
        # Open remainder still estimates slippage until those fills confirm.
        open_only = 6 * 11.0 + 6 * sps
        self.assertAlmostEqual(trade_remaining_risk(confirmed), open_only, places=6)

    def test_high_confirmed_prices_do_not_inflate_confirmed_qty(self) -> None:
        """₹-ratio would overstate confirmed qty; durable exact qty must win."""
        from trading_engine_risk import (
            _confirmed_exited_price_pnl,
            _exited_confirmed_qty,
            exited_cost_reservation,
            slippage_per_share,
        )

        slip = 50.0
        sps = slippage_per_share(110.0, slippage_bps=slip)
        # 2 confirmed @ 150 (₹300) + 2 unpriced est @ 50 (₹100).
        # Monetary ratio 300/400 * 4 = 3 — must NOT release the 2nd unpriced share.
        mixed = _trade(
            status="partial_exit",
            qty=10,
            filled_qty=10,
            exited_qty=4,
            remaining_position_qty=6,
            protected_qty=6,
            entry_fill=110.0,
            entry_value=1100.0,
            exit_value=300.0,
            exit_value_est=100.0,
            exit_confirmed_qty=2,
            exit_est_qty=2,
            pnl_provisional=1,
            closed_loss_contribution=0.0,
            current_stop=50.0,
            charge_bps=0.0,
            slippage_bps=slip,
        )
        self.assertEqual(_exited_confirmed_qty(mixed), 2)
        self.assertAlmostEqual(exited_cost_reservation(mixed), 2 * sps, places=6)
        pnl = _confirmed_exited_price_pnl(mixed)
        assert pnl is not None
        # Confirmed slice: entry 2×110=220 vs exit 300 → +80 on UP (not used for loss).
        self.assertAlmostEqual(pnl, 80.0, places=6)
        # Without stamps, mixed monetary fields must not invent confirmed qty.
        legacy = _trade(
            status="partial_exit",
            qty=10,
            filled_qty=10,
            exited_qty=4,
            remaining_position_qty=6,
            protected_qty=6,
            entry_fill=110.0,
            entry_value=1100.0,
            exit_value=300.0,
            exit_value_est=100.0,
            pnl_provisional=1,
            current_stop=50.0,
            charge_bps=0.0,
            slippage_bps=slip,
        )
        self.assertEqual(_exited_confirmed_qty(legacy), 0)
        self.assertAlmostEqual(exited_cost_reservation(legacy), 4 * sps, places=6)

    def test_partial_fill_reserves_pending_and_filled(self) -> None:
        from trading_engine_risk import open_notional_total

        partial = _trade(
            status="partial_entry",
            qty=80,
            intended_qty=80,
            filled_qty=30,
            remaining_entry_qty=50,
            remaining_position_qty=30,
            protected_qty=30,
            entry_fill=110.0,
            current_stop=99.0,
            notional=3300.0,  # stale filled-only figure must not win
        )
        reserved = trade_remaining_risk(partial, cost_bps=0.0)
        self.assertAlmostEqual(reserved, 80 * 11.0, places=6)
        self.assertAlmostEqual(open_notional_total([partial]), 80 * 110.0, places=6)
        # Distinct symbol can still size while concurrency allows.
        decision = size_new_trade(
            _candidate(setup_id="s2", tradingsymbol="BBB"),
            [partial],
            total_capital=DEFAULT_TOTAL_CAPITAL,
            cost_bps=0.0,
            max_concurrent_positions=2,
        )
        self.assertTrue(decision.allow)

    def test_partial_realized_loss_consumes_daily_budget(self) -> None:
        open_partial_loss = _trade(
            status="partial_exit",
            qty=10,
            filled_qty=10,
            exited_qty=4,
            remaining_position_qty=6,
            protected_qty=6,
            realised_pnl=-220.0,
            closed_loss_contribution=220.0,
            entry_fill=110.0,
            current_stop=99.0,
        )
        snap = risk_snapshot([open_partial_loss], cost_bps=0.0)
        self.assertEqual(snap.closed_loss_today, 220.0)
        self.assertLess(snap.remaining_daily, DAILY_LOSS_CAP - 219.0)

    def test_concurrency_limit_two_positions(self) -> None:
        a = _trade(
            trade_id="a",
            setup_id="s1",
            symbol="AAA",
            status="protected_open",
            remaining_position_qty=10,
            protected_qty=10,
            filled_qty=10,
        )
        b = _trade(
            trade_id="b",
            setup_id="s2",
            symbol="BBB",
            status="protected_open",
            remaining_position_qty=10,
            protected_qty=10,
            filled_qty=10,
        )
        decision = size_new_trade(
            _candidate(setup_id="s3", tradingsymbol="CCC"),
            [a, b],
            total_capital=DEFAULT_TOTAL_CAPITAL,
            max_concurrent_positions=2,
            cost_bps=0.0,
        )
        self.assertFalse(decision.allow)
        self.assertEqual(decision.reason, "concurrency_limit")

    def test_five_distinct_filled_setups_per_day(self) -> None:
        filled = []
        for i in range(5):
            filled.append(
                _trade(
                    trade_id=f"t{i}",
                    setup_id=f"setup{i}",
                    symbol=f"S{i}",
                    status="closed",
                    filled_qty=10,
                    exited_qty=10,
                    remaining_position_qty=0,
                    qty=10,
                    closed_loss_contribution=0.0,
                )
            )
        decision = size_new_trade(
            _candidate(setup_id="setup_new", tradingsymbol="ZZZ"),
            filled,
            total_capital=DEFAULT_TOTAL_CAPITAL,
            cost_bps=0.0,
        )
        self.assertFalse(decision.allow)
        self.assertEqual(decision.reason, "daily_filled_setups_limit")
        # Additional partial on an already-filled setup does not consume a new slot.
        again = size_new_trade(
            _candidate(setup_id="setup0", tradingsymbol="ZZZ"),
            filled,
            total_capital=DEFAULT_TOTAL_CAPITAL,
            cost_bps=0.0,
        )
        self.assertTrue(again.allow)

    def test_pending_entry_reserves_daily_setup_slot(self) -> None:
        filled = []
        for i in range(4):
            filled.append(
                _trade(
                    trade_id=f"t{i}",
                    setup_id=f"setup{i}",
                    symbol=f"S{i}",
                    status="closed",
                    filled_qty=10,
                    exited_qty=10,
                    remaining_position_qty=0,
                    qty=10,
                    closed_loss_contribution=0.0,
                )
            )
        pending = _trade(
            trade_id="pending",
            setup_id="setup_pending",
            symbol="PEND",
            status="entry_submitting",
            qty=10,
            intended_qty=10,
            filled_qty=0,
            remaining_entry_qty=10,
            remaining_position_qty=0,
            entry_fill=None,
            notional=1100.0,
            margin_blocked=220.0,
        )
        decision = size_new_trade(
            _candidate(setup_id="setup_new", tradingsymbol="ZZZ"),
            filled + [pending],
            total_capital=DEFAULT_TOTAL_CAPITAL,
            cost_bps=0.0,
            max_concurrent_positions=3,
        )
        self.assertFalse(decision.allow)
        self.assertEqual(decision.reason, "daily_filled_setups_limit")
        # Zero-fill rejection releases the slot.
        released = _trade(
            trade_id="pending",
            setup_id="setup_pending",
            symbol="PEND",
            status="rejected",
            qty=0,
            intended_qty=0,
            filled_qty=0,
            remaining_entry_qty=0,
            remaining_position_qty=0,
            closed_loss_contribution=0.0,
            notional=0.0,
            margin_blocked=0.0,
        )
        after = size_new_trade(
            _candidate(setup_id="setup_new", tradingsymbol="ZZZ"),
            filled + [released],
            total_capital=DEFAULT_TOTAL_CAPITAL,
            cost_bps=0.0,
            max_concurrent_positions=3,
        )
        self.assertTrue(after.allow)

    def test_symbol_busy_blocks_second_same_symbol(self) -> None:
        open_t = _trade(
            status="protected_open",
            symbol="AAA",
            remaining_position_qty=10,
            protected_qty=10,
            filled_qty=10,
        )
        decision = size_new_trade(
            _candidate(setup_id="s2", tradingsymbol="AAA"),
            [open_t],
            total_capital=DEFAULT_TOTAL_CAPITAL,
            cost_bps=0.0,
        )
        self.assertFalse(decision.allow)
        self.assertEqual(decision.reason, "symbol_busy")

    def test_live_sizing_does_not_use_demo_leverage_as_buying_power(self) -> None:
        # Tiny capital: demo 5× would allow shares; live without 5× must refuse.
        decision = size_new_trade(
            _candidate(),
            [],
            total_capital=200.0,
            use_demo_leverage=False,
            available_broker_margin=50.0,
            cost_bps=0.0,
        )
        self.assertFalse(decision.allow)
        self.assertEqual(decision.reason, "insufficient_margin")
        self.assertEqual(decision.kind, "rejected")
        unknown = size_new_trade(
            _candidate(),
            [],
            total_capital=300_000.0,
            use_demo_leverage=False,
            available_broker_margin=None,
            cost_bps=0.0,
        )
        self.assertFalse(unknown.allow)
        self.assertEqual(unknown.reason, "margin_unavailable")
        self.assertEqual(unknown.kind, "rejected")
        paper = size_new_trade(
            _candidate(),
            [],
            total_capital=200.0,
            use_demo_leverage=True,
            cost_bps=0.0,
        )
        self.assertTrue(paper.allow)

    def test_notional_cap_equals_allocated_capital(self) -> None:
        from trading_engine_risk import open_notional_total

        # Large notional but small stop distance so daily risk budget is not the limiter.
        heavy = _trade(
            status="protected_open",
            symbol="AAA",
            setup_id="s1",
            remaining_position_qty=100,
            protected_qty=100,
            filled_qty=100,
            notional=295_000.0,
            entry_fill=110.0,
            initial_stop=109.0,
            current_stop=109.0,
            qty=100,
        )
        self.assertEqual(open_notional_total([heavy]), 11_000.0)
        decision = size_new_trade(
            _candidate(setup_id="s2", tradingsymbol="BBB"),
            [heavy],
            total_capital=300_000.0,
            aggregate_notional_cap=12_000.0,
            cost_bps=0.0,
            max_concurrent_positions=2,
        )
        self.assertTrue(decision.allow)
        self.assertLessEqual(
            open_notional_total([heavy]) + decision.notional, 12_000.0 + 1e-6
        )
        filled_headroom = _trade(
            trade_id="t2",
            setup_id="s2",
            symbol="BBB",
            status="protected_open",
            remaining_position_qty=9,
            protected_qty=9,
            filled_qty=9,
            notional=990.0,
            entry_fill=110.0,
            initial_stop=109.0,
            current_stop=109.0,
            qty=9,
        )
        self.assertGreaterEqual(open_notional_total([heavy, filled_headroom]), 11_990.0)
        blocked = size_new_trade(
            _candidate(setup_id="s3", tradingsymbol="CCC"),
            [heavy, filled_headroom],
            total_capital=300_000.0,
            aggregate_notional_cap=12_000.0,
            cost_bps=0.0,
            max_concurrent_positions=3,
        )
        self.assertFalse(blocked.allow)
        self.assertEqual(blocked.reason, "notional_cap")


if __name__ == "__main__":
    unittest.main()
