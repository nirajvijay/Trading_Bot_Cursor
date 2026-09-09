"""Loss-halt accounting, deliberately separate from admission reservations."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
import math

from trading_engine_quotes import TouchQuote, age_seconds
from trading_engine_types import TradeRecord


@dataclass(frozen=True)
class LossSlice:
    realised_net: float
    unrealised: float | None
    realised_complete: bool
    complete: bool
    reason: str | None = None


def trade_loss_slice(trade: TradeRecord, quote: TouchQuote | None, now: datetime,
                     max_quote_age: float = 2) -> LossSlice:
    """Confirmed realised price P&L less stamped charges once; open liquidation MTM.

    Charges are estimates from the immutable trade profile, not broker tax invoices.
    No estimated slippage is added to confirmed executions. Admission reservations
    are not included in this metric and profits may offset losses here only.
    """
    filled, exited, remaining = trade.filled_qty, trade.exited_qty, trade.remaining_position_qty
    if filled <= 0:
        return LossSlice(0., 0., True, True)
    priced = (trade.entry_value_est <= 0 and trade.entry_value > 0
              and math.isfinite(trade.entry_value))
    entry = trade.entry_value / filled if priced else None
    confirmed = trade.exit_confirmed_qty
    if confirmed is None:
        confirmed = exited if trade.exit_value > 0 and trade.exit_value_est <= 0 else 0
    confirmed = min(exited, max(0, confirmed))
    realised = float(trade.halt_realised_net or 0.)
    realised_complete = exited == 0
    if exited == 0:
        realised = 0.
    elif priced and confirmed > 0 and math.isfinite(trade.exit_value) and trade.exit_value > 0:
        sign = 1 if trade.direction == "UP" else -1
        realised = sign * (trade.exit_value - entry * confirmed)
        charge = trade.charge_bps
        if charge is None or not math.isfinite(charge) or charge < 0:
            return LossSlice(float(trade.halt_realised_net or 0.), None, False, False, "charges_unknown")
        realised -= entry * confirmed * charge / 10000
        realised_complete = confirmed == exited and trade.exit_value_est <= 0
    if not priced:
        return LossSlice(realised, None, realised_complete, False, "entry_value_unresolved")
    if remaining <= 0:
        return LossSlice(realised, 0., realised_complete, realised_complete,
                         None if realised_complete else "exit_value_unresolved")
    age = age_seconds(now, quote.as_of) if quote else None
    touch = (quote.bid if trade.direction == "UP" else quote.ask) if quote else None
    if age is None or not 0 <= age <= max_quote_age or touch is None or not math.isfinite(touch) or touch <= 0:
        return LossSlice(realised, None, realised_complete, False, "liquidation_quote_unavailable")
    if quote.bid is not None and quote.ask is not None and quote.bid > quote.ask:
        return LossSlice(realised, None, realised_complete, False, "liquidation_quote_crossed")
    sign = 1 if trade.direction == "UP" else -1
    unrealised = sign * remaining * (touch - entry)
    return LossSlice(realised, unrealised, realised_complete, realised_complete,
                     None if realised_complete else "exit_value_unresolved")
