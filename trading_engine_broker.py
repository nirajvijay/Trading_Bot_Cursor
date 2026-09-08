"""Broker port: FakeBroker for demo, KiteBroker gated behind live-orders flag.

KiteBroker never logs tokens. FakeBroker never calls kiteconnect.place_order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from continuation_features import price_to_ticks, ticks_to_price

from trading_engine_types import (
    STOP_ORDER_TYPES,
    BrokerOrder,
    MarginQuote,
    PositionQuote,
    broker_order_filled_qty,
    broker_order_pending_qty,
)

LIVE_ORDERS_DISABLED_REASON = "live_orders_disabled"
SL_CANCELLED = {"CANCELLED", "REJECTED"}
# Verified Kite/NSE convention: timezone-less order/exchange timestamps are IST.
KITE_EXCHANGE_TZ = ZoneInfo("Asia/Kolkata")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_timestamp_text(raw: object) -> Optional[datetime]:
    """Parse a timestamp string without inventing a timezone for naive values."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    if "T" not in text and " " in text:
        text = text.replace(" ", "T", 1)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def normalize_broker_timestamp(
    raw: object,
    *,
    naive_tz: Optional[ZoneInfo],
) -> Optional[str]:
    """Normalize a broker timestamp to an explicit UTC-offset ISO string.

    Timezone-aware inputs keep their instant. Timezone-less inputs are interpreted
    only when ``naive_tz`` is the adapter's verified convention; otherwise return
    None (unknown source → leave attribution unresolved).
    """
    parsed = parse_timestamp_text(raw)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        if naive_tz is None:
            return None
        parsed = parsed.replace(tzinfo=naive_tz)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


class BrokerPort(Protocol):
    def orders_by_tag(self, tag: str) -> List[BrokerOrder]: ...

    def orders_for_symbol(self, tradingsymbol: str) -> List[BrokerOrder]: ...

    def place_market_mis(
        self,
        *,
        tradingsymbol: str,
        transaction_type: str,
        quantity: int,
        tag: str,
    ) -> BrokerOrder: ...

    def place_slm(
        self,
        *,
        tradingsymbol: str,
        transaction_type: str,
        quantity: int,
        trigger_price: float,
        tag: str,
        tick_size: float = 0.05,
    ) -> BrokerOrder: ...

    def modify_slm(
        self,
        order_id: str,
        trigger_price: float,
        *,
        tick_size: float = 0.05,
        transaction_type: Optional[str] = None,
        quantity: Optional[int] = None,
    ) -> BrokerOrder:
        """Resize/reprice a working stop.

        Port contract for ``quantity``: desired *remaining working cover* (pending size),
        not the broker's total order quantity. Adapters that speak total-qty APIs
        (e.g. Kite) must translate: total = filled_quantity + quantity.
        """
        ...

    def cancel_order(self, order_id: str) -> Optional[BrokerOrder]: ...

    def poll_order(self, order_id: str) -> Optional[BrokerOrder]: ...

    def net_position_qty(self, tradingsymbol: str) -> Optional[int]: ...

    def position_quote(self, tradingsymbol: str) -> Optional[PositionQuote]: ...

    def clear_quote_cache(self) -> None: ...

    def order_margins(
        self,
        *,
        tradingsymbol: str,
        transaction_type: str,
        quantity: int,
    ) -> MarginQuote: ...

    def ltp(self, tradingsymbol: str) -> Optional[float]: ...


def _new_order_id() -> str:
    return uuid4().hex[:16]


def _is_stop_order(order: BrokerOrder) -> bool:
    return str(order.order_type) in STOP_ORDER_TYPES


def modify_slm_total_quantity(*, desired_remaining_cover: int, filled_quantity: int) -> int:
    """Translate port-level remaining cover to broker total order quantity (Kite-style)."""
    return max(0, int(filled_quantity)) + max(0, int(desired_remaining_cover))


def _vwap_after_fill(
    *,
    prev_filled: int,
    prev_avg: Optional[float],
    new_filled: int,
    fill_price: float,
) -> float:
    """Cumulative VWAP when adding fills at fill_price up to new_filled."""
    new_filled = max(0, int(new_filled))
    prev_filled = max(0, int(prev_filled))
    if new_filled <= 0:
        return float(fill_price)
    if new_filled <= prev_filled or prev_filled <= 0 or prev_avg is None:
        return float(fill_price)
    bump = new_filled - prev_filled
    return (float(prev_filled) * float(prev_avg) + float(bump) * float(fill_price)) / float(
        new_filled
    )


def _limit_price_for_stop(
    *,
    transaction_type: str,
    trigger_price: float,
    tick_size: float,
    worse_ticks: int = 0,
) -> float:
    aligned = ticks_to_price(price_to_ticks(trigger_price, tick_size), tick_size)
    if worse_ticks <= 0:
        return aligned
    ticks = price_to_ticks(aligned, tick_size)
    side = str(transaction_type).upper()
    if side == "SELL":
        ticks -= int(worse_ticks)
    else:
        ticks += int(worse_ticks)
    if ticks < 0:
        ticks = 0
    return ticks_to_price(ticks, tick_size)


def _copy_order(order: BrokerOrder, **changes: object) -> BrokerOrder:
    data = {
        "order_id": order.order_id,
        "tag": order.tag,
        "tradingsymbol": order.tradingsymbol,
        "transaction_type": order.transaction_type,
        "order_type": order.order_type,
        "quantity": order.quantity,
        "status": order.status,
        "average_price": order.average_price,
        "trigger_price": order.trigger_price,
        "price": order.price,
        "product": order.product,
        "variety": order.variety,
        "exchange": order.exchange,
        "filled_quantity": order.filled_quantity,
        "pending_quantity": order.pending_quantity,
        "cancelled_quantity": order.cancelled_quantity,
        "order_timestamp": order.order_timestamp,
    }
    data.update(changes)
    return BrokerOrder(**data)  # type: ignore[arg-type]


@dataclass
class FakeBroker:
    """In-memory MIS/SL simulator. 5x margin is a demo heuristic only."""

    auto_fill_entry: bool = True
    auto_confirm_sl: bool = True
    demo_leverage: float = 5.0
    remaining_capital: float = 300_000.0
    last_prices: Dict[str, float] = field(default_factory=dict)
    orders: Dict[str, BrokerOrder] = field(default_factory=dict)
    market_place_count: int = 0
    slm_place_count: int = 0
    modify_count: int = 0
    live_orders_enabled: bool = False
    reject_equal_sl_price: bool = False
    position_quotes: Dict[str, PositionQuote] = field(default_factory=dict)
    positions_error: bool = False
    modify_error: Optional[str] = None
    # When True, modify_slm ignores quantity updates (stale broker response simulation).
    stale_modify_quantity: bool = False
    # Delay tag visibility for MARKET orders until reveal_tag() (lost-response tests).
    hide_market_tags: bool = False
    _hidden_tags: set = field(default_factory=set)
    # When True, cancel_order leaves the order working (unconfirmed cancel).
    cancel_noop: bool = False
    # Extra shares filled at cancel time (cancellation-race simulation).
    cancel_additional_fill: int = 0
    # Optional fixed timestamp for new orders (tests); otherwise wall clock.
    next_order_timestamp: Optional[str] = None

    def _stamp(self) -> str:
        if self.next_order_timestamp is not None:
            # Test override must still be explicit-offset (or IST-normalized).
            normalized = normalize_broker_timestamp(
                self.next_order_timestamp, naive_tz=KITE_EXCHANGE_TZ
            )
            if normalized is not None:
                return normalized
            return str(self.next_order_timestamp)
        return _utc_now_iso()

    def _coerce_order_timestamp(
        self,
        order_timestamp: Optional[str],
        *,
        stamp: bool,
        timezone_known: bool = True,
    ) -> Optional[str]:
        """Normalize timestamps at the FakeBroker (NSE/MIS) adapter boundary.

        FakeBroker follows the same verified IST convention as Kite for timezone-less
        broker stamps when ``timezone_known`` is True. Unknown-source timezone-less
        values are left as raw naive strings so attribution rejects them.
        """
        if order_timestamp is not None:
            text = str(order_timestamp).strip()
            if not text:
                return None
            if timezone_known:
                return normalize_broker_timestamp(text, naive_tz=KITE_EXCHANGE_TZ)
            # Preserve raw form (possibly naive) — engine requires explicit TZ.
            return text
        if stamp:
            return self._stamp()
        return None

    def clear_quote_cache(self) -> None:
        return

    def orders_by_tag(self, tag: str) -> List[BrokerOrder]:
        if self.hide_market_tags and tag in self._hidden_tags:
            return [
                o
                for o in self.orders.values()
                if o.tag == tag and o.order_type != "MARKET"
            ]
        return [o for o in self.orders.values() if o.tag == tag]

    def reveal_tag(self, tag: str) -> None:
        self._hidden_tags.discard(tag)

    def orders_for_symbol(self, tradingsymbol: str) -> List[BrokerOrder]:
        return [o for o in self.orders.values() if o.tradingsymbol == tradingsymbol]

    def place_market_mis(
        self,
        *,
        tradingsymbol: str,
        transaction_type: str,
        quantity: int,
        tag: str,
    ) -> BrokerOrder:
        existing = [
            o
            for o in self.orders.values()
            if o.tag == tag and o.order_type == "MARKET"
        ]
        if existing:
            return existing[0]
        self.market_place_count += 1
        status = "COMPLETE" if self.auto_fill_entry else "OPEN"
        avg = self.last_prices.get(tradingsymbol)
        filled = quantity if status == "COMPLETE" else 0
        pending = 0 if status == "COMPLETE" else quantity
        order = BrokerOrder(
            order_id=_new_order_id(),
            tag=tag,
            tradingsymbol=tradingsymbol,
            transaction_type=transaction_type,
            order_type="MARKET",
            quantity=quantity,
            status=status,
            average_price=avg if status == "COMPLETE" else None,
            filled_quantity=filled,
            pending_quantity=pending,
            cancelled_quantity=0,
            order_timestamp=self._stamp(),
        )
        self.orders[order.order_id] = order
        if self.hide_market_tags:
            self._hidden_tags.add(tag)
        return order

    def place_slm(
        self,
        *,
        tradingsymbol: str,
        transaction_type: str,
        quantity: int,
        trigger_price: float,
        tag: str,
        tick_size: float = 0.05,
    ) -> BrokerOrder:
        # Only reuse working stops — never COMPLETE/CANCELLED/REJECTED.
        existing = [
            o
            for o in self.orders_by_tag(tag)
            if _is_stop_order(o) and o.status in {"TRIGGER PENDING", "OPEN"}
        ]
        if existing:
            return existing[0]
        self.slm_place_count += 1
        status = "TRIGGER PENDING" if self.auto_confirm_sl else "OPEN"
        price = _limit_price_for_stop(
            transaction_type=transaction_type,
            trigger_price=trigger_price,
            tick_size=tick_size,
            worse_ticks=1 if self.reject_equal_sl_price else 0,
        )
        order = BrokerOrder(
            order_id=_new_order_id(),
            tag=tag,
            tradingsymbol=tradingsymbol,
            transaction_type=transaction_type,
            order_type="SL",
            quantity=quantity,
            status=status,
            trigger_price=trigger_price,
            price=price,
            filled_quantity=0,
            pending_quantity=quantity,
            cancelled_quantity=0,
            order_timestamp=self._stamp(),
        )
        self.orders[order.order_id] = order
        return order

    def modify_slm(
        self,
        order_id: str,
        trigger_price: float,
        *,
        tick_size: float = 0.05,
        transaction_type: Optional[str] = None,
        quantity: Optional[int] = None,
    ) -> BrokerOrder:
        if self.modify_error:
            raise RuntimeError(self.modify_error)
        order = self.orders[order_id]
        self.modify_count += 1
        side = transaction_type or order.transaction_type
        price = _limit_price_for_stop(
            transaction_type=side,
            trigger_price=trigger_price,
            tick_size=tick_size,
            worse_ticks=1 if self.reject_equal_sl_price else 0,
        )
        qty = int(order.quantity or 0) if quantity is None else int(quantity)
        filled = int(order.filled_quantity or 0)
        if self.stale_modify_quantity:
            qty = int(order.quantity or 0)
            pending = max(
                0,
                int(order.pending_quantity or 0)
                if order.pending_quantity is not None
                else qty - filled,
            )
        elif quantity is not None:
            # Port contract: quantity = desired remaining working cover.
            pending = int(quantity)
            qty = modify_slm_total_quantity(
                desired_remaining_cover=pending, filled_quantity=filled
            )
        else:
            pending = max(0, qty - filled)
        updated = _copy_order(
            order,
            trigger_price=trigger_price,
            price=price,
            order_type="SL",
            quantity=qty,
            pending_quantity=pending,
            filled_quantity=filled,
        )
        self.orders[order_id] = updated
        return updated

    def cancel_order(self, order_id: str) -> Optional[BrokerOrder]:
        order = self.orders.get(order_id)
        if order is None:
            return None
        filled = int(order.filled_quantity or 0)
        if filled <= 0 and str(order.status).upper() == "COMPLETE":
            filled = int(order.quantity or 0)
        pending = int(order.pending_quantity or 0)
        if pending <= 0 and str(order.status).upper() not in {"COMPLETE", "CANCELLED", "REJECTED"}:
            pending = max(0, int(order.quantity or 0) - filled)
        # Simulate fills that land in the cancel race window.
        extra = max(0, int(self.cancel_additional_fill or 0))
        if extra > 0 and pending > 0:
            add = min(extra, pending)
            filled += add
            pending -= add
            self.cancel_additional_fill = max(0, extra - add)
        if self.cancel_noop:
            updated = _copy_order(
                order,
                filled_quantity=filled,
                pending_quantity=pending,
                average_price=order.average_price or self.last_prices.get(order.tradingsymbol),
            )
            self.orders[order_id] = updated
            return updated
        cancelled = max(0, int(order.quantity or 0) - filled)
        updated = _copy_order(
            order,
            status="CANCELLED",
            pending_quantity=0,
            cancelled_quantity=cancelled,
            filled_quantity=filled,
            average_price=order.average_price or self.last_prices.get(order.tradingsymbol),
        )
        self.orders[order_id] = updated
        return updated

    def poll_order(self, order_id: str) -> Optional[BrokerOrder]:
        return self.orders.get(order_id)

    def net_position_qty(self, tradingsymbol: str) -> Optional[int]:
        try:
            quote = self.position_quote(tradingsymbol)
        except Exception:  # noqa: BLE001
            return None
        if quote is not None:
            return int(quote.quantity)
        return 0

    def position_quote(self, tradingsymbol: str) -> Optional[PositionQuote]:
        if self.positions_error:
            raise RuntimeError("positions_down")
        if tradingsymbol in self.position_quotes:
            return self.position_quotes[tradingsymbol]
        qty = 0
        avg = None
        for order in self.orders.values():
            if order.tradingsymbol != tradingsymbol:
                continue
            if str(order.product or "MIS") != "MIS":
                continue
            status = str(order.status).upper()
            filled = int(order.filled_quantity or 0)
            if filled <= 0 and status == "COMPLETE":
                filled = int(order.quantity or 0)
            if filled <= 0:
                continue
            # Include partially filled stops/exits (filled_quantity > 0) even if not COMPLETE.
            signed = filled if str(order.transaction_type).upper() == "BUY" else -filled
            qty += signed
            if order.average_price is not None and avg is None:
                avg = float(order.average_price)
        last = self.last_prices.get(tradingsymbol)
        pnl = None
        if avg is not None and last is not None:
            pnl = float(qty) * (float(last) - float(avg))
        return PositionQuote(
            quantity=qty,
            average_price=avg,
            last_price=last,
            pnl=pnl,
            unrealised=pnl,
        )

    def order_margins(
        self,
        *,
        tradingsymbol: str,
        transaction_type: str,
        quantity: int,
    ) -> MarginQuote:
        px = self.last_prices.get(tradingsymbol, 0.0)
        required = (quantity * px) / self.demo_leverage if px else 0.0
        if required > self.remaining_capital:
            return MarginQuote(ok=False, required=required, reason="insufficient_margin")
        return MarginQuote(ok=True, required=required)

    def ltp(self, tradingsymbol: str) -> Optional[float]:
        return self.last_prices.get(tradingsymbol)

    def fill_entry(self, order_id: str, price: float) -> BrokerOrder:
        order = self.orders[order_id]
        filled = _copy_order(
            order,
            status="COMPLETE",
            average_price=price,
            filled_quantity=int(order.quantity or 0),
            pending_quantity=0,
            cancelled_quantity=0,
        )
        self.orders[order_id] = filled
        self.last_prices[order.tradingsymbol] = price
        return filled

    def fill_entry_partial(
        self,
        order_id: str,
        filled_quantity: int,
        price: float,
        *,
        complete: bool = False,
    ) -> BrokerOrder:
        """Simulate a partial (or completing) entry fill. Cumulative filled_quantity."""
        order = self.orders[order_id]
        total = int(order.quantity or 0)
        prev_filled = int(order.filled_quantity or 0)
        filled_quantity = max(0, min(int(filled_quantity), total))
        pending = 0 if complete or filled_quantity >= total else total - filled_quantity
        status = "COMPLETE" if (complete or filled_quantity >= total) else "OPEN"
        if filled_quantity > 0 and status != "COMPLETE":
            status = "OPEN"
        avg = _vwap_after_fill(
            prev_filled=prev_filled,
            prev_avg=order.average_price,
            new_filled=filled_quantity,
            fill_price=price,
        )
        updated = _copy_order(
            order,
            status=status,
            average_price=avg,
            filled_quantity=filled_quantity,
            pending_quantity=pending,
            cancelled_quantity=0,
        )
        self.orders[order_id] = updated
        self.last_prices[order.tradingsymbol] = price
        return updated

    def fill_sl_partial(
        self,
        order_id: str,
        filled_quantity: int,
        price: Optional[float] = None,
        *,
        complete: bool = False,
        average_price: Optional[float] = None,
    ) -> BrokerOrder:
        """Partial stop execution: triggered OPEN with executable remainder (not TRIGGER PENDING).

        Pass price=None (and average_price=None) to leave the fill unpriced until
        ``set_order_average_price`` reconciles a confirmed execution price.
        """
        order = self.orders[order_id]
        total = int(order.quantity or 0)
        prev_filled = int(order.filled_quantity or 0)
        filled_quantity = max(0, min(int(filled_quantity), total))
        pending = 0 if complete or filled_quantity >= total else total - filled_quantity
        if complete or filled_quantity >= total:
            status = "COMPLETE"
            pending = 0
        elif filled_quantity > 0:
            status = "OPEN"
        else:
            status = "TRIGGER PENDING"
        if average_price is not None:
            avg: Optional[float] = float(average_price)
        elif price is None:
            avg = order.average_price if filled_quantity <= prev_filled else None
        else:
            avg = _vwap_after_fill(
                prev_filled=prev_filled,
                prev_avg=order.average_price,
                new_filled=filled_quantity,
                fill_price=float(price),
            )
        updated = _copy_order(
            order,
            status=status,
            average_price=avg,
            filled_quantity=filled_quantity,
            pending_quantity=pending,
        )
        self.orders[order_id] = updated
        if price is not None:
            self.last_prices[order.tradingsymbol] = float(price)
        return updated

    def set_order_average_price(self, order_id: str, average_price: float) -> BrokerOrder:
        """Reconcile a previously unpriced fill with a confirmed average price."""
        order = self.orders[order_id]
        updated = _copy_order(order, average_price=float(average_price))
        self.orders[order_id] = updated
        return updated

    def confirm_sl(self, order_id: str) -> BrokerOrder:
        order = self.orders[order_id]
        confirmed = _copy_order(
            order,
            status="TRIGGER PENDING",
            pending_quantity=int(order.quantity or 0),
            filled_quantity=0,
        )
        self.orders[order_id] = confirmed
        return confirmed

    def fill_sl(self, order_id: str, price: float) -> BrokerOrder:
        order = self.orders[order_id]
        filled = _copy_order(
            order,
            status="COMPLETE",
            average_price=price,
            filled_quantity=int(order.quantity or 0),
            pending_quantity=0,
        )
        self.orders[order_id] = filled
        self.last_prices[order.tradingsymbol] = price
        return filled

    def flatten_mis(
        self,
        tradingsymbol: str,
        price: float,
        *,
        order_type: str = "MARKET",
        order_timestamp: Optional[str] = None,
        stamp: bool = True,
        timezone_known: bool = True,
    ) -> BrokerOrder:
        net = self.net_position_qty(tradingsymbol)
        if net is None or net == 0:
            raise ValueError("no_position")
        side = "SELL" if net > 0 else "BUY"
        ot = str(order_type or "MARKET").upper()
        ts = self._coerce_order_timestamp(
            order_timestamp,
            stamp=stamp,
            timezone_known=timezone_known,
        )
        order = BrokerOrder(
            order_id=_new_order_id(),
            tag="",
            tradingsymbol=tradingsymbol,
            transaction_type=side,
            order_type=ot,
            quantity=abs(net),
            status="COMPLETE",
            average_price=price,
            filled_quantity=abs(net),
            pending_quantity=0,
            cancelled_quantity=0,
            order_timestamp=ts,
        )
        self.orders[order.order_id] = order
        self.last_prices[tradingsymbol] = price
        return order

    def inject_complete_order(
        self,
        *,
        tradingsymbol: str,
        transaction_type: str,
        quantity: int,
        price: float,
        order_type: str = "MARKET",
        tag: str = "",
        product: str = "MIS",
        exchange: str = "NSE",
        order_timestamp: Optional[str] = None,
        stamp: bool = True,
        timezone_known: bool = True,
    ) -> BrokerOrder:
        """Inject an unrelated/manual complete order (tests / external activity)."""
        ts = self._coerce_order_timestamp(
            order_timestamp,
            stamp=stamp,
            timezone_known=timezone_known,
        )
        order = BrokerOrder(
            order_id=_new_order_id(),
            tag=tag,
            tradingsymbol=tradingsymbol,
            transaction_type=transaction_type,
            order_type=str(order_type).upper(),
            quantity=int(quantity),
            status="COMPLETE",
            average_price=float(price),
            filled_quantity=int(quantity),
            pending_quantity=0,
            cancelled_quantity=0,
            product=product,
            exchange=exchange,
            order_timestamp=ts,
        )
        self.orders[order.order_id] = order
        return order


def _kite_order_to_broker(raw: dict) -> BrokerOrder:
    price_raw = raw.get("price")
    price = None
    if price_raw not in (None, 0, 0.0, "0"):
        price = float(price_raw)
    quantity = int(raw.get("quantity") or 0)
    filled_quantity = int(raw.get("filled_quantity") or 0)
    pending_quantity = int(raw.get("pending_quantity") or 0)
    cancelled_quantity = int(raw.get("cancelled_quantity") or 0)
    status = str(raw.get("status") or "")
    if filled_quantity <= 0 and status.upper() == "COMPLETE":
        filled_quantity = quantity
    if pending_quantity <= 0 and status.upper() not in {"COMPLETE", "CANCELLED", "REJECTED"}:
        pending_quantity = max(0, quantity - filled_quantity)
    return BrokerOrder(
        order_id=str(raw.get("order_id") or ""),
        tag=str(raw.get("tag") or ""),
        tradingsymbol=str(raw.get("tradingsymbol") or ""),
        transaction_type=str(raw.get("transaction_type") or ""),
        order_type=str(raw.get("order_type") or ""),
        quantity=quantity,
        status=status,
        average_price=(
            float(raw["average_price"])
            if raw.get("average_price") not in (None, 0, 0.0, "0")
            else None
        ),
        trigger_price=(
            float(raw["trigger_price"]) if raw.get("trigger_price") else None
        ),
        price=price,
        product=str(raw.get("product") or "MIS"),
        variety=str(raw.get("variety") or "regular"),
        exchange=str(raw.get("exchange") or "NSE"),
        filled_quantity=filled_quantity,
        pending_quantity=pending_quantity,
        cancelled_quantity=cancelled_quantity,
        order_timestamp=normalize_broker_timestamp(
            raw.get("order_timestamp") or raw.get("exchange_timestamp"),
            naive_tz=KITE_EXCHANGE_TZ,
        ),
    )


def _optional_float(value: object) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _find_mis_position(data: dict, tradingsymbol: str) -> Optional[dict]:
    for bucket in ("net", "day"):
        for item in data.get(bucket) or []:
            if str(item.get("tradingsymbol") or "") != tradingsymbol:
                continue
            if str(item.get("product") or "MIS") != "MIS":
                continue
            return item
    return None


class KiteBroker:
    """Live Kite Connect adapter. Must not run unless live_orders_enabled."""

    def __init__(self, kite: object, *, live_orders_enabled: bool) -> None:
        self._kite = kite
        self.live_orders_enabled = live_orders_enabled
        self._positions_payload: Optional[object] = None
        self._positions_failed = False

    def clear_quote_cache(self) -> None:
        self._positions_payload = None
        self._positions_failed = False

    def _require_live(self) -> None:
        if not self.live_orders_enabled:
            raise RuntimeError(LIVE_ORDERS_DISABLED_REASON)

    def orders_by_tag(self, tag: str) -> List[BrokerOrder]:
        raw = self._kite.orders()  # type: ignore[attr-defined]
        return [_kite_order_to_broker(o) for o in raw if str(o.get("tag") or "") == tag]

    def orders_for_symbol(self, tradingsymbol: str) -> List[BrokerOrder]:
        raw = self._kite.orders()  # type: ignore[attr-defined]
        return [
            _kite_order_to_broker(o)
            for o in raw
            if str(o.get("tradingsymbol") or "") == tradingsymbol
        ]

    def place_market_mis(
        self,
        *,
        tradingsymbol: str,
        transaction_type: str,
        quantity: int,
        tag: str,
    ) -> BrokerOrder:
        self._require_live()
        existing = [o for o in self.orders_by_tag(tag) if o.order_type == "MARKET"]
        if existing:
            return existing[0]
        result = self._kite.place_order(  # type: ignore[attr-defined]
            variety="regular",
            exchange="NSE",
            tradingsymbol=tradingsymbol,
            transaction_type=transaction_type,
            quantity=quantity,
            product="MIS",
            order_type="MARKET",
            validity="DAY",
            tag=tag,
            market_protection=-1,
        )
        order_id = result["order_id"] if isinstance(result, dict) else str(result)
        polled = self.poll_order(str(order_id))
        if polled is not None:
            return polled
        return BrokerOrder(
            order_id=str(order_id),
            tag=tag,
            tradingsymbol=tradingsymbol,
            transaction_type=transaction_type,
            order_type="MARKET",
            quantity=quantity,
            status="OPEN",
        )

    def _place_sl_limit(
        self,
        *,
        tradingsymbol: str,
        transaction_type: str,
        quantity: int,
        trigger_price: float,
        price: float,
        tag: str,
    ) -> object:
        return self._kite.place_order(  # type: ignore[attr-defined]
            variety="regular",
            exchange="NSE",
            tradingsymbol=tradingsymbol,
            transaction_type=transaction_type,
            quantity=quantity,
            product="MIS",
            order_type="SL",
            trigger_price=trigger_price,
            price=price,
            validity="DAY",
            tag=tag,
        )

    def place_slm(
        self,
        *,
        tradingsymbol: str,
        transaction_type: str,
        quantity: int,
        trigger_price: float,
        tag: str,
        tick_size: float = 0.05,
    ) -> BrokerOrder:
        self._require_live()
        existing = [
            o
            for o in self.orders_by_tag(tag)
            if _is_stop_order(o) and o.status.upper() not in (SL_CANCELLED | {"COMPLETE"})
        ]
        if existing:
            return existing[0]
        equal = _limit_price_for_stop(
            transaction_type=transaction_type,
            trigger_price=trigger_price,
            tick_size=tick_size,
            worse_ticks=0,
        )
        try:
            result = self._place_sl_limit(
                tradingsymbol=tradingsymbol,
                transaction_type=transaction_type,
                quantity=quantity,
                trigger_price=equal,
                price=equal,
                tag=tag,
            )
        except Exception:  # noqa: BLE001
            worse = _limit_price_for_stop(
                transaction_type=transaction_type,
                trigger_price=trigger_price,
                tick_size=tick_size,
                worse_ticks=1,
            )
            result = self._place_sl_limit(
                tradingsymbol=tradingsymbol,
                transaction_type=transaction_type,
                quantity=quantity,
                trigger_price=equal,
                price=worse,
                tag=tag,
            )
        order_id = result["order_id"] if isinstance(result, dict) else str(result)
        polled = self.poll_order(str(order_id))
        if polled is not None:
            return polled
        return BrokerOrder(
            order_id=str(order_id),
            tag=tag,
            tradingsymbol=tradingsymbol,
            transaction_type=transaction_type,
            order_type="SL",
            quantity=quantity,
            status="OPEN",
            trigger_price=equal,
            price=equal,
        )

    def modify_slm(
        self,
        order_id: str,
        trigger_price: float,
        *,
        tick_size: float = 0.05,
        transaction_type: Optional[str] = None,
        quantity: Optional[int] = None,
    ) -> BrokerOrder:
        self._require_live()
        existing = self.poll_order(order_id)
        if existing is None:
            raise RuntimeError("modify_slm_requires_confirmed_order_state")

        status = str(existing.status).upper()
        filled = broker_order_filled_qty(existing)
        pending = broker_order_pending_qty(existing)
        side = transaction_type or existing.transaction_type or "SELL"

        # Classify from confirmed broker status — never infer "waiting" from zero fills.
        is_waiting = status == "TRIGGER PENDING"
        is_triggered_executable = status == "OPEN" and filled > 0 and pending > 0

        modify_kwargs: dict = {
            "variety": "regular",
            "order_id": order_id,
        }
        if quantity is not None:
            # Port contract: quantity = desired remaining cover.
            modify_kwargs["quantity"] = modify_slm_total_quantity(
                desired_remaining_cover=int(quantity),
                filled_quantity=filled,
            )

        if is_waiting:
            equal = _limit_price_for_stop(
                transaction_type=side,
                trigger_price=trigger_price,
                tick_size=tick_size,
                worse_ticks=0,
            )
            modify_kwargs["order_type"] = "SL"
            modify_kwargs["trigger_price"] = equal
            modify_kwargs["price"] = equal
        elif is_triggered_executable:
            # Quantity-only resize. Do not send order_type / price / trigger.
            if quantity is None:
                raise RuntimeError("modify_slm_triggered_quantity_only")
        else:
            raise RuntimeError(f"modify_slm_uncertain_order_state:{status}")

        try:
            self._kite.modify_order(**modify_kwargs)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            # Ambiguous write: reconcile current broker state; do not immediately retry.
            polled = self.poll_order(order_id)
            if polled is not None:
                return polled
            raise RuntimeError(f"modify_slm_ambiguous:{exc}") from exc

        polled = self.poll_order(order_id)
        if polled is None:
            raise RuntimeError("modify_slm_unconfirmed_after_write")
        return polled

    def cancel_order(self, order_id: str) -> Optional[BrokerOrder]:
        self._require_live()
        self._kite.cancel_order(variety="regular", order_id=order_id)  # type: ignore[attr-defined]
        return self.poll_order(order_id)

    def poll_order(self, order_id: str) -> Optional[BrokerOrder]:
        raw = self._kite.orders()  # type: ignore[attr-defined]
        for item in raw:
            if str(item.get("order_id")) == str(order_id):
                return _kite_order_to_broker(item)
        return None

    def net_position_qty(self, tradingsymbol: str) -> Optional[int]:
        quote = self.position_quote(tradingsymbol)
        if quote is None:
            return None
        return int(quote.quantity)

    def position_quote(self, tradingsymbol: str) -> Optional[PositionQuote]:
        data = self._positions()
        if data is None:
            return None
        item = _find_mis_position(data, tradingsymbol)
        if item is None:
            return PositionQuote(quantity=0)
        qty = int(item.get("quantity") or 0)
        avg = item.get("average_price")
        last = item.get("last_price")
        pnl = item.get("pnl")
        unrealised = item.get("unrealised")
        realised = item.get("realised")
        return PositionQuote(
            quantity=qty,
            average_price=float(avg) if avg not in (None, "", 0, 0.0) else None,
            last_price=float(last) if last not in (None, "", 0, 0.0) else None,
            pnl=_optional_float(pnl),
            unrealised=_optional_float(unrealised),
            realised=_optional_float(realised),
        )

    def _positions(self) -> Optional[dict]:
        if self._positions_failed:
            return None
        if self._positions_payload is not None:
            return self._positions_payload if isinstance(self._positions_payload, dict) else None
        try:
            data = self._kite.positions()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            self._positions_failed = True
            return None
        self._positions_payload = data
        return data if isinstance(data, dict) else None

    def order_margins(
        self,
        *,
        tradingsymbol: str,
        transaction_type: str,
        quantity: int,
    ) -> MarginQuote:
        self._require_live()
        try:
            params = [
                {
                    "exchange": "NSE",
                    "tradingsymbol": tradingsymbol,
                    "transaction_type": transaction_type,
                    "variety": "regular",
                    "product": "MIS",
                    "order_type": "MARKET",
                    "quantity": quantity,
                }
            ]
            result = self._kite.order_margins(params)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            return MarginQuote(ok=False, required=0.0, reason=f"margin_unavailable:{exc}")
        if isinstance(result, list) and result:
            first = result[0]
            if first.get("error") or first.get("status") == "error":
                msg = str(first.get("error") or first.get("message") or "mis_unavailable")
                return MarginQuote(ok=False, required=0.0, reason=msg)
            required = float(
                first.get("total")
                or first.get("margin")
                or (first.get("final") or {}).get("total")
                or 0.0
            )
            return MarginQuote(ok=True, required=required)
        if isinstance(result, dict):
            required = float(result.get("total") or 0.0)
            return MarginQuote(ok=True, required=required)
        return MarginQuote(ok=False, required=0.0, reason="mis_unavailable")

    def ltp(self, tradingsymbol: str) -> Optional[float]:
        data = self._kite.ltp([f"NSE:{tradingsymbol}"])  # type: ignore[attr-defined]
        key = f"NSE:{tradingsymbol}"
        if isinstance(data, dict) and key in data:
            last = data[key].get("last_price")
            return float(last) if last is not None else None
        return None
