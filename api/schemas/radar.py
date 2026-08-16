"""Pydantic schemas for radar API responses."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

RunnerState = Literal["running", "stopped"]


class RadarRow(BaseModel):
    symbol: str
    instrument_token: Optional[int] = None
    last_1m_close: Optional[float] = None
    pct_change: Optional[float] = None
    phase: str
    direction: Optional[str] = None
    spike: str = "-"
    pullback: str = "-"
    continuation: str = "-"
    volume: Optional[int] = None
    trigger_price: Optional[float] = None
    distance_pct: Optional[float] = None
    last_event: str = "-"
    updated_at: Optional[str] = None
    setup_count: int = 0


class RadarResponse(BaseModel):
    session_date: str
    rows: List[RadarRow]


class SessionCoverage(BaseModel):
    session_date: str
    subscribed: int
    tokens_with_1m: int
    tokens_with_5m: int
    baseline_as_of: Optional[str] = None
    spikes: int = 0
    setups: int = 0
    continuation_arms: int = 0
    continuation_decisions: int = 0
    continuation_successful: int = 0
    continuation_failed: int = 0


class RunnerStatus(BaseModel):
    session_date: Optional[str] = None
    subscribed_tokens: Optional[int] = None
    feed_status: Optional[str] = None
    last_tick_time: Optional[str] = None
    updated_at: Optional[str] = None
    runner_state: RunnerState = "stopped"


class HealthResponse(BaseModel):
    status: str
    live_db: bool
    instruments_db: bool
    baselines_db: bool


class InstrumentRow(BaseModel):
    tradingsymbol: str
    instrument_token: int
    exchange: Optional[str] = None
