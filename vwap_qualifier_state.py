"""
In-memory live 5-minute candle-based session VWAP state.

Committed 5m bars are insert-if-absent. In-progress is live-only and is
never mixed with historical minutes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
from zoneinfo import ZoneInfo

from candle_aggregation import (
    BUCKET_SIZE,
    SESSION_MINUTE_START,
    ensure_ist,
    floor_five_minute_bucket_start,
    minute_of_day_from_datetime,
)
from tick_event import IST
from vwap_qualifier_features import hlc3_pv
from vwap_qualifier_types import VwapContribution, VwapProvenance

_IST = ZoneInfo(IST)


def freeze_cutoff(now: datetime) -> datetime:
    """Exclusive end of the last fully closed 1m: current IST minute start."""
    ist = ensure_ist(now)
    return ist.replace(second=0, microsecond=0)


def session_open_dt(session_date: str) -> datetime:
    y, m, d = (int(p) for p in session_date.split("-"))
    return datetime(y, m, d, 9, 15, 0, tzinfo=_IST)


def bucket_start_dt(ts: datetime) -> Optional[datetime]:
    ist = ensure_ist(ts)
    minute = minute_of_day_from_datetime(ist)
    start_min = floor_five_minute_bucket_start(minute)
    if start_min is None:
        return None
    return ist.replace(
        hour=start_min // 60,
        minute=start_min % 60,
        second=0,
        microsecond=0,
    )


def first_session_bucket(session_date: str) -> datetime:
    return session_open_dt(session_date)


def startup_bucket(cutoff: datetime, session_date: str) -> datetime:
    """Active 5m bucket at manual start (in-session, else first session bar)."""
    bucket = bucket_start_dt(cutoff)
    if bucket is not None:
        return bucket
    return first_session_bucket(session_date)


def bucket_end_dt(bucket_start: datetime) -> datetime:
    return ensure_ist(bucket_start) + timedelta(minutes=BUCKET_SIZE)


def bucket_is_closed(bucket_start: datetime, now: datetime) -> bool:
    return ensure_ist(now) >= bucket_end_dt(bucket_start)


@dataclass
class CommittedFiveMinute:
    bucket_start: datetime
    pv: float
    volume: int
    provenance: VwapProvenance


@dataclass
class InProgressFiveMinute:
    bucket_start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    seen_minutes: Set[int] = field(default_factory=set)
    last_tick_minute: Optional[int] = None


class TokenVwapState:
    """Per-token session VWAP accumulator."""

    def __init__(self, *, b0: datetime) -> None:
        self.b0 = ensure_ist(b0)
        self.committed: Dict[datetime, CommittedFiveMinute] = {}
        self.in_progress: Optional[InProgressFiveMinute] = None
        self.last_sequence: int = 0
        self.last_cumulative_volume: Optional[int] = None
        self.volume_reset: bool = False
        self.overflowed: bool = False
        self.bootstrap_ready: bool = False
        self.uncertain_buckets: Set[datetime] = {self.b0}
        self.failed_buckets: Set[datetime] = set()
        self.repair_attempts: Dict[datetime, int] = {}

    @property
    def cum_pv(self) -> float:
        return sum(bar.pv for bar in self.committed.values())

    @property
    def cum_vol(self) -> int:
        return sum(bar.volume for bar in self.committed.values())

    def commit_if_absent(
        self,
        bucket_start: datetime,
        *,
        pv: float,
        volume: int,
        provenance: VwapProvenance,
    ) -> bool:
        key = ensure_ist(bucket_start)
        if key in self.committed:
            return False
        self.committed[key] = CommittedFiveMinute(
            bucket_start=key,
            pv=pv,
            volume=volume,
            provenance=provenance,
        )
        self.uncertain_buckets.discard(key)
        if self.in_progress is not None and ensure_ist(self.in_progress.bucket_start) == key:
            self.in_progress = None
        return True

    def mark_uncertain(self, bucket_start: datetime) -> None:
        key = ensure_ist(bucket_start)
        self.uncertain_buckets.add(key)
        if self.in_progress is not None and ensure_ist(self.in_progress.bucket_start) == key:
            self.in_progress = None

    def is_uncertain(self, bucket_start: Optional[datetime]) -> bool:
        if bucket_start is None:
            return False
        return ensure_ist(bucket_start) in self.uncertain_buckets

    def snapshot_contributions(self) -> List[VwapContribution]:
        rows = sorted(self.committed.values(), key=lambda b: b.bucket_start)
        return [
            VwapContribution(
                bucket_start=bar.bucket_start,
                volume=bar.volume,
                pv=bar.pv,
                provenance=bar.provenance,
            )
            for bar in rows
        ]

    def live_in_progress_pv(self) -> tuple[float, int]:
        """HLC3-volume of the current live in-progress bar, or (0, 0)."""
        ip = self.in_progress
        if ip is None or ip.volume <= 0:
            return 0.0, 0
        return hlc3_pv(ip.high, ip.low, ip.close, ip.volume), ip.volume
