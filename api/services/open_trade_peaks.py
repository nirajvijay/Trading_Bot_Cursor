"""Open-trade "how far from the open" columns for the desk. Display only.

Nothing that makes a trading decision reads these numbers: not entries, stops,
Close / square-off, ARB / GUC or risk. They are computed in the API, per
request, from files the engine and the observation runner already write.

Every figure is a percent of the stock's day open, never rupees:

    pct(price) = (price / day_open - 1) x 100

* **Day open** -- the open of the stock's 09:15 1-minute bar in the live
  candles DB (same rule as engine_arb_context.day_open). If that bar is not
  stored (runner started late, or no data), every %-from-open field is None
  and the desk shows a dash: never a guess from a later bar.
* **Entry %** -- the trade's entry fill (entry_price is Kite's fill average
  once the entry filled; see engine_entry.apply_entry_fill).
* **Now %** -- the live price. While the trade's mark comes from the WebSocket
  ("ws"), the price is backed out of the live mark exactly:
  live_pnl = (ltp - entry) x qty for UP, (entry - ltp) x qty for DOWN. A Kite
  REST mark is the stock's whole-day pnl, not this trade's, so it is never used
  for a price; the last completed 1-minute close is used instead (lags up to a
  minute, flagged as now_price_source = "1m_close").
* **Today's peak %** -- since the session open: for UP the highest high, for
  DOWN the lowest low, over today's 1-minute bars, also counting the entry fill
  and the live price (so the peak never trails Now % inside the current minute).
* **Usual peak %** -- the stock's average open-to-high (UP) or open-to-low
  (DOWN) move, for 2026 and for the full 3 years, from the static table
  config/open_to_high_low_by_stock.csv. Signed like the move: positive for UP,
  negative for DOWN.
* **Gap to usual** -- room left before the usual peak, positive = room left,
  negative = already past it: UP usual - now, DOWN now - usual (usual negative).

The table (config/open_to_high_low_by_stock.csv) is static for now. It was
produced offline from the 3-year Nifty 100 1-minute DB (Nov 2023 - Nov 2026,
nifty-radar-research/nifty100_1m_3y), one row per stock: for each trading day,
session window 09:15 to 14:50, open = the day's open (assumed: first
session bar's open), open-to-high =
day_high / open - 1 and open-to-low = day_low / open - 1; averaged over all
days ("avg_open_to_high_%", "avg_open_to_low_%") and over 2026 days only
("avg_high_2026_%", "avg_low_2026_%"). Open-to-low is stored as a positive
magnitude. Medians are in the file but not used here.
"""
from __future__ import annotations

import csv
import logging
import math
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional

from api.db import open_readonly
from api.services.desk_pnl import HOLDING_STATES

logger = logging.getLogger(__name__)

DEFAULT_TABLE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "config" / "open_to_high_low_by_stock.csv"
)

# The CSV's own headers (all percent, open-to-low as a positive magnitude).
COL_STOCK = "stock"
COL_HIGH_3Y = "avg_open_to_high_%"
COL_LOW_3Y = "avg_open_to_low_%"
COL_HIGH_2026 = "avg_high_2026_%"
COL_LOW_2026 = "avg_low_2026_%"

NOW_SOURCE_WS = "ws"
NOW_SOURCE_1M_CLOSE = "1m_close"

# States whose entry_price is a real fill (pending/submitted hold the trigger).
# Same set the desk P&L uses (mirrors engine_reconcile.HOLDING_STATES).
_HELD_STATES = HOLDING_STATES

# The live DB read must never hold up /execution/positions (it carries the
# Close button and is polled every second). WAL readers almost never wait, but
# if they do (WAL recovery, a checkpoint) give up fast and show dashes instead
# of sqlite's default 5 s wait.
_BUSY_TIMEOUT_MS = 200


@dataclass(frozen=True)
class UsualPeak:
    """Average open-to-high / open-to-low, percent, both positive magnitudes."""

    high_3y: Optional[float] = None
    low_3y: Optional[float] = None
    high_2026: Optional[float] = None
    low_2026: Optional[float] = None


@dataclass(frozen=True)
class DayBars:
    """Today's 1-minute bars of one stock, reduced to what the columns need."""

    day_open: Optional[float]  # the 09:15 bar's open; None when not stored
    high: Optional[float]
    low: Optional[float]
    last_close: Optional[float]


# ----------------------------------------------------------------------
# The static table
# ----------------------------------------------------------------------


def _opt_float(value: object) -> Optional[float]:
    try:
        out = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None  # NaN / inf -> None


def parse_usual_peaks(lines: Iterable[str]) -> Dict[str, UsualPeak]:
    table: Dict[str, UsualPeak] = {}
    for row in csv.DictReader(lines):
        stock = str(row.get(COL_STOCK) or "").strip()
        if not stock:
            continue
        table[stock] = UsualPeak(
            high_3y=_opt_float(row.get(COL_HIGH_3Y)),
            low_3y=_opt_float(row.get(COL_LOW_3Y)),
            high_2026=_opt_float(row.get(COL_HIGH_2026)),
            low_2026=_opt_float(row.get(COL_LOW_2026)),
        )
    return table


@lru_cache(maxsize=4)
def load_usual_peaks(path: Path = DEFAULT_TABLE_PATH) -> Dict[str, UsualPeak]:
    """The table, read once per process. Missing or unreadable: empty."""
    try:
        with Path(path).open(newline="", encoding="utf-8") as handle:
            return parse_usual_peaks(handle)
    except OSError as exc:
        logger.warning("usual-peak table unavailable (%s): %s", path, exc)
        return {}


# ----------------------------------------------------------------------
# The math (pure)
# ----------------------------------------------------------------------


def _is_up(direction: str) -> bool:
    return str(direction).upper() == "UP"


def pct_from_open(price: Optional[float], day_open: Optional[float]) -> Optional[float]:
    if price is None or day_open is None or float(day_open) <= 0 or float(price) <= 0:
        return None
    return (float(price) / float(day_open) - 1.0) * 100.0


def signed_usual_peak(direction: str, usual: UsualPeak, *, period: str) -> Optional[float]:
    """The usual peak for this side, signed like the move (DOWN is negative)."""
    if period == "2026":
        high, low = usual.high_2026, usual.low_2026
    else:
        high, low = usual.high_3y, usual.low_3y
    if _is_up(direction):
        return high
    return -abs(low) if low is not None else None


def gap_to_usual(
    direction: str, now_pct: Optional[float], usual_signed: Optional[float]
) -> Optional[float]:
    """Room left to the usual peak: positive = room left, negative = past it.

    UP:   usual high - now          (now +0.30, usual +1.25 -> +0.95)
    DOWN: now - (-usual low)        (now -0.50, usual -1.38 -> +0.88)
    """
    if now_pct is None or usual_signed is None:
        return None
    if _is_up(direction):
        return usual_signed - now_pct
    return now_pct - usual_signed


def now_price_from_mark(
    *,
    direction: str,
    entry_price: Optional[float],
    qty: int,
    live_pnl: Optional[float],
    source: Optional[str],
) -> Optional[float]:
    """The live price behind a WebSocket mark, or None for any other mark."""
    if source != NOW_SOURCE_WS or live_pnl is None or entry_price is None:
        return None
    if int(qty or 0) <= 0 or float(entry_price) <= 0:
        return None
    per_share = float(live_pnl) / int(qty)
    price = float(entry_price) + per_share if _is_up(direction) else float(entry_price) - per_share
    return price if math.isfinite(price) and price > 0 else None


def today_peak_pct(
    direction: str,
    bars: Optional[DayBars],
    *extra_prices: Optional[float],
) -> Optional[float]:
    """Highest (UP) or lowest (DOWN) % from open since the session open."""
    if bars is None or bars.day_open is None:
        return None
    bar_extreme = bars.high if _is_up(direction) else bars.low
    candidates = [p for p in (bar_extreme, *extra_prices) if p is not None and p > 0]
    if not candidates:
        return None
    extreme = max(candidates) if _is_up(direction) else min(candidates)
    return pct_from_open(extreme, bars.day_open)


def _round(value: Optional[float]) -> Optional[float]:
    """4 dp, and None for anything non-finite: the JSON response is encoded
    with allow_nan=False, so one inf/NaN here would 500 the whole endpoint."""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return round(out, 4) if math.isfinite(out) else None


def open_trade_fields(
    *,
    direction: str,
    state: str,
    entry_price: Optional[float],
    qty: int,
    live_pnl: Optional[float],
    live_pnl_source: Optional[str],
    bars: Optional[DayBars],
    usual: Optional[UsualPeak],
) -> Dict[str, object]:
    """Every new column of one open row. Missing inputs give None, not guesses."""
    day_open = bars.day_open if bars is not None else None
    held = str(state) in _HELD_STATES
    entry = entry_price if held else None

    now_price = (
        now_price_from_mark(
            direction=direction,
            entry_price=entry,
            qty=qty,
            live_pnl=live_pnl,
            source=live_pnl_source,
        )
        if held
        else None
    )
    now_source: Optional[str] = NOW_SOURCE_WS if now_price is not None else None
    if now_price is None and bars is not None and bars.last_close is not None:
        now_price = bars.last_close
        now_source = NOW_SOURCE_1M_CLOSE

    entry_pct = pct_from_open(entry, day_open)
    now_pct = pct_from_open(now_price, day_open)
    if now_pct is None:
        now_source = None
    peak_pct = today_peak_pct(direction, bars, entry, now_price)

    usual_2026 = signed_usual_peak(direction, usual, period="2026") if usual else None
    usual_3y = signed_usual_peak(direction, usual, period="3y") if usual else None

    return {
        "day_open_price": _round(day_open),
        "entry_pct_from_open": _round(entry_pct),
        "now_pct_from_open": _round(now_pct),
        "now_price_source": now_source,
        "today_peak_pct": _round(peak_pct),
        "usual_peak_2026_pct": _round(usual_2026),
        "usual_peak_3y_pct": _round(usual_3y),
        "gap_to_usual_2026_pct": _round(gap_to_usual(direction, now_pct, usual_2026)),
        "gap_to_usual_3y_pct": _round(gap_to_usual(direction, now_pct, usual_3y)),
    }


# ----------------------------------------------------------------------
# The live 1-minute bars (read-only)
# ----------------------------------------------------------------------


def day_open_minute(session_date: str) -> str:
    """candle_time text of the 09:15 bar (live_1m_candles stores ISO IST)."""
    return f"{session_date}T09:15:00+05:30"


def read_day_bars(
    live_db: Path, session_date: str, symbols: Iterable[str]
) -> Dict[str, DayBars]:
    """Today's bars of the given stocks, one read-only query. Missing DB: {}."""
    wanted = sorted({str(s) for s in symbols if s})
    if not wanted:
        return {}
    try:
        conn = open_readonly(Path(live_db))
    except FileNotFoundError:
        return {}
    placeholders = ",".join("?" for _ in wanted)
    try:
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        rows = conn.execute(
            f"""
            SELECT tradingsymbol, candle_time, open, high, low, close
            FROM live_1m_candles
            WHERE session_date = ? AND tradingsymbol IN ({placeholders})
            ORDER BY tradingsymbol, candle_time
            """,
            (session_date, *wanted),
        ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("live 1m bars unavailable: %s", exc)
        return {}
    finally:
        conn.close()

    open_minute = day_open_minute(session_date)
    out: Dict[str, DayBars] = {}
    acc: Dict[str, Dict[str, Optional[float]]] = {}
    for row in rows:
        sym = str(row["tradingsymbol"])
        a = acc.setdefault(sym, {"open": None, "high": None, "low": None, "close": None})
        high, low = float(row["high"]), float(row["low"])
        if str(row["candle_time"]) == open_minute and float(row["open"]) > 0:
            a["open"] = float(row["open"])
        a["high"] = high if a["high"] is None else max(a["high"], high)
        a["low"] = low if a["low"] is None else min(a["low"], low)
        a["close"] = float(row["close"])  # rows are in candle_time order
    for sym, a in acc.items():
        out[sym] = DayBars(day_open=a["open"], high=a["high"], low=a["low"], last_close=a["close"])
    return out


def fields_for_open_rows(
    rows: Iterable[Mapping[str, object]],
    *,
    live_db: Path,
    session_date: str,
    table: Optional[Mapping[str, UsualPeak]] = None,
) -> Dict[str, Dict[str, object]]:
    """trade_id -> the new columns, for each open row given.

    Each row needs trade_id, tradingsymbol, direction, state, entry_price, qty,
    live_pnl and live_pnl_source. Never raises: the endpoint that carries the
    Close button must not fail because a display column could not be read.
    """
    try:
        rows = list(rows)
        usual_table = load_usual_peaks() if table is None else table
        bars = read_day_bars(live_db, session_date, (str(r["tradingsymbol"]) for r in rows))
        out: Dict[str, Dict[str, object]] = {}
        for r in rows:
            symbol = str(r["tradingsymbol"])
            try:
                out[str(r["trade_id"])] = open_trade_fields(
                    direction=str(r["direction"]),
                    state=str(r["state"]),
                    entry_price=r.get("entry_price"),  # type: ignore[arg-type]
                    qty=int(r.get("qty") or 0),  # type: ignore[arg-type]
                    live_pnl=r.get("live_pnl"),  # type: ignore[arg-type]
                    live_pnl_source=r.get("live_pnl_source"),  # type: ignore[arg-type]
                    bars=bars.get(symbol),
                    usual=usual_table.get(symbol),
                )
            except Exception as exc:  # noqa: BLE001 - one bad row must not blank the rest
                logger.warning("open-trade peak fields failed for %s: %s", symbol, exc)
        return out
    except Exception as exc:  # noqa: BLE001 - display must never break the desk
        logger.warning("open-trade peak fields unavailable: %s", exc)
        return {}
