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
        decision = size_new_trade(_candidate(), [], total_capital=DEFAULT_TOTAL_CAPITAL)
        self.assertTrue(decision.allow)
        self.assertEqual(decision.initial_stop, 99.0)
        self.assertEqual(decision.risk_per_share, 11.0)
        self.assertEqual(decision.qty, 81)  # floor(900/11)
        self.assertLessEqual(decision.proposed_risk, 900.0 + 1e-9)

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
        pending = _trade(status="entry_submitting", qty=81, entry_fill=None, current_stop=99.0)
        snap = risk_snapshot([pending])
        self.assertGreater(snap.committed_risk, 0)
        self.assertEqual(snap.in_flight_count, 1)
        decision = size_new_trade(
            _candidate(setup_id="s2"), [pending], total_capital=DEFAULT_TOTAL_CAPITAL
        )
        self.assertEqual(decision.reason, "unprotected_lockout")

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


if __name__ == "__main__":
    unittest.main()
