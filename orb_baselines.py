"""ORB baselines: each stock's ATR14 (daily) and average opening volume.

Read-only against the historical 1-minute candle DB, and look-ahead safe: only
sessions strictly before the live session date are used.

The arithmetic is the backtest's (research orb.py), kept in the same order so
the same candles give the same floats:

* daily high / low / close come from the session's 1-minute candles (the close
  is the last candle's close);
* ATR14 is the plain mean of the last 14 true ranges, the first of which uses
  the close of the session before them, so 15 sessions are needed;
* the average opening volume is the mean, over the last 14 sessions, of the
  volume of the candles before 09:20.

What the backtest did not need and live does: a baseline is only "ready" when
its history is fresh and whole, with no trading session missing in between (a
skipped day would fold weeks of movement into one true range). Anything else
leaves that stock out for the day rather than ranking it on a guess.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from nse_trading_calendar import is_special_session_day, prior_nse_trading_session
from orb_config import DEFAULT_ORB_CONFIG, OrbConfig
from orb_types import (
    BASELINE_CALENDAR,
    BASELINE_DEGENERATE,
    BASELINE_GAP,
    BASELINE_INCOMPLETE,
    BASELINE_INSUFFICIENT,
    BASELINE_NO_HISTORY,
    BASELINE_STALE,
    DaySummary,
    OrbBaseline,
)

# (candle_time, high, low, close, volume), oldest first.
CandleRow = Tuple[str, float, float, float, int]

_CANDLES_SQL = """
SELECT candle_time, high, low, close, volume
FROM candles
WHERE instrument_token = ?
  AND candle_time >= ?
  AND candle_time < ?
ORDER BY candle_time ASC
"""


def _hm(candle_time: str) -> str:
    return candle_time[11:16]


def summarise_sessions(
    rows: Iterable[CandleRow], config: OrbConfig = DEFAULT_ORB_CONFIG
) -> List[DaySummary]:
    """Reduce one stock's 1-minute candles (oldest first) to one row per day."""
    range_end = config.range_end.strftime("%H:%M")
    days: List[DaySummary] = []
    current: Optional[str] = None
    high = low = close = 0.0
    or_volume = or_bars = 0
    first_bar = last_bar = ""

    def flush() -> None:
        if current is not None:
            days.append(
                DaySummary(
                    session_date=current,
                    high=high,
                    low=low,
                    close=close,
                    or_volume=or_volume,
                    or_bars=or_bars,
                    first_bar=first_bar,
                    last_bar=last_bar,
                )
            )

    for candle_time, bar_high, bar_low, bar_close, volume in rows:
        day = str(candle_time)[:10]
        hm = _hm(str(candle_time))
        if day != current:
            flush()
            current = day
            high, low = float(bar_high), float(bar_low)
            or_volume = or_bars = 0
            first_bar = hm
        else:
            high = max(high, float(bar_high))
            low = min(low, float(bar_low))
        close = float(bar_close)
        last_bar = hm
        if hm < range_end:
            or_volume += int(volume)
            or_bars += 1
    flush()
    return days


def compute_baseline_values(
    days: Sequence[DaySummary], config: OrbConfig = DEFAULT_ORB_CONFIG
) -> Tuple[float, float]:
    """(ATR, average opening volume) from the most recent sessions.

    ``days`` is oldest first and must hold at least ``config.sessions_needed``
    sessions. Same operations in the same order as research orb.py.
    """
    if len(days) < config.sessions_needed:
        raise ValueError(
            f"need {config.sessions_needed} sessions, got {len(days)}"
        )
    atr_days = list(days)[-config.atr_sessions:]
    prev_close = days[-config.atr_sessions - 1].close
    true_ranges: List[float] = []
    for day in atr_days:
        true_ranges.append(
            max(day.high - day.low, abs(day.high - prev_close), abs(day.low - prev_close))
        )
        prev_close = day.close
    atr = sum(true_ranges) / len(true_ranges)
    volumes = [day.or_volume for day in list(days)[-config.volume_lookback_sessions:]]
    average = sum(volumes) / len(volumes)
    return atr, average


def baseline_from_days(
    *,
    instrument_token: int,
    tradingsymbol: str,
    days: Sequence[DaySummary],
    prior_session: Optional[str],
    config: OrbConfig = DEFAULT_ORB_CONFIG,
    expected_sessions: Optional[Sequence[str]] = None,
) -> OrbBaseline:
    """Build one stock's baseline, or say exactly why it is not ready.

    ``days`` are that stock's sessions strictly before the live session,
    oldest first. ``prior_session`` is the calendar's previous trading day.
    ``expected_sessions``, when given, are the trading sessions the window
    must contain (expected_sessions_before); one without candles is a gap.
    """

    def not_ready(reason: str, detail: str) -> OrbBaseline:
        return OrbBaseline(
            instrument_token=instrument_token,
            tradingsymbol=tradingsymbol,
            ready=False,
            reason=reason,
            detail=detail,
            history_as_of=days[-1].session_date if days else None,
        )

    if not days:
        return not_ready(BASELINE_NO_HISTORY, "no candles before this session")
    needed = config.sessions_needed
    if len(days) < needed:
        return not_ready(BASELINE_INSUFFICIENT, f"{len(days)} of {needed} sessions")
    if prior_session is None:
        return not_ready(BASELINE_CALENDAR, "previous trading day unknown")
    window = list(days)[-needed:]
    latest = window[-1].session_date
    if latest < prior_session:
        return not_ready(
            BASELINE_STALE, f"latest candles {latest}, expected {prior_session}"
        )
    if expected_sessions is not None:
        have = {day.session_date for day in days}
        missing = [session for session in expected_sessions if session not in have]
        if missing:
            more = f" and {len(missing) - 1} more" if len(missing) > 1 else ""
            return not_ready(BASELINE_GAP, f"no candles for {missing[0]}{more}")
    min_last_bar = config.history_min_last_bar.strftime("%H:%M")
    for day in window:
        if day.last_bar < min_last_bar:
            return not_ready(
                BASELINE_INCOMPLETE,
                f"{day.session_date} candles stop at {day.last_bar}",
            )
    volume_days = window[-config.volume_lookback_sessions:]
    for day in volume_days:
        if day.or_bars < 1:
            return not_ready(
                BASELINE_INCOMPLETE, f"{day.session_date} has no opening candles"
            )
    atr, average = compute_baseline_values(window, config)
    if not (math.isfinite(atr) and math.isfinite(average) and atr > 0 and average > 0):
        return not_ready(BASELINE_DEGENERATE, f"atr={atr!r} average volume={average!r}")
    return OrbBaseline(
        instrument_token=instrument_token,
        tradingsymbol=tradingsymbol,
        ready=True,
        atr14=atr,
        avg_or_volume=average,
        history_as_of=latest,
        sessions=tuple(day.session_date for day in window),
        min_or_bars=min(day.or_bars for day in volume_days),
    )


def load_orb_baselines(
    historical_db: Path,
    session_date: str,
    token_to_symbol: Mapping[int, str],
    *,
    config: OrbConfig = DEFAULT_ORB_CONFIG,
    prior_session_fn: Callable[[str], Optional[str]] = prior_nse_trading_session,
    is_special_fn: Callable[[date], bool] = is_special_session_day,
) -> Dict[int, OrbBaseline]:
    """Every watched stock's baseline for ``session_date``. Never raises.

    A missing or unreadable DB gives a not-ready baseline for every stock:
    observation must start regardless, and ORB simply stays off for the day.
    """

    def all_not_ready(detail: str, reason: str = BASELINE_NO_HISTORY) -> Dict[int, OrbBaseline]:
        return {
            int(token): OrbBaseline(
                instrument_token=int(token),
                tradingsymbol=str(symbol),
                ready=False,
                reason=reason,
                detail=detail,
            )
            for token, symbol in token_to_symbol.items()
        }

    path = Path(historical_db)
    if not path.exists():
        return all_not_ready("historical db missing")
    try:
        session_day = date.fromisoformat(session_date)
    except ValueError:
        return all_not_ready(f"bad session date {session_date!r}")
    lower = (session_day - timedelta(days=config.history_lookback_calendar_days)).isoformat()
    prior_session = prior_session_fn(session_date)
    expected = expected_sessions_before(
        session_date, config.sessions_needed, prior_session_fn, is_special_fn
    )
    if prior_session is not None and expected is None:
        return all_not_ready("calendar does not reach back far enough", BASELINE_CALENDAR)

    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return all_not_ready(f"historical db unreadable: {exc}")
    out: Dict[int, OrbBaseline] = {}
    try:
        for token, symbol in token_to_symbol.items():
            try:
                rows = conn.execute(_CANDLES_SQL, (int(token), lower, session_date)).fetchall()
            except sqlite3.Error as exc:
                out[int(token)] = OrbBaseline(
                    instrument_token=int(token),
                    tradingsymbol=str(symbol),
                    ready=False,
                    reason=BASELINE_NO_HISTORY,
                    detail=f"historical db read failed: {exc}",
                )
                continue
            days = [
                day
                for day in summarise_sessions(rows, config)
                if not _is_special(day.session_date, is_special_fn)
            ]
            out[int(token)] = baseline_from_days(
                instrument_token=int(token),
                tradingsymbol=str(symbol),
                days=days,
                prior_session=prior_session,
                config=config,
                expected_sessions=expected,
            )
    finally:
        conn.close()
    return out


def expected_sessions_before(
    session_date: str,
    count: int,
    prior_session_fn: Callable[[str], Optional[str]] = prior_nse_trading_session,
    is_special_fn: Callable[[date], bool] = is_special_session_day,
) -> Optional[Tuple[str, ...]]:
    """The ``count`` regular trading sessions before ``session_date``, oldest
    first; None when the calendar cannot say (the baseline is then not ready)."""
    out: List[str] = []
    day = session_date
    for _ in range(count * 4):  # far more steps than holidays can ever need
        previous = prior_session_fn(day)
        if previous is None:
            return None
        day = previous
        if not _is_special(day, is_special_fn):
            out.append(day)
            if len(out) == count:
                return tuple(reversed(out))
    return None


def _is_special(session_date: str, is_special_fn: Callable[[date], bool]) -> bool:
    try:
        return bool(is_special_fn(date.fromisoformat(session_date)))
    except ValueError:
        return True  # an unreadable date is never a regular session


def ready_count(baselines: Mapping[int, OrbBaseline]) -> int:
    return sum(1 for baseline in baselines.values() if baseline.ready)
