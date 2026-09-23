"""Every wall-clock rule the engine obeys, in one place, all IST.

Each function takes `now` so nothing in the test suite has to sleep or depend
on when it happens to run. Rationale for each constant is in
Reference/execution_engine_rebuild_notes.md (run-loop design series, parts 1
and 4).
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# NSE cash session. Outside this there is no live market to trade in at all.
MARKET_OPEN_IST = time(9, 15)
MARKET_CLOSE_IST = time(15, 30)

# New entries stop here. The engine keeps running and managing open positions
# past this time — it does not force-close anything at 14:00 and does not stop.
ENTRY_CUTOFF_IST = time(14, 0)

# Proactive square-off of everything still open. A ~10 minute buffer before
# Zerodha's 15:25 equity MIS auto-square-off, which costs Rs 50 + 18% GST per
# position and executes into the rush of every other trader's forced exits.
# Also avoids the last, thinner/choppier minutes before close. Since entries
# already stopped at 14:00, anything still open has had over an hour to play
# out — this is not cutting trades short.
EOD_SQUAREOFF_IST = time(15, 15)


def to_ist(now: Optional[datetime] = None) -> datetime:
    """Interpret `now` in IST. A naive datetime is taken to already be IST."""
    if now is None:
        return datetime.now(IST)
    if now.tzinfo is None:
        return now.replace(tzinfo=IST)
    return now.astimezone(IST)


def ist_time(now: Optional[datetime] = None) -> time:
    return to_ist(now).time()


def market_session_open(now: Optional[datetime] = None) -> bool:
    """True inside the NSE cash session, 09:15-15:30 IST inclusive."""
    current = ist_time(now)
    return MARKET_OPEN_IST <= current <= MARKET_CLOSE_IST


def new_entries_allowed(now: Optional[datetime] = None) -> bool:
    """True while fresh entries may still be taken: market open, before 14:00."""
    return market_session_open(now) and ist_time(now) < ENTRY_CUTOFF_IST


def start_allowed(now: Optional[datetime] = None) -> bool:
    """True when a start click may launch an engine.

    Blocked outside market hours (nothing to trade) and blocked after 14:00 even
    though that is still market hours — a session started then could never take
    a single trade, so it is refused as pointless rather than merely harmless.
    Refusal is outright: a start never queues and waits for the window to open.
    """
    return new_entries_allowed(now)


def start_refusal_reason(now: Optional[datetime] = None) -> Optional[str]:
    """Why a start is refused right now, or None when it is allowed."""
    current = ist_time(now)
    if current < MARKET_OPEN_IST:
        return "market_not_open_yet"
    if current > MARKET_CLOSE_IST:
        return "market_closed"
    if current >= ENTRY_CUTOFF_IST:
        return "past_entry_cutoff"
    return None


def eod_squareoff_due(now: Optional[datetime] = None) -> bool:
    """True from 15:15 IST onward: force-close everything still open."""
    return ist_time(now) >= EOD_SQUAREOFF_IST
