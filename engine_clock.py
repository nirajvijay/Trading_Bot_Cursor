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

# Proactive square-off of everything still open. Zerodha stops accepting new
# MIS orders at 15:12 IST — after that it will refuse both a fresh protective
# stop and a fresh exit, and may auto-square-off the position itself for a
# Rs 50 + 18% GST fee. 14:50 leaves ~22 minutes of buffer before that wall,
# enough for squareoff_all to submit exits for everything open and for
# reconciliation to confirm each one actually closed, not just submitted.
# (An earlier version of this constant, 15:15, was set off a stale belief that
# Zerodha's cutoff was 15:25 — it was already past Zerodha's real 15:12 cutoff
# and cost a live auto-square-off charge on 2026-09-23; see
# Reference/broker_tag_fix_and_live_pnl_lag.md section 3.)
EOD_SQUAREOFF_IST = time(14, 50)

# Zerodha's real MIS order cutoff (not ours — we cannot change this). Anything
# still trying to place a fresh order at or after this time is guaranteed to
# be rejected by the broker.
BROKER_MIS_CUTOFF_IST = time(15, 12)

# Stop retrying a protective stop placement once this close to the broker's
# real cutoff — a retry here can only ever be rejected, so continuing just
# hammers Kite and spams the event log. With EOD_SQUAREOFF_IST at 14:50 this
# should rarely matter; it exists as a belt-and-suspenders guard for whatever
# keeps a position open past 14:50 anyway (manual override, a stuck
# square-off, etc).
PROTECTION_RETRY_CUTOFF_IST = time(15, 5)


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
    """True from EOD_SQUAREOFF_IST onward: force-close everything still open."""
    return ist_time(now) >= EOD_SQUAREOFF_IST


def protection_retry_allowed(now: Optional[datetime] = None) -> bool:
    """False once too close to Zerodha's real MIS cutoff to place a new stop."""
    return ist_time(now) < PROTECTION_RETRY_CUTOFF_IST
