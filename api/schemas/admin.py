"""Pydantic schemas for Admin Console V1 API."""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field, model_validator


class AdminConfigValues(BaseModel):
    per_trade_risk_cap_inr: float = Field(..., gt=0, le=2000)
    limited_per_trade_risk_cap_inr: float = Field(..., gt=0)
    daily_loss_cap_inr: float = Field(..., gt=0, le=50000)
    vwap_accept_gap_exclusive_max: float = Field(..., ge=0)
    vwap_limited_gap_inclusive_max: float = Field(..., gt=0, le=0.01)

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
