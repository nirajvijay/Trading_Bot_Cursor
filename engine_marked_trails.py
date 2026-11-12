"""ARB marked-stock trails (2026-11-12 plan), as pure functions. No I/O.

Behind one setting, ``ArbSettings.marked_trails_enabled`` (off by default).
With it on, three rules apply to both ARB sleeves (ORB and GUC):

1. **Stock filter.** Only stocks in the rules table (config/marked_trails.json)
   are traded. ORB still ranks every stock; a selected top-3 stock that is
   not marked is skipped and its slot stays empty (no next-ranked stock is
   pulled in). A GUC trigger on an unmarked stock is skipped.
2. **Level filter.** Each stock has its average day high and low from the
   open (``avg_high_pct`` / ``avg_low_pct``). A buy is taken only if its
   entry is at or below open * (1 + avg_high), a sell only if at or above
   open * (1 - avg_low). This applies to both sides of a marked stock.
3. **Trail after touch.** Level L = open * (1 + avg_high) for buys,
   open * (1 - avg_low) for sells. Once a completed 1-minute bar after the
   entry minute reaches L, the trade's stop switches from the normal stop /
   lock ratchet to a trailing stop p% behind the running high (buys) or low
   (sells) since that bar. From then on it only moves one way. Modes:

   * ``plain``: stop = run * (1 - p) (buy) or run * (1 + p) (sell).
   * ``level_floor`` (LODHA, DMART buys): never below L.
   * ``level_cap`` (LTM sell): starts at L * (1 + cap), then
     min(that, run * (1 + p)).

   A marked stock's trade on a side with no rule (e.g. a DMART sell: DMART
   has a buy rule only) is still taken if within level, with normal exits.

The trail switch may LOOSEN the stop (below the lock stop, even below the
initial stop for wide trails such as TRENT 4%): the research replaces the
stop outright, so the engine does too. See resolve_trail_stop.

Day halts are unchanged: a halt that flattens a sleeve flattens its trailed
trades too (they have no exemption). Research and the per-stock studies are
in /Users/nj/nifty-radar-research/plan_marked_trails_2026-11/REPORT.md.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from engine_trailing import is_long, is_tighter, round_stop

DEFAULT_TABLE_PATH = Path(__file__).resolve().parent / "config" / "marked_trails.json"

MODE_PLAIN = "plain"
MODE_LEVEL_FLOOR = "level_floor"
MODE_LEVEL_CAP = "level_cap"
BUY_MODES = (MODE_PLAIN, MODE_LEVEL_FLOOR)
SELL_MODES = (MODE_PLAIN, MODE_LEVEL_CAP)

# Skip reasons (stored on the skipped row).
REASON_UNMARKED = "arb_marked_unmarked_stock"
REASON_OUTSIDE_LEVEL = "arb_marked_outside_level"
REASON_OPEN_UNKNOWN = "arb_marked_open_unknown"
REASON_TABLE_UNAVAILABLE = "arb_marked_table_unavailable"

# position.extra key holding the plan stamped at entry. Written once: a
# restart or a later table edit never changes an open trade's rule.
MARKED_EXTRA_KEY = "marked_trail"
# position.extra key set once the trail has taken over the stop (the touch
# minute). Until then the first write may loosen; after it, tighten only.
MARKED_SWITCHED_KEY = "marked_trail_switched"

# Close reason when the trail stop is at or through the market (the research
# fills those at the next bar's open; Kite would refuse the stop).
EXIT_TRAIL_THROUGH_MARKET = "marked_trail_through_market"


class MarkedTableError(ValueError):
    """The rules table is missing or malformed."""


# -----------------------------------------------------------------------------
# The rules table
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class TrailRule:
    trail_pct: float
    mode: str = MODE_PLAIN
    cap_pct: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"trail_pct": self.trail_pct, "mode": self.mode}
        if self.mode == MODE_LEVEL_CAP:
            out["cap_pct"] = self.cap_pct
        return out


@dataclass(frozen=True)
class StockMarks:
    symbol: str
    avg_high_pct: float
    avg_low_pct: float
    buy: Optional[TrailRule] = None
    sell: Optional[TrailRule] = None
    below_actual: bool = False

    def rule_for(self, direction: str) -> Optional[TrailRule]:
        return self.buy if is_long(direction) else self.sell

    def avg_pct(self, direction: str) -> float:
        return self.avg_high_pct if is_long(direction) else self.avg_low_pct


def _positive(value: Any, what: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise MarkedTableError(f"{what} must be a number") from None
    if not math.isfinite(number) or number <= 0:
        raise MarkedTableError(f"{what} must be greater than zero")
    return number


def _rule(raw: Any, symbol: str, side: str) -> Optional[TrailRule]:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise MarkedTableError(f"{symbol} {side}: rule must be an object")
    mode = str(raw.get("mode") or MODE_PLAIN)
    allowed = BUY_MODES if side == "buy" else SELL_MODES
    if mode not in allowed:
        raise MarkedTableError(f"{symbol} {side}: mode {mode!r} not in {allowed}")
    pct = _positive(raw.get("trail_pct"), f"{symbol} {side} trail_pct")
    if pct >= 20:
        raise MarkedTableError(f"{symbol} {side}: trail_pct {pct} is not a percent")
    cap = _positive(raw.get("cap_pct"), f"{symbol} {side} cap_pct") if mode == MODE_LEVEL_CAP else 0.0
    return TrailRule(trail_pct=pct, mode=mode, cap_pct=cap)


@dataclass(frozen=True)
class MarkedTrailBook:
    """The marked stocks, read from the rules table."""

    stocks: Mapping[str, StockMarks]
    generated: str = ""
    source: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MarkedTrailBook":
        if not isinstance(data, Mapping) or not isinstance(data.get("stocks"), Mapping):
            raise MarkedTableError("table needs a 'stocks' object")
        stocks: Dict[str, StockMarks] = {}
        for symbol, raw in data["stocks"].items():
            if not isinstance(raw, Mapping):
                raise MarkedTableError(f"{symbol}: entry must be an object")
            marks = StockMarks(
                symbol=str(symbol),
                avg_high_pct=_positive(raw.get("avg_high_pct"), f"{symbol} avg_high_pct"),
                avg_low_pct=_positive(raw.get("avg_low_pct"), f"{symbol} avg_low_pct"),
                buy=_rule(raw.get("buy"), str(symbol), "buy"),
                sell=_rule(raw.get("sell"), str(symbol), "sell"),
                below_actual=bool(raw.get("below_actual", False)),
            )
            if marks.buy is None and marks.sell is None:
                raise MarkedTableError(f"{symbol}: neither a buy nor a sell rule")
            stocks[str(symbol)] = marks
        if not stocks:
            raise MarkedTableError("table has no stocks")
        return cls(stocks=stocks, generated=str(data.get("generated") or ""),
                   source=str(data.get("source") or ""))

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "MarkedTrailBook":
        target = Path(path) if path is not None else DEFAULT_TABLE_PATH
        try:
            data = json.loads(target.read_text())
        except (OSError, ValueError) as exc:
            raise MarkedTableError(f"cannot read {target}: {exc}") from exc
        return cls.from_dict(data)

    def without_below_actual(self) -> "MarkedTrailBook":
        """The book with the marks that lose to the normal exits in their own
        study (HINDUNILVR, HINDZINC) removed."""
        kept = {s: m for s, m in self.stocks.items() if not m.below_actual}
        return MarkedTrailBook(stocks=kept, generated=self.generated, source=self.source)

    def get(self, symbol: str) -> Optional[StockMarks]:
        return self.stocks.get(str(symbol))

    def __contains__(self, symbol: object) -> bool:
        return str(symbol) in self.stocks

    @property
    def rule_counts(self) -> Tuple[int, int]:
        return (
            sum(1 for m in self.stocks.values() if m.buy is not None),
            sum(1 for m in self.stocks.values() if m.sell is not None),
        )


# -----------------------------------------------------------------------------
# Rules 1 and 2: the entry check
# -----------------------------------------------------------------------------


def level_price(day_open: float, direction: str, avg_pct: float) -> float:
    """L: the stock's average day high (buy) or low (sell) from the open."""
    if is_long(direction):
        return float(day_open) * (1.0 + float(avg_pct) / 100.0)
    return float(day_open) * (1.0 - float(avg_pct) / 100.0)


def within_level(entry: float, level: float, direction: str) -> bool:
    """Rule 2: a buy at or below L, a sell at or above L."""
    return float(entry) <= float(level) if is_long(direction) else float(entry) >= float(level)


@dataclass(frozen=True)
class EntryCheck:
    ok: bool
    reason: Optional[str] = None
    # Stamped on the position at entry (MARKED_EXTRA_KEY); also recorded on
    # a skip so the desk shows why.
    plan: Optional[Dict[str, Any]] = None


def check_entry(
    book: MarkedTrailBook,
    *,
    symbol: str,
    direction: str,
    entry_price: float,
    day_open: Optional[float],
) -> EntryCheck:
    """Rules 1 and 2 for one trigger. Fails closed on an unknown open."""
    marks = book.get(symbol)
    if marks is None:
        return EntryCheck(False, REASON_UNMARKED, {"symbol": symbol})
    if day_open is None or not math.isfinite(float(day_open)) or float(day_open) <= 0:
        return EntryCheck(False, REASON_OPEN_UNKNOWN, {"symbol": symbol})
    avg = marks.avg_pct(direction)
    level = level_price(float(day_open), direction, avg)
    rule = marks.rule_for(direction)
    plan = {
        "symbol": symbol,
        "direction": "UP" if is_long(direction) else "DOWN",
        "day_open": float(day_open),
        "avg_pct": avg,
        "level": round(level, 6),
        "entry_price": float(entry_price),
        "rule": None if rule is None else rule.as_dict(),
        "table_generated": book.generated,
    }
    if not within_level(entry_price, level, direction):
        return EntryCheck(False, REASON_OUTSIDE_LEVEL, plan)
    return EntryCheck(True, None, plan)


def rule_from_plan(plan: Optional[Mapping[str, Any]]) -> Optional[TrailRule]:
    """The trail rule stamped on a position, or None (normal exits)."""
    if not plan or not plan.get("rule"):
        return None
    raw = plan["rule"]
    return TrailRule(
        trail_pct=float(raw["trail_pct"]),
        mode=str(raw.get("mode") or MODE_PLAIN),
        cap_pct=float(raw.get("cap_pct") or 0.0),
    )


# -----------------------------------------------------------------------------
# Rule 3: the trail after the touch
# -----------------------------------------------------------------------------


def trail_stop_raw(run: float, level: float, direction: str, rule: TrailRule) -> float:
    """The trail stop for a running high (buy) / low (sell), before rounding."""
    p = float(rule.trail_pct) / 100.0
    if is_long(direction):
        stop = float(run) * (1.0 - p)
        if rule.mode == MODE_LEVEL_FLOOR:
            stop = max(float(level), stop)
        return stop
    stop = float(run) * (1.0 + p)
    if rule.mode == MODE_LEVEL_CAP:
        stop = min(float(level) * (1.0 + float(rule.cap_pct) / 100.0), stop)
    return stop


@dataclass(frozen=True)
class TrailBar:
    """One completed 1-minute bar (only minute, high and low are used)."""

    minute: str
    high: float
    low: float


@dataclass(frozen=True)
class TrailState:
    bars: int = 0
    last_minute: Optional[str] = None
    touched_minute: Optional[str] = None
    # Best price since (and including) the touch bar; never worse than L.
    run: Optional[float] = None
    # The trail stop asked for, on the tick grid when a tick size was given.
    target: Optional[float] = None

    @property
    def touched(self) -> bool:
        return self.touched_minute is not None


def advance_trail(
    state: TrailState,
    bar: TrailBar,
    *,
    direction: str,
    level: float,
    rule: TrailRule,
    tick_size: Optional[float] = None,
) -> TrailState:
    """Fold one completed bar after the entry minute into the trail state."""
    long = is_long(direction)
    run = state.run
    touched = state.touched_minute
    if touched is None:
        reached = bar.high >= level if long else bar.low <= level
        if not reached:
            return TrailState(state.bars + 1, bar.minute, None, None, None)
        touched = bar.minute
        run = max(float(level), float(bar.high)) if long else min(float(level), float(bar.low))
    else:
        run = max(float(run), float(bar.high)) if long else min(float(run), float(bar.low))
    raw = trail_stop_raw(run, level, direction, rule)
    target = raw if tick_size is None else round_stop(raw, float(tick_size), direction)
    if state.target is not None and not is_tighter(target, state.target, direction):
        target = state.target  # one way only
    return TrailState(state.bars + 1, bar.minute, touched, run, target)


def fold_trail(
    bars: Sequence[Any],
    *,
    direction: str,
    level: float,
    rule: TrailRule,
    entry_minute: str,
    tick_size: Optional[float] = None,
    state: Optional[TrailState] = None,
) -> TrailState:
    """Fold bars (ascending, anything with minute/high/low) onto ``state``.
    The entry minute's own bar and bars already folded are ignored: the touch
    is judged on bars that completed while the trade was held."""
    current = state or TrailState()
    for bar in bars:
        minute = str(bar.minute)
        if minute <= entry_minute:
            continue
        if current.last_minute is not None and minute <= current.last_minute:
            continue
        current = advance_trail(
            current,
            TrailBar(minute, float(bar.high), float(bar.low)),
            direction=direction,
            level=level,
            rule=rule,
            tick_size=tick_size,
        )
    return current


@dataclass(frozen=True)
class TrailMove:
    """What the engine should do with a trailed position's stop now."""

    action: str                 # "none" | "move" | "flatten"
    target: Optional[float] = None
    reason: Optional[str] = None


def resolve_trail_stop(
    *,
    direction: str,
    current: float,
    target: float,
    tick_size: float,
    switched: bool,
    ltp: Optional[float],
) -> TrailMove:
    """Decide the stop write for a trailed position.

    Unlike engine_trailing.resolve_stop, the first write after the touch
    (``switched`` False) may loosen the stop, even past the initial stop:
    the trail replaces the normal stop outright. After that it only tightens.
    A target at or through the market cannot be placed (Kite refuses it):
    the position is flattened instead, as the backtest fills it at once.
    """
    target = round_stop(float(target), float(tick_size), direction)
    half_tick = float(tick_size) / 2.0
    if abs(target - float(current)) < half_tick:
        return TrailMove("none", reason="unchanged")
    if switched and not is_tighter(target, float(current), direction):
        return TrailMove("none", reason="trail_cannot_loosen")
    if ltp is None:
        return TrailMove("none", reason="no_price")
    through = target >= float(ltp) if is_long(direction) else target <= float(ltp)
    if through:
        return TrailMove("flatten", target, EXIT_TRAIL_THROUGH_MARKET)
    return TrailMove("move", target)


# -----------------------------------------------------------------------------
# The backtest walk (arb_replay and the parity check)
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class TrailExit:
    price: float
    minute: str
    reason: str            # "trail" | "eod"
    touched_minute: str


def walk_trail(
    bars: Sequence[Any],
    *,
    direction: str,
    level: float,
    rule: TrailRule,
    entry_minute: str,
    touch_until: str,
    touch_inclusive: bool,
    squareoff_hhmm: str = "14:50",
    research_eod: bool = False,
    tick_size: Optional[float] = None,
) -> Optional[TrailExit]:
    """Walk a trade from its touch bar; None when L is never reached.

    ``bars`` carry minute ("YYYY-MM-DDTHH:MM"), open, high, low, close.
    The touch is looked for in bars after the entry minute up to the normal
    exit's minute ``touch_until`` (inclusive when ``touch_inclusive``: the
    research scans up to and including the exit bar; live can only switch
    on a bar that completed before the normal exit). From the next bar the
    trail stop is checked: a gap through it fills at the open, else at the
    stop. ``research_eod`` reproduces the research's end of day (check the
    14:50 bar too, then exit at its close) instead of the engine's 14:50
    open square-off.
    """
    long = is_long(direction)
    state = TrailState()
    walked = [b for b in bars if str(b.minute) > entry_minute]
    for index, bar in enumerate(walked):
        minute = str(bar.minute)
        beyond = minute > touch_until if touch_inclusive else minute >= touch_until
        if beyond or minute[11:16] > squareoff_hhmm:
            return None
        state = advance_trail(state, TrailBar(minute, float(bar.high), float(bar.low)),
                              direction=direction, level=level, rule=rule, tick_size=tick_size)
        if state.touched:
            rest = walked[index + 1:]
            break
    else:
        return None
    last = walked[index]
    for bar in rest:
        hhmm = str(bar.minute)[11:16]
        if hhmm > squareoff_hhmm:
            break
        if hhmm == squareoff_hhmm and not research_eod:
            return TrailExit(float(bar.open), str(bar.minute), "eod", state.touched_minute)
        stop = float(state.target)
        if (bar.low <= stop) if long else (bar.high >= stop):
            price = min(stop, float(bar.open)) if long else max(stop, float(bar.open))
            return TrailExit(price, str(bar.minute), "trail", state.touched_minute)
        last = bar
        if hhmm == squareoff_hhmm:
            return TrailExit(float(bar.close), str(bar.minute), "eod", state.touched_minute)
        state = advance_trail(state, TrailBar(str(bar.minute), float(bar.high), float(bar.low)),
                              direction=direction, level=level, rule=rule, tick_size=tick_size)
    return TrailExit(float(last.close), str(last.minute), "eod", state.touched_minute)
