"""Pure continuation rule evaluation — no I/O."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from continuation_features import price_reached_trigger, volume_confirms
from intraday_continuation_config import IntradayContinuationRuleConfig
from spike_types import SpikeDirection


@dataclass(frozen=True)
class ContinuationVolumeDecision:
    outcome: str  # "triggered" | "rejected"
    reason: Optional[str]
    avg_prior_3_1m_volume: Optional[float]
    volume_ok: bool
    volume_reliable: bool


def evaluate_price_reach(
    *,
    direction: SpikeDirection,
    last_price: float,
    tick_size: float,
    trigger_price_ticks: int,
) -> bool:
    return price_reached_trigger(
        direction=direction,
        last_price=last_price,
        tick_size=tick_size,
        trigger_price_ticks=trigger_price_ticks,
    )


def evaluate_volume_confirmation(
    *,
    in_progress_volume: int,
    prior_volumes: Sequence[int],
    volume_reliable: bool,
    config: IntradayContinuationRuleConfig,
) -> ContinuationVolumeDecision:
    if not volume_reliable:
        return ContinuationVolumeDecision(
            outcome="rejected",
            reason="unreliable_breakout_volume",
            avg_prior_3_1m_volume=None,
            volume_ok=False,
            volume_reliable=False,
        )
    ok, avg, fail_reason = volume_confirms(
        in_progress_volume=in_progress_volume,
        prior_volumes=prior_volumes,
        required_count=config.prior_completed_1m_count,
    )
    if ok:
        return ContinuationVolumeDecision(
            outcome="triggered",
            reason=None,
            avg_prior_3_1m_volume=avg,
            volume_ok=True,
            volume_reliable=True,
        )
    return ContinuationVolumeDecision(
        outcome="rejected",
        reason=fail_reason or "failed_breakout_volume_confirmation",
        avg_prior_3_1m_volume=avg,
        volume_ok=False,
        volume_reliable=True,
    )


class IntradayContinuationRuleEngine:
    def __init__(self, config: Optional[IntradayContinuationRuleConfig] = None) -> None:
        self._config = config if config is not None else IntradayContinuationRuleConfig()

    @property
    def config(self) -> IntradayContinuationRuleConfig:
        return self._config

    def price_reached(
        self,
        *,
        direction: SpikeDirection,
        last_price: float,
        tick_size: float,
        trigger_price_ticks: int,
    ) -> bool:
        return evaluate_price_reach(
            direction=direction,
            last_price=last_price,
            tick_size=tick_size,
            trigger_price_ticks=trigger_price_ticks,
        )

    def volume_decision(
        self,
        *,
        in_progress_volume: int,
        prior_volumes: Sequence[int],
        volume_reliable: bool,
    ) -> ContinuationVolumeDecision:
        return evaluate_volume_confirmation(
            in_progress_volume=in_progress_volume,
            prior_volumes=prior_volumes,
            volume_reliable=volume_reliable,
            config=self._config,
        )
