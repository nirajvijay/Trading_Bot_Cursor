"""
Convert Kite Connect WebSocket tick dictionaries into internal tick models.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from tick_event import IST, Ohlc, TickEvent

_IST = ZoneInfo(IST)


@dataclass(frozen=True)
class NormalizedTick:
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


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _ensure_ist(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_IST)
    return dt.astimezone(_IST)


def _parse_ohlc(raw: Dict[str, Any], last_price: float) -> Optional[Ohlc]:
    ohlc_raw = raw.get("ohlc")
    if not isinstance(ohlc_raw, dict):
        return Ohlc(
            open=last_price,
            high=last_price,
            low=last_price,
            close=last_price,
        )

    open_price = _to_float(ohlc_raw.get("open"))
    high = _to_float(ohlc_raw.get("high"))
    low = _to_float(ohlc_raw.get("low"))
    close = _to_float(ohlc_raw.get("close"))

    if None in (open_price, high, low, close):
        return None

    return Ohlc(open=open_price, high=high, low=low, close=close)


def normalize_kite_tick(
    raw: Dict[str, Any],
    *,
    received_at: datetime,
) -> Optional[NormalizedTick]:
    """Parse a Kite MODE_FULL tick dict. Returns None on invalid input."""
    if not isinstance(raw, dict):
        return None

    instrument_token = raw.get("instrument_token")
    if instrument_token is None:
        return None
    try:
        token = int(instrument_token)
    except (TypeError, ValueError):
        return None

    last_price = _to_float(raw.get("last_price"))
    if last_price is None or last_price <= 0:
        return None

    ohlc = _parse_ohlc(raw, last_price)
    if ohlc is None:
        return None

    received_ist = _ensure_ist(received_at)
    if received_ist is None:
        return None

    exchange_ts = _ensure_ist(raw.get("exchange_timestamp"))
    last_trade_ts = _ensure_ist(raw.get("last_trade_time"))
    exchange_timestamp = exchange_ts or last_trade_ts or received_ist

    return NormalizedTick(
        instrument_token=token,
        last_price=last_price,
        exchange_timestamp=exchange_timestamp,
        received_at=received_ist,
        volume_traded=_to_int(raw.get("volume_traded")),
        last_traded_quantity=_to_int(raw.get("last_traded_quantity")),
        average_traded_price=_to_float(raw.get("average_traded_price")) or 0.0,
        ohlc=ohlc,
        change_pct=_to_float(raw.get("change")),
        last_trade_time=last_trade_ts,
    )


def to_tick_event(normalized: NormalizedTick, sequence: int) -> TickEvent:
    return TickEvent(
        sequence=sequence,
        instrument_token=normalized.instrument_token,
        last_price=normalized.last_price,
        exchange_timestamp=normalized.exchange_timestamp,
        received_at=normalized.received_at,
        volume_traded=normalized.volume_traded,
        last_traded_quantity=normalized.last_traded_quantity,
        average_traded_price=normalized.average_traded_price,
        ohlc=normalized.ohlc,
        change_pct=normalized.change_pct,
        last_trade_time=normalized.last_trade_time,
    )
