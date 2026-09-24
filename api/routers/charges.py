"""The Charges tab: Kite's charges and net P&L per closed trade.

Display only. Reads the engine database read-only and keeps its own cache;
nothing here reaches the engine or the Execution Desk, and the daily loss cap
is unaffected.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query

import engine_clock
from api import config
from api.auth.deps import require_web_session
from api.schemas.charges import ChargeBreakdown, ChargesResponse, TradeChargesView
from api.services.trade_charges import STATUS_OK, TradeCharges, compute_day

router = APIRouter(prefix="/charges", tags=["charges"])


def _today() -> str:
    return engine_clock.to_ist().strftime("%Y-%m-%d")


def _r(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 2)


def _view(item: TradeCharges) -> TradeChargesView:
    trade = item.trade
    charges = (
        ChargeBreakdown(**{k: round(v, 2) for k, v in item.charges.items()})
        if item.charges is not None
        else None
    )
    return TradeChargesView(
        trade_id=trade.trade_id,
        tradingsymbol=trade.tradingsymbol,
        direction=trade.direction,
        qty=trade.qty,
        closed_at=trade.closed_at,
        close_reason=trade.close_reason,
        entry_avg=_r(item.entry_avg),
        exit_avg=_r(item.exit_avg),
        gross_pnl=_r(trade.realised_pnl),
        charges=charges,
        net_pnl=_r(item.net_pnl),
        status=item.status,
        reason=item.reason,
    )


@router.get("", response_model=ChargesResponse, dependencies=[Depends(require_web_session)])
def trade_charges(
    session_date: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> ChargesResponse:
    today = _today()
    day = compute_day(
        session_date or today,
        today=today,
        engine_db=config.execution_engine_db_path(),
        cache_db=config.trade_charges_db_path(),
    )
    live = [t for t in day.trades if t.trade.is_live]
    # Totals only over trades with both a gross and charges, so the three
    # numbers always reconcile: gross - charges == net.
    priced: List[TradeCharges] = [
        t for t in live if t.status == STATUS_OK and t.net_pnl is not None
    ]
    gross = sum(float(t.trade.realised_pnl or 0.0) for t in priced)
    charges = sum(float(t.total_charges or 0.0) for t in priced)
    return ChargesResponse(
        session_date=day.session_date,
        trades=[_view(t) for t in day.trades],
        total_gross_pnl=round(gross, 2),
        total_charges=round(charges, 2),
        total_net_pnl=round(gross - charges, 2),
        priced_count=len(priced),
        live_closed_count=len(live),
        kite_error=day.kite_error,
    )
