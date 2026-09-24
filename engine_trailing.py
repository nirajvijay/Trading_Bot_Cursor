"""Moving a protected position's stop: automatic trailing and manual nudges.

**One rule, applied to every trade.** R is the per-share distance between the
entry fill and the original structural stop. For every whole 0.5R of profit
the stop sits exactly one 0.5R step behind it: 0.5R of profit moves it to
breakeven (the entry price), 1.0R locks in +0.5R, 1.5R locks in +1.0R, and so
on, with no tightening at higher R. Nothing here depends on the setup type.

**Decided from pulled truth only.** The price used is the ``last_price`` from
this tick's REST positions read (``BrokerTruth.last_price``), never a
websocket tick: a push may only wake the loop early (engine_runloop.LoopWake).

**One funnel.** Auto trailing and manual nudges both go through ``move_stop``,
so both share the same floor, the same favourable-only rule while auto is on,
the same modification counter, and the same crash-safe write.

* Auto ON: the stop only ever moves in the trade's favour, whichever side asks.
* Auto OFF: a manual nudge may also loosen it.
* Always: never further away than the original entry stop (the floor).

**Kite allows 25 modifications per order.** The count per stop order id is
kept in ``position.extra`` (so it survives restarts), shared by both paths,
bumped for stop edits adopted from Kite, and cross-checked against Kite's own
order history near the cap. At ``REPLACE_AT_MODIFICATIONS`` the stop is
cancelled and re-placed instead, which starts a fresh order id at zero.
Cancel first, then place: two working stops for one position could both fire
and flip it.

Every target is rounded to the instrument's own tick size, away from the
market (down for a long's stop, up for a short's). A missing or invalid tick
size refuses the move; there is never a guessed fallback.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any, Dict, Optional

from engine_entry import exit_transaction_type_for
from engine_types import ExecutionState, Position

# --- position.extra keys ------------------------------------------------------
# Auto-trail on/off. Set True at first protection; re-protection keeps the choice.
TRAIL_ENABLED_KEY = "trail_enabled"
# The structural stop the position was first protected with: the floor, and
# the basis of R. Written once, never changed.
INITIAL_STOP_KEY = "initial_stop_price"
# {stop order id: modifications counted against Kite's per-order cap}.
STOP_MODS_KEY = "stop_mods"
# What a stop write in flight intended, recorded before the write, so a change
# that lands after an unclear response is credited to us and not to Kite.
STOP_MOVE_PENDING_KEY = "stop_move_pending"

# --- sources of a stop change -------------------------------------------------
SOURCE_AUTO = "auto"
SOURCE_MANUAL = "manual"
SOURCE_KITE = "kite"

# --- events -------------------------------------------------------------------
EVENT_STOP_MOVED = "stop_moved"
EVENT_STOP_ADOPTED = "stop_adopted"
EVENT_STOP_MOVE_FAILED = "stop_move_failed"
EVENT_STOP_REPLACED = "stop_replaced_for_mod_cap"
EVENT_TRAIL_TOGGLED = "trail_toggled"

TRAIL_STEP_R = Decimal("0.5")
KITE_MAX_MODIFICATIONS_PER_ORDER = 25
# Leaves headroom for edits made directly in Kite that we only see as one
# adoption, however many there were between two ticks.
REPLACE_AT_MODIFICATIONS = 20
# Below this, the local count is trusted without asking Kite.
CHECK_KITE_COUNT_FROM = 10


# -----------------------------------------------------------------------------
# Pure rules
# -----------------------------------------------------------------------------


def tick_size_of(position: Position) -> Optional[float]:
    """The instrument's own tick size, or None when it cannot be trusted."""
    raw = getattr(position.candidate, "tick_size", None)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def initial_stop_of(position: Position) -> Optional[float]:
    raw = position.extra.get(INITIAL_STOP_KEY)
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def is_long(direction: str) -> bool:
    return str(direction).upper() == "UP"


def round_stop(price: float, tick_size: float, direction: str) -> float:
    """Snap to the tick grid away from the market: a long's stop rounds down,
    a short's rounds up. An on-grid price is returned unchanged."""
    tick = Decimal(str(tick_size))
    ticks = (Decimal(str(price)) / tick).quantize(
        Decimal("1"), rounding=ROUND_FLOOR if is_long(direction) else ROUND_CEILING
    )
    return float(ticks * tick)


def r_points(entry: Optional[float], initial_stop: Optional[float]) -> Optional[float]:
    """Per-share risk. Equal to risk_taken_rupees / qty, but unaffected by a
    quantity later adopted from Kite."""
    if entry is None or initial_stop is None:
        return None
    r = abs(float(entry) - float(initial_stop))
    return r if r > 0 else None


def schedule_stop(
    *,
    direction: str,
    entry: float,
    r_pts: float,
    ltp: float,
    tick_size: float,
) -> Optional[float]:
    """Where the 0.5R schedule puts the stop at this price, or None below 0.5R."""
    profit = Decimal(str(ltp)) - Decimal(str(entry))
    if not is_long(direction):
        profit = -profit
    step = TRAIL_STEP_R * Decimal(str(r_pts))
    steps = int((profit / step).to_integral_value(rounding=ROUND_FLOOR))
    if steps < 1:
        return None
    locked = (steps - 1) * step
    raw = Decimal(str(entry)) + (locked if is_long(direction) else -locked)
    return round_stop(float(raw), tick_size, direction)


def nudge_target(current: float, ticks: int, tick_size: float) -> float:
    """``ticks`` whole ticks up (+) or down (-) in price from the current stop."""
    tick = Decimal(str(tick_size))
    on_grid = (Decimal(str(current)) / tick).quantize(Decimal("1"))
    return float((on_grid + int(ticks)) * tick)


def is_tighter(candidate: float, current: float, direction: str) -> bool:
    """Closer to the market, i.e. more favourable for the trade."""
    if is_long(direction):
        return candidate > current
    return candidate < current


@dataclass(frozen=True)
class StopDecision:
    target: Optional[float]
    reason: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.target is not None


def resolve_stop(
    *,
    direction: str,
    current: float,
    candidate: float,
    floor: float,
    tick_size: float,
    auto_on: bool,
    ltp: Optional[float],
) -> StopDecision:
    """Apply the floor, the favourable-only rule and the market check."""
    target = round_stop(candidate, tick_size, direction)
    half_tick = tick_size / 2.0
    if abs(target - current) < half_tick:
        return StopDecision(None, "unchanged")
    if is_tighter(floor, target, direction) and abs(target - floor) >= half_tick:
        return StopDecision(None, "beyond_initial_stop")
    tighter = is_tighter(target, current, direction)
    if auto_on and not tighter:
        return StopDecision(None, "auto_trail_on_cannot_loosen")
    if tighter:
        if ltp is None:
            return StopDecision(None, "no_fresh_price")
        # A stop at or through the market is refused by Kite or fires at once.
        if is_long(direction) and target >= ltp:
            return StopDecision(None, "at_or_through_market")
        if not is_long(direction) and target <= ltp:
            return StopDecision(None, "at_or_through_market")
    return StopDecision(target)


def auto_target(position: Position, ltp: Optional[float]) -> Optional[float]:
    """The schedule's stop for this position right now, before the rules."""
    tick = tick_size_of(position)
    initial = initial_stop_of(position)
    r_pts = r_points(position.entry_price, initial)
    if tick is None or r_pts is None or ltp is None or position.entry_price is None:
        return None
    return schedule_stop(
        direction=position.candidate.direction,
        entry=float(position.entry_price),
        r_pts=r_pts,
        ltp=float(ltp),
        tick_size=tick,
    )


def adopted_source(position: Position, broker_stop_price: float) -> str:
    """Who set the stop Kite now shows: our own write in flight, or a human in Kite."""
    pending = position.extra.get(STOP_MOVE_PENDING_KEY) or {}
    tick = tick_size_of(position) or 0.0
    try:
        target = float(pending.get("target"))
    except (TypeError, ValueError):
        return SOURCE_KITE
    if (
        str(pending.get("order_id")) == str(position.stop_order_id)
        and abs(target - float(broker_stop_price)) <= max(tick / 2.0, 1e-9)
    ):
        return str(pending.get("source") or SOURCE_KITE)
    return SOURCE_KITE


def mod_count(position: Position, order_id: Optional[str] = None) -> int:
    oid = str(order_id or position.stop_order_id or "")
    return int((position.extra.get(STOP_MODS_KEY) or {}).get(oid, 0))


def bump_mod_count(position: Position, order_id: Optional[str] = None, by: int = 1) -> int:
    oid = str(order_id or position.stop_order_id or "")
    mods: Dict[str, int] = dict(position.extra.get(STOP_MODS_KEY) or {})
    mods[oid] = int(mods.get(oid, 0)) + int(by)
    position.extra[STOP_MODS_KEY] = mods
    return mods[oid]


# -----------------------------------------------------------------------------
# The one write path
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class MoveOutcome:
    moved: bool
    reason: Optional[str] = None
    stop_price: Optional[float] = None
    # True when the broker call itself failed, as opposed to a rule refusing.
    failed: bool = False


def _refresh_mod_count(position: Position, broker) -> int:
    """Near the cap, let Kite's own history raise (never lower) our count."""
    local = mod_count(position)
    if local < CHECK_KITE_COUNT_FROM:
        return local
    counter = getattr(broker, "order_modification_count", None)
    if counter is None:
        return local
    try:
        kite = counter(str(position.stop_order_id))
    except Exception:  # noqa: BLE001 - the local count still stands
        return local
    if kite is not None and int(kite) > local:
        bump_mod_count(position, by=int(kite) - local)
        return int(kite)
    return local


def move_stop(
    position: Position,
    target: float,
    *,
    source: str,
    broker,
    store,
    detail: Optional[Dict[str, Any]] = None,
) -> MoveOutcome:
    """Move a PROTECTED position's live stop to ``target`` (already resolved).

    Modify in place; at the modification cap, cancel and re-place instead.
    The stored stop only changes once Kite confirms the new trigger. If the
    confirmation is unclear, the pending marker lets the next tick's
    reconciliation credit the change to ``source`` rather than to Kite.
    """
    from engine_exit import cancel_order_confirmed
    from engine_orders import transition
    from engine_protection import ensure_protected

    if position.state != ExecutionState.PROTECTED or not position.stop_order_id:
        return MoveOutcome(False, "not_protected")
    tick = tick_size_of(position)
    if tick is None:
        return MoveOutcome(False, "tick_size_unavailable")

    was = position.stop_price
    order_id = str(position.stop_order_id)
    payload: Dict[str, Any] = {"from": was, "to": target, "source": source}
    payload.update(detail or {})

    if _refresh_mod_count(position, broker) >= REPLACE_AT_MODIFICATIONS:
        status = cancel_order_confirmed(broker, order_id)
        if status != "cancelled":
            # Never place a second stop while the first may still be live.
            store.append_event(
                position.trade_id,
                EVENT_STOP_MOVE_FAILED,
                {**payload, "reason": f"replace_cancel_{status}", "order_id": order_id},
            )
            return MoveOutcome(False, f"replace_cancel_{status}", failed=True)
        position.stop_order_id = None
        position.stop_price = target
        position.extra.pop(STOP_MOVE_PENDING_KEY, None)
        transition(position, ExecutionState.ENTERED)
        store.save_with_event(
            position,
            EVENT_STOP_REPLACED,
            {**payload, "old_order_id": order_id, "mods": mod_count(position, order_id)},
        )
        outcome = ensure_protected(position, broker=broker, store=store)
        if not outcome.protected:
            # Stays ENTERED: the protection step retries it next tick at the
            # new price, and the unprotected-exit timer covers the worst case.
            return MoveOutcome(False, f"replace_place_{outcome.reason}", failed=True)
        store.append_event(
            position.trade_id,
            EVENT_STOP_MOVED,
            {**payload, "order_id": position.stop_order_id, "mods": 0, "replaced": True},
        )
        return MoveOutcome(True, stop_price=target)

    # Durable before the write: a crash or lost response mid-modify must still
    # be attributable.
    position.extra[STOP_MOVE_PENDING_KEY] = {
        "order_id": order_id,
        "target": target,
        "source": source,
    }
    store.save(position)
    try:
        order = broker.modify_slm(
            order_id,
            float(target),
            tick_size=tick,
            transaction_type=exit_transaction_type_for(position.candidate.direction),
        )
    except Exception as exc:  # noqa: BLE001 - reported, retried by the caller
        reason = str(exc) or exc.__class__.__name__
        store.append_event(
            position.trade_id, EVENT_STOP_MOVE_FAILED, {**payload, "reason": reason}
        )
        return MoveOutcome(False, reason, failed=True)

    confirmed = (
        order is not None
        and order.trigger_price is not None
        and abs(float(order.trigger_price) - float(target)) <= tick / 2.0
    )
    if not confirmed:
        # Kite has not shown the new trigger. Leave the pending marker; the
        # next reconciliation adopts whatever Kite really holds.
        store.append_event(
            position.trade_id,
            EVENT_STOP_MOVE_FAILED,
            {**payload, "reason": "modify_unconfirmed"},
        )
        return MoveOutcome(False, "modify_unconfirmed", failed=True)

    position.stop_price = float(target)
    position.extra.pop(STOP_MOVE_PENDING_KEY, None)
    mods = bump_mod_count(position, order_id)
    store.save_with_event(
        position, EVENT_STOP_MOVED, {**payload, "order_id": order_id, "mods": mods}
    )
    return MoveOutcome(True, stop_price=float(target))
