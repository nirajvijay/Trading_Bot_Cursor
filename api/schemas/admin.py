"""Pydantic schemas for Admin Console V1 API."""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field, model_validator


class AdminConfigValues(BaseModel):
    model_config = {"extra":"forbid", "allow_inf_nan":False}
    auto_trail_default_enabled: int = Field(1, ge=0, le=1)
    per_trade_risk_cap_inr: float = Field(..., gt=0, le=2000)
    limited_per_trade_risk_cap_inr: float = Field(..., gt=0)
    daily_loss_cap_inr: float = Field(..., gt=0, le=50000)
    vwap_accept_gap_exclusive_max: float = Field(..., ge=0)
    vwap_limited_gap_inclusive_max: float = Field(..., gt=0, le=0.01)
    allocated_capital_inr: float = Field(300000, gt=0)
    max_concurrent_positions: int = Field(2, ge=1, le=20)
    max_filled_setups_per_day: int = Field(5, ge=1, le=50)
    one_position_or_unresolved_entry_per_symbol: float = Field(1, ge=0, le=1)
    aggregate_notional_cap_equals_allocated_capital: float = Field(1, ge=0, le=1)
    round_trip_charge_bps: float = Field(0, ge=0, le=100)
    estimated_slippage_bps: float = Field(0, ge=0, le=100)
    protection_confirm_deadline_seconds: float = Field(5, ge=1, le=120)
    entry_remainder_cancel_seconds: float = Field(5, ge=1, le=120)
    entry_cutoff_ist: float = 1445
    square_off_ist: float = 1515
    setup_expiry_seconds: float = Field(30, gt=0, le=300)
    max_quote_age_seconds: float = Field(2, gt=0, le=30)
    max_entry_drift_r: float = Field(.1, gt=0, le=1)

    @model_validator(mode="after")
    def _cross_field(self) -> "AdminConfigValues":
        if self.limited_per_trade_risk_cap_inr > self.per_trade_risk_cap_inr:
            raise ValueError("limited_per_trade_risk_cap_inr must be <= per_trade_risk_cap_inr")
        if self.vwap_limited_gap_inclusive_max <= self.vwap_accept_gap_exclusive_max:
            raise ValueError("vwap_limited_gap_inclusive_max must exceed accept threshold")
        return self


class AdminConfigPatchRequest(BaseModel):
    values: AdminConfigValues
    expected_version_id: Optional[str] = None
    comment: Optional[str] = None


class AdminConfigResponse(BaseModel):
    version_id: str
    entries_paused: bool
    values: AdminConfigValues
    vwap_accept_gap_percent: float
    vwap_limited_gap_percent: float
    warnings: List[str] = Field(default_factory=list)
    accepting_triggers: bool = False
    engine_running: bool = False
    effective_values: dict[str, float] = Field(default_factory=dict)
    effective_version_id: Optional[str] = None
    pending_next_arm: List[str] = Field(default_factory=list)


class AdminRollbackRequest(BaseModel):
    target_version_id: str


class AdminAuditEntry(BaseModel):
    id: int
    at: str
    actor_username: str
    action: str
    version_id: Optional[str] = None
    from_version_id: Optional[str] = None
    diff_json: str
    result: str
    detail: Optional[str] = None
    step_up_verified: bool = True


class AdminAuditResponse(BaseModel):
    entries: List[AdminAuditEntry]
    limit: int
    offset: int


class AdminActionResponse(BaseModel):
    success: bool
    message: str
    entries_paused: Optional[bool] = None
    detail: Optional[str] = None
    command_id: Optional[int] = None
    state: Optional[str] = None
