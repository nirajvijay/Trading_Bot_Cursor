"""Response shapes for the /strategy API (the Strategy tab). Display only."""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class OrbRules(BaseModel):
    rule_version: str
    range: str
    min_rvol: float
    top_n: int
    stop_atr_mult: float
    # Breakouts count while the clock is before this (HH:MM, IST).
    trigger_end: str
    max_overshoot_atr: float


class OrbStatus(BaseModel):
    # collecting | ranked | done | disabled
    state: str
    reason: Optional[str] = None
    updated_at: str
    restored: bool = False


class OrbBaselineIssue(BaseModel):
    symbol: str
    reason: Optional[str] = None
    detail: Optional[str] = None


class OrbBaselines(BaseModel):
    ready: int = 0
    total: int = 0
    as_of: Optional[str] = None
    not_ready: List[OrbBaselineIssue] = Field(default_factory=list)


class OrbArmView(BaseModel):
    setup_id: str
    symbol: str
    direction: str
    rank: int
    rvol: float
    level: float
    atr14: float
    stop_distance: float
    stop_at_level: Optional[float] = None
    or_open: float
    or_high: float
    or_low: float
    or_close: float
    or_volume: int
    avg_or_volume: float
    # ARMED | TRIGGERED | MISSED | EXPIRED
    state: str
    reason: Optional[str] = None
    decided_at: Optional[str] = None
    trigger_price: Optional[float] = None
    planned_stop: Optional[float] = None
    overshoot: Optional[float] = None
    overshoot_bps: Optional[float] = None


class OrbRankedStock(BaseModel):
    symbol: str
    direction: Optional[str] = None
    rank: Optional[int] = None
    rvol: Optional[float] = None
    level: Optional[float] = None
    reason: Optional[str] = None


class OrbDayResponse(BaseModel):
    session_date: str
    rules: OrbRules
    # None until the detector has started today.
    status: Optional[OrbStatus] = None
    baselines: OrbBaselines
    ranked: bool = False
    ranked_at: Optional[str] = None
    arms: List[OrbArmView] = Field(default_factory=list)
    in_play_not_armed: List[OrbRankedStock] = Field(default_factory=list)
    near_misses: List[OrbRankedStock] = Field(default_factory=list)
    valid_count: int = 0
    # reason -> how many stocks had no usable opening range for it
    unusable: Dict[str, int] = Field(default_factory=dict)


class FunnelTriggerView(BaseModel):
    setup_id: str
    symbol: str
    direction: str
    trigger_time: Optional[str] = None
    trigger_price: float
    stop: Optional[float] = None
    vwap_classification: Optional[str] = None
    volume_ratio: Optional[float] = None
    stop_pct: Optional[float] = None
    is_first_trigger_today: Optional[bool] = None
    align_market_pct: Optional[float] = None
    align_sector_pct: Optional[float] = None
    passes: bool = False
    reason: Optional[str] = None
    score: Optional[int] = None
    score_parts: Dict[str, bool] = Field(default_factory=dict)
    # Full-size rupees for this score, before the ARB budget.
    base_risk: Optional[float] = None


class FunnelFeedResponse(BaseModel):
    session_date: str
    triggers: List[FunnelTriggerView] = Field(default_factory=list)
    # Triggers not scored yet; they fill in over the next refreshes.
    pending: int = 0
