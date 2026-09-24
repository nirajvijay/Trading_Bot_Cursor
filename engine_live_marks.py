"""One trade's live Open P&L for the desk: WebSocket first, Kite REST fallback.

Display only. Nothing that makes a trading decision reads these numbers.

    UP (long):    (live tick - entry avg) x qty
    DOWN (short): (entry avg - live tick) x qty

`entry avg` is Kite's average_price of this trade's OWN entry order. Not the
positions-row average (that covers the whole stock for the whole day, so a
same-day re-entry would skew it), and not the stored entry_price. Only the
price side comes from the WebSocket.

When the feed cannot be trusted -- down, never started, no tick yet, or the
last tick older than LIVE_TICK_STALE_SECONDS -- the mark falls back to Kite's
REST `pnl` and says why. Kite REST pnl is only used then, never alongside.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, Optional

from engine_live_ticks import Tick, TickFeedHealth

LIVE_TICK_STALE_SECONDS = 5.0

SOURCE_WS = "ws"
SOURCE_KITE_REST = "kite_rest"

# Overall feed state, for the desk's badge.
FEED_LIVE = "live"          # every held trade marked from the WebSocket
FEED_FALLBACK = "fallback"  # at least one held trade on Kite REST
FEED_OFF = "off"            # PAPER: no feed at all

REASON_NOT_ENTERED = "not_entered"
REASON_NO_TICK = "no_tick_yet"
REASON_TICK_STALE = "tick_stale"
REASON_ENTRY_UNKNOWN = "entry_order_not_visible"


@dataclass(frozen=True)
class LiveMark:
    value: Optional[float]
    source: str
    reason: Optional[str] = None


def compute_live_mark(
    *,
    direction: str,
    entry_avg: Optional[float],
    qty: int,
    tick: Optional[Tick],
    feed_health: TickFeedHealth,
    kite_rest_pnl: Optional[float],
    now: datetime,
    stale_after: float = LIVE_TICK_STALE_SECONDS,
) -> LiveMark:
    def fallback(reason: Optional[str]) -> LiveMark:
        return LiveMark(value=kite_rest_pnl, source=SOURCE_KITE_REST, reason=reason)

    if not feed_health.live:
        return fallback(feed_health.reason)
    if not feed_health.connected:
        return fallback(feed_health.reason)
    if tick is None:
        return fallback(REASON_NO_TICK)
    if (now - tick.received_at).total_seconds() > stale_after:
        return fallback(REASON_TICK_STALE)
    if entry_avg is None or float(entry_avg) <= 0 or int(qty) <= 0:
        return fallback(REASON_ENTRY_UNKNOWN)

    move = float(tick.last_price) - float(entry_avg)
    if str(direction).upper() != "UP":
        move = -move
    return LiveMark(value=round(move * int(qty), 2), source=SOURCE_WS)


def feed_state(
    *, feed_health: TickFeedHealth, sources: Iterable[str]
) -> str:
    """The desk badge: live, fallback, or off (PAPER)."""
    if not feed_health.live:
        return FEED_OFF
    if not feed_health.connected:
        return FEED_FALLBACK
    return FEED_FALLBACK if any(s != SOURCE_WS for s in sources) else FEED_LIVE


def first_reason(reasons: Dict[str, Optional[str]]) -> Optional[str]:
    for reason in reasons.values():
        if reason:
            return reason
    return None
