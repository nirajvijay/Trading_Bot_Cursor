"""Risk enforcement: daily loss cap and per-trade cap lookup.

Deliberately does not import trading_engine_risk.TradeRecord-coupled helpers
(closed_loss_today, blocks_new_entries) — those infer state from status
strings on the old TradeRecord. The new engine has explicit ExecutionState,
so "is this trade unprotected / blocking entries" is a state check, not a
string-matching inference. This module only owns loss arithmetic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence

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


def realised_loss_rupees(positions: Sequence[Position]) -> float:
    """The day's realised loss, as a positive number: Kite's figure.

    Kite is the source of truth for money actually lost. For each stock, the
    day's realised P&L is Kite's own figure, pinned on the closed row that
    left the stock flat (it already covers every trade in that stock up to
    then), and its loss counts as Kite reports it.

    Only a close with no Kite figure falls back to our per-trade number, and
    then only its loss counts: a profit never buys back room under the cap.
    Gains in one stock never offset losses in another.
    """
    by_symbol: Dict[str, List[Position]] = {}
    for position in positions:
        by_symbol.setdefault(position.candidate.tradingsymbol, []).append(position)

    total = 0.0
    for rows in by_symbol.values():
        rows.sort(key=lambda p: (str(p.candidate.created_at or ""), p.trade_id))
        kite_pnl: float = 0.0
        unpinned_loss = 0.0
        for position in rows:
            pinned = (position.extra.get(STOCK_DAY_KEY) or {}).get("kite_pnl")
            if pinned is not None:
                # Kite's figure covers every trade in this stock so far.
                kite_pnl = float(pinned)
                unpinned_loss = 0.0
            elif position.realised_pnl is not None and position.realised_pnl < 0:
                unpinned_loss += -float(position.realised_pnl)
        total += max(0.0, -kite_pnl) + unpinned_loss
    return round(total, 2)
