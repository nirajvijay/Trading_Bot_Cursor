"""Response shapes for the /charges API (the Charges tab). Display only."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class ChargeBreakdown(BaseModel):
    brokerage: float
    stt: float
    exchange: float
    sebi: float
    stamp_duty: float
    gst: float
    total: float


class TradeChargesView(BaseModel):
    trade_id: str
    tradingsymbol: str
    direction: str
    qty: int
    closed_at: Optional[str] = None
    close_reason: Optional[str] = None
    entry_avg: Optional[float] = None
    exit_avg: Optional[float] = None
    # The engine's realised P&L, unchanged: Kite's fills, before charges.
    gross_pnl: Optional[float] = None
    charges: Optional[ChargeBreakdown] = None
    net_pnl: Optional[float] = None
    # ok | paper | unavailable
    status: str
    reason: Optional[str] = None


class ChargesResponse(BaseModel):
    session_date: str
    trades: List[TradeChargesView] = Field(default_factory=list)
    # Over live trades whose charges are known, so gross - charges == net.
    total_gross_pnl: float = 0.0
    total_charges: float = 0.0
    total_net_pnl: float = 0.0
    priced_count: int = 0
    live_closed_count: int = 0
    kite_error: Optional[str] = None
