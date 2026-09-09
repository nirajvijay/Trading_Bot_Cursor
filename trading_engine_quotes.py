"""Fresh, bounded touch-price decisions shared by preview and execution."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import math


@dataclass(frozen=True)
class TouchQuote:
    bid: float | None
    ask: float | None
    as_of: str | None


@dataclass(frozen=True)
class EntryLimitDecision:
    price: float | None
    reason: str | None
    signal_age: float | None = None
    quote_age: float | None = None


def age_seconds(now: datetime, stamp: str | None) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if parsed.tzinfo is None or now.tzinfo is None:
            return None
        age = (now.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
        return age if math.isfinite(age) else None
    except (TypeError, ValueError, OverflowError):
        return None


def bounded_entry_limit(*, now: datetime, trigger_time: str | None,
                        trigger: float, stop: float, tick: float, direction: str,
                        quote: TouchQuote | None, expiry: float = 30,
                        max_quote_age: float = 2, drift_r: float = .1) -> EntryLimitDecision:
    values = (trigger, stop, tick, expiry, max_quote_age, drift_r)
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        return EntryLimitDecision(None, "entry_inputs_invalid")
    if min(trigger, stop, tick, expiry, max_quote_age) <= 0 or drift_r < 0 or direction not in {"UP", "DOWN"}:
        return EntryLimitDecision(None, "entry_inputs_invalid")
    risk = abs(trigger - stop)
    if risk <= 0 or (direction == "UP" and stop >= trigger) or (direction == "DOWN" and stop <= trigger):
        return EntryLimitDecision(None, "structural_stop_invalid")
    signal_age = age_seconds(now, trigger_time)
    if signal_age is None or not 0 <= signal_age <= expiry:
        return EntryLimitDecision(None, "signal_expired_or_invalid", signal_age)
    quote_age = age_seconds(now, quote.as_of) if quote else None
    if quote_age is None or not 0 <= quote_age <= max_quote_age:
        return EntryLimitDecision(None, "quote_stale_or_invalid", signal_age, quote_age)
    touch = quote.ask if direction == "UP" else quote.bid
    if touch is None or not math.isfinite(touch) or touch <= 0:
        return EntryLimitDecision(None, "quote_touch_missing", signal_age, quote_age)
    if quote.bid is not None and quote.ask is not None and quote.bid > quote.ask:
        return EntryLimitDecision(None, "quote_crossed", signal_age, quote_age)
    if abs(touch - trigger) / risk > drift_r + 1e-12:
        return EntryLimitDecision(None, "entry_drift_exceeded", signal_age, quote_age)
    t, p, r, d, q = map(lambda v: Decimal(str(v)), (tick, trigger, risk, drift_r, touch))
    if direction == "UP":
        price = (q / t).to_integral_value(rounding=ROUND_CEILING) * t
        bound = ((p + r * d) / t).to_integral_value(rounding=ROUND_FLOOR) * t
        allowed = price <= bound
    else:
        price = (q / t).to_integral_value(rounding=ROUND_FLOOR) * t
        bound = ((p - r * d) / t).to_integral_value(rounding=ROUND_CEILING) * t
        allowed = price >= bound
    return EntryLimitDecision(float(price) if allowed else None,
                              None if allowed else "entry_tick_bound_unsatisfiable", signal_age, quote_age)
