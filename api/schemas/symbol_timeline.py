"""Pydantic schemas for per-symbol event timeline API."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class TimelineEvent(BaseModel):
    sequence_number: int
    event_type: str
    resulting_state: str
    label: str
    evaluation_candle_time: Optional[str] = None
    created_at: str


class TimelineContinuation(BaseModel):
    trigger_price: float
    armed_at: str
    decision: Optional[str] = None
    reason: Optional[str] = None


class TimelineSetup(BaseModel):
    setup_id: str
    direction: str
    spike_candle_time: str
    created_at: str
    final_state: str
    status: str
    events: List[TimelineEvent]
    continuation: Optional[TimelineContinuation] = None


class TimelineSpike(BaseModel):
    candle_time: str
    direction: str
    detected_at: str
    close: float


class SymbolTimelineResponse(BaseModel):
    session_date: str
    symbol: str
    spikes: List[TimelineSpike]
    setups: List[TimelineSetup]
