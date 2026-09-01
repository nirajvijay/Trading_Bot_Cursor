"""
Per-token session VWAP minute ledger for vwap_qualifier_v2.

Live candles replace historical for the same minute. No double-counting.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from candle_aggregation import CompletedOneMinuteCandle, ensure_ist
from tick_event import IST
from vwap_qualifier_v2_features import hlc3_pv
from vwap_qualifier_v2_types import MinuteLedgerEntry, MinuteProvenance, VwapQualityReason
from vwap_snapshot import VwapSnapshot

_IST = ZoneInfo(IST)


def session_open_dt(session_date: str) -> datetime:
    y, m, d = (int(p) for p in session_date.split("-"))
    return datetime(y, m, d, 9, 15, 0, tzinfo=_IST)


def last_completed_minute_start(ts: datetime) -> datetime:
    """Start of the last fully completed 1m candle at trigger time."""
    floored = ensure_ist(ts).replace(second=0, microsecond=0)
    return floored - timedelta(minutes=1)


def validate_live_candle(candle: CompletedOneMinuteCandle) -> bool:
    if not candle.has_full_minute_coverage:
        return False
    if not candle.volume_reliable:
        return False
    if candle.volume < 0:
        return False
    for val in (candle.open, candle.high, candle.low, candle.close):
        if not math.isfinite(val):
            return False
    return True


def validate_historical_ohlc(
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: int,
) -> bool:
    if volume < 0:
        return False
    for val in (open_, high, low, close):
        if not math.isfinite(val):
            return False
    return True


@dataclass
class _TokenLedger:
    session_date: str
    minutes: Dict[datetime, MinuteLedgerEntry] = field(default_factory=dict)
    cum_pv: float = 0.0
    cum_volume: int = 0
    historical_count: int = 0
    live_count: int = 0
    last_update: Optional[datetime] = None
    volume_reset_detected: bool = False

    def _recompute_cumulants(self) -> None:
        pv = 0.0
        vol = 0
        hist = 0
        live = 0
        for entry in self.minutes.values():
            pv += entry.pv
            vol += entry.volume
            if entry.provenance == "historical":
                hist += 1
            else:
                live += 1
        self.cum_pv = pv
        self.cum_volume = vol
        self.historical_count = hist
        self.live_count = live

    def _apply_entry(self, entry: MinuteLedgerEntry, *, allow_replace: bool) -> bool:
        existing = self.minutes.get(entry.minute)
        if existing is not None:
            if existing.provenance == "live" and entry.provenance == "historical":
                return False
            if not allow_replace and existing.provenance == entry.provenance:
                return False
            self.minutes.pop(entry.minute)
        self.minutes[entry.minute] = entry
        self._recompute_cumulants()
        self.last_update = entry.minute
        return True

    def apply_live(self, candle: CompletedOneMinuteCandle) -> bool:
        if not validate_live_candle(candle):
            return False
        minute = ensure_ist(candle.candle_time).replace(second=0, microsecond=0)
        pv = hlc3_pv(candle.high, candle.low, candle.close, candle.volume)
        entry = MinuteLedgerEntry(
            minute=minute,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.volume,
            pv=pv,
            provenance="live",
        )
        return self._apply_entry(entry, allow_replace=True)

    def apply_historical(
        self,
        minute: datetime,
        *,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: int,
    ) -> bool:
        if not validate_historical_ohlc(
            open_=open_, high=high, low=low, close=close, volume=volume
        ):
            return False
        m = ensure_ist(minute).replace(second=0, microsecond=0)
        pv = hlc3_pv(high, low, close, volume)
        entry = MinuteLedgerEntry(
            minute=m,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            pv=pv,
            provenance="historical",
        )
        return self._apply_entry(entry, allow_replace=False)

    def destroy(self) -> None:
        self.minutes.clear()
        self.cum_pv = 0.0
        self.cum_volume = 0
        self.historical_count = 0
        self.live_count = 0
        self.last_update = None
        self.volume_reset_detected = False

    def last_committed_minute(self) -> Optional[datetime]:
        if not self.minutes:
            return None
        return max(self.minutes.keys())

    def has_continuous_chain(self, through: datetime) -> bool:
        """Every minute from session open through `through` must exist."""
        if through < session_open_dt(self.session_date):
            return False
        cursor = session_open_dt(self.session_date)
        end = ensure_ist(through).replace(second=0, microsecond=0)
        while cursor <= end:
            if cursor not in self.minutes:
                return False
            cursor += timedelta(minutes=1)
        return True

    def snapshot(
        self,
        *,
        requested_cutoff: datetime,
        cache_state: str,
        feed_stale: bool,
    ) -> VwapSnapshot:
        through = last_completed_minute_start(requested_cutoff)
        last_min = self.last_committed_minute()
        quality_reason: Optional[VwapQualityReason] = None
        quality_ok = True

        if cache_state == "cold":
            quality_ok = False
            quality_reason = "cache_cold"
        elif cache_state == "warming":
            quality_ok = False
            quality_reason = "cache_warming"
        elif cache_state == "degraded":
            quality_ok = False
            quality_reason = "cache_degraded"
        elif feed_stale:
            quality_ok = False
            quality_reason = "feed_stale"
        elif self.volume_reset_detected:
            quality_ok = False
            quality_reason = "volume_reset"
        elif last_min is None or last_min < through:
            quality_ok = False
            quality_reason = "gap_in_minutes"
        elif not self.has_continuous_chain(through):
            quality_ok = False
            quality_reason = "gap_in_minutes"
        elif self.cum_volume <= 0:
            quality_ok = False
            quality_reason = "zero_volume"
        else:
            vwap = self.cum_pv / float(self.cum_volume)
            if not math.isfinite(vwap):
                quality_ok = False
                quality_reason = "non_finite_vwap"
            else:
                age = 0
                if last_min is not None:
                    age = int(
                        (through - last_min).total_seconds() // 60
                    )
                return VwapSnapshot(
                    instrument_token=0,
                    session_date=self.session_date,
                    vwap=vwap,
                    cum_pv=self.cum_pv,
                    cum_volume=self.cum_volume,
                    last_committed_minute=last_min,
                    requested_cutoff_minute=requested_cutoff,
                    cache_state=cache_state,  # type: ignore[arg-type]
                    feed_stale=feed_stale,
                    quality_ok=True,
                    quality_reason=None,
                    historical_minute_count=self.historical_count,
                    live_minute_count=self.live_count,
                    cache_age_minutes=max(0, age),
                )

        vwap_val: Optional[float] = None
        if self.cum_volume > 0:
            vwap_val = self.cum_pv / float(self.cum_volume)
        age = 0
        if last_min is not None:
            age = max(0, int((through - last_min).total_seconds() // 60))
        return VwapSnapshot(
            instrument_token=0,
            session_date=self.session_date,
            vwap=vwap_val if quality_ok else vwap_val,
            cum_pv=self.cum_pv,
            cum_volume=self.cum_volume,
            last_committed_minute=last_min,
            requested_cutoff_minute=requested_cutoff,
            cache_state=cache_state,  # type: ignore[arg-type]
            feed_stale=feed_stale,
            quality_ok=quality_ok,
            quality_reason=quality_reason,
            historical_minute_count=self.historical_count,
            live_minute_count=self.live_count,
            cache_age_minutes=age,
        )


class SessionVwapCache:
    def __init__(self, session_date: str) -> None:
        self._session_date = session_date
        self._lock = threading.RLock()
        self._ledgers: Dict[int, _TokenLedger] = {}

    @property
    def session_date(self) -> str:
        return self._session_date

    def _ledger(self, token: int) -> _TokenLedger:
        ledger = self._ledgers.get(token)
        if ledger is None:
            ledger = _TokenLedger(session_date=self._session_date)
            self._ledgers[token] = ledger
        return ledger

    def destroy_token(self, token: int) -> None:
        with self._lock:
            ledger = self._ledgers.pop(token, None)
            if ledger is not None:
                ledger.destroy()

    def on_live_candle(self, candle: CompletedOneMinuteCandle) -> bool:
        with self._lock:
            return self._ledger(candle.instrument_token).apply_live(candle)

    def apply_historical_minutes(
        self,
        token: int,
        rows: List[Tuple[datetime, float, float, float, float, int]],
    ) -> int:
        applied = 0
        with self._lock:
            ledger = self._ledger(token)
            for minute, o, h, l, c, vol in rows:
                if ledger.apply_historical(
                    minute, open_=o, high=h, low=l, close=c, volume=vol
                ):
                    applied += 1
        return applied

    def build_snapshot(
        self,
        token: int,
        *,
        requested_cutoff: datetime,
        cache_state: str,
        feed_stale: bool,
    ) -> VwapSnapshot:
        with self._lock:
            snap = self._ledger(token).snapshot(
                requested_cutoff=requested_cutoff,
                cache_state=cache_state,
                feed_stale=feed_stale,
            )
        return VwapSnapshot(
            instrument_token=token,
            session_date=snap.session_date,
            vwap=snap.vwap,
            cum_pv=snap.cum_pv,
            cum_volume=snap.cum_volume,
            last_committed_minute=snap.last_committed_minute,
            requested_cutoff_minute=snap.requested_cutoff_minute,
            cache_state=snap.cache_state,
            feed_stale=snap.feed_stale,
            quality_ok=snap.quality_ok,
            quality_reason=snap.quality_reason,
            historical_minute_count=snap.historical_minute_count,
            live_minute_count=snap.live_minute_count,
            cache_age_minutes=snap.cache_age_minutes,
        )
