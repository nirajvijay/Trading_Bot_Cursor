"""Pure risk, stop, quantity, and P&L math for the Version 1 trading engine."""

from __future__ import annotations

import math
from typing import Iterable, Optional, Sequence

from continuation_features import price_to_ticks, ticks_to_price

from trading_engine_types import (
    DAILY_LOSS_CAP,
    DEMO_LEVERAGE_FACTOR,
    IN_FLIGHT_BLOCK_STATES,
    PER_TRADE_RISK_CAP,
    RISK_CONSUMING_STATES,
    TERMINAL_FLAT_STATES,
    UNPROTECTED_STATES,
    CapitalSnapshot,
    RiskSnapshot,
    SizeDecision,
    TradeRecord,
    TriggerCandidate,
)


def structural_stop_price(
    *,
    direction: str,
    swing_high: Optional[float],
    swing_low: Optional[float],
    tick_size: float,
    buffer_ticks: int,
) -> Optional[float]:
    """Long: one tick below swing low. Short: one tick above swing high."""
    if tick_size <= 0:
        return None
    try:
        if direction == "UP":
            if swing_low is None:
                return None
            ticks = price_to_ticks(float(swing_low), tick_size) - int(buffer_ticks)
            if ticks < 0:
                return None
            return ticks_to_price(ticks, tick_size)
        if direction == "DOWN":
            if swing_high is None:
                return None
            ticks = price_to_ticks(float(swing_high), tick_size) + int(buffer_ticks)
            return ticks_to_price(ticks, tick_size)
    except ValueError:
        return None
    return None


def remaining_downside_risk(
    *,
    direction: str,
    qty: int,
    entry: Optional[float],
    current_stop: Optional[float],
) -> float:
    """Downside remaining to the current stop. Profit-locked stop → 0."""
    if qty <= 0 or entry is None or current_stop is None:
        return 0.0
    if direction == "UP":
        return float(qty) * max(0.0, float(entry) - float(current_stop))
    if direction == "DOWN":
        return float(qty) * max(0.0, float(current_stop) - float(entry))
    return 0.0


def trade_remaining_risk(trade: TradeRecord) -> float:
    if trade.status not in RISK_CONSUMING_STATES:
        return 0.0
    if trade.status in TERMINAL_FLAT_STATES:
        return 0.0
    entry = trade.entry_fill if trade.entry_fill is not None else trade.entry_estimate
    stop = trade.current_stop if trade.current_stop is not None else trade.initial_stop
    pos = int(getattr(trade, "remaining_position_qty", 0) or 0)
    filled = int(getattr(trade, "filled_qty", 0) or 0)
    intended = int(getattr(trade, "intended_qty", 0) or trade.qty or 0)
    remaining_entry = int(getattr(trade, "remaining_entry_qty", 0) or 0)
    # Open downside on remaining position; while submitting with no fills, reserve intended.
    if pos > 0:
        qty = pos
    elif filled > 0:
        qty = filled
    elif remaining_entry > 0 or trade.status in {
        "entry_submitting",
        "submission_unknown",
        "reconciliation_required",
    }:
        qty = intended
    else:
        qty = 0
    return remaining_downside_risk(
        direction=trade.direction,
        qty=qty,
        entry=entry,
        current_stop=stop,
    )


def closed_loss_today(trades: Sequence[TradeRecord]) -> float:
    total = 0.0
    for trade in trades:
        if trade.status != "closed":
            continue
        if trade.closed_loss_contribution > 0:
            total += trade.closed_loss_contribution
    return total


def is_unprotected(trade: TradeRecord) -> bool:
    """True when open exposure is not fully covered by broker-confirmed protection."""
    if trade.status in TERMINAL_FLAT_STATES:
        return False
    pos = int(getattr(trade, "remaining_position_qty", 0) or 0)
    protected = int(getattr(trade, "protected_qty", 0) or 0)
    if pos > 0:
        return protected < pos
    # Legacy / pre-position-qty path: filled but not yet flat and not terminal.
    filled = int(getattr(trade, "filled_qty", 0) or 0)
    if filled > 0 and trade.status in UNPROTECTED_STATES | {
        "protection_pending",
        "stop_pending",
        "entry_filled",
        "partial_entry",
        "reconciliation_required",
        "exit_pending",
        "partial_exit",
    }:
        return protected < filled
    if trade.status in {"entry_filled", "stop_pending", "protection_pending"} and filled <= 0:
        return True
    return False


def blocks_new_entries(trades: Iterable[TradeRecord]) -> bool:
    return any(t.status in IN_FLIGHT_BLOCK_STATES for t in trades)


def risk_snapshot(
    trades: Sequence[TradeRecord],
    *,
    daily_loss_cap: float = DAILY_LOSS_CAP,
    per_trade_cap: float = PER_TRADE_RISK_CAP,
) -> RiskSnapshot:
    committed = sum(trade_remaining_risk(t) for t in trades)
    closed = closed_loss_today(trades)
    unprotected = sum(1 for t in trades if is_unprotected(t))
    in_flight = sum(1 for t in trades if t.status in IN_FLIGHT_BLOCK_STATES)
    remaining = max(0.0, float(daily_loss_cap) - closed - committed)
    return RiskSnapshot(
        closed_loss_today=closed,
        committed_risk=committed,
        remaining_daily=remaining,
        per_trade_cap=float(per_trade_cap),
        daily_cap=float(daily_loss_cap),
        unprotected_count=unprotected,
        in_flight_count=in_flight,
        limits_protected=unprotected == 0 and in_flight == 0,
    )


def margin_blocked(*, qty: int, entry: float, leverage_factor: float) -> float:
    if qty <= 0 or entry <= 0 or leverage_factor <= 0:
        return 0.0
    return (float(qty) * float(entry)) / float(leverage_factor)


def capital_snapshot(
    trades: Sequence[TradeRecord],
    *,
    total_capital: float,
    leverage_factor: float = DEMO_LEVERAGE_FACTOR,
) -> CapitalSnapshot:
    used = sum(
        t.margin_blocked for t in trades if t.status in RISK_CONSUMING_STATES
    )
    remaining = float(total_capital) - used
    buying = remaining * float(leverage_factor)
    return CapitalSnapshot(
        total_capital=float(total_capital),
        leverage_factor=float(leverage_factor),
        margin_used=used,
        remaining_capital=remaining,
        buying_power=buying,
    )


def size_new_trade(
    candidate: TriggerCandidate,
    trades: Sequence[TradeRecord],
    *,
    total_capital: float,
    leverage_factor: float = DEMO_LEVERAGE_FACTOR,
    per_trade_risk_cap: float = PER_TRADE_RISK_CAP,
    daily_loss_cap: float = DAILY_LOSS_CAP,
) -> SizeDecision:
    stop = structural_stop_price(
        direction=candidate.direction,
        swing_high=candidate.pullback_swing_high,
        swing_low=candidate.pullback_swing_low,
        tick_size=candidate.tick_size,
        buffer_ticks=candidate.buffer_ticks,
    )
    if stop is None:
        return SizeDecision(
            allow=False,
            qty=0,
            initial_stop=None,
            risk_per_share=0.0,
            proposed_risk=0.0,
            notional=0.0,
            margin_blocked=0.0,
            reason="missing_stop",
            kind="skipped",
        )

    entry = float(candidate.trigger_price)
    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0:
        return SizeDecision(
            allow=False,
            qty=0,
            initial_stop=stop,
            risk_per_share=risk_per_share,
            proposed_risk=0.0,
            notional=0.0,
            margin_blocked=0.0,
            reason="missing_stop",
            kind="skipped",
        )

    if blocks_new_entries(trades):
        return SizeDecision(
            allow=False,
            qty=0,
            initial_stop=stop,
            risk_per_share=risk_per_share,
            proposed_risk=0.0,
            notional=0.0,
            margin_blocked=0.0,
            reason="unprotected_lockout",
            kind="rejected",
        )

    snap = risk_snapshot(
        trades,
        daily_loss_cap=daily_loss_cap,
        per_trade_cap=per_trade_risk_cap,
    )
    allowed = min(
        per_trade_risk_cap,
        daily_loss_cap - snap.closed_loss_today - snap.committed_risk,
    )
    if allowed < risk_per_share:
        return SizeDecision(
            allow=False,
            qty=0,
            initial_stop=stop,
            risk_per_share=risk_per_share,
            proposed_risk=0.0,
            notional=0.0,
            margin_blocked=0.0,
            reason="daily_or_per_trade_risk",
            kind="skipped",
        )

    qty_risk = int(math.floor(allowed / risk_per_share))
    cap = capital_snapshot(
        trades, total_capital=total_capital, leverage_factor=leverage_factor
    )
    if cap.remaining_capital <= 0 or entry <= 0:
        return SizeDecision(
            allow=False,
            qty=0,
            initial_stop=stop,
            risk_per_share=risk_per_share,
            proposed_risk=0.0,
            notional=0.0,
            margin_blocked=0.0,
            reason="insufficient_capital",
            kind="skipped",
        )
    qty_capital = int(math.floor(cap.buying_power / entry))
    qty = min(qty_risk, qty_capital)
    if qty < 1:
        return SizeDecision(
            allow=False,
            qty=0,
            initial_stop=stop,
            risk_per_share=risk_per_share,
            proposed_risk=0.0,
            notional=0.0,
            margin_blocked=0.0,
            reason="insufficient_capital",
            kind="skipped",
        )

    proposed = qty * risk_per_share
    if snap.closed_loss_today + snap.committed_risk + proposed > daily_loss_cap + 1e-9:
        return SizeDecision(
            allow=False,
            qty=0,
            initial_stop=stop,
            risk_per_share=risk_per_share,
            proposed_risk=proposed,
            notional=0.0,
            margin_blocked=0.0,
            reason="daily_or_per_trade_risk",
            kind="skipped",
        )

    notional = qty * entry
    blocked = margin_blocked(qty=qty, entry=entry, leverage_factor=leverage_factor)
    return SizeDecision(
        allow=True,
        qty=qty,
        initial_stop=stop,
        risk_per_share=risk_per_share,
        proposed_risk=proposed,
        notional=notional,
        margin_blocked=blocked,
        kind="accept",
    )


def trail_is_tighten_only(
    *,
    direction: str,
    current_stop: float,
    new_stop: float,
) -> bool:
    if direction == "UP":
        return new_stop >= current_stop - 1e-12
    if direction == "DOWN":
        return new_stop <= current_stop + 1e-12
    return False


def trail_crosses_last_price(
    *,
    direction: str,
    new_stop: float,
    last_price: Optional[float],
) -> bool:
    if last_price is None:
        return False
    if direction == "UP":
        return new_stop >= last_price
    if direction == "DOWN":
        return new_stop <= last_price
    return False


def realised_pnl(
    *,
    direction: str,
    qty: int,
    entry_fill: float,
    exit_fill: float,
) -> float:
    if direction == "UP":
        return float(qty) * (float(exit_fill) - float(entry_fill))
    return float(qty) * (float(entry_fill) - float(exit_fill))


def realised_pnl_from_values(
    *,
    direction: str,
    entry_value: float,
    exit_value: float,
    qty: int,
) -> float:
    """P&L from cumulative entry/exit notionals (handles multi-price executions)."""
    if qty <= 0:
        return 0.0
    entry_avg = float(entry_value) / float(qty)
    exit_avg = float(exit_value) / float(qty)
    return realised_pnl(
        direction=direction, qty=qty, entry_fill=entry_avg, exit_fill=exit_avg
    )


def open_pnl(
    *,
    direction: str,
    qty: int,
    entry_fill: Optional[float],
    mark: Optional[float],
) -> float:
    if entry_fill is None or mark is None or qty <= 0:
        return 0.0
    return realised_pnl(
        direction=direction, qty=qty, entry_fill=entry_fill, exit_fill=mark
    )


def live_pnl(trades: Sequence[TradeRecord]) -> float:
    total = 0.0
    for trade in trades:
        if trade.status == "closed":
            total += trade.realised_pnl
        elif trade.status in RISK_CONSUMING_STATES:
            total += trade.open_pnl
    return total
