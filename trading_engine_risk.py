"""Pure risk, stop, quantity, and P&L math for the Version 1 trading engine."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Optional, Sequence, Set

from continuation_features import price_to_ticks, ticks_to_price

from trading_engine_types import (
    ACTIVE_STATES,
    DAILY_LOSS_CAP,
    DEFAULT_ESTIMATED_SLIPPAGE_BPS,
    DEFAULT_ROUND_TRIP_CHARGE_BPS,
    DEFAULT_ROUND_TRIP_COST_BPS,
    DEFAULT_TOTAL_CAPITAL,
    DEMO_LEVERAGE_FACTOR,
    IN_FLIGHT_BLOCK_STATES,
    MAX_CONCURRENT_POSITIONS,
    MAX_FILLED_SETUPS_PER_DAY,
    PER_TRADE_RISK_CAP,
    RISK_CONSUMING_STATES,
    SKIPPED_STATES,
    TERMINAL_FLAT_STATES,
    UNPROTECTED_STATES,
    CapitalSnapshot,
    RiskSnapshot,
    SizeDecision,
    TradeRecord,
    TriggerCandidate,
)


@dataclass(frozen=True)
class PostFillRiskDecision:
    state: str
    reasons: tuple[str, ...] = ()


def post_fill_risk_decision(trade: TradeRecord, trades: Sequence[TradeRecord],
                            limits: dict) -> PostFillRiskDecision:
    """Confirmed cap violations and incomplete information are different states.

    Limits are the profile frozen before the entry write; later Admin reductions
    restrict new admissions but do not retroactively liquidate accepted positions.
    """
    if trade.filled_qty <= 0:
        return PostFillRiskDecision("safe")
    reasons = []
    if trade.filled_qty > trade.intended_qty:
        reasons.append("filled_quantity_exceeds_intent")
    required = ("per_trade_risk_cap_inr", "daily_loss_cap_inr", "allocated_capital_inr",
                "max_concurrent_positions", "max_filled_setups_per_day")
    if any(key not in limits or not math.isfinite(float(limits[key])) or float(limits[key]) <= 0 for key in required):
        return PostFillRiskDecision("breach" if reasons else "unknown", tuple(reasons or ["risk_profile_unknown"]))
    if concurrent_position_count(trades) > int(limits["max_concurrent_positions"]):
        reasons.append("concurrency_limit")
    if len(reserved_setup_ids(trades)) > int(limits["max_filled_setups_per_day"]):
        reasons.append("setup_limit")
    peers = [t for t in trades if t.symbol == trade.symbol and counts_toward_concurrency(t)]
    if bool(limits.get("one_position_or_unresolved_entry_per_symbol", True)) and len(peers) > 1:
        reasons.append("symbol_limit")
    priced = all(t.filled_qty <= 0 or (t.entry_value_est <= 0 and t.entry_fill is not None
                 and math.isfinite(t.entry_fill) and t.entry_fill > 0) for t in trades if counts_toward_concurrency(t))
    if not priced:
        return PostFillRiskDecision("breach" if reasons else "unknown", tuple(reasons or ["entry_price_unresolved"]))
    cap = min(float(limits["per_trade_risk_cap_inr"]), trade.risk_cap_used_inr or float(limits["per_trade_risk_cap_inr"]))
    if trade_remaining_risk(trade) > cap + 1e-9:
        reasons.append("per_trade_risk_limit")
    snap = risk_snapshot(trades, daily_loss_cap=float(limits["daily_loss_cap_inr"]))
    if snap.closed_loss_today + snap.committed_risk > float(limits["daily_loss_cap_inr"]) + 1e-9:
        reasons.append("daily_risk_limit")
    if open_notional_total(trades) > float(limits["allocated_capital_inr"]) + 1e-9:
        reasons.append("notional_limit")
    return PostFillRiskDecision("breach" if reasons else "safe", tuple(reasons))


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


def estimated_cost_per_share(
    entry: float,
    *,
    cost_bps: float = DEFAULT_ROUND_TRIP_COST_BPS,
) -> float:
    """Per-share ₹ from entry × bps (legacy combined helper)."""
    if entry <= 0 or cost_bps <= 0:
        return 0.0
    return abs(float(entry)) * (float(cost_bps) / 10_000.0)


def charge_per_share(
    entry: float,
    *,
    charge_bps: float = DEFAULT_ROUND_TRIP_CHARGE_BPS,
) -> float:
    """Estimated round-trip fees/charges per share (never embedded in fill prices)."""
    return estimated_cost_per_share(entry, cost_bps=charge_bps)


def slippage_per_share(
    entry: float,
    *,
    slippage_bps: float = DEFAULT_ESTIMATED_SLIPPAGE_BPS,
) -> float:
    """Estimated execution slippage per share (reserved only until prices confirm)."""
    return estimated_cost_per_share(entry, cost_bps=slippage_bps)


def trade_cost_profile(
    trade: TradeRecord,
    *,
    charge_bps: float = DEFAULT_ROUND_TRIP_CHARGE_BPS,
    slippage_bps: float = DEFAULT_ESTIMATED_SLIPPAGE_BPS,
) -> tuple[float, float]:
    """Return (charge_bps, slippage_bps) preferring the trade-stamped profile."""
    stamped_charge = getattr(trade, "charge_bps", None)
    stamped_slip = getattr(trade, "slippage_bps", None)
    c = float(stamped_charge) if stamped_charge is not None else float(charge_bps)
    s = float(stamped_slip) if stamped_slip is not None else float(slippage_bps)
    return c, s


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


def _entry_price_for_costs(trade: TradeRecord) -> float:
    entry = trade.entry_fill if trade.entry_fill is not None else trade.entry_estimate
    return float(entry or 0.0)


def _confirmed_exited_price_pnl(trade: TradeRecord) -> Optional[float]:
    """Realised price P&L on the confirmed-priced exited portion only."""
    exited = int(getattr(trade, "exited_qty", 0) or 0)
    filled = int(getattr(trade, "filled_qty", 0) or 0)
    if exited <= 0 or filled <= 0:
        return None
    entry_value = float(getattr(trade, "entry_value", 0) or 0)
    exit_value = float(getattr(trade, "exit_value", 0) or 0)
    if entry_value <= 0 or exit_value <= 0:
        return None
    confirmed_qty = _exited_confirmed_qty(trade)
    if confirmed_qty <= 0:
        return None
    entry_slice = entry_value * (float(confirmed_qty) / float(filled))
    return realised_pnl_from_values(
        direction=trade.direction,
        entry_value=entry_slice,
        exit_value=exit_value,
        qty=confirmed_qty,
    )


def _exited_confirmed_qty(trade: TradeRecord) -> int:
    """Exact confirmed-priced exit qty from durable reconciliation — never ₹-ratio.

    Prefers ``exit_confirmed_qty`` stamped from per-order records. Legacy rows without
    the stamp never infer qty from exit_value/(exit_value+exit_est): high confirmed
    prices would otherwise overstate confirmed qty and release slippage reservations.
    """
    exited = int(getattr(trade, "exited_qty", 0) or 0)
    if exited <= 0:
        return 0
    stamped_c = getattr(trade, "exit_confirmed_qty", None)
    if stamped_c is not None:
        return min(exited, max(0, int(stamped_c)))
    # Legacy fallback without qty stamps: fully confirmed notionals only.
    exit_value = float(getattr(trade, "exit_value", 0) or 0)
    exit_est = float(getattr(trade, "exit_value_est", 0) or 0)
    if exit_value <= 0:
        return 0
    if exit_est <= 0:
        return exited
    # Mixed legacy monetary fields without exact qty — do not release reservations.
    return 0


def fold_exit_costs_into_loss(
    *,
    price_pnl: float,
    qty: int,
    entry: float,
    charge_bps: float,
) -> float:
    """Authoritative close: price loss (includes slippage) + fees/charges only."""
    price_loss = abs(price_pnl) if price_pnl < 0 else 0.0
    charges = charge_per_share(entry, charge_bps=charge_bps) * float(max(0, qty))
    return float(price_loss + charges)


def exited_cost_reservation(
    trade: TradeRecord,
    *,
    charge_bps: float = DEFAULT_ROUND_TRIP_CHARGE_BPS,
    slippage_bps: float = DEFAULT_ESTIMATED_SLIPPAGE_BPS,
) -> float:
    """Unresolved exit cost budget: charges always; estimated slippage only if unpriced.

    Confirmed execution prices already embed slippage — never add estimated
    slippage on top. Fees/charges are never in fill prices, so they remain
    reserved until authoritative close folds them into ``closed_loss_contribution``.
    """
    exited = int(getattr(trade, "exited_qty", 0) or 0)
    if exited <= 0:
        return 0.0
    if trade.status in SKIPPED_STATES:
        return 0.0
    provisional = bool(int(getattr(trade, "pnl_provisional", 0) or 0))
    if trade.status == "closed" and not provisional:
        return 0.0
    charge_bps, slippage_bps = trade_cost_profile(
        trade, charge_bps=charge_bps, slippage_bps=slippage_bps
    )
    entry = _entry_price_for_costs(trade)
    cps_charge = charge_per_share(entry, charge_bps=charge_bps)
    cps_slip = slippage_per_share(entry, slippage_bps=slippage_bps)
    confirmed_qty = _exited_confirmed_qty(trade)
    unconfirmed_qty = max(0, exited - confirmed_qty)
    # Charges for all exited shares until authoritative fold.
    reserved = cps_charge * float(exited)
    # Estimated slippage only while prices are missing.
    reserved += cps_slip * float(unconfirmed_qty)
    return float(reserved)


def _reserved_qty(trade: TradeRecord) -> int:
    """Quantity that still consumes daily risk/notional (filled position + pending entry).

    Pending shares remain reserved until cancellation/rejection is confirmed with
    zero remaining entry quantity. A partial fill of 30 with 50 still working
    reserves 80, not 30.
    """
    if trade.status not in RISK_CONSUMING_STATES:
        return 0
    if trade.status in TERMINAL_FLAT_STATES:
        return 0
    pos = int(getattr(trade, "remaining_position_qty", 0) or 0)
    filled = int(getattr(trade, "filled_qty", 0) or 0)
    intended = int(getattr(trade, "intended_qty", 0) or trade.qty or 0)
    remaining_entry = int(getattr(trade, "remaining_entry_qty", 0) or 0)
    if pos > 0 or remaining_entry > 0:
        return max(0, pos) + max(0, remaining_entry)
    if filled > 0:
        return filled
    if trade.status in {
        "entry_submitting",
        "submission_unknown",
        "reconciliation_required",
    }:
        return intended
    return 0


def trade_remaining_risk(
    trade: TradeRecord,
    *,
    charge_bps: float = DEFAULT_ROUND_TRIP_CHARGE_BPS,
    slippage_bps: float = DEFAULT_ESTIMATED_SLIPPAGE_BPS,
    cost_bps: Optional[float] = None,
) -> float:
    """Open downside + open charges/slippage estimates + unresolved exited reservations.

    ``cost_bps`` is accepted as a legacy combined override (split evenly only when
    neither component is trade-stamped and both defaults would apply). Prefer
    ``charge_bps`` / ``slippage_bps`` or the trade-stamped profile.
    """
    if cost_bps is not None and getattr(trade, "charge_bps", None) is None:
        # Legacy call sites passing a single combined bps.
        charge_bps = float(cost_bps)
        slippage_bps = 0.0
    charge_bps, slippage_bps = trade_cost_profile(
        trade, charge_bps=charge_bps, slippage_bps=slippage_bps
    )
    qty = _reserved_qty(trade)
    open_risk = 0.0
    if qty > 0:
        entry = trade.entry_fill if trade.entry_fill is not None else (trade.entry_limit_price or trade.entry_estimate)
        if trade.remaining_entry_qty > 0 and trade.entry_limit_price is not None:
            entry = max(entry, trade.entry_limit_price) if trade.direction == "UP" else min(entry, trade.entry_limit_price)
        stop = trade.current_stop if trade.current_stop is not None else trade.initial_stop
        downside = remaining_downside_risk(
            direction=trade.direction,
            qty=qty,
            entry=entry,
            current_stop=stop,
        )
        entry_px = float(entry or 0.0)
        open_costs = (
            charge_per_share(entry_px, charge_bps=charge_bps)
            + slippage_per_share(entry_px, slippage_bps=slippage_bps)
        ) * float(qty)
        open_risk = float(max(0.0, downside) + open_costs)
    return float(
        open_risk
        + exited_cost_reservation(
            trade, charge_bps=charge_bps, slippage_bps=slippage_bps
        )
    )


def closed_loss_today(trades: Sequence[TradeRecord]) -> float:
    """Sum confirmed loss contributions, including partial exits on still-open trades."""
    total = 0.0
    for trade in trades:
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
    """Legacy helper: any unprotected or hard in-flight lockout."""
    return any(is_unprotected(t) for t in trades) or any(
        t.status in IN_FLIGHT_BLOCK_STATES and is_unprotected(t) for t in trades
    )


def counts_toward_concurrency(trade: TradeRecord) -> bool:
    """True when the trade occupies a concurrent position / unresolved entry slot."""
    if trade.status in TERMINAL_FLAT_STATES | SKIPPED_STATES:
        return False
    if trade.status == "candidate":
        return False
    pos = int(getattr(trade, "remaining_position_qty", 0) or 0)
    rem_entry = int(getattr(trade, "remaining_entry_qty", 0) or 0)
    filled = int(getattr(trade, "filled_qty", 0) or 0)
    intended = int(getattr(trade, "intended_qty", 0) or trade.qty or 0)
    if pos > 0 or rem_entry > 0:
        return True
    if filled > 0 and trade.status in ACTIVE_STATES:
        return True
    if trade.status in {
        "entry_submitting",
        "submission_unknown",
        "partial_entry",
        "reconciliation_required",
    } and intended > 0:
        return True
    return False


def concurrent_position_count(trades: Sequence[TradeRecord]) -> int:
    return sum(1 for t in trades if counts_toward_concurrency(t))


def filled_setup_ids(trades: Sequence[TradeRecord]) -> Set[str]:
    """Distinct setup_id values that have received at least one entry fill."""
    out: Set[str] = set()
    for trade in trades:
        if int(getattr(trade, "filled_qty", 0) or 0) > 0:
            out.add(str(trade.setup_id))
    return out


def filled_setups_today(trades: Sequence[TradeRecord]) -> int:
    return len(filled_setup_ids(trades))


def reserved_setup_ids(trades: Sequence[TradeRecord]) -> Set[str]:
    """Daily setup slots: filled setups plus pending submissions still unresolved.

    A slot is reserved at submission (before any fill). It is released only when the
    trade reaches a terminal skip/reject/cancel with confirmed zero fills.
    """
    out: Set[str] = set()
    for trade in trades:
        setup = str(trade.setup_id)
        filled = int(getattr(trade, "filled_qty", 0) or 0)
        if filled > 0:
            out.add(setup)
            continue
        if trade.status in TERMINAL_FLAT_STATES | SKIPPED_STATES | {"candidate"}:
            continue
        if counts_toward_concurrency(trade):
            out.add(setup)
            continue
        if trade.status in {
            "entry_submitting",
            "submission_unknown",
            "partial_entry",
            "reconciliation_required",
        }:
            intended = int(getattr(trade, "intended_qty", 0) or trade.qty or 0)
            if intended > 0:
                out.add(setup)
    return out


def reserved_setups_today(trades: Sequence[TradeRecord]) -> int:
    return len(reserved_setup_ids(trades))


def symbol_has_open_or_unresolved(trades: Sequence[TradeRecord], symbol: str) -> bool:
    for trade in trades:
        if str(trade.symbol) != str(symbol):
            continue
        if counts_toward_concurrency(trade):
            return True
    return False


def open_notional_total(trades: Sequence[TradeRecord]) -> float:
    """Aggregate notional for reserved quantity (filled position + pending entry)."""
    total = 0.0
    for trade in trades:
        if not counts_toward_concurrency(trade):
            continue
        entry = trade.entry_fill if trade.entry_fill is not None else (trade.entry_limit_price or trade.entry_estimate)
        if trade.remaining_entry_qty > 0 and trade.entry_limit_price is not None:
            entry = max(entry, trade.entry_limit_price)
        qty = _reserved_qty(trade)
        if entry is not None and qty > 0:
            total += float(entry) * float(qty)
            continue
        # Fallback only when price unknown — prefer stored notional if present.
        if trade.notional and float(trade.notional) > 0:
            total += float(trade.notional)
    return total


def risk_snapshot(
    trades: Sequence[TradeRecord],
    *,
    daily_loss_cap: float = DAILY_LOSS_CAP,
    per_trade_cap: float = PER_TRADE_RISK_CAP,
    charge_bps: float = DEFAULT_ROUND_TRIP_CHARGE_BPS,
    slippage_bps: float = DEFAULT_ESTIMATED_SLIPPAGE_BPS,
    cost_bps: Optional[float] = None,
) -> RiskSnapshot:
    committed = sum(
        trade_remaining_risk(
            t,
            charge_bps=charge_bps,
            slippage_bps=slippage_bps,
            cost_bps=cost_bps,
        )
        for t in trades
    )
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
        limits_protected=unprotected == 0,
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
    max_concurrent_positions: int = MAX_CONCURRENT_POSITIONS,
    max_filled_setups_per_day: int = MAX_FILLED_SETUPS_PER_DAY,
    one_per_symbol: bool = True,
    aggregate_notional_cap: Optional[float] = None,
    charge_bps: float = DEFAULT_ROUND_TRIP_CHARGE_BPS,
    slippage_bps: float = DEFAULT_ESTIMATED_SLIPPAGE_BPS,
    cost_bps: Optional[float] = None,
    use_demo_leverage: bool = True,
    available_broker_margin: Optional[float] = None,
) -> SizeDecision:
    """Size a new entry against risk, costs, concurrency, fills/day, and capital/margin."""
    if cost_bps is not None:
        charge_bps = float(cost_bps)
        slippage_bps = 0.0
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
    cost_per_share = charge_per_share(entry, charge_bps=charge_bps) + slippage_per_share(
        entry, slippage_bps=slippage_bps
    )
    risk_per_share_total = risk_per_share + cost_per_share
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

    # Unprotected exposure blocks new risk (pause/recovery), independent of concurrency.
    if any(is_unprotected(t) for t in trades):
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

    if one_per_symbol and symbol_has_open_or_unresolved(trades, candidate.tradingsymbol):
        return SizeDecision(
            allow=False,
            qty=0,
            initial_stop=stop,
            risk_per_share=risk_per_share,
            proposed_risk=0.0,
            notional=0.0,
            margin_blocked=0.0,
            reason="symbol_busy",
            kind="skipped",
        )

    if concurrent_position_count(trades) >= int(max_concurrent_positions):
        return SizeDecision(
            allow=False,
            qty=0,
            initial_stop=stop,
            risk_per_share=risk_per_share,
            proposed_risk=0.0,
            notional=0.0,
            margin_blocked=0.0,
            reason="concurrency_limit",
            kind="skipped",
        )

    reserved_setups = reserved_setup_ids(trades)
    if len(reserved_setups) >= int(max_filled_setups_per_day):
        # Same setup already reserved/filled does not consume an extra slot.
        if str(candidate.setup_id) not in reserved_setups:
            return SizeDecision(
                allow=False,
                qty=0,
                initial_stop=stop,
                risk_per_share=risk_per_share,
                proposed_risk=0.0,
                notional=0.0,
                margin_blocked=0.0,
                reason="daily_filled_setups_limit",
                kind="skipped",
            )

    snap = risk_snapshot(
        trades,
        daily_loss_cap=daily_loss_cap,
        per_trade_cap=per_trade_risk_cap,
        charge_bps=charge_bps,
        slippage_bps=slippage_bps,
    )
    allowed = min(
        per_trade_risk_cap,
        daily_loss_cap - snap.closed_loss_today - snap.committed_risk,
    )
    if allowed < risk_per_share_total:
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

    qty_risk = int(math.floor(allowed / risk_per_share_total))
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

    # PAPER may use demo leverage for simulated buying power.
    # LIVE requires verified available broker margin; unknown margin blocks entry.
    if use_demo_leverage:
        qty_capital = int(math.floor(cap.buying_power / entry))
    else:
        if available_broker_margin is None:
            return SizeDecision(
                allow=False,
                qty=0,
                initial_stop=stop,
                risk_per_share=risk_per_share,
                proposed_risk=0.0,
                notional=0.0,
                margin_blocked=0.0,
                reason="margin_unavailable",
                kind="rejected",
            )
        margin_budget = float(available_broker_margin)
        if margin_budget <= 0:
            return SizeDecision(
                allow=False,
                qty=0,
                initial_stop=stop,
                risk_per_share=risk_per_share,
                proposed_risk=0.0,
                notional=0.0,
                margin_blocked=0.0,
                reason="insufficient_margin",
                kind="rejected",
            )
        qty_capital = int(math.floor(margin_budget / entry))

    notional_cap = (
        float(aggregate_notional_cap)
        if aggregate_notional_cap is not None
        else float(total_capital)
    )
    headroom = max(0.0, notional_cap - open_notional_total(trades))
    qty_notional = int(math.floor(headroom / entry)) if entry > 0 else 0

    qty = min(qty_risk, qty_capital, qty_notional)
    if qty < 1:
        reason = "insufficient_capital"
        kind = "skipped"
        if qty_notional < 1 and min(qty_risk, qty_capital) >= 1:
            reason = "notional_cap"
        elif (not use_demo_leverage) and qty_capital < 1:
            reason = "insufficient_margin"
            kind = "rejected"
        return SizeDecision(
            allow=False,
            qty=0,
            initial_stop=stop,
            risk_per_share=risk_per_share,
            proposed_risk=0.0,
            notional=0.0,
            margin_blocked=0.0,
            reason=reason,
            kind=kind,
        )

    proposed = qty * risk_per_share_total
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
    blocked = margin_blocked(
        qty=qty,
        entry=entry,
        leverage_factor=leverage_factor if use_demo_leverage else 1.0,
    )
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


def freeze_r_value(*, entry: float, initial_stop: float) -> float:
    """Frozen R = |actual average entry − original structural stop|."""
    return abs(float(entry) - float(initial_stop))


def cost_adjusted_break_even(
    *,
    direction: str,
    entry: float,
    charge_bps: float,
) -> float:
    """Break-even stop after round-trip charges (never embeds estimated slippage)."""
    cps = charge_per_share(entry, charge_bps=charge_bps)
    if direction == "UP":
        return float(entry) + cps
    if direction == "DOWN":
        return float(entry) - cps
    return float(entry)


def favorable_r_multiple(
    *,
    direction: str,
    entry: float,
    last_price: float,
    r_value: float,
) -> float:
    """How many R of favorable excursion from entry to last_price."""
    if r_value <= 1e-12:
        return 0.0
    if direction == "UP":
        return max(0.0, (float(last_price) - float(entry)) / float(r_value))
    if direction == "DOWN":
        return max(0.0, (float(entry) - float(last_price)) / float(r_value))
    return 0.0


def update_trail_extreme(
    *,
    direction: str,
    last_price: float,
    current_extreme: Optional[float],
) -> float:
    """Persist favorable extreme (high for UP, low for DOWN)."""
    px = float(last_price)
    if current_extreme is None:
        return px
    if direction == "UP":
        return max(float(current_extreme), px)
    if direction == "DOWN":
        return min(float(current_extreme), px)
    return px


def staged_r_desired_stop(
    *,
    direction: str,
    entry: float,
    initial_stop: float,
    current_stop: float,
    extreme: float,
    last_price: float,
    r_value: float,
    charge_bps: float,
    tick_size: float,
    stage_one_r: float = 1.0,
    stage_two_r: float = 2.0,
    stage_one_gap_r: float = 1.0,
    stage_two_gap_r: float = 0.5,
) -> float:
    """§3.14 staged-R desired stop (tighten-only vs current is caller's job).

    Stage selection uses the attained favorable *extreme* (not the latest mark),
    so a retracement cannot downgrade the stage before a modification succeeds.
    Below +1R → structural. +1R to < +2R → tightest of existing, cost BE, 1R behind extreme.
    +2R+ → tightest of existing, cost BE, 0.5R behind extreme.
    """
    structural = float(initial_stop)
    existing = float(current_stop)
    be = cost_adjusted_break_even(direction=direction, entry=entry, charge_bps=charge_bps)
    # Stage from extreme; last_price kept for call-site compatibility / diagnostics.
    _ = last_price
    mult = favorable_r_multiple(
        direction=direction, entry=entry, last_price=float(extreme), r_value=r_value
    )
    if mult < stage_one_r - 1e-12:
        desired = structural
    else:
        behind = stage_one_gap_r * float(r_value) if mult < stage_two_r - 1e-12 else stage_two_gap_r * float(r_value)
        if direction == "UP":
            behind_extreme = float(extreme) - behind
            desired = max(existing, be, behind_extreme)
        else:
            behind_extreme = float(extreme) + behind
            desired = min(existing, be, behind_extreme)
    # Align to tick grid (half-up); desired may sit mid-tick after R offsets.
    tick = Decimal(str(tick_size))
    if tick <= 0:
        raise ValueError("tick_size must be positive")
    ticks = (Decimal(str(desired)) / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return ticks_to_price(int(ticks), tick_size)


def trail_improvement_ticks(
    *,
    direction: str,
    current_stop: float,
    new_stop: float,
    tick_size: float,
) -> int:
    from continuation_features import price_to_ticks

    cur = price_to_ticks(current_stop, tick_size)
    nxt = price_to_ticks(new_stop, tick_size)
    if direction == "UP":
        return max(0, nxt - cur)
    if direction == "DOWN":
        return max(0, cur - nxt)
    return 0


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
