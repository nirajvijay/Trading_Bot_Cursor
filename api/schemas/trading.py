"""Pydantic schemas for the trading-engine API."""

from __future__ import annotations

from typing import Any, List, Optional, Literal

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


class TradingAutoTrailRequest(BaseModel):
    enabled: bool


class TradingCommandRequest(BaseModel):
    model_config = {"extra":"forbid", "allow_inf_nan":False}
    client_command_id: str = Field(..., min_length=8, max_length=128)
    kind: Literal["arm_session","approve_entry","pause_entries","disarm","stop_engine",
                  "switch_manual","close_position","close_all","trail_stop","set_auto_trail","reconcile_now"]
    trade_id: Optional[str] = None
    setup_id: Optional[str] = None
    continuation_rule_version: Optional[str] = None
    execution_mode: Literal["PAPER","LIVE"] = "PAPER"
    entry_mode: Literal["MANUAL","AUTOPILOT"] = "MANUAL"
    live_confirmation: bool = False
    config_version_id: Optional[str] = None
    qty_override: Optional[int] = Field(None, strict=True, gt=0)
    stop_tighten: Optional[float] = Field(None, gt=0)
    new_stop: Optional[float] = Field(None, gt=0)
    enabled: Optional[bool] = None


class TradingPreviewRequest(BaseModel):
    model_config = {"extra":"forbid", "allow_inf_nan":False}
    setup_id: str
    continuation_rule_version: str
    qty_override: Optional[int] = Field(None, strict=True, gt=0)
    stop_tighten: Optional[float] = Field(None, gt=0)


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
    accepting_triggers: bool = False
    require_vwap_accept: bool = True


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
    accepting_triggers: bool = False
    active: List[dict[str, Any]]
    closed: List[dict[str, Any]]
    skipped: List[dict[str, Any]]
    require_vwap_accept: bool = True
