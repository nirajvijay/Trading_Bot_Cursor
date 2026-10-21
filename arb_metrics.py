"""Per-trade measurements for the ARB forward test, as pure functions.

What the desk and the Performance tab show about one trade: which source it
came from, how far the fill was from the price the backtest assumed, and its
result in R. Nothing here decides anything; the engine never imports it.

**Slippage is measured against the backtest's assumption.** The backtest
entered a funnel trade at the trigger price and an ORB trade at the opening
range level, so those are the reference prices. For ORB that deliberately
includes how far past the level the detector's first tick already was.
Positive basis points always mean "worse than assumed".

**R is before charges.** Stages are judged in R before charges, because at
micro size charges are about twice as large in R as at full size
(Reference/arb_strategy_2026-09/IMPLEMENTATION_IDEA.md section 6.3).
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Optional

from trading_engine_types import STRATEGY_FUNNEL, STRATEGY_ORB

SOURCE_ORB = "ORB"
FUNNEL_SOURCES = ("F0", "F1", "F2", "F3")
SOURCES = (SOURCE_ORB,) + FUNNEL_SOURCES


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _sign(direction: Any) -> float:
    return 1.0 if str(direction).upper() == "UP" else -1.0


def source_label(arb: Optional[Mapping[str, Any]]) -> Optional[str]:
    """"ORB", or "F0".."F3" by funnel score. None for a trade ARB did not stamp."""
    if not arb:
        return None
    strategy = arb.get("strategy")
    if strategy == STRATEGY_ORB:
        return SOURCE_ORB
    if strategy != STRATEGY_FUNNEL:
        return None
    try:
        score = int(arb.get("score"))
    except (TypeError, ValueError):
        return None
    return FUNNEL_SOURCES[max(0, min(score, 3))]


def signal_price(candidate: Mapping[str, Any]) -> Optional[float]:
    """The entry price the backtest assumed for this signal."""
    if candidate.get("strategy") == STRATEGY_ORB:
        level = _number(candidate.get("orb_level"))
        if level is not None and level > 0:
            return level
    price = _number(candidate.get("trigger_price"))
    return price if price is not None and price > 0 else None


def adverse_bps(direction: Any, reference: Any, actual: Any, *, entry: bool) -> Optional[float]:
    """How much worse ``actual`` is than ``reference``, in basis points.

    For an entry, worse means paying more (long) or selling lower (short);
    for an exit it is the opposite.
    """
    ref, got = _number(reference), _number(actual)
    if ref is None or got is None or ref <= 0:
        return None
    paid_more = (got - ref) * _sign(direction)
    return round((paid_more if entry else -paid_more) / ref * 1e4, 2)


def entry_slippage_bps(candidate: Mapping[str, Any], entry_price: Any) -> Optional[float]:
    return adverse_bps(candidate.get("direction"), signal_price(candidate), entry_price, entry=True)


def exit_price(direction: Any, entry_price: Any, qty: Any, realised_pnl: Any) -> Optional[float]:
    """The average exit price implied by a closed trade's gross P&L."""
    entry, pnl = _number(entry_price), _number(realised_pnl)
    try:
        shares = int(qty or 0)
    except (TypeError, ValueError):
        return None
    if entry is None or pnl is None or shares <= 0:
        return None
    return entry + _sign(direction) * pnl / shares


def r_points(entry_price: Any, initial_stop: Any) -> Optional[float]:
    entry, stop = _number(entry_price), _number(initial_stop)
    if entry is None or stop is None:
        return None
    distance = abs(entry - stop)
    return distance if distance > 0 else None


def r_gross(entry_price: Any, initial_stop: Any, qty: Any, pnl: Any) -> Optional[float]:
    """A P&L in R before charges: rupees / (shares x per-share risk at entry)."""
    per_share = r_points(entry_price, initial_stop)
    amount = _number(pnl)
    try:
        shares = int(qty or 0)
    except (TypeError, ValueError):
        return None
    if per_share is None or amount is None or shares <= 0:
        return None
    return round(amount / (per_share * shares), 4)


def stop_exit_slippage_bps(
    direction: Any, stop_price: Any, entry_price: Any, qty: Any, realised_pnl: Any
) -> Optional[float]:
    """For a trade closed by its stop: how far past the stop it filled."""
    got = exit_price(direction, entry_price, qty, realised_pnl)
    return adverse_bps(direction, stop_price, got, entry=False)


def entry_slippage_split(candidate: Mapping[str, Any], entry_price: Any) -> Optional[dict]:
    """Where an entry's slippage happened, in bps against the signal price.

    ``at_signal``: how far past the signal price the last traded price already
    was when the signal fired (nothing the engine could have done about it).
    ``after_signal``: the rest, from the signal to Kite's fill. The two add up
    to ``entry_slippage_bps``. None when the signal carried no last price.
    """
    total = entry_slippage_bps(candidate, entry_price)
    at_signal = adverse_bps(
        candidate.get("direction"), signal_price(candidate), candidate.get("last_price"), entry=True
    )
    if total is None or at_signal is None:
        return None
    return {"at_signal": at_signal, "after_signal": round(total - at_signal, 2)}


def entry_slippage_rupees(candidate: Mapping[str, Any], entry_price: Any, qty: Any) -> Optional[float]:
    """The entry slippage in rupees: (fill - signal price) x shares, worse = positive."""
    reference, entry = signal_price(candidate), _number(entry_price)
    try:
        shares = int(qty or 0)
    except (TypeError, ValueError):
        return None
    if reference is None or entry is None or shares <= 0:
        return None
    return round((entry - reference) * _sign(candidate.get("direction")) * shares, 2)


def _seconds_between(start: Any, end: Any) -> Optional[float]:
    from datetime import datetime

    try:
        a, b = datetime.fromisoformat(str(start)), datetime.fromisoformat(str(end))
    except (TypeError, ValueError):
        return None
    if (a.tzinfo is None) != (b.tzinfo is None):
        return None
    return round((b - a).total_seconds(), 2)


def entry_timing(candidate: Mapping[str, Any], decided_at: Any, filled_at: Any) -> dict:
    """Seconds from the exchange's signal tick to the engine, and on to the fill.

    The signal time has one-second resolution, so the first figure is +/- 1 s.
    """
    return {
        "signal_to_engine_s": _seconds_between(candidate.get("trigger_exchange_ts"), decided_at),
        "engine_to_fill_s": _seconds_between(decided_at, filled_at),
    }
