"""Durable local PAPER account. Market data is read-only; order writes are SQLite only.

Simulation fills the full remaining quantity at a fresh touch when executable.
It does not model exchange queue priority or displayed depth, and is not live-fill
evidence. Stop LIMIT gaps remain working rather than fabricating a stop-price fill.
"""
from __future__ import annotations

import fcntl
import json
import math
import sqlite3
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from trading_engine_broker import FakeBroker
from trading_engine_quotes import age_seconds
from trading_engine_types import BrokerOrder


class PaperBroker(FakeBroker):
    def __init__(self, path: Path, *, quote_provider, total_capital=300000.0, clock_fn=None):
        super().__init__(auto_fill_entry=False, demo_leverage=1.0, remaining_capital=total_capital)
        self._clock = clock_fn or (lambda: datetime.now(timezone.utc))
        self._quote_provider = quote_provider
        self._quotes = {}
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lease = path.with_suffix(path.suffix + ".lock").open("a+")
        try:
            fcntl.flock(self._lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lease.close()
            raise RuntimeError("paper_account_already_in_use") from None
        self._db = sqlite3.connect(str(path))
        try:
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("CREATE TABLE IF NOT EXISTS paper_account (id INTEGER PRIMARY KEY CHECK(id=1), snapshot TEXT NOT NULL)")
            row = self._db.execute("SELECT snapshot FROM paper_account WHERE id=1").fetchone()
            if row:
                state = json.loads(row[0])
                if state.get("version") != 1:
                    raise ValueError("paper_account_version_unknown")
                self.orders = {o["order_id"]: BrokerOrder(**o) for o in state["orders"]}
                for key in ("market_place_count", "limit_place_count", "slm_place_count", "modify_count"):
                    setattr(self, key, int(state[key]))
                self._hidden_tags = set(state.get("hidden_tags", []))
                self._hidden_order_ids = set(state.get("hidden_ids", []))
            else:
                self._persist()
        except Exception:
            self.close()
            raise

    def close(self):
        self._db.close()
        self._lease.close()

    def _stamp(self):
        return self._clock().astimezone(timezone.utc).isoformat()

    def _persist(self):
        state = {"version": 1, "orders": [asdict(o) for o in self.orders.values()],
                 "hidden_tags": sorted(self._hidden_tags), "hidden_ids": sorted(self._hidden_order_ids)}
        for key in ("market_place_count", "limit_place_count", "slm_place_count", "modify_count"):
            state[key] = getattr(self, key)
        self._db.execute("INSERT INTO paper_account VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET snapshot=excluded.snapshot",
                         (json.dumps(state, allow_nan=False),))
        self._db.commit()

    def touch_quote(self, tradingsymbol):
        if tradingsymbol not in self._quotes:
            try:
                self._quotes[tradingsymbol] = self._quote_provider(tradingsymbol)
            except Exception:
                self._quotes[tradingsymbol] = None
        return self._quotes[tradingsymbol]

    def _touch(self, order):
        quote = self.touch_quote(order.tradingsymbol)
        age = age_seconds(self._clock(), quote.as_of) if quote else None
        if age is None or not 0 <= age <= 2:
            return None
        if quote.bid is not None and quote.ask is not None and quote.bid > quote.ask:
            return None
        touch = quote.ask if order.transaction_type == "BUY" else quote.bid
        if not isinstance(touch, (int, float)) or not math.isfinite(touch) or touch <= 0:
            return None
        return float(touch)

    def clear_quote_cache(self):
        self._quotes.clear()
        self._advance()

    def ltp(self, tradingsymbol):
        # PAPER trailing uses fresh liquidation touch, not a cached execution price.
        qty = sum(o.filled_quantity * (1 if o.transaction_type == "BUY" else -1)
                  for o in self.orders.values() if o.tradingsymbol == tradingsymbol)
        probe = BrokerOrder("", "", tradingsymbol, "SELL" if qty >= 0 else "BUY",
                            "MARKET", 0, "OPEN")
        return self._touch(probe)

    def position_quote(self, tradingsymbol):
        position = super().position_quote(tradingsymbol)
        # Engine's durable per-order accounting owns P&L; don't expose the
        # FakeBroker first-fill approximation as an authoritative broker value.
        return replace(position, last_price=self.ltp(tradingsymbol), pnl=None, unrealised=None)

    def _advance(self):
        changed = False
        for oid, order in list(self.orders.items()):
            if order.status not in {"OPEN", "TRIGGER PENDING"} or order.pending_quantity <= 0:
                continue
            touch = self._touch(order)
            if touch is None:
                continue
            buy = order.transaction_type == "BUY"
            if order.status == "TRIGGER PENDING":
                trigger = order.trigger_price
                if trigger is None or not (touch >= trigger if buy else touch <= trigger):
                    continue
                order = replace(order, status="OPEN")
                self.orders[oid] = order
                changed = True
            executable = order.order_type == "MARKET" or (
                order.price is not None and (touch <= order.price if buy else touch >= order.price))
            if executable:
                if order.filled_quantity and order.average_price is None:
                    # An injected unresolved execution cannot be priced from zero.
                    continue
                previous_value = order.filled_quantity * (order.average_price or 0)
                filled = order.filled_quantity + order.pending_quantity
                self.orders[oid] = replace(order, status="COMPLETE", pending_quantity=0,
                    filled_quantity=filled, average_price=(previous_value + order.pending_quantity*touch)/filled)
                self.last_prices[order.tradingsymbol] = touch
                changed = True
        if changed:
            self._persist()

    def _place_entry_mis(self, **kwargs):
        # Parent is used only for order construction / test fault hooks, never execution.
        previous = self.auto_fill_entry
        self.auto_fill_entry = False
        try:
            order = super()._place_entry_mis(**kwargs)
            self._advance()
            return self.orders[order.order_id]
        finally:
            self.auto_fill_entry = previous
            self._persist()  # Also covers accept-then-raise fault injection.

    def place_slm(self, **kwargs):
        try:
            order = super().place_slm(**kwargs)
            self._advance()
            return self.orders[order.order_id]
        finally:
            self._persist()

    def modify_slm(self, order_id, trigger_price, **kwargs):
        try:
            original = self.orders[order_id]
            if original.status not in {"OPEN", "TRIGGER PENDING"} or original.pending_quantity <= 0:
                raise ValueError("paper_stop_not_working")
            order = super().modify_slm(order_id, trigger_price, **kwargs)
            if original.status == "OPEN":
                # A triggered LIMIT remainder must not be rearmed or repriced.
                order = replace(order, status="OPEN", trigger_price=original.trigger_price, price=original.price)
                self.orders[order_id] = order
            self._advance()
            return self.orders[order_id]
        finally:
            self._persist()

    def cancel_order(self, order_id):
        try:
            self._advance()  # Executions can win a cancellation race.
            order = self.orders.get(order_id)
            if order is not None and order.status in {"COMPLETE", "CANCELLED", "REJECTED"}:
                return self.poll_order(order_id)
            return super().cancel_order(order_id)
        finally:
            self._persist()

    def reveal_tag(self, tag):
        super().reveal_tag(tag)
        self._persist()

    def reveal_order(self, order_id):
        super().reveal_order(order_id)
        self._persist()

    def hide_order(self, order_id):
        super().hide_order(order_id)
        self._persist()
