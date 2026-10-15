"""ARB (Adaptive Risk Budget): the strategy's decisions, as pure functions.

No I/O. The engine gathers the facts (positions, prices, market context) and
this module decides. It is a port of the backtest behind
Reference/arb_strategy_2026-09/REPORT.md:

* funnel filters and score: research port3.base, port4.score, combo2.fun_cands
* risk per trade: hybrid_v2.run(H=1e9, house=1.0, open_cap=6000, cut_after=1)
* hard day stop and day lock: redctl.day_manage(arm=3000, keep=0.4, hard=5000)

Every rupee figure is multiplied by ``risk_scale`` (the rollout stage). The
keep fraction, the R rules and the times are never scaled.

See Reference/arb_strategy_2026-09/PR_A_PLAN.md for the decisions (D1-D16)
this module implements.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from engine_charges import round_trip_charges
from engine_types import ExecutionState, Position

STRATEGY_FUNNEL = "FUNNEL"
STRATEGY_ORB = "ORB"

# The rollout stages (IMPLEMENTATION_IDEA.md section 6). Anything else is refused.
ALLOWED_RISK_SCALES = (0.05, 0.25, 0.5, 1.0)
# The day-lock keep fraction the robustness tests support. At 0.45 and above
# results collapse with 5 bps of slippage.
KEEP_MIN, KEEP_MAX = 0.35, 0.40

# position.extra key holding the ARB facts stamped at entry.
ARB_EXTRA_KEY = "arb"

# Skip reasons, as stored on the skipped row.
REASON_NO_TRIGGER_TIME = "arb_no_trigger_time"
REASON_AFTER_CUTOFF = "arb_after_funnel_cutoff"
REASON_VOLUME = "arb_volume_below_min"
REASON_NO_STOP = "arb_no_structural_stop"
REASON_STOP_TOO_TIGHT = "arb_stop_below_min_pct"
REASON_NOT_FIRST = "arb_not_first_trigger_today"
REASON_SCORE0_LIMIT = "arb_score0_limit"
REASON_BELOW_MIN_RISK = "arb_below_min_risk"

PENDING_STATES = frozenset({ExecutionState.PENDING_ENTRY, ExecutionState.ENTRY_SUBMITTED})
HOLDING_STATES = frozenset(
    {
        ExecutionState.ENTERED,
        ExecutionState.PROTECTED,
        ExecutionState.TRAILING,
        ExecutionState.EXIT_SUBMITTED,
    }
)


class InvalidArbSettings(ValueError):
    """ARB settings that cannot be traded safely."""


# -----------------------------------------------------------------------------
# Settings
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ArbSettings:
    """The full-size (100%) ARB numbers and the stage multiplier.

    Rupee fields are the full-size values; read them through the ``*_rupees``
    methods, which apply ``risk_scale``.
    """

    risk_scale: float = 1.0
    orb_risk: float = 2500.0
    funnel_risks: Tuple[float, float, float, float] = (1000.0, 2500.0, 4500.0, 6000.0)
    max_score0_per_day: int = 2
    open_risk_cap: float = 6000.0
    house_money_mult: float = 1.0
    cut_after_losers: int = 1
    cut_mult: float = 0.5
    min_risk: float = 500.0
    hard_day_stop: float = 5000.0
    day_lock_arm: float = 3000.0
    day_lock_keep: float = 0.40
    closed_loss_backstop: float = 6000.0
    # Funnel filters and score.
    funnel_cutoff: time = time(13, 0)
    min_volume_ratio: float = 2.0
    min_stop_pct: float = 0.25
    score_market_against_pct: float = -0.2
    score_volume_ratio: float = 6.0
    score_early_before: time = time(10, 30)
    # Micro stage only: buy one share when the size rounds to zero and one
    # share risks no more than this multiple of the allowed risk.
    one_share_floor_mult: float = 1.25
    one_share_floor_below_scale: float = 0.25
    # A position whose last Kite LTP is older than this counts at its stop.
    stale_price_seconds: float = 3.0

    def _rupees(self, value: float) -> float:
        return float(value) * float(self.risk_scale)

    def funnel_risk_rupees(self, score: int) -> float:
        index = max(0, min(int(score), len(self.funnel_risks) - 1))
        return self._rupees(self.funnel_risks[index])

    def orb_risk_rupees(self) -> float:
        return self._rupees(self.orb_risk)

    def open_risk_cap_rupees(self) -> float:
        return self._rupees(self.open_risk_cap)

    def min_risk_rupees(self) -> float:
        return self._rupees(self.min_risk)

    def hard_day_stop_rupees(self) -> float:
        return self._rupees(self.hard_day_stop)

    def day_lock_arm_rupees(self) -> float:
        return self._rupees(self.day_lock_arm)

    def closed_loss_backstop_rupees(self) -> float:
        return self._rupees(self.closed_loss_backstop)

    def max_trade_risk_rupees(self) -> float:
        return max(self.funnel_risk_rupees(i) for i in range(len(self.funnel_risks)))

    @property
    def one_share_floor_active(self) -> bool:
        return float(self.risk_scale) < float(self.one_share_floor_below_scale)


def validate_settings(settings: ArbSettings) -> None:
    """Raise InvalidArbSettings unless these settings are safe to start with."""
    if not any(math.isclose(settings.risk_scale, s) for s in ALLOWED_RISK_SCALES):
        raise InvalidArbSettings(
            f"risk_scale must be one of {ALLOWED_RISK_SCALES}, got {settings.risk_scale!r}"
        )
    if len(settings.funnel_risks) != 4:
        raise InvalidArbSettings("funnel_risks needs four values: score 0, 1, 2, 3+")
    positive = {
        "orb_risk": settings.orb_risk,
        "open_risk_cap": settings.open_risk_cap,
        "min_risk": settings.min_risk,
        "hard_day_stop": settings.hard_day_stop,
        "day_lock_arm": settings.day_lock_arm,
        "closed_loss_backstop": settings.closed_loss_backstop,
        "min_volume_ratio": settings.min_volume_ratio,
        "min_stop_pct": settings.min_stop_pct,
        "stale_price_seconds": settings.stale_price_seconds,
    }
    for index, value in enumerate(settings.funnel_risks):
        positive[f"funnel_risks[{index}]"] = value
    for name, value in positive.items():
        if not (isinstance(value, (int, float)) and math.isfinite(value) and value > 0):
            raise InvalidArbSettings(f"{name} must be greater than zero")
    if not KEEP_MIN <= settings.day_lock_keep <= KEEP_MAX:
        raise InvalidArbSettings(
            f"day_lock_keep must be between {KEEP_MIN} and {KEEP_MAX}, "
            f"got {settings.day_lock_keep!r}"
        )
    if not 0 < settings.cut_mult <= 1:
        raise InvalidArbSettings("cut_mult must be in (0, 1]")
    if settings.cut_after_losers < 1:
        raise InvalidArbSettings("cut_after_losers must be at least 1")
    if settings.house_money_mult < 0:
        raise InvalidArbSettings("house_money_mult must not be negative")
    if settings.max_score0_per_day < 0:
        raise InvalidArbSettings("max_score0_per_day must not be negative")
    if settings.open_risk_cap < max(max(settings.funnel_risks), settings.orb_risk):
        raise InvalidArbSettings("open_risk_cap must cover the largest single trade risk")
    if settings.min_risk >= min(settings.funnel_risks):
        raise InvalidArbSettings("min_risk must be below the smallest funnel risk")
    if settings.hard_day_stop >= settings.closed_loss_backstop:
        raise InvalidArbSettings("hard_day_stop must be below closed_loss_backstop")
    if settings.one_share_floor_mult < 1:
        raise InvalidArbSettings("one_share_floor_mult must be at least 1")


# -----------------------------------------------------------------------------
# Funnel filters and score
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class FunnelFeatures:
    """What the engine knows about a funnel trigger at the moment it fires."""

    trigger_time_ist: Optional[time]
    volume_ratio: Optional[float]
    # |trigger price - structural stop| as a percent of the trigger price.
    stop_pct: Optional[float]
    is_first_trigger_today: bool
    # Mean % change from the day's open across the watchlist, signed so that
    # positive means "moving the trade's way". None when there is no data.
    align_market_pct: Optional[float]
    # The same over the stock's sector peers (the stock itself excluded).
    align_sector_pct: Optional[float]

    def as_dict(self) -> Dict[str, object]:
        return {
            "trigger_time_ist": (
                None if self.trigger_time_ist is None else self.trigger_time_ist.isoformat()
            ),
            "volume_ratio": self.volume_ratio,
            "stop_pct": self.stop_pct,
            "is_first_trigger_today": self.is_first_trigger_today,
            "align_market_pct": self.align_market_pct,
            "align_sector_pct": self.align_sector_pct,
        }


@dataclass(frozen=True)
class FunnelVerdict:
    ok: bool
    reason: Optional[str]
    score: Optional[int] = None
    base_risk_rupees: float = 0.0
    score_parts: Dict[str, bool] = field(default_factory=dict)


def funnel_score(features: FunnelFeatures, settings: ArbSettings) -> Tuple[int, Dict[str, bool]]:
    """The 0-3 quality score (4 conditions, capped at 3), and which ones held."""
    market_against = (
        features.align_market_pct is not None
        and features.align_market_pct < settings.score_market_against_pct
    )
    sector_with = (
        market_against
        and features.align_sector_pct is not None
        and features.align_sector_pct > 0
    )
    big_volume = (
        features.volume_ratio is not None
        and features.volume_ratio >= settings.score_volume_ratio
    )
    early = (
        features.trigger_time_ist is not None
        and features.trigger_time_ist < settings.score_early_before
    )
    parts = {
        "market_against": market_against,
        "sector_with": sector_with,
        "volume_6x": big_volume,
        "before_1030": early,
    }
    return min(sum(parts.values()), 3), parts


def classify_funnel(
    features: FunnelFeatures, settings: ArbSettings, *, score0_taken_today: int
) -> FunnelVerdict:
    """Filters first, then the score and its base risk."""
    if features.trigger_time_ist is None:
        return FunnelVerdict(False, REASON_NO_TRIGGER_TIME)
    if features.trigger_time_ist >= settings.funnel_cutoff:
        return FunnelVerdict(False, REASON_AFTER_CUTOFF)
    if features.volume_ratio is None or features.volume_ratio < settings.min_volume_ratio:
        return FunnelVerdict(False, REASON_VOLUME)
    if features.stop_pct is None:
        return FunnelVerdict(False, REASON_NO_STOP)
    if features.stop_pct < settings.min_stop_pct:
        return FunnelVerdict(False, REASON_STOP_TOO_TIGHT)
    if not features.is_first_trigger_today:
        return FunnelVerdict(False, REASON_NOT_FIRST)
    score, parts = funnel_score(features, settings)
    if score == 0 and score0_taken_today >= settings.max_score0_per_day:
        return FunnelVerdict(False, REASON_SCORE0_LIMIT, score=0, score_parts=parts)
    return FunnelVerdict(
        True,
        None,
        score=score,
        base_risk_rupees=settings.funnel_risk_rupees(score),
        score_parts=parts,
    )


# -----------------------------------------------------------------------------
# Positions: live risk, net P&L, losers
# -----------------------------------------------------------------------------


def _sign(direction: str) -> int:
    return 1 if str(direction).upper() == "UP" else -1


def _extra_float(position: Position, key: str) -> Optional[float]:
    raw = position.extra.get(key)
    try:
        value = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
    return value if value is not None and math.isfinite(value) else None


def position_live_risk(position: Position) -> float:
    """Rupees this position can still lose from here (D6). Never negative.

    A trade whose stop is at or beyond its entry counts zero: its risk is
    "freed" and no longer uses the open-risk budget.
    """
    if position.state in PENDING_STATES:
        return max(0.0, _extra_float(position, "risk_cap_rupees") or 0.0)
    if position.state not in HOLDING_STATES:
        return 0.0
    qty = int(position.qty or 0)
    if qty <= 0:
        return 0.0
    if position.entry_price is not None and position.stop_price is not None:
        per_share = (float(position.entry_price) - float(position.stop_price)) * _sign(
            position.candidate.direction
        )
        return max(0.0, per_share * qty)
    fallback = position.risk_taken_rupees
    if fallback is None:
        fallback = _extra_float(position, "risk_cap_rupees")
    return max(0.0, float(fallback or 0.0))


def closed_net_pnl(position: Position) -> Optional[float]:
    """Realised P&L minus estimated charges for a closed trade.

    None when the close has no realised figure. The exit price for the charge
    estimate is recovered from the gross P&L: exit = entry + pnl / qty
    (long), entry - pnl / qty (short).
    """
    if position.realised_pnl is None:
        return None
    gross = float(position.realised_pnl)
    qty = int(position.qty or 0)
    if qty <= 0 or position.entry_price is None:
        return gross
    entry = float(position.entry_price)
    exit_price = entry + _sign(position.candidate.direction) * gross / qty
    return gross - round_trip_charges(
        direction=position.candidate.direction, qty=qty, entry=entry, exit_price=exit_price
    )


def is_loser(position: Position) -> bool:
    """A closed trade that lost money after charges (D5).

    A close with no realised figure counts as a loser: it only ever makes the
    next trade smaller, never larger.
    """
    if position.state is not ExecutionState.CLOSED:
        return False
    net = closed_net_pnl(position)
    return net is None or net < 0


def count_losers(closed_positions: Iterable[Position]) -> int:
    return sum(1 for position in closed_positions if is_loser(position))


def open_mark(position: Position, ltp: float) -> float:
    """Net P&L of an open position if it were closed at ``ltp`` now."""
    qty = int(position.qty or 0)
    if qty <= 0 or position.entry_price is None:
        return 0.0
    entry = float(position.entry_price)
    gross = (float(ltp) - entry) * _sign(position.candidate.direction) * qty
    return gross - round_trip_charges(
        direction=position.candidate.direction, qty=qty, entry=entry, exit_price=float(ltp)
    )


def worst_case_mark(position: Position) -> float:
    """Net P&L of an open position if its stop filled now (the stale fallback)."""
    qty = int(position.qty or 0)
    if qty <= 0 or position.entry_price is None:
        return 0.0
    if position.stop_price is not None:
        return open_mark(position, float(position.stop_price))
    return -position_live_risk(position)


# -----------------------------------------------------------------------------
# Day P&L
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class DayMtm:
    """The day's net P&L: closed trades plus open positions marked now."""

    mtm: float
    closed_net: float
    open_net: float
    # Open positions priced at their stop because no fresh LTP was available.
    stale_symbols: Tuple[str, ...] = ()
    # Closed trades without a realised figure (counted as 0 here).
    unpriced_closes: int = 0

    @property
    def complete(self) -> bool:
        return not self.stale_symbols


def day_mtm(
    closed_positions: Sequence[Position],
    open_positions: Sequence[Position],
    fresh_prices: Mapping[str, float],
) -> DayMtm:
    """D3/D4: closed net + open marks; a holding position with no fresh price
    counts at its stop."""
    closed_net = 0.0
    unpriced = 0
    for position in closed_positions:
        if position.state is not ExecutionState.CLOSED:
            continue
        net = closed_net_pnl(position)
        if net is None:
            unpriced += 1
            continue
        closed_net += net
    open_net = 0.0
    stale: List[str] = []
    for position in open_positions:
        if position.state not in HOLDING_STATES:
            continue
        if int(position.qty or 0) <= 0 or position.entry_price is None:
            continue
        symbol = position.candidate.tradingsymbol
        ltp = fresh_prices.get(symbol)
        if ltp is None or not math.isfinite(float(ltp)) or float(ltp) <= 0:
            stale.append(symbol)
            open_net += worst_case_mark(position)
        else:
            open_net += open_mark(position, float(ltp))
    return DayMtm(
        mtm=round(closed_net + open_net, 2),
        closed_net=round(closed_net, 2),
        open_net=round(open_net, 2),
        stale_symbols=tuple(sorted(set(stale))),
        unpriced_closes=unpriced,
    )


# -----------------------------------------------------------------------------
# Risk for a new trade
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class RiskDecision:
    risk_rupees: float  # 0 when the trade must be skipped
    base_rupees: float
    cut: bool           # halved after a loser
    room_rupees: float  # open cap + house money - live risk
    reason: Optional[str] = None


def allowed_risk(
    settings: ArbSettings,
    *,
    base_rupees: float,
    losers_today: int,
    day_mtm_rupees: float,
    live_risk_rupees: float,
) -> RiskDecision:
    """D10: min(base, cut if a loser, open cap + house money - live risk)."""
    cut = losers_today >= settings.cut_after_losers
    risk = float(base_rupees) * (settings.cut_mult if cut else 1.0)
    room = (
        settings.open_risk_cap_rupees()
        + max(0.0, float(day_mtm_rupees)) * settings.house_money_mult
        - max(0.0, float(live_risk_rupees))
    )
    risk = min(risk, room)
    if risk < settings.min_risk_rupees():
        return RiskDecision(0.0, float(base_rupees), cut, room, REASON_BELOW_MIN_RISK)
    return RiskDecision(risk, float(base_rupees), cut, room)


def apply_one_share_floor(
    settings: ArbSettings, *, risk_rupees: float, risk_per_share: float
) -> Tuple[float, bool]:
    """D12: at the micro stage, lift the risk to exactly one share's when the
    size would otherwise round to zero and one share is close enough.

    Returns (risk to size with, whether the floor was used). Sizing then gives
    floor(r / r) = 1 share, and the abnormal-slippage line is judged against
    that one share's risk.
    """
    if not settings.one_share_floor_active:
        return risk_rupees, False
    if risk_per_share <= 0 or risk_rupees <= 0:
        return risk_rupees, False
    if math.floor(risk_rupees / risk_per_share) >= 1:
        return risk_rupees, False
    if risk_per_share <= settings.one_share_floor_mult * risk_rupees:
        return float(risk_per_share), True
    return risk_rupees, False


# -----------------------------------------------------------------------------
# Day controls
# -----------------------------------------------------------------------------


def hard_stop_hit(settings: ArbSettings, mtm_rupees: float) -> bool:
    """D1: the day's net P&L is at or below minus the hard day stop."""
    return float(mtm_rupees) <= -settings.hard_day_stop_rupees()


@dataclass
class DayLockTracker:
    """D2: the day-lock peak and line, sampled once per minute.

    The first tick of each new minute with complete prices gives that
    minute's sample (about the previous 1-minute close, as in the backtest).
    ``peak`` survives restarts through the store.
    """

    settings: ArbSettings
    peak: Optional[float] = None
    sampled_minute: Optional[datetime] = None

    def observe(self, minute: datetime, mtm: DayMtm) -> bool:
        """Record a sample if this minute has none yet. True = lock hit."""
        if self.sampled_minute is not None and minute <= self.sampled_minute:
            return False
        if not mtm.complete:
            return False  # try again on the next tick of this minute
        self.sampled_minute = minute
        value = float(mtm.mtm)
        self.peak = value if self.peak is None else max(self.peak, value)
        return self.lock_hit(value)

    def lock_hit(self, value: float) -> bool:
        if self.peak is None:
            return False
        return (
            self.peak >= self.settings.day_lock_arm_rupees()
            and value <= self.settings.day_lock_keep * self.peak
        )

    @property
    def armed(self) -> bool:
        return self.peak is not None and self.peak >= self.settings.day_lock_arm_rupees()

    @property
    def lock_line(self) -> Optional[float]:
        if not self.armed:
            return None
        return round(self.settings.day_lock_keep * float(self.peak), 2)
