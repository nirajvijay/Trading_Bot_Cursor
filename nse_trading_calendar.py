"""NSE cash-market trading calendar (weekends + exchange holidays).

Prior-session resolution uses this calendar only — never the historical DB max date.
Holiday dates are sourced from NSE circular NSE/CMTR/71775 (Dec 2025) and addendum
NSE/CMTR/72260 (Jan 2026). Update annually when NSE publishes the new list.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import FrozenSet, Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# NSE capital-market closed days (YYYY-MM-DD). Weekends are handled separately.
NSE_HOLIDAYS: FrozenSet[str] = frozenset(
    {
        # 2025
        "2025-02-26",
        "2025-03-14",
        "2025-03-31",
        "2025-04-10",
        "2025-04-14",
        "2025-04-18",
        "2025-05-01",
        "2025-08-15",
        "2025-08-27",
        "2025-10-02",
        "2025-10-21",
        "2025-10-22",
        "2025-11-05",
        "2025-12-25",
        # 2026 (NSE/CMTR/71775 + 72260)
        "2026-01-15",
        "2026-01-26",
        "2026-03-03",
        "2026-03-26",
        "2026-03-31",
        "2026-04-03",
        "2026-04-14",
        "2026-05-01",
        "2026-05-28",
        "2026-06-26",
        "2026-09-14",
        "2026-10-02",
        "2026-10-20",
        "2026-11-10",
        "2026-11-24",
        "2026-12-25",
        # 2027 (placeholder — extend when NSE publishes official list)
    }
)


def _parse_iso_date(value: str) -> date:
    return date.fromisoformat(value)


def is_nse_trading_day(day: date) -> bool:
    """Return True when NSE cash market is open on this calendar date."""
    if day.weekday() >= 5:
        return False
    return day.isoformat() not in NSE_HOLIDAYS


def prior_nse_trading_session(session_date: str) -> Optional[str]:
    """
    Immediately prior NSE trading session strictly before session_date D.

    Walks the calendar backward skipping weekends and NSE holidays.
    Returns ISO date string YYYY-MM-DD, or None if no trading day found in range.
    """
    current = _parse_iso_date(session_date) - timedelta(days=1)
    # Safety bound: one year of calendar lookback
    for _ in range(366):
        if is_nse_trading_day(current):
            return current.isoformat()
        current -= timedelta(days=1)
    return None


def session_date_ist_today() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")
