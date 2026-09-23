"""The structural stop price.

Extracted from the old engine's trading_engine_risk, which is otherwise dead:
the rest of that module was TradeRecord-coupled sizing (replaced by
engine_sizing) and staged-R trailing math (deliberately dropped, to be
redesigned from scratch).

The stop comes from market structure and nothing else. It is computed once,
before the entry order is ever sent, and then never recalculated off the fill
price and never moved to make a risk number fit. A stop dragged inward to
satisfy arithmetic has no relationship to structure any more and is close to
guaranteed to be taken out by ordinary noise — which converts a slippage
problem into a near-certain, meaningless loss.
"""
from __future__ import annotations

from typing import Optional

from continuation_features import price_to_ticks, ticks_to_price


def structural_stop_price(
    *,
    direction: str,
    swing_high: Optional[float],
    swing_low: Optional[float],
    tick_size: float,
    buffer_ticks: int,
) -> Optional[float]:
    """Long: buffer ticks below the swing low. Short: above the swing high.

    Returns None when the inputs cannot produce a valid price, which the
    caller treats as "do not enter" rather than substituting a guess.
    """
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
