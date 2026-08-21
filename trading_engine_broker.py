"""Broker port: FakeBroker for demo, KiteBroker gated behind live-orders flag.

KiteBroker never logs tokens. FakeBroker never calls kiteconnect.place_order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol
from uuid import uuid4

from continuation_features import price_to_ticks, ticks_to_price

from trading_engine_types import STOP_ORDER_TYPES, BrokerOrder, MarginQuote, PositionQuote

LIVE_ORDERS_DISABLED_REASON = "live_orders_disabled"
SL_CANCELLED = {"CANCELLED", "REJECTED"}


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
    ) -> BrokerOrder: ...

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

    def clear_quote_cache(self) -> None:
        return

    def orders_by_tag(self, tag: str) -> List[BrokerOrder]:
        return [o for o in self.orders.values() if o.tag == tag]

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
            for o in self.orders_by_tag(tag)
            if o.order_type == "MARKET"
        ]
        if existing:
            return existing[0]
        self.market_place_count += 1
        status = "COMPLETE" if self.auto_fill_entry else "OPEN"
        avg = self.last_prices.get(tradingsymbol)
        order = BrokerOrder(
            order_id=_new_order_id(),
            tag=tag,
            tradingsymbol=tradingsymbol,
            transaction_type=transaction_type,
            order_type="MARKET",
            quantity=quantity,
            status=status,
            average_price=avg if status == "COMPLETE" else None,
        )
        self.orders[order.order_id] = order
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
        existing = [
            o
            for o in self.orders_by_tag(tag)
            if _is_stop_order(o)
            and o.status in {"TRIGGER PENDING", "OPEN", "COMPLETE"}
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
        updated = _copy_order(
            order,
            trigger_price=trigger_price,
            price=price,
            order_type="SL",
        )
        self.orders[order_id] = updated
        return updated

    def cancel_order(self, order_id: str) -> Optional[BrokerOrder]:
        order = self.orders.get(order_id)
        if order is None:
            return None
        updated = _copy_order(order, status="CANCELLED")
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
            if str(order.status).upper() != "COMPLETE":
                continue
            if str(order.product or "MIS") != "MIS":
                continue
            signed = order.quantity if str(order.transaction_type).upper() == "BUY" else -order.quantity
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
        filled = _copy_order(order, status="COMPLETE", average_price=price)
        self.orders[order_id] = filled
        self.last_prices[order.tradingsymbol] = price
        return filled

    def confirm_sl(self, order_id: str) -> BrokerOrder:
        order = self.orders[order_id]
        confirmed = _copy_order(order, status="TRIGGER PENDING")
        self.orders[order_id] = confirmed
        return confirmed

    def fill_sl(self, order_id: str, price: float) -> BrokerOrder:
        order = self.orders[order_id]
        filled = _copy_order(order, status="COMPLETE", average_price=price)
        self.orders[order_id] = filled
        self.last_prices[order.tradingsymbol] = price
        return filled

    def flatten_mis(self, tradingsymbol: str, price: float) -> BrokerOrder:
        net = self.net_position_qty(tradingsymbol)
        if net is None or net == 0:
            raise ValueError("no_position")
        side = "SELL" if net > 0 else "BUY"
        order = BrokerOrder(
            order_id=_new_order_id(),
            tag="",
            tradingsymbol=tradingsymbol,
            transaction_type=side,
            order_type="MARKET",
            quantity=abs(net),
            status="COMPLETE",
            average_price=price,
        )
        self.orders[order.order_id] = order
        self.last_prices[tradingsymbol] = price
        return order


def _kite_order_to_broker(raw: dict) -> BrokerOrder:
    price_raw = raw.get("price")
    price = None
    if price_raw not in (None, 0, 0.0, "0"):
        price = float(price_raw)
    return BrokerOrder(
        order_id=str(raw.get("order_id") or ""),
        tag=str(raw.get("tag") or ""),
        tradingsymbol=str(raw.get("tradingsymbol") or ""),
        transaction_type=str(raw.get("transaction_type") or ""),
        order_type=str(raw.get("order_type") or ""),
        quantity=int(raw.get("quantity") or 0),
        status=str(raw.get("status") or ""),
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
            if _is_stop_order(o) and o.status.upper() not in SL_CANCELLED
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
    ) -> BrokerOrder:
        self._require_live()
        side = transaction_type
        if side is None:
            existing = self.poll_order(order_id)
            side = existing.transaction_type if existing is not None else "SELL"
        equal = _limit_price_for_stop(
            transaction_type=side,
            trigger_price=trigger_price,
            tick_size=tick_size,
            worse_ticks=0,
        )
        try:
            self._kite.modify_order(  # type: ignore[attr-defined]
                variety="regular",
                order_id=order_id,
                order_type="SL",
                trigger_price=equal,
                price=equal,
            )
        except Exception:  # noqa: BLE001
            worse = _limit_price_for_stop(
                transaction_type=side,
                trigger_price=trigger_price,
                tick_size=tick_size,
                worse_ticks=1,
            )
            self._kite.modify_order(  # type: ignore[attr-defined]
                variety="regular",
                order_id=order_id,
                order_type="SL",
                trigger_price=equal,
                price=worse,
            )
        polled = self.poll_order(order_id)
        if polled is not None:
            return polled
        return BrokerOrder(
            order_id=order_id,
            tag="",
            tradingsymbol="",
            transaction_type=side or "",
            order_type="SL",
            quantity=0,
            status="OPEN",
            trigger_price=equal,
            price=equal,
        )

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
