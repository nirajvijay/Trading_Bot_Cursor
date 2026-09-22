"""Feed health monitoring.

Structurally fixes the known bug: the old engine compared a 5s staleness
threshold (FEED_STALE_PAUSE_SECONDS) against runner_status.json, a file that
is only written every ~10s. A healthy feed could look "stale" for up to
half the write interval, on every single check.

The fix is architectural, not a bigger threshold: staleness must be judged
against the actual last-tick timestamp from the data path, never against a
status file's write cadence. FeedMonitor takes that timestamp via a callable
so it stays decoupled from wherever ticks are actually received.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional


@dataclass(frozen=True)
class FeedHealth:
    healthy: bool
    age_seconds: Optional[float]
    reason: Optional[str] = None


class FeedMonitor:
    def __init__(
        self,
        *,
        last_tick_at: Callable[[], Optional[datetime]],
        stale_after_seconds: float,
    ) -> None:
        self._last_tick_at = last_tick_at
        self._stale_after_seconds = stale_after_seconds

    def check(self, *, now: Optional[datetime] = None) -> FeedHealth:
        now = now or datetime.now(timezone.utc)
        last_tick = self._last_tick_at()
        if last_tick is None:
            return FeedHealth(healthy=False, age_seconds=None, reason="no_tick_seen_yet")

        age = (now - last_tick).total_seconds()
        if age >= self._stale_after_seconds:
            return FeedHealth(healthy=False, age_seconds=age, reason="feed_stale")
        return FeedHealth(healthy=True, age_seconds=age)
