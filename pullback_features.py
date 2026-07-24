"""Pure pullback feature math and sequence updates."""

from __future__ import annotations

from statistics import median
from typing import Optional, Tuple

from candle_aggregation import CompletedFiveMinuteCandle
from pullback_types import PullbackFeatures, PullbackSequenceState, PullbackType
from spike_types import SpikeDirection


def retracement_percent(
    *,
    direction: SpikeDirection,
    impulse_high: float,
    impulse_low: float,
    pullback_low: float,
    pullback_high: float,
) -> Optional[float]:
    impulse_range = impulse_high - impulse_low
    if impulse_range <= 0:
        return None
    if direction == "UP":
        return (impulse_high - pullback_low) / impulse_range * 100.0
    if direction == "DOWN":
        return (pullback_high - impulse_low) / impulse_range * 100.0
    return None


def ema20_interacted(
    *,
    direction: SpikeDirection,
    lowest_low: float,
    highest_high: float,
    ema20: Optional[float],
) -> bool:
    if ema20 is None:
        return False
    if direction == "UP":
        return lowest_low <= ema20
    if direction == "DOWN":
        return highest_high >= ema20
    return False


def classify_pullback_type(
    *,
    direction: SpikeDirection,
    lowest_low: float,
    highest_high: float,
    ema20: Optional[float],
) -> PullbackType:
    interacted = ema20_interacted(
        direction=direction,
        lowest_low=lowest_low,
        highest_high=highest_high,
        ema20=ema20,
    )
    if ema20 is not None and interacted:
        return "EMA_PULLBACK"
    return "SHALLOW_STRUCTURE_PULLBACK"


def opposing_body_ratio(
    *,
    direction: SpikeDirection,
    open_: float,
    high: float,
    low: float,
    close: float,
) -> Tuple[bool, float]:
    candle_range = high - low
    if candle_range <= 0:
        return False, 0.0
    signed = close - open_
    if direction == "UP":
        opposing = signed < 0
    elif direction == "DOWN":
        opposing = signed > 0
    else:
        return False, 0.0
    body = abs(signed) / candle_range
    return opposing, body


def empty_sequence_after_impulse(
    impulse_high: float,
    impulse_low: float,
) -> PullbackSequenceState:
    return PullbackSequenceState(
        highest_high_since_impulse=impulse_high,
        lowest_low_since_impulse=impulse_low,
        retracement_percent=0.0,
        deepest_retracement_percent=0.0,
        cumulative_pullback_volume=0,
        median_pullback_volume=0.0,
        number_of_opposing_candles=0,
        largest_opposing_body_ratio=0.0,
        last_close=impulse_high,  # placeholder until first pb candle
        pullback_candle_count=0,
        last_eval_5m_candle_time=None,
        spike_extreme_breached=False,
        spike_extreme_breached_at=None,
        ema20_value=None,
        ema20_interacted=False,
        volumes=(),
    )


def update_sequence(
    previous: PullbackSequenceState,
    *,
    direction: SpikeDirection,
    candle: CompletedFiveMinuteCandle,
    impulse_high: float,
    impulse_low: float,
    spike_high: float,
    spike_low: float,
    ema20: Optional[float],
) -> PullbackSequenceState:
    highest = max(previous.highest_high_since_impulse, candle.high)
    lowest = min(previous.lowest_low_since_impulse, candle.low)
    # On first pullback candle, replace impulse-seeded extremes with candle extremes
    # so retracement reflects pullback path (still include this bar).
    if previous.pullback_candle_count == 0:
        highest = candle.high
        lowest = candle.low

    ret = retracement_percent(
        direction=direction,
        impulse_high=impulse_high,
        impulse_low=impulse_low,
        pullback_low=lowest,
        pullback_high=highest,
    )
    retrace = 0.0 if ret is None else ret
    deepest = max(previous.deepest_retracement_percent, retrace)

    volumes = previous.volumes + (candle.volume,)
    opposing, body = opposing_body_ratio(
        direction=direction,
        open_=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
    )
    opp_count = previous.number_of_opposing_candles + (1 if opposing else 0)
    largest_opp = max(previous.largest_opposing_body_ratio, body if opposing else 0.0)

    breached = previous.spike_extreme_breached
    breached_at = previous.spike_extreme_breached_at
    if not breached:
        if direction == "UP" and candle.low < spike_low:
            breached = True
            breached_at = candle.candle_time
        elif direction == "DOWN" and candle.high > spike_high:
            breached = True
            breached_at = candle.candle_time

    interacted = ema20_interacted(
        direction=direction,
        lowest_low=lowest,
        highest_high=highest,
        ema20=ema20,
    ) or previous.ema20_interacted

    return PullbackSequenceState(
        highest_high_since_impulse=highest,
        lowest_low_since_impulse=lowest,
        retracement_percent=retrace,
        deepest_retracement_percent=deepest,
        cumulative_pullback_volume=previous.cumulative_pullback_volume + candle.volume,
        median_pullback_volume=float(median(volumes)) if volumes else 0.0,
        number_of_opposing_candles=opp_count,
        largest_opposing_body_ratio=largest_opp,
        last_close=candle.close,
        pullback_candle_count=previous.pullback_candle_count + 1,
        last_eval_5m_candle_time=candle.candle_time,
        spike_extreme_breached=breached,
        spike_extreme_breached_at=breached_at,
        ema20_value=ema20,
        ema20_interacted=interacted,
        volumes=volumes,
    )


def impulse_close_break(
    *,
    direction: SpikeDirection,
    close: float,
    impulse_high: float,
    impulse_low: float,
) -> bool:
    if direction == "UP":
        return close < impulse_low
    if direction == "DOWN":
        return close > impulse_high
    return False


def build_features(
    *,
    instrument_token: int,
    direction: SpikeDirection,
    candle: CompletedFiveMinuteCandle,
    impulse_high: float,
    impulse_low: float,
    spike_high: float,
    spike_low: float,
    sequence: PullbackSequenceState,
    ema20: Optional[float],
    vwap: Optional[float],
) -> PullbackFeatures:
    impulse_range = impulse_high - impulse_low
    return PullbackFeatures(
        instrument_token=instrument_token,
        direction=direction,
        eval_5m_candle_time=candle.candle_time,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        volume=candle.volume,
        impulse_5m_high=impulse_high,
        impulse_5m_low=impulse_low,
        impulse_range=impulse_range,
        spike_1m_high=spike_high,
        spike_1m_low=spike_low,
        retracement_percent=sequence.retracement_percent,
        deepest_retracement_percent=sequence.deepest_retracement_percent,
        ema20_value=ema20,
        ema20_interacted=sequence.ema20_interacted,
        vwap=vwap,
        spike_extreme_breached=sequence.spike_extreme_breached,
        impulse_close_break=impulse_close_break(
            direction=direction,
            close=candle.close,
            impulse_high=impulse_high,
            impulse_low=impulse_low,
        ),
        pullback_candle_count=sequence.pullback_candle_count,
    )
