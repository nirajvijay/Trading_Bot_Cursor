"""Risk enforcement: daily loss cap and per-trade cap lookup.

Deliberately does not import trading_engine_risk.TradeRecord-coupled helpers
(closed_loss_today, blocks_new_entries) — those infer state from status
strings on the old TradeRecord. The new engine has explicit ExecutionState,
so "is this trade unprotected / blocking entries" is a state check, not a
string-matching inference. This module only owns loss arithmetic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from engine_types import Position, RiskLimits


@dataclass(frozen=True)
class DailyLossCheck:
    breached: bool
    closed_loss_rupees: float
    cap_rupees: float


class RiskPolicy:
    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits

    def per_trade_cap(self, *, vwap_limited: bool) -> float:
        return (
            self.limits.per_trade_cap_vwap_limited_rupees
            if vwap_limited
            else self.limits.per_trade_cap_rupees
        )

    def check_daily_loss(self, positions: Sequence[Position]) -> DailyLossCheck:
        closed_loss = sum(
            -p.realised_pnl for p in positions if p.realised_pnl is not None and p.realised_pnl < 0
        )
        return DailyLossCheck(
            breached=closed_loss >= self.limits.daily_loss_cap_rupees,
            closed_loss_rupees=closed_loss,
            cap_rupees=self.limits.daily_loss_cap_rupees,
        )
