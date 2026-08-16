"""Pydantic schemas for observation start/readiness API."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from api.schemas.checklist import ChecklistStatus


class ObservationReadinessResponse(BaseModel):
    checklist_ok: bool
    checklist_status: ChecklistStatus
    market_open: bool
    runner_running: bool
    can_start: bool
    reason: str = ""
    session_date: str
    expected_stop_at: Optional[str] = None


class ObservationStartResponse(BaseModel):
    success: bool
    message: str
    pid: Optional[int] = None
