"""Continuous reconciliation against broker truth.

Not "did my order fill". The real job is: ask the broker what is actually true
right now and make local records match it, **every tick, for every open
position, for the entire life of that position** — not only when something has
been flagged uncertain.

Two scenarios forced this from a startup-only special case into an every-tick
one:

1. A stop, once triggered, executes as a market order from that point, so on a
   fast move it can fill materially worse than the level we set. The price we
   recorded is only ever a target; the broker's actual fill is the truth.
2. A human can edit an order or close a position directly in Kite while the
   engine is running normally — no crash, no restart, nothing to trigger a
   check. If reconciliation only ran at special checkpoints, that change would
   simply never be noticed.

So "startup reconciliation" is not a separate system any more. It is this,
running for the first time when the loop starts.

**Batching is a hard constraint, not an optimization.** One broker read per
tick covering everything, never one per position: the Quote endpoint allows
1 request/second, so a per-position loop would blow the limit the moment a
second position opened. fetch_broker_truth issues exactly two network reads
(the positions book and the order book) regardless of how many positions are
open, because KiteBroker caches the positions payload for the tick.

**Boundary:** only positions the engine recognizes by its own trade tag.
Unrelated manual activity in the same broker account is ignored — the engine
reconciles what it is responsible for, not the whole account.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Iterable, List, Optional, Sequence

from engine_entry import (
    broker_tag_for,
    entry_fill_final,
    exit_transaction_type_for,
    fill_price_of,
)
from engine_types import ExecutionState, Position
from trading_engine_types import (
    STOP_ORDER_TYPES,
    BrokerOrder,
    broker_order_filled_qty,
)

# States in which we believe we are holding size. Only these can go flat, so
# only these are candidates for exit finalization — a net quantity of zero on
# an unfilled entry is simply normal.
HOLDING_STATES = (
    ExecutionState.ENTERED,
    ExecutionState.PROTECTED,
    ExecutionState.TRAILING,
    ExecutionState.EXIT_SUBMITTED,
)

# States whose stop order should currently be live at the broker.
STOP_EXPECTED_STATES = (ExecutionState.PROTECTED, ExecutionState.TRAILING)

# Entries still waiting at the broker. The engine watches both reasons to
# escalate a stall (engine_entry.ENTRY_STALL_ESCALATE_SECONDS).
# Holding shares whose fill is not final yet (those shares have no stop):
ENTRY_PARTIAL_WAIT_REASON = "entry_partially_filled_waiting"
# Still working at the broker with nothing filled:
ENTRY_WORKING_WAIT_REASON = "entry_still_working"

DEAD_ORDER_STATUSES = {"REJECTED", "CANCELLED"}


class ReconcileAction(str, Enum):
    APPLY_ENTRY_FILL = "apply_entry_fill"
    ENTRY_REJECTED = "entry_rejected"
    ENTRY_CANCELLED = "entry_cancelled"
    ADOPT_QTY = "adopt_qty"
    ADOPT_STOP_PRICE = "adopt_stop_price"
    REPLACE_STOP = "replace_stop"
    FINALIZE_EXIT = "finalize_exit"
    # The broker holds size the opposite way to the trade. Blocks every other
    # action: no stop, no adopted quantity (see engine_core).
    DIRECTION_MISMATCH = "direction_mismatch"

# Set while an exit waits on its stop cancel (engine_exit.EXIT_PENDING_KEY).
EXIT_PENDING_KEY = "exit_pending"


@dataclass(frozen=True)
class BrokerTruth:
    """One tick's snapshot of what the broker says is real."""

    net_qty: Dict[str, int] = field(default_factory=dict)
    pnl: Dict[str, Optional[float]] = field(default_factory=dict)
    orders_by_id: Dict[str, BrokerOrder] = field(default_factory=dict)
    orders_by_tag: Dict[str, List[BrokerOrder]] = field(default_factory=dict)
    fetched_at: Optional[datetime] = None
    ok: bool = True
    reason: Optional[str] = None

    def net_for(self, tradingsymbol: str) -> int:
        """Absolute size held, per the broker.

        A symbol absent from a successfully-read positions book genuinely means
        no MIS net position exists, so absence is flat. A book that could not be
        read at all sets ok=False instead, and nothing acts on it.
        """
        return abs(self.signed_net_for(tradingsymbol))

    def signed_net_for(self, tradingsymbol: str) -> int:
        """Size held with its sign: positive long, negative short."""
        return int(self.net_qty.get(tradingsymbol, 0))

    def stop_filled_for(self, position: Position) -> bool:
        """Did one of our stops already execute? Then it is not missing: the
        position is on its way to flat and must never get a replacement."""
        exit_side = exit_transaction_type_for(position.candidate.direction)
        stop = self.order(position.stop_order_id)
        if stop is not None and broker_order_filled_qty(stop) > 0:
            return True
        for order in self.orders_by_tag.get(broker_tag_for(position.trade_id), []):
            if position.entry_order_id and str(order.order_id) == str(position.entry_order_id):
                continue
            if str(order.transaction_type).upper() != exit_side:
                continue
            if str(order.order_type) in STOP_ORDER_TYPES and broker_order_filled_qty(order) > 0:
                return True
        return False

    def order(self, order_id: Optional[str]) -> Optional[BrokerOrder]:
        if not order_id:
            return None
        return self.orders_by_id.get(str(order_id))

    def stop_order_for(self, position: Position) -> Optional[BrokerOrder]:
        """The live stop for this position, found by id first and then by tag.

        The tag fallback matters after a crash between placing a stop and
        persisting its id: the order exists, we just lost the pointer.
        """
        direct = self.order(position.stop_order_id)
        if direct is not None and _is_live_stop(direct):
            return direct
        for candidate in self.orders_by_tag.get(broker_tag_for(position.trade_id), []):
            if _is_live_stop(candidate):
                return candidate
        return None


@dataclass(frozen=True)
class ReconcileDecision:
    """What this tick should do about one position."""

    actions: List[ReconcileAction] = field(default_factory=list)
    broker_qty: Optional[int] = None
    fill_price: Optional[float] = None
    fill_qty: Optional[int] = None
    broker_stop_price: Optional[float] = None
    order_id: Optional[str] = None
    reason: Optional[str] = None

    def has(self, action: ReconcileAction) -> bool:
        return action in self.actions

    @property
    def is_noop(self) -> bool:
        return not self.actions


def _is_live_stop(order: BrokerOrder) -> bool:
    return (
        str(order.order_type) in STOP_ORDER_TYPES
        and str(order.status).upper() not in DEAD_ORDER_STATUSES
    )


def fetch_broker_truth(broker, symbols: Iterable[str] = ()) -> BrokerTruth:
    """The single batched read for this tick.

    Order matters: clear the cache first so this tick sees fresh data, then let
    every subsequent lookup in this tick be served from that one payload.
    """
    now = datetime.now(timezone.utc)
    clear = getattr(broker, "clear_quote_cache", None)
    if clear is not None:
        try:
            clear()
        except Exception:  # noqa: BLE001 - a cache reset must never end a tick
            pass

    try:
        net_qty = dict(broker.list_net_positions() or {})
    except Exception as exc:  # noqa: BLE001
        return BrokerTruth(
            fetched_at=now, ok=False, reason=f"positions_unreadable: {exc}"
        )

    try:
        orders = list(broker.list_orders() or [])
    except Exception as exc:  # noqa: BLE001
        return BrokerTruth(
            fetched_at=now, ok=False, reason=f"orders_unreadable: {exc}"
        )

    orders_by_id: Dict[str, BrokerOrder] = {}
    orders_by_tag: Dict[str, List[BrokerOrder]] = {}
    for order in orders:
        orders_by_id[str(order.order_id)] = order
        orders_by_tag.setdefault(str(order.tag or ""), []).append(order)

    # Per-symbol P&L, read straight from the same cached positions payload.
    # Copying Kite's own `pnl` field rather than recomputing from last price
    # and average price guarantees parity with what the Kite site displays.
    pnl: Dict[str, Optional[float]] = {}
    position_quote = getattr(broker, "position_quote", None)
    if position_quote is not None:
        for symbol in set(symbols):
            try:
                quote = position_quote(symbol)
            except Exception:  # noqa: BLE001 - a missing mark is not an error
                continue
            if quote is not None:
                pnl[symbol] = quote.pnl

    return BrokerTruth(
        net_qty=net_qty,
        pnl=pnl,
        orders_by_id=orders_by_id,
        orders_by_tag=orders_by_tag,
        fetched_at=now,
        ok=True,
    )


def symbols_of(positions: Sequence[Position]) -> List[str]:
    return sorted({p.candidate.tradingsymbol for p in positions})


def reconcile(
    position: Position,
    truth: BrokerTruth,
    *,
    stop_price_tolerance: Optional[float] = None,
) -> ReconcileDecision:
    """Compare one position against broker truth. Pure: decides, never acts."""
    if not truth.ok:
        # Never make a decision from a read that failed. Acting on an unreadable
        # book is how a live position gets recorded as closed.
        return ReconcileDecision(reason=truth.reason)

    if position.state in (
        ExecutionState.CLOSED,
        ExecutionState.REJECTED,
        ExecutionState.CANCELLED,
    ):
        return ReconcileDecision()

    if position.state == ExecutionState.ENTRY_SUBMITTED:
        return _reconcile_entry(position, truth)

    if position.state not in HOLDING_STATES:
        # PENDING_ENTRY: nothing is at the broker yet by construction.
        return ReconcileDecision()

    symbol = position.candidate.tradingsymbol
    signed_qty = truth.signed_net_for(symbol)
    broker_qty = abs(signed_qty)

    expected_sign = 1 if str(position.candidate.direction).upper() == "UP" else -1
    if signed_qty * expected_sign < 0:
        # Short against a long trade (or the reverse). Adopting that size, or
        # placing a stop on the trade's side, would only add to it.
        return ReconcileDecision(
            actions=[ReconcileAction.DIRECTION_MISMATCH],
            broker_qty=signed_qty,
            reason="direction_mismatch",
        )

    if broker_qty == 0:
        # The position went flat. Which order actually did it is a separate
        # question (attribution lives in engine_exit) — this only reports that
        # it happened.
        return ReconcileDecision(
            actions=[ReconcileAction.FINALIZE_EXIT],
            broker_qty=0,
            reason="broker_flat",
        )

    actions: List[ReconcileAction] = []
    if broker_qty != int(position.qty or 0):
        actions.append(ReconcileAction.ADOPT_QTY)

    broker_stop_price: Optional[float] = None
    exiting = bool(position.extra.get(EXIT_PENDING_KEY))
    if position.state in STOP_EXPECTED_STATES and not exiting:
        stop_order = truth.stop_order_for(position)
        if stop_order is None and truth.stop_filled_for(position):
            # The stop executed; the positions book just has not caught up.
            # Wait for it to show flat rather than sell a second time.
            return ReconcileDecision(
                actions=actions, broker_qty=broker_qty, reason="stop_filled_awaiting_flat"
            )
        if stop_order is None:
            # The stop is genuinely not at the broker any more, so the position
            # genuinely is not protected. Say so and re-place it.
            actions.append(ReconcileAction.REPLACE_STOP)
        else:
            broker_stop_price = (
                None if stop_order.trigger_price is None else float(stop_order.trigger_price)
            )
            tolerance = stop_price_tolerance
            if tolerance is None:
                tolerance = max(float(position.candidate.tick_size or 0.05) / 2.0, 1e-9)
            if (
                broker_stop_price is not None
                and position.stop_price is not None
                and abs(broker_stop_price - float(position.stop_price)) > tolerance
            ):
                # A human edited the stop in Kite. Adopt their price: by the
                # time that stop fires, our records already reflect it, so it
                # is just a normal stop_hit at the correct real price.
                actions.append(ReconcileAction.ADOPT_STOP_PRICE)

    return ReconcileDecision(
        actions=actions,
        broker_qty=broker_qty,
        broker_stop_price=broker_stop_price,
    )


def _reconcile_entry(position: Position, truth: BrokerTruth) -> ReconcileDecision:
    """An entry we submitted: did it fill, die, or is it still working?"""
    order = truth.order(position.entry_order_id)
    if order is None:
        for candidate in truth.orders_by_tag.get(broker_tag_for(position.trade_id), []):
            if str(candidate.order_type) not in STOP_ORDER_TYPES:
                order = candidate
                break
    if order is None:
        # Submitted, but not visible in the order book. Wait rather than guess:
        # inventing either outcome here is exactly what the three-way response
        # discipline exists to prevent.
        return ReconcileDecision(reason="entry_order_not_visible")

    status = str(order.status).upper()
    filled = broker_order_filled_qty(order)
    if filled > 0:
        fill_price = fill_price_of(order)
        if entry_fill_final(order) and fill_price is not None:
            return ReconcileDecision(
                actions=[ReconcileAction.APPLY_ENTRY_FILL],
                fill_price=fill_price,
                fill_qty=int(filled),
                order_id=str(order.order_id),
            )
        # Shares are held but the fill is not final yet (or Kite has not
        # priced it). Wait: never fall through to the cancelled/rejected
        # branches below, which would record held shares as a failed entry.
        return ReconcileDecision(
            fill_qty=int(filled),
            order_id=str(order.order_id),
            reason=ENTRY_PARTIAL_WAIT_REASON,
        )
    if status == "REJECTED":
        return ReconcileDecision(
            actions=[ReconcileAction.ENTRY_REJECTED],
            order_id=str(order.order_id),
            reason="broker_rejected",
        )
    if status == "CANCELLED":
        return ReconcileDecision(
            actions=[ReconcileAction.ENTRY_CANCELLED],
            order_id=str(order.order_id),
            reason="broker_cancelled",
        )
    return ReconcileDecision(order_id=str(order.order_id), reason=ENTRY_WORKING_WAIT_REASON)
