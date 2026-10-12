"""Replaying ARB on 1-minute bars: the backtest's walk, built on the live rules.

Two layers, both pure (no I/O):

* ``simulate_exit`` walks one trade bar by bar: the stop is checked on the
  backtest's price path inside each bar (open, then the far side from the
  close, then the near side, then the close), and at each bar's close the
  stop is tightened by the live lock rule (engine_lock). It is a port of
  research trail_research.walk (funnel) and orb.walk (ORB).
* ``replay_day`` runs a whole day of candidates through the live budget
  (engine_arb) with those simulated exits, then the day controls on
  1-minute closes: research hybrid_v2.run followed by redctl.day_manage.

Used by the golden parity tests (tests/test_arb_replay_golden.py) and by the
post-market check (arb_daily_check), which compares what the live engine did
with what these rules do on the same day's bars.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import engine_arb
from engine_arb import ArbSettings, DayLockTracker, DayMtm, FunnelFeatures
from engine_charges import round_trip_charges
from engine_lock import LockBar, LockState, advance
from engine_sizing import RiskCappedSizing
from engine_trailing import is_long, is_tighter
from trading_engine_types import STRATEGY_FUNNEL, STRATEGY_ORB

# The engine squares everything off at 14:50; a bar starting then exits at its open.
SQUAREOFF_HHMM = "14:50"

EXIT_STOP = "stop"            # the initial stop
EXIT_LOCK = "lock"            # a stop the lock rule had moved
EXIT_EOD = "eod"
EXIT_END_OF_DATA = "end_of_data"
EXIT_DAY_HALT = "day_halt"    # flattened by the hard day stop or the day lock

FREE_BY_STOP = "stop"
FREE_BY_CLOSE = "close_1r"


@dataclass(frozen=True)
class ReplayBar:
    """A 1-minute bar. ``minute`` is its start as "YYYY-MM-DDTHH:MM"."""

    minute: str
    open: float
    high: float
    low: float
    close: float

    @property
    def hhmm(self) -> str:
        return self.minute[11:16]


@dataclass(frozen=True)
class ExitResult:
    price: float
    minute: str
    reason: str
    # Every stop the trade had: (minute it took effect after, stop price).
    stops: Tuple[Tuple[str, float], ...]
    lock: LockState


def bar_path(bar: ReplayBar) -> Tuple[float, float, float, float]:
    """The backtest's order of prices inside a bar: an up bar visits its low
    first, a down bar its high first."""
    if bar.close >= bar.open:
        return (bar.open, bar.low, bar.high, bar.close)
    return (bar.open, bar.high, bar.low, bar.close)


def _hit(price: float, stop: float, long: bool) -> bool:
    return price <= stop if long else price >= stop


def simulate_exit(
    *,
    strategy: str,
    direction: str,
    entry: float,
    stop: float,
    tick_size: float,
    bars: Sequence[ReplayBar],
    entry_minute: str,
    squareoff_hhmm: str = SQUAREOFF_HHMM,
) -> Optional[ExitResult]:
    """Walk one trade from its entry minute. None when there are no bars.

    ``entry`` is the price the trade is taken at and ``stop`` its initial
    stop; R is the distance between them.
    """
    long = is_long(direction)
    r_pts = abs(float(entry) - float(stop))
    current = float(stop)
    lock = LockState()
    stops: List[Tuple[str, float]] = [(entry_minute, current)]
    walked = [bar for bar in bars if bar.minute >= entry_minute]
    if not walked or r_pts <= 0:
        return None

    def done(price: float, minute: str, reason: str) -> ExitResult:
        return ExitResult(float(price), minute, reason, tuple(stops), lock)

    for bar in walked:
        if bar.hhmm >= squareoff_hhmm:
            return done(bar.open, bar.minute, EXIT_EOD)
        entry_bar = bar.minute == entry_minute
        reason = EXIT_STOP if current == float(stop) else EXIT_LOCK
        path = bar_path(bar)
        if entry_bar and strategy == STRATEGY_ORB:
            # Conservative: a breakout bar that also traded through the stop
            # is taken as stopped.
            if _hit(bar.low if long else bar.high, current, long):
                return done(current, bar.minute, reason)
        else:
            start = 0
            if entry_bar:
                # Only prices after the one that reached the entry count.
                crossed = [
                    i for i, p in enumerate(path) if (p >= entry if long else p <= entry)
                ]
                start = (crossed[0] + 1) if crossed else 3
            for index in range(start, 4):
                price = path[index]
                if _hit(price, current, long):
                    # A gap through the stop at the open fills at the open.
                    if index == 0:
                        return done(min(price, current) if long else max(price, current), bar.minute, reason)
                    return done(current, bar.minute, reason)
        lock = advance(
            lock,
            LockBar(bar.minute, bar.high, bar.low, bar.close),
            direction=direction,
            entry=float(entry),
            initial_stop=float(stop),
            tick_size=float(tick_size),
            entry_bar=entry_bar,
        )
        if lock.target is not None and is_tighter(lock.target, current, direction):
            current = float(lock.target)
            stops.append((bar.minute, current))
    last = walked[-1]
    return done(last.close, last.minute, EXIT_END_OF_DATA)


def stop_at(result: ExitResult, minute: str) -> float:
    """The stop in force during ``minute`` (set by bars that closed before it)."""
    current = result.stops[0][1]
    for moved_after, price in result.stops[1:]:
        if moved_after < minute:
            current = price
    return current


# -----------------------------------------------------------------------------
# A whole day
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplayCandidate:
    """One signal, as the replay needs it."""

    setup_id: str
    strategy: str          # STRATEGY_FUNNEL | STRATEGY_ORB
    symbol: str
    direction: str
    minute: str            # the trigger's minute, "YYYY-MM-DDTHH:MM"
    moment: str            # full timestamp text, for ordering within a minute
    trigger_price: float
    stop: Optional[float]
    tick_size: float
    # Funnel only: what the engine's context reader gives for this trigger.
    features: Optional[FunnelFeatures] = None
    # ORB only: False when the trigger fired at or after the ORB cutoff.
    orb_in_window: bool = True


@dataclass
class ReplayTrade:
    candidate: ReplayCandidate
    decision: str                       # "enter" | "skip"
    reason: Optional[str] = None
    score: Optional[int] = None
    base_risk: float = 0.0
    risk_used: float = 0.0
    half_size: bool = False
    one_share_floor: bool = False
    qty: int = 0
    exit: Optional[ExitResult] = None
    exit_price: Optional[float] = None
    exit_minute: Optional[str] = None
    exit_reason: Optional[str] = None
    gross: float = 0.0
    charges: float = 0.0
    net: float = 0.0
    r_gross: Optional[float] = None
    info: Dict[str, object] = field(default_factory=dict)

    @property
    def entered(self) -> bool:
        return self.decision == "enter"

    @property
    def up(self) -> bool:
        return is_long(self.candidate.direction)


@dataclass(frozen=True)
class DayReplay:
    trades: Tuple[ReplayTrade, ...]
    halt_minute: Optional[str]
    halt_reason: Optional[str]          # CloseReason value
    net: float
    r_gross: float


def _net_at(trade: ReplayTrade, price: float, slip_rate: float) -> float:
    c = trade.candidate
    sign = 1.0 if trade.up else -1.0
    gross = (float(price) - c.trigger_price) * sign * trade.qty
    charges = round_trip_charges(
        direction=c.direction, qty=trade.qty, entry=c.trigger_price, exit_price=float(price)
    )
    return gross - charges - slip_rate * c.trigger_price * trade.qty


def _close_before(bars: Sequence[ReplayBar], minute: str, since: str) -> Optional[float]:
    """The last close among bars with since <= bar.minute < minute."""
    price = None
    for bar in bars:
        if bar.minute < since:
            continue
        if bar.minute >= minute:
            break
        price = bar.close
    return price


def _live_risk(
    held: "ReplayTrade", bars: Sequence[ReplayBar], minute: str, free_rule: str
) -> float:
    """Rupees an open trade still has at risk when a new candidate arrives."""
    c = held.candidate
    sign = 1.0 if held.up else -1.0
    r_pts = abs(c.trigger_price - float(c.stop))
    if free_rule == FREE_BY_CLOSE:
        for bar in bars:
            if bar.minute < c.minute:
                continue
            if bar.minute >= minute:
                break
            if (bar.close - c.trigger_price) * sign >= r_pts:
                return 0.0
        return held.qty * r_pts
    stop_now = stop_at(held.exit, minute) if held.exit is not None else float(c.stop)
    return max(0.0, (c.trigger_price - stop_now) * sign * held.qty)


def _close_upto(bars: Sequence[ReplayBar], minute: str, since: str) -> Optional[float]:
    """The last close among bars with since <= bar.minute <= minute."""
    price = None
    for bar in bars:
        if bar.minute < since:
            continue
        if bar.minute > minute:
            break
        price = bar.close
    return price


def replay_day(
    candidates: Sequence[ReplayCandidate],
    bars_by_symbol: Mapping[str, Sequence[ReplayBar]],
    settings: ArbSettings,
    *,
    capital_rupees: float,
    leverage: float = 5.0,
    slip_rate: float = 0.0,
    sizing: Optional[RiskCappedSizing] = None,
    free_rule: str = FREE_BY_STOP,
) -> DayReplay:
    """Run one day's candidates, in time order, through the live rules.

    The day state a candidate sees is built the way the backtest built it:
    trades already out count their final net; trades still open are marked
    at the last 1-minute close before the candidate's minute.

    ``free_rule`` says when an open trade stops using the open-risk budget:
    FREE_BY_STOP is the live engine's rule (its stop is at or beyond entry,
    PR A D6); FREE_BY_CLOSE is the backtest's (a 1-minute close at +1R).
    """
    sizing = sizing or RiskCappedSizing()
    # Stable: candidates with the same timestamp keep the order they came in
    # (the backtest's order for ORB breakouts in one bar is their RVOL rank).
    ordered = sorted(candidates, key=lambda c: c.moment)
    out: List[ReplayTrade] = []
    taken: List[ReplayTrade] = []
    open_until: Dict[str, str] = {}
    score0_taken = 0

    for cand in ordered:
        trade = ReplayTrade(candidate=cand, decision="skip")
        out.append(trade)
        if open_until.get(cand.symbol, "") > cand.minute:
            # Still in the stock: its exit bar starts after this trigger's minute.
            trade.reason = "symbol_already_open"
            continue

        if cand.strategy == STRATEGY_ORB:
            if not cand.orb_in_window:
                trade.reason = engine_arb.REASON_ORB_OUTSIDE_WINDOW
                continue
            if cand.stop is None:
                trade.reason = engine_arb.REASON_ORB_NO_STOP
                continue
            base = settings.orb_risk_rupees()
        elif cand.strategy == STRATEGY_FUNNEL:
            if cand.features is None:
                trade.reason = "arb_context_unavailable"
                continue
            verdict = engine_arb.classify_funnel(
                cand.features, settings, score0_taken_today=score0_taken
            )
            trade.score = verdict.score
            if not verdict.ok:
                trade.reason = verdict.reason
                continue
            base = verdict.base_risk_rupees
        else:
            trade.reason = engine_arb.REASON_UNKNOWN_STRATEGY
            continue

        mtm = 0.0
        live_risk = 0.0
        losers = 0
        margin = 0.0
        for held in taken:
            if held.exit_minute is not None and held.exit_minute <= cand.minute:
                mtm += held.net
                losers += held.net < 0
                continue
            margin += held.qty * held.candidate.trigger_price / leverage
            bars = bars_by_symbol.get(held.candidate.symbol, ())
            price = _close_before(bars, cand.minute, held.candidate.minute)
            if price is not None:
                mtm += _net_at(held, price, slip_rate)
            live_risk += _live_risk(held, bars, cand.minute, free_rule)

        decision = engine_arb.allowed_risk(
            settings,
            base_rupees=base,
            losers_today=losers,
            day_mtm_rupees=mtm,
            live_risk_rupees=live_risk,
        )
        trade.base_risk = decision.base_rupees
        trade.half_size = decision.cut
        trade.info = {"day_mtm": round(mtm, 2), "live_risk": round(live_risk, 2), "losers": losers}
        if decision.risk_rupees <= 0:
            trade.reason = decision.reason
            continue
        risk, floored = engine_arb.apply_one_share_floor(
            settings,
            risk_rupees=decision.risk_rupees,
            risk_per_share=abs(cand.trigger_price - float(cand.stop)),
        )
        trade.one_share_floor = floored
        size = sizing.decide(
            entry_price=cand.trigger_price,
            stop_price=float(cand.stop),
            risk_cap_rupees=risk,
            available_capital_rupees=max(0.0, capital_rupees - margin),
            leverage_factor=leverage,
        )
        if size.qty <= 0:
            trade.reason = "sized_to_zero"
            continue
        result = simulate_exit(
            strategy=cand.strategy,
            direction=cand.direction,
            entry=cand.trigger_price,
            stop=float(cand.stop),
            tick_size=cand.tick_size,
            bars=bars_by_symbol.get(cand.symbol, ()),
            entry_minute=cand.minute,
        )
        if result is None:
            trade.reason = "no_bars"
            continue
        trade.decision = "enter"
        trade.risk_used = risk
        trade.qty = int(size.qty)
        trade.exit = result
        _book_exit(trade, result.price, result.minute, result.reason, slip_rate)
        taken.append(trade)
        open_until[cand.symbol] = result.minute
        if cand.strategy == STRATEGY_FUNNEL and trade.score == 0:
            score0_taken += 1

    halt_minute, halt_reason = _day_controls(taken, bars_by_symbol, settings, slip_rate)
    if halt_minute is not None:
        for trade in out:
            if not trade.entered:
                if trade.candidate.minute > halt_minute and trade.reason is None:
                    trade.reason = "day_halted"
                continue
            if trade.candidate.minute > halt_minute:
                # Taken by the first pass, but the day had already stopped.
                trade.decision = "skip"
                trade.reason = "day_halted"
                trade.qty = 0
                trade.net = trade.gross = trade.charges = 0.0
                trade.r_gross = None
                trade.exit = None
                trade.exit_price = trade.exit_minute = trade.exit_reason = None
                continue
            if trade.exit_minute is not None and trade.exit_minute <= halt_minute:
                continue
            bars = bars_by_symbol.get(trade.candidate.symbol, ())
            price = _close_upto(bars, halt_minute, trade.candidate.minute)
            _book_exit(
                trade,
                trade.candidate.trigger_price if price is None else price,
                halt_minute,
                EXIT_DAY_HALT,
                slip_rate,
            )
    entered = [t for t in out if t.entered]
    return DayReplay(
        trades=tuple(out),
        halt_minute=halt_minute,
        halt_reason=halt_reason,
        net=round(sum(t.net for t in entered), 2),
        r_gross=round(sum(t.r_gross or 0.0 for t in entered), 4),
    )


def _book_exit(
    trade: ReplayTrade, price: float, minute: str, reason: str, slip_rate: float
) -> None:
    c = trade.candidate
    sign = 1.0 if trade.up else -1.0
    trade.exit_price = float(price)
    trade.exit_minute = minute
    trade.exit_reason = reason
    trade.gross = (float(price) - c.trigger_price) * sign * trade.qty
    trade.charges = round_trip_charges(
        direction=c.direction, qty=trade.qty, entry=c.trigger_price, exit_price=float(price)
    )
    trade.net = trade.gross - trade.charges - slip_rate * c.trigger_price * trade.qty
    r_pts = abs(c.trigger_price - float(c.stop))
    trade.r_gross = (float(price) - c.trigger_price) * sign / r_pts if r_pts > 0 else None


def _day_controls(
    taken: Sequence[ReplayTrade],
    bars_by_symbol: Mapping[str, Sequence[ReplayBar]],
    settings: ArbSettings,
    slip_rate: float,
) -> Tuple[Optional[str], Optional[str]]:
    """redctl.day_manage: the day lock, then the hard stop, on 1-minute closes."""
    if not taken:
        return None, None
    paths: List[Dict[str, float]] = []
    minutes = set()
    for trade in taken:
        path = {
            bar.minute: bar.close
            for bar in bars_by_symbol.get(trade.candidate.symbol, ())
            if trade.candidate.minute <= bar.minute
            and (trade.exit_minute is None or bar.minute < trade.exit_minute)
        }
        paths.append(path)
        minutes.update(path)
        if trade.exit_minute is not None:
            minutes.add(trade.exit_minute)
    tracker = DayLockTracker(settings)
    last_close: List[Optional[float]] = [None] * len(taken)
    for minute in sorted(minutes):
        equity = 0.0
        for index, trade in enumerate(taken):
            if trade.candidate.minute > minute:
                continue
            if trade.exit_minute is not None and trade.exit_minute <= minute:
                equity += trade.net
                continue
            if minute in paths[index]:
                last_close[index] = paths[index][minute]
            if last_close[index] is not None:
                equity += _net_at(trade, last_close[index], slip_rate)
        when = datetime.strptime(minute, "%Y-%m-%dT%H:%M")
        if tracker.observe(when, DayMtm(mtm=equity, closed_net=0.0, open_net=0.0)):
            return minute, "day_lock"
        if engine_arb.hard_stop_hit(settings, equity):
            return minute, "hard_day_stop"
    return None, None
