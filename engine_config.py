"""Session risk configuration: the caps and capital a run is launched with.

Read once, at the moment start is clicked, and fixed for the entire session.
The running engine never re-reads this — changing a cap requires a stop and a
fresh start. That avoids the ambiguity of altering a risk number mid-day while
a trade is already sized and open.

Capital is expressed as *capital*, not as broker margin, and leverage is
applied exactly once (in engine_sizing.RiskCappedSizing.decide). Feeding a
broker "available margin" figure in here instead would double-count leverage,
since Kite's available margin already reflects MIS leverage. Keeping one
multiplication in the system is also why no separate notional/exposure cap is
needed — see Reference/execution_engine_rebuild_notes.md, "Position sizing
formula".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from engine_types import Position, RiskLimits

# Defaults for the current account. Both caps and total capital are editable at
# start (the Execution Desk's start panel); leverage stays 5x unless there is a
# reason to change it.
DEFAULT_TOTAL_CAPITAL_RUPEES = 300_000.0
DEFAULT_LEVERAGE_FACTOR = 5.0
DEFAULT_PER_TRADE_CAP_RUPEES = 900.0
DEFAULT_PER_TRADE_CAP_VWAP_LIMITED_RUPEES = 450.0
DEFAULT_DAILY_LOSS_CAP_RUPEES = 3_000.0


class InvalidSessionConfig(ValueError):
    """A start was attempted with numbers that cannot be traded safely."""


@dataclass(frozen=True)
class SessionRiskConfig:
    per_trade_cap_rupees: float = DEFAULT_PER_TRADE_CAP_RUPEES
    per_trade_cap_vwap_limited_rupees: float = DEFAULT_PER_TRADE_CAP_VWAP_LIMITED_RUPEES
    daily_loss_cap_rupees: float = DEFAULT_DAILY_LOSS_CAP_RUPEES
    total_capital_rupees: float = DEFAULT_TOTAL_CAPITAL_RUPEES
    leverage_factor: float = DEFAULT_LEVERAGE_FACTOR

    @property
    def buying_power_rupees(self) -> float:
        """Total capital geared up: 3,00,000 x 5 = 15,00,000 at the defaults."""
        return self.total_capital_rupees * self.leverage_factor

    def remaining_capital_rupees(self, margin_used_rupees: float) -> float:
        """Capital left after margin already committed to open positions.

        This is what feeds sizing as available_capital_rupees: "what's left
        after other open positions". Floored at zero so an over-committed
        account sizes to zero rather than going negative.
        """
        return max(0.0, self.total_capital_rupees - max(0.0, margin_used_rupees))

    def remaining_buying_power_rupees(self, margin_used_rupees: float) -> float:
        return self.remaining_capital_rupees(margin_used_rupees) * self.leverage_factor

    def to_risk_limits(self) -> RiskLimits:
        """The subset engine_risk.RiskPolicy needs, unchanged."""
        return RiskLimits(
            per_trade_cap_rupees=self.per_trade_cap_rupees,
            per_trade_cap_vwap_limited_rupees=self.per_trade_cap_vwap_limited_rupees,
            daily_loss_cap_rupees=self.daily_loss_cap_rupees,
        )


def margin_used_rupees(positions: Sequence[Position], leverage_factor: float) -> float:
    """Capital committed to positions that currently hold size.

    Notional divided by leverage, which is the MIS margin model. Uses the real
    entry fill once known and the trigger price before that, so a position in
    flight still consumes capital rather than looking free.
    """
    if leverage_factor <= 0:
        return 0.0
    total = 0.0
    for position in positions:
        qty = int(position.qty or 0)
        if qty <= 0:
            continue
        price = position.entry_price or position.candidate.trigger_price
        if not price:
            continue
        total += (qty * float(price)) / float(leverage_factor)
    return total


def validate(config: SessionRiskConfig) -> None:
    """Raise InvalidSessionConfig unless this config is safe to start with.

    The per-trade-exceeds-daily rule is the obvious-mistake guard from the
    testing-strategy decision: a per-trade cap above the daily cap means the
    daily cap can never bind, which is never what anyone means.
    """
    positive_fields = (
        ("per_trade_cap_rupees", config.per_trade_cap_rupees),
        ("per_trade_cap_vwap_limited_rupees", config.per_trade_cap_vwap_limited_rupees),
        ("daily_loss_cap_rupees", config.daily_loss_cap_rupees),
        ("total_capital_rupees", config.total_capital_rupees),
    )
    for name, value in positive_fields:
        if not value > 0:
            raise InvalidSessionConfig(f"{name} must be greater than zero")

    if config.leverage_factor < 1:
        raise InvalidSessionConfig("leverage_factor must be at least 1")

    for name, value in positive_fields[:2]:
        if value > config.daily_loss_cap_rupees:
            raise InvalidSessionConfig(
                f"{name} ({value:g}) must not exceed "
                f"daily_loss_cap_rupees ({config.daily_loss_cap_rupees:g})"
            )
