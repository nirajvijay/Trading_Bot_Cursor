"""The desk's P&L numbers: totals and the per-row Stock Day Total.

Pure arithmetic over the positions rows (realised P&L, and Kite's pinned day
figure for a stock that went flat) and the engine's live marks file (live
Ongoing P&L). No I/O, so the API and its tests share exactly one rule.

Stock Day Total, per row, never merged across rows of the same stock:

    this trade's P&L (live if open, realised if closed)
      + realised P&L of the EARLIER closed trades in the same stock today

Where a closed row carries Kite's own day figure for the stock (pinned by the
engine when that close left the stock flat), that figure is the stock's total
at that point: Kite's number is always the one displayed. Later trades build
on it.

Example: RELIANCE #1 closes +500 -> 500. #2 open at +200 -> 700, moving live.
#2 closes +300 -> 800, fixed.

Realised P&L, per closed row, is Kite's too wherever Kite pinned a figure:
the pinned stock total minus what the earlier trades in that stock already
booked. So a trade whose own orders did more than the trade itself (DRREDDY,
2026-09-24: 6 shares bought, 387 sold, 381 bought back by hand) shows what
it really cost, and the closed rows always add up to Total Realised.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

# Rows that count towards the desk's P&L. Skipped, rejected and cancelled
# entries never held anything.
OPEN_STATES = frozenset(
    {"pending_entry", "entry_submitted", "entered", "protected", "trailing", "exit_submitted"}
)
HOLDING_STATES = frozenset({"entered", "protected", "trailing", "exit_submitted"})
CLOSED_STATE = "closed"


@dataclass(frozen=True)
class DeskRow:
    trade_id: str
    tradingsymbol: str
    state: str
    realised_pnl: Optional[float]
    created_at: str
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RowPnl:
    stock_day_total: Optional[float] = None
    # This closed trade's realised P&L as Kite has it (see module docstring);
    # None where there is no closed figure to show.
    realised: Optional[float] = None
    # Kite's pinned day figure differs from our per-trade sum by over Rs 1.
    mismatch: Optional[Dict[str, float]] = None
    # Closed, but no closing order was found at Kite.
    unattributed: bool = False


@dataclass(frozen=True)
class DeskPnl:
    rows: Dict[str, RowPnl]
    total_realised: float
    total_ongoing: float
    ongoing_complete: bool

    @property
    def total_day(self) -> float:
        return round(self.total_realised + self.total_ongoing, 2)


def build_desk_pnl(
    rows: Iterable[DeskRow], live_pnl: Mapping[str, Optional[float]]
) -> DeskPnl:
    by_symbol: Dict[str, List[DeskRow]] = {}
    for row in rows:
        if row.state in OPEN_STATES or row.state == CLOSED_STATE:
            by_symbol.setdefault(row.tradingsymbol, []).append(row)

    out: Dict[str, RowPnl] = {}
    total_realised = 0.0
    total_ongoing = 0.0
    ongoing_complete = True

    for symbol_rows in by_symbol.values():
        # "Earlier" = entered earlier; trade_id breaks exact ties.
        symbol_rows.sort(key=lambda r: (r.created_at, r.trade_id))
        realised_so_far = 0.0
        for row in symbol_rows:
            if row.state == CLOSED_STATE:
                stock_day = row.extra.get("stock_day") or {}
                kite_pnl = stock_day.get("kite_pnl")
                booked_before = realised_so_far
                if kite_pnl is not None:
                    realised_so_far = float(kite_pnl)
                elif row.realised_pnl is not None:
                    realised_so_far += float(row.realised_pnl)
                realised = (
                    round(realised_so_far - booked_before, 2)
                    if kite_pnl is not None or row.realised_pnl is not None
                    else None
                )
                mismatch = None
                if stock_day.get("mismatch"):
                    mismatch = {
                        "kite_pnl": float(stock_day.get("kite_pnl") or 0.0),
                        "ours": float(stock_day.get("ours") or 0.0),
                        "diff": float(stock_day.get("diff") or 0.0),
                    }
                unattributed = row.realised_pnl is None
                out[row.trade_id] = RowPnl(
                    # An unattributed close with no Kite figure has no honest
                    # total to show: blank, not a misleading running sum.
                    stock_day_total=(
                        None
                        if unattributed and kite_pnl is None
                        else round(realised_so_far, 2)
                    ),
                    realised=realised,
                    mismatch=mismatch,
                    unattributed=unattributed,
                )
                continue

            live = live_pnl.get(row.trade_id)
            if row.state not in HOLDING_STATES:
                out[row.trade_id] = RowPnl()
                continue
            if live is None:
                ongoing_complete = False
                out[row.trade_id] = RowPnl()
                continue
            total_ongoing += float(live)
            out[row.trade_id] = RowPnl(stock_day_total=round(realised_so_far + float(live), 2))

        total_realised += realised_so_far

    return DeskPnl(
        rows=out,
        total_realised=round(total_realised, 2),
        total_ongoing=round(total_ongoing, 2),
        ongoing_complete=ongoing_complete,
    )
