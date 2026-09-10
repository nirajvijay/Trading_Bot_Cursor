"""NSE cash-market trading calendar (weekends + exchange holidays).

Prior-session resolution uses this calendar only — never the historical DB max date.
Holiday dates are sourced from NSE circular NSE/CMTR/71775 (Dec 2025) and addendum
NSE/CMTR/72260 (Jan 2026). Update annually when NSE publishes the new list.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import math
from typing import FrozenSet, Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
SUPPORTED_CALENDAR_YEARS = frozenset({2025, 2026})


def calendar_session_status(now: datetime) -> dict:
    now = now.astimezone(IST)
    reason = entry_calendar_block_reason(now.date().isoformat(), now)
    minute = now.hour * 60 + now.minute
    if reason and reason != "nse_holiday_or_weekend":
        state = "UNKNOWN"
    elif reason:
        state = "CLOSED"
    else:
        state = "OPEN" if 555 <= minute < 930 else "CLOSED"
    return {"state":state,"reason":reason,"as_of":now.isoformat(),
            "source":"configured NSE calendar; not a market-feed health claim"}

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


# Special / muhurat-style sessions. A date alone never authorizes trading under
# normal-day timings — V1 requires an explicit SpecialSessionSchedule.
NSE_SPECIAL_SESSIONS: FrozenSet[str] = frozenset(
    {
        # Placeholder muhurat / special cash sessions (extend when NSE publishes).
        "2025-10-21",
        "2026-11-08",
    }
)


def is_special_session_day(day: date) -> bool:
    return day.isoformat() in NSE_SPECIAL_SESSIONS


def parse_hhmm(hhmm: object) -> Optional[int]:
    """Parse a strict integer HHMM into minutes since midnight.

    Accepts only finite ``int``/``float`` values with no fractional part, hours
    00–23, and minutes 00–59. Returns None for malformed input (never raises).
    """
    if isinstance(hhmm, bool) or not isinstance(hhmm, (int, float)):
        return None
    value = float(hhmm)
    if not math.isfinite(value):
        return None
    # Reject fractional HHMM (e.g. 1445.5) — do not round.
    if value != math.trunc(value):
        return None
    n = int(value)
    if n < 0:
        return None
    hour, minute = divmod(n, 100)
    if hour > 23 or minute > 59:
        return None
    return hour * 60 + minute


def hhmm_to_minutes(hhmm: object) -> int:
    """Convert a validated HHMM to minutes since midnight.

    Prefer ``parse_hhmm`` when a blocking validation result is required.
    Raises ValueError for malformed input (does not coerce 1865→19:05).
    """
    minutes = parse_hhmm(hhmm)
    if minutes is None:
        raise ValueError(f"invalid HHMM: {hhmm!r}")
    return minutes


@dataclass(frozen=True)
class SpecialSessionSchedule:
    """Explicit special-session timetable. Date allowlists are not sufficient."""

    session_date: str
    session_open_ist: float  # HHMM, e.g. 1815.0
    entry_cutoff_ist: float
    square_off_ist: float

    def validation_error(self) -> Optional[str]:
        try:
            _parse_iso_date(str(self.session_date).strip())
        except ValueError:
            return "special_session_schedule_incomplete"
        open_m = parse_hhmm(self.session_open_ist)
        cut_m = parse_hhmm(self.entry_cutoff_ist)
        sq_m = parse_hhmm(self.square_off_ist)
        if open_m is None or cut_m is None or sq_m is None:
            return "special_session_schedule_incomplete"
        if not (open_m < cut_m <= sq_m):
            return "special_session_schedule_incomplete"
        return None


def validate_session_gate_hhmm_pair(
    entry_cutoff_ist: object,
    square_off_ist: object,
    *,
    require_cutoff_before_square_off: bool = True,
) -> Optional[str]:
    """Validate normal-session Admin cutoff/square-off HHMM pair.

    Returns an error label, or None when both values are strict HHMM and ordered.
    """
    cut_m = parse_hhmm(entry_cutoff_ist)
    sq_m = parse_hhmm(square_off_ist)
    if cut_m is None:
        return "entry_cutoff_ist_invalid"
    if sq_m is None:
        return "square_off_ist_invalid"
    if require_cutoff_before_square_off and not (cut_m < sq_m):
        return "square_off_ist_not_after_entry_cutoff"
    return None


def entry_calendar_block_reason(
    session_date: str,
    now: datetime,
    *,
    special_session_schedule: Optional[SpecialSessionSchedule] = None,
) -> Optional[str]:
    """Return a skip/block reason for *new* entries, or None when the session is valid.

    Blocks on session-date mismatch vs IST clock, weekends/holidays, and special
    sessions without a complete explicit schedule (open / cutoff / square-off).
    A date allowlist alone never authorizes normal-day timings.
    Existing exposure recovery is unaffected by this gate.
    """
    try:
        session_day = _parse_iso_date(str(session_date).strip())
    except ValueError:
        return "session_date_invalid"
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    clock_day = now.astimezone(IST).date()
    if clock_day != session_day:
        return "session_date_mismatch"
    if session_day.year not in SUPPORTED_CALENDAR_YEARS:
        return "calendar_year_unconfigured"
    iso = session_day.isoformat()
    if is_special_session_day(session_day):
        if special_session_schedule is None:
            return "special_session_unconfigured"
        if str(special_session_schedule.session_date).strip() != iso:
            return "special_session_unconfigured"
        err = special_session_schedule.validation_error()
        if err is not None:
            return err
        return None
    if not is_nse_trading_day(session_day):
        return "nse_holiday_or_weekend"
    return None
