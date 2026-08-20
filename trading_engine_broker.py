"""Broker port: FakeBroker for demo, KiteBroker gated behind live-orders flag.

KiteBroker never logs tokens. FakeBroker never calls kiteconnect.place_order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol
from uuid import uuid4

from trading_engine_types import BrokerOrder, MarginQuote

LIVE_ORDERS_DISABLED_REASON = "live_orders_disabled"


class BrokerPort(Protocol):
    def orders_by_tag(self, tag: str) -> List[BrokerOrder]: ...

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
    ) -> BrokerOrder: ...

    def modify_slm(self, order_id: str, trigger_price: float) -> BrokerOrder: ...

    def poll_order(self, order_id: str) -> Optional[BrokerOrder]: ...

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


@dataclass
class FakeBroker:
    """In-memory MIS/SL-M simulator. 5x margin is a demo heuristic only."""

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

    def orders_by_tag(self, tag: str) -> List[BrokerOrder]:
        return [o for o in self.orders.values() if o.tag == tag]

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
    ) -> BrokerOrder:
        existing = [
            o
            for o in self.orders_by_tag(tag)
            if o.order_type == "SL-M"
            and o.status in {"TRIGGER PENDING", "OPEN", "COMPLETE"}
        ]
        if existing:
            return existing[0]
        self.slm_place_count += 1
        status = "TRIGGER PENDING" if self.auto_confirm_sl else "OPEN"
        order = BrokerOrder(
            order_id=_new_order_id(),
            tag=tag,
            tradingsymbol=tradingsymbol,
            transaction_type=transaction_type,
            order_type="SL-M",
            quantity=quantity,
            status=status,
            trigger_price=trigger_price,
        )
        self.orders[order.order_id] = order
        return order

    def modify_slm(self, order_id: str, trigger_price: float) -> BrokerOrder:
        order = self.orders[order_id]
        self.modify_count += 1
        updated = BrokerOrder(
            order_id=order.order_id,
            tag=order.tag,
            tradingsymbol=order.tradingsymbol,
            transaction_type=order.transaction_type,
            order_type=order.order_type,
            quantity=order.quantity,
            status=order.status,
            average_price=order.average_price,
            trigger_price=trigger_price,
        )
        self.orders[order_id] = updated
        return updated

    def poll_order(self, order_id: str) -> Optional[BrokerOrder]:
        return self.orders.get(order_id)

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
        filled = BrokerOrder(
            order_id=order.order_id,
            tag=order.tag,
            tradingsymbol=order.tradingsymbol,
            transaction_type=order.transaction_type,
            order_type=order.order_type,
            quantity=order.quantity,
            status="COMPLETE",
            average_price=price,
            trigger_price=order.trigger_price,
        )
        self.orders[order_id] = filled
        self.last_prices[order.tradingsymbol] = price
        return filled

    def confirm_sl(self, order_id: str) -> BrokerOrder:
        order = self.orders[order_id]
        confirmed = BrokerOrder(
            order_id=order.order_id,
            tag=order.tag,
            tradingsymbol=order.tradingsymbol,
            transaction_type=order.transaction_type,
            order_type=order.order_type,
            quantity=order.quantity,
            status="TRIGGER PENDING",
            average_price=order.average_price,
            trigger_price=order.trigger_price,
        )
        self.orders[order_id] = confirmed
        return confirmed

    def fill_sl(self, order_id: str, price: float) -> BrokerOrder:
        order = self.orders[order_id]
        filled = BrokerOrder(
            order_id=order.order_id,
            tag=order.tag,
            tradingsymbol=order.tradingsymbol,
            transaction_type=order.transaction_type,
            order_type=order.order_type,
            quantity=order.quantity,
            status="COMPLETE",
            average_price=price,
            trigger_price=order.trigger_price,
        )
        self.orders[order_id] = filled
        self.last_prices[order.tradingsymbol] = price
        return filled


def _kite_order_to_broker(raw: dict) -> BrokerOrder:
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
        product=str(raw.get("product") or "MIS"),
        variety=str(raw.get("variety") or "regular"),
        exchange=str(raw.get("exchange") or "NSE"),
    )


class KiteBroker:
    """Live Kite Connect adapter. Must not run unless live_orders_enabled."""

    def __init__(self, kite: object, *, live_orders_enabled: bool) -> None:
        self._kite = kite
        self.live_orders_enabled = live_orders_enabled

    def _require_live(self) -> None:
        if not self.live_orders_enabled:
            raise RuntimeError(LIVE_ORDERS_DISABLED_REASON)

    def orders_by_tag(self, tag: str) -> List[BrokerOrder]:
        raw = self._kite.orders()  # type: ignore[attr-defined]
        return [_kite_order_to_broker(o) for o in raw if str(o.get("tag") or "") == tag]

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

    def place_slm(
        self,
        *,
        tradingsymbol: str,
        transaction_type: str,
        quantity: int,
        trigger_price: float,
        tag: str,
    ) -> BrokerOrder:
        self._require_live()
        existing = [
            o
            for o in self.orders_by_tag(tag)
            if o.order_type == "SL-M"
            and o.status.upper() not in {"CANCELLED", "REJECTED"}
        ]
        if existing:
            return existing[0]
        result = self._kite.place_order(  # type: ignore[attr-defined]
            variety="regular",
            exchange="NSE",
            tradingsymbol=tradingsymbol,
            transaction_type=transaction_type,
            quantity=quantity,
            product="MIS",
            order_type="SL-M",
            trigger_price=trigger_price,
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
            order_type="SL-M",
            quantity=quantity,
            status="OPEN",
            trigger_price=trigger_price,
        )

    def modify_slm(self, order_id: str, trigger_price: float) -> BrokerOrder:
        self._require_live()
        self._kite.modify_order(  # type: ignore[attr-defined]
            variety="regular",
            order_id=order_id,
            trigger_price=trigger_price,
        )
        polled = self.poll_order(order_id)
        if polled is not None:
            return polled
        return BrokerOrder(
            order_id=order_id,
            tag="",
            tradingsymbol="",
            transaction_type="",
            order_type="SL-M",
            quantity=0,
            status="OPEN",
            trigger_price=trigger_price,
        )

    def poll_order(self, order_id: str) -> Optional[BrokerOrder]:
        raw = self._kite.orders()  # type: ignore[attr-defined]
        for item in raw:
            if str(item.get("order_id")) == str(order_id):
                return _kite_order_to_broker(item)
        return None

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
