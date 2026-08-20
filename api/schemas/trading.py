"""Pydantic schemas for the trading-engine API."""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field


class TradingStartRequest(BaseModel):
    confirm_live_orders: bool = False
    session_date: Optional[str] = None
    total_capital: Optional[float] = None


class TradingStartResponse(BaseModel):
    success: bool
    message: str
    pid: Optional[int] = None


class TradingStopResponse(BaseModel):
    success: bool
    message: str


class TradingCapitalRequest(BaseModel):
    total_capital: float = Field(..., gt=0)


class TradingTrailRequest(BaseModel):
    new_stop: float
    last_price: Optional[float] = None


class TradingStatusResponse(BaseModel):
    state: str
    session_date: str
    live_orders_enabled: bool
    live_orders_env_enabled: bool
    unprotected_count: int
    limits_protected: bool
    closed_loss_today: float
    committed_risk: float
    remaining_daily: float
    live_pnl: float
    total_capital: float
    leverage_factor: float
    margin_used: float
    remaining_capital: float
    buying_power: float
    last_error: Optional[str] = None
    engine_running: bool
    can_confirm_live: bool


class TradingSnapshotResponse(BaseModel):
    state: str
    session_date: str
    live_orders_enabled: bool
    unprotected_count: int
    limits_protected: bool
    closed_loss_today: float
    committed_risk: float
    remaining_daily: float
    live_pnl: float
    total_capital: float
    leverage_factor: float
    margin_used: float
    remaining_capital: float
    buying_power: float
    last_error: Optional[str] = None
    active: List[dict[str, Any]]
    closed: List[dict[str, Any]]
    skipped: List[dict[str, Any]]
