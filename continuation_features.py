"""Pure continuation feature math: tick units, reach, volume confirmation."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from statistics import mean
from typing import Optional, Sequence, Tuple

from spike_types import SpikeDirection


def _tick_decimal(tick_size: float) -> Decimal:
    return Decimal(str(tick_size))


def price_to_ticks(price: float, tick_size: float) -> int:
    """
    Convert a price to integer tick units on the exchange grid.

    Uses decimal half-up rounding. Raises if tick_size is non-positive or
    the price is not on-grid after rounding (within a tiny residual).
    """
    if tick_size <= 0:
        raise ValueError("tick_size must be positive")
    tick = _tick_decimal(tick_size)
    px = Decimal(str(price))
    ticks = (px / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    reconstructed = ticks * tick
    if abs(reconstructed - px) > tick * Decimal("0.0000001"):
        # Allow tiny float representation noise only.
        residual = abs(px - reconstructed)
        if residual > tick * Decimal("0.001"):
            raise ValueError(
                "price %s is not on tick grid tick_size=%s" % (price, tick_size)
            )
    return int(ticks)


def ticks_to_price(ticks: int, tick_size: float) -> float:
    if tick_size <= 0:
        raise ValueError("tick_size must be positive")
    return float(Decimal(ticks) * _tick_decimal(tick_size))


def compute_trigger_ticks(
    *,
    direction: SpikeDirection,
    swing_high: Optional[float],
    swing_low: Optional[float],
    tick_size: float,
    buffer_ticks: int,
) -> Tuple[int, float]:
    """Return (trigger_price_ticks, trigger_price)."""
    if direction == "UP":
        if swing_high is None:
            raise ValueError("UP setup requires pullback_swing_high")
        swing_ticks = price_to_ticks(swing_high, tick_size)
        trigger_ticks = swing_ticks + buffer_ticks
    elif direction == "DOWN":
        if swing_low is None:
            raise ValueError("DOWN setup requires pullback_swing_low")
        swing_ticks = price_to_ticks(swing_low, tick_size)
        trigger_ticks = swing_ticks - buffer_ticks
    else:
        raise ValueError("direction must be UP or DOWN")
    return trigger_ticks, ticks_to_price(trigger_ticks, tick_size)


def price_reached_trigger(
    *,
    direction: SpikeDirection,
    last_price: float,
    tick_size: float,
    trigger_price_ticks: int,
) -> bool:
    last_ticks = price_to_ticks(last_price, tick_size)
    if direction == "UP":
        return last_ticks >= trigger_price_ticks
    if direction == "DOWN":
        return last_ticks <= trigger_price_ticks
    return False


def average_prior_volumes(volumes: Sequence[int]) -> Optional[float]:
    if not volumes:
        return None
    return float(mean(volumes))


def volume_confirms(
    *,
    in_progress_volume: int,
    prior_volumes: Sequence[int],
    required_count: int,
) -> Tuple[bool, Optional[float], Optional[str]]:
    """
    Returns (ok, avg_prior, fail_reason).

    fail_reason is insufficient_volume_history or failed_breakout_volume_confirmation.
    """
    if len(prior_volumes) < required_count:
        return False, None, "insufficient_volume_history"
    avg = average_prior_volumes(prior_volumes)
    assert avg is not None
    if in_progress_volume > avg:
        return True, avg, None
    return False, avg, "failed_breakout_volume_confirmation"
