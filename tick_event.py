"""
Kite-agnostic tick event model for the live market-data pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

IST = "Asia/Kolkata"


@dataclass(frozen=True)
class Ohlc:
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class TickEvent:
    sequence: int
    instrument_token: int
    last_price: float
    exchange_timestamp: datetime
    received_at: datetime
    volume_traded: int
    last_traded_quantity: int
    average_traded_price: float
    ohlc: Ohlc
    change_pct: Optional[float] = None
    last_trade_time: Optional[datetime] = None


TickCallback = Callable[[TickEvent], None]
FeedLifecycleCallback = Callable[[datetime], None]
