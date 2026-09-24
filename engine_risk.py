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
        closed_loss = realised_loss_rupees(positions)
        return DailyLossCheck(
            breached=closed_loss >= self.limits.daily_loss_cap_rupees,
            closed_loss_rupees=closed_loss,
            cap_rupees=self.limits.daily_loss_cap_rupees,
        )


# Kite's own day P&L for a stock, pinned on the closed row that left it flat
# (engine_core.STOCK_DAY_KEY).
STOCK_DAY_KEY = "stock_day"


def trade_loss_rupees(position: Position) -> float:
    """What one closed trade adds to the day's realised loss, as a positive number.

    Our per-trade loss, plus any loss Kite shows for the stock beyond our
    per-trade sum. Kite is the source of truth for money actually lost: when
    its day figure is worse than ours (an engine order sold more than the
    trade held, say), the difference is real and counts toward the cap. When
    Kite's figure is better, nothing is subtracted -- the cap never loosens
    on a number we cannot explain.
    """
    loss = 0.0
    if position.realised_pnl is not None and position.realised_pnl < 0:
        loss = -float(position.realised_pnl)
    stock_day = position.extra.get(STOCK_DAY_KEY) or {}
    diff = stock_day.get("diff")
    if stock_day.get("mismatch") and diff is not None and float(diff) < 0:
        loss += -float(diff)
    return loss


def realised_loss_rupees(positions: Sequence[Position]) -> float:
    """Total realised loss as a positive number. Profits do not offset."""
    return round(sum(trade_loss_rupees(p) for p in positions), 2)
