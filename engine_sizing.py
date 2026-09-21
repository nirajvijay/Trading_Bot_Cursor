"""Position sizing policy.

qty is the tighter of two independent caps: risk-based and capital-based.
No notional/aggregate-exposure cap — dropped as redundant, since
available_capital_rupees is already "what's left after other open
positions," so capital-based sizing inherently limits total exposure on
its own.

The risk cap covers price risk only (|entry - stop|) — trading costs
(brokerage, estimated slippage) are a real, additional expense on top of
it, not subtracted from it. A trade sized to the cap can cost slightly
more than the cap once costs are included; that's accepted, not sized
around.
"""
from __future__ import annotations

import math
from typing import Protocol

from engine_types import SizeDecision


class SizingPolicy(Protocol):
    def decide(
        self,
        *,
        entry_price: float,
        stop_price: float,
        risk_cap_rupees: float,
        available_capital_rupees: float,
        leverage_factor: float,
    ) -> SizeDecision: ...


class RiskCappedSizing:
    def decide(
        self,
        *,
        entry_price: float,
        stop_price: float,
        risk_cap_rupees: float,
        available_capital_rupees: float,
        leverage_factor: float,
    ) -> SizeDecision:
        risk_per_share = abs(entry_price - stop_price)
        if risk_per_share <= 0 or entry_price <= 0:
            return SizeDecision(0, 0, 0, "risk")

        risk_based_qty = math.floor(risk_cap_rupees / risk_per_share)
        capital_based_qty = math.floor(
            (available_capital_rupees * leverage_factor) / entry_price
        )

        if risk_based_qty <= capital_based_qty:
            qty, binding = risk_based_qty, "risk"
        else:
            qty, binding = capital_based_qty, "capital"

        return SizeDecision(
            qty=max(0, qty),
            risk_based_qty=risk_based_qty,
            capital_based_qty=capital_based_qty,
            binding_constraint=binding,
        )
