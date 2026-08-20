"""One engine cycle: ingest triggers, drive state machine, commands, heartbeat."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from continuation_features import price_to_ticks, ticks_to_price

from trading_engine_broker import BrokerPort, FakeBroker
from trading_engine_handoff import fetch_triggered_since
from trading_engine_risk import (
    blocks_new_entries,
    capital_snapshot,
    live_pnl,
    margin_blocked,
    open_pnl,
    realised_pnl,
    remaining_downside_risk,
    risk_snapshot,
    size_new_trade,
    trail_crosses_last_price,
    trail_is_tighten_only,
)
from trading_engine_store import TradingEngineStore
from trading_engine_types import (
    ACTIVE_STATES,
    DEMO_LEVERAGE_FACTOR,
    SKIPPED_STATES,
    UNPROTECTED_STATES,
    EngineState,
    TradeRecord,
    TriggerCandidate,
)

ENTRY_COMPLETE = {"COMPLETE"}
ENTRY_REJECTED = {"REJECTED", "CANCELLED"}
SL_WORKING = {"TRIGGER PENDING"}
SL_FILLED = {"COMPLETE"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _entry_side(direction: str) -> str:
    return "BUY" if direction == "UP" else "SELL"


def _stop_side(direction: str) -> str:
    return "SELL" if direction == "UP" else "BUY"


def _align_stop(price: float, tick_size: float) -> float:
    ticks = price_to_ticks(price, tick_size)
    return ticks_to_price(ticks, tick_size)


def write_heartbeat(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def engine_ui_state(trades: list[TradeRecord], *, running: bool, last_error: Optional[str]) -> EngineState:
    if last_error:
        return "error"
    if any(t.status in UNPROTECTED_STATES for t in trades):
        return "critical"
    if running:
        return "running"
    return "stopped"


def snapshot_dict(
    store: TradingEngineStore,
    *,
    session_date: str,
    total_capital: float,
    leverage_factor: float,
    live_orders_enabled: bool,
    running: bool,
    last_error: Optional[str] = None,
) -> dict[str, Any]:
    trades = store.list_trades(session_date)
    risk = risk_snapshot(trades)
    capital = capital_snapshot(
        trades, total_capital=total_capital, leverage_factor=leverage_factor
    )
    state = engine_ui_state(trades, running=running, last_error=last_error)
    pnl = live_pnl(trades)

    def remaining(t: TradeRecord) -> float:
        entry = t.entry_fill if t.entry_fill is not None else t.entry_estimate
        stop = t.current_stop if t.current_stop is not None else t.initial_stop
        return remaining_downside_risk(
            direction=t.direction, qty=t.qty, entry=entry, current_stop=stop
        )

    def trade_json(t: TradeRecord) -> dict[str, Any]:
        return {
            "trade_id": t.trade_id,
            "setup_id": t.setup_id,
            "symbol": t.symbol,
            "direction": t.direction,
            "qty": t.qty,
            "entry_estimate": t.entry_estimate,
            "entry_fill": t.entry_fill,
            "initial_stop": t.initial_stop,
            "current_stop": t.current_stop,
            "exit_fill": t.exit_fill,
            "margin_blocked": t.margin_blocked,
            "status": t.status,
            "skip_reason": t.skip_reason,
            "reject_reason": t.reject_reason,
            "close_reason": t.close_reason,
            "trigger_time": t.trigger_time,
            "entry_time": t.entry_time,
            "close_time": t.close_time,
            "realised_pnl": t.realised_pnl,
            "open_pnl": t.open_pnl,
            "remaining_downside_risk": remaining(t),
            "stop_revised": (
                t.initial_stop is not None
                and t.current_stop is not None
                and abs(t.initial_stop - t.current_stop) > 1e-9
            ),
        }

    active = [trade_json(t) for t in trades if t.status in ACTIVE_STATES]
    closed = [trade_json(t) for t in trades if t.status == "closed"]
    skipped = [trade_json(t) for t in trades if t.status in SKIPPED_STATES]
    return {
        "state": state,
        "session_date": session_date,
        "live_orders_enabled": live_orders_enabled,
        "unprotected_count": risk.unprotected_count,
        "limits_protected": risk.limits_protected,
        "closed_loss_today": risk.closed_loss_today,
        "committed_risk": risk.committed_risk,
        "remaining_daily": risk.remaining_daily,
        "live_pnl": pnl,
        "total_capital": capital.total_capital,
        "leverage_factor": capital.leverage_factor,
        "margin_used": capital.margin_used,
        "remaining_capital": capital.remaining_capital,
        "buying_power": capital.buying_power,
        "last_error": last_error,
        "active": active,
        "closed": closed,
        "skipped": skipped,
    }


class TradingEngineCycle:
    def __init__(
        self,
        store: TradingEngineStore,
        broker: BrokerPort,
        *,
        live_db: Path,
        session_date: str,
        started_at: str,
        run_id: str,
        live_orders_enabled: bool,
        leverage_factor: float = DEMO_LEVERAGE_FACTOR,
        status_file: Optional[Path] = None,
    ) -> None:
        self.store = store
        self.broker = broker
        self.live_db = live_db
        self.session_date = session_date
        self.started_at = started_at
        self.run_id = run_id
        self.live_orders_enabled = live_orders_enabled
        self.leverage_factor = leverage_factor
        self.status_file = status_file
        self.consume_new_triggers = True
        self.last_error: Optional[str] = None
        self.running = True

    def total_capital(self) -> float:
        return self.store.get_total_capital(self.run_id)

    def tick(self) -> None:
        if self.consume_new_triggers:
            self.ingest_triggers()
        self.drive_open()
        self.process_commands()
        self.mark_to_market()
        self.write_status()

    def ingest_triggers(self) -> None:
        candidates = fetch_triggered_since(self.live_db, created_at_gte=self.started_at)
        for candidate in candidates:
            self._handle_candidate(candidate)

    def _handle_candidate(self, candidate: TriggerCandidate) -> None:
        existing = self.store.find_trade(
            candidate.setup_id, candidate.continuation_rule_version
        )
        if existing is not None:
            return
        trade = self.store.insert_candidate(
            setup_id=candidate.setup_id,
            continuation_rule_version=candidate.continuation_rule_version,
            session_date=candidate.session_date,
            symbol=candidate.tradingsymbol,
            instrument_token=candidate.instrument_token,
            direction=candidate.direction,
            entry_estimate=candidate.trigger_price,
            tick_size=candidate.tick_size,
            trigger_time=candidate.trigger_exchange_ts,
        )
        if trade is None:
            return
        self.store.append_event(trade.trade_id, "candidate", payload={"setup_id": candidate.setup_id})
        trades = self.store.list_trades(self.session_date)
        # Exclude this candidate (qty 0) from consuming risk until accepted.
        peers = [t for t in trades if t.trade_id != trade.trade_id]
        decision = size_new_trade(
            candidate,
            peers,
            total_capital=self.total_capital(),
            leverage_factor=self.leverage_factor,
        )
        self.store.append_event(
            trade.trade_id,
            "sized",
            payload={"allow": decision.allow, "qty": decision.qty, "reason": decision.reason},
        )
        if not decision.allow:
            status = "skipped" if decision.kind == "skipped" else "rejected"
            reason_field = "skip_reason" if status == "skipped" else "reject_reason"
            self.store.update_trade(
                trade.trade_id,
                status=status,
                initial_stop=decision.initial_stop,
                current_stop=decision.initial_stop,
                **{reason_field: decision.reason},
            )
            self.store.append_event(trade.trade_id, status, payload={"reason": decision.reason})
            return

        if isinstance(self.broker, FakeBroker) and candidate.last_price:
            self.broker.last_prices.setdefault(candidate.tradingsymbol, candidate.last_price)
        elif isinstance(self.broker, FakeBroker):
            self.broker.last_prices.setdefault(candidate.tradingsymbol, candidate.trigger_price)

        trade = self.store.update_trade(
            trade.trade_id,
            qty=decision.qty,
            initial_stop=decision.initial_stop,
            current_stop=decision.initial_stop,
            notional=decision.notional,
            margin_blocked=decision.margin_blocked,
            status="entry_submitting",
        )
        self.store.append_event(trade.trade_id, "entry_submitting")
        self._submit_entry(trade)

    def _submit_entry(self, trade: TradeRecord) -> None:
        existing = [
            o for o in self.broker.orders_by_tag(trade.broker_tag) if o.order_type == "MARKET"
        ]
        if existing:
            self.store.append_event(
                trade.trade_id, "reconcile", payload={"order_id": existing[0].order_id}
            )
            self._apply_entry_order(trade, existing[0])
            return

        if self.live_orders_enabled:
            quote = self.broker.order_margins(
                tradingsymbol=trade.symbol,
                transaction_type=_entry_side(trade.direction),
                quantity=trade.qty,
            )
            cap = capital_snapshot(
                [t for t in self.store.list_trades(self.session_date) if t.trade_id != trade.trade_id],
                total_capital=self.total_capital(),
                leverage_factor=self.leverage_factor,
            )
            if not quote.ok or quote.required > cap.remaining_capital:
                reason = quote.reason or "insufficient_margin"
                if not quote.ok and "mis" in reason.lower():
                    reason = "mis_unavailable"
                self.store.update_trade(
                    trade.trade_id, status="rejected", reject_reason=reason, qty=0,
                    notional=0, margin_blocked=0,
                )
                self.store.append_event(trade.trade_id, "rejected", payload={"reason": reason})
                return

        order = self.broker.place_market_mis(
            tradingsymbol=trade.symbol,
            transaction_type=_entry_side(trade.direction),
            quantity=trade.qty,
            tag=trade.broker_tag,
        )
        self.store.append_event(
            trade.trade_id, "entry_placed", payload={"order_id": order.order_id}
        )
        self._apply_entry_order(trade, order)

    def _apply_entry_order(self, trade: TradeRecord, order: Any) -> None:
        status = str(order.status).upper()
        if status in ENTRY_COMPLETE:
            fill = float(order.average_price or trade.entry_estimate)
            blocked = margin_blocked(
                qty=trade.qty, entry=fill, leverage_factor=self.leverage_factor
            )
            self.store.update_trade(
                trade.trade_id,
                status="entry_filled",
                entry_fill=fill,
                entry_order_id=order.order_id,
                entry_time=_now(),
                notional=trade.qty * fill,
                margin_blocked=blocked,
            )
            self.store.append_event(trade.trade_id, "entry_filled", payload={"fill": fill})
            refreshed = self.store.get_trade(trade.trade_id)
            if refreshed:
                self._place_stop(refreshed)
            return
        if status in ENTRY_REJECTED:
            self.store.update_trade(
                trade.trade_id,
                status="rejected",
                reject_reason="entry_rejected",
                entry_order_id=order.order_id,
                qty=0,
                notional=0,
                margin_blocked=0,
            )
            self.store.append_event(trade.trade_id, "rejected", payload={"reason": "entry_rejected"})
            return
        self.store.update_trade(trade.trade_id, entry_order_id=order.order_id)

    def _place_stop(self, trade: TradeRecord) -> None:
        if trade.current_stop is None:
            self.store.update_trade(
                trade.trade_id, status="rejected", reject_reason="missing_stop"
            )
            return
        existing = [
            o for o in self.broker.orders_by_tag(trade.broker_tag) if o.order_type == "SL-M"
        ]
        if existing:
            self._apply_sl_order(trade, existing[0])
            return
        order = self.broker.place_slm(
            tradingsymbol=trade.symbol,
            transaction_type=_stop_side(trade.direction),
            quantity=trade.qty,
            trigger_price=trade.current_stop,
            tag=trade.broker_tag,
        )
        self.store.append_event(
            trade.trade_id, "sl_placed", payload={"order_id": order.order_id}
        )
        self._apply_sl_order(trade, order)

    def _apply_sl_order(self, trade: TradeRecord, order: Any) -> None:
        status = str(order.status).upper()
        if status in SL_FILLED:
            self._close_trade(trade, float(order.average_price or trade.current_stop or 0), "sl_hit")
            return
        if status in SL_WORKING:
            self.store.update_trade(
                trade.trade_id, status="protected_open", sl_order_id=order.order_id
            )
            self.store.append_event(trade.trade_id, "protected_open")
            return
        self.store.update_trade(
            trade.trade_id, status="stop_pending", sl_order_id=order.order_id
        )
        self.store.append_event(trade.trade_id, "stop_pending")

    def drive_open(self) -> None:
        for trade in self.store.list_trades(self.session_date):
            if trade.status == "entry_submitting":
                if trade.entry_order_id:
                    polled = self.broker.poll_order(trade.entry_order_id)
                    if polled:
                        self._apply_entry_order(trade, polled)
                else:
                    self._submit_entry(trade)
            elif trade.status == "entry_filled":
                self._place_stop(trade)
            elif trade.status == "stop_pending":
                if trade.sl_order_id:
                    polled = self.broker.poll_order(trade.sl_order_id)
                    if polled:
                        self._apply_sl_order(trade, polled)
                else:
                    self._place_stop(trade)
            elif trade.status == "protected_open":
                if trade.sl_order_id:
                    polled = self.broker.poll_order(trade.sl_order_id)
                    if polled and str(polled.status).upper() in SL_FILLED:
                        self._close_trade(
                            trade,
                            float(polled.average_price or trade.current_stop or 0),
                            "sl_hit",
                        )

    def _close_trade(self, trade: TradeRecord, exit_fill: float, reason: str) -> None:
        if trade.status == "closed":
            return
        entry = trade.entry_fill if trade.entry_fill is not None else trade.entry_estimate
        pnl = realised_pnl(
            direction=trade.direction, qty=trade.qty, entry_fill=entry, exit_fill=exit_fill
        )
        loss = abs(pnl) if pnl < 0 else 0.0
        self.store.update_trade(
            trade.trade_id,
            status="closed",
            exit_fill=exit_fill,
            close_reason=reason,
            close_time=_now(),
            realised_pnl=pnl,
            open_pnl=0.0,
            closed_loss_contribution=loss,
            margin_blocked=0.0,
        )
        self.store.append_event(
            trade.trade_id, "sl_filled", payload={"exit": exit_fill, "pnl": pnl}
        )

    def process_commands(self) -> None:
        for command in self.store.pending_commands():
            if command.kind == "stop_engine":
                self.consume_new_triggers = False
                self.store.set_consume_triggers(self.run_id, False)
                self.store.mark_command_processed(command.command_id)
                continue
            if command.kind == "trail_stop" and command.trade_id:
                payload = json.loads(command.payload_json or "{}")
                try:
                    self.apply_trail(
                        command.trade_id,
                        float(payload["new_stop"]),
                        last_price=payload.get("last_price"),
                        actor="user",
                    )
                except ValueError as exc:
                    self.store.append_event(
                        command.trade_id,
                        "error",
                        actor="user",
                        payload={"reason": str(exc)},
                    )
                self.store.mark_command_processed(command.command_id)

    def apply_trail(
        self,
        trade_id: str,
        new_stop: float,
        *,
        last_price: Optional[float] = None,
        actor: str = "user",
    ) -> TradeRecord:
        trade = self.store.get_trade(trade_id)
        if trade is None:
            raise ValueError("trade_not_found")
        if trade.status != "protected_open":
            raise ValueError("trail_only_protected_open")
        if trade.current_stop is None or trade.sl_order_id is None:
            raise ValueError("missing_stop")
        aligned = _align_stop(new_stop, trade.tick_size)
        if not trail_is_tighten_only(
            direction=trade.direction,
            current_stop=trade.current_stop,
            new_stop=aligned,
        ):
            raise ValueError("widen_blocked")
        mark = last_price if last_price is not None else self.broker.ltp(trade.symbol)
        if trail_crosses_last_price(
            direction=trade.direction, new_stop=aligned, last_price=mark
        ):
            raise ValueError("would_trigger_immediately")
        old = trade.current_stop
        self.store.append_event(
            trade.trade_id,
            "sl_modify_requested",
            actor=actor,
            old_stop=old,
            new_stop=aligned,
        )
        self.broker.modify_slm(trade.sl_order_id, aligned)
        updated = self.store.update_trade(trade.trade_id, current_stop=aligned)
        self.store.append_event(
            trade.trade_id,
            "sl_modified",
            actor=actor,
            old_stop=old,
            new_stop=aligned,
        )
        return updated

    def mark_to_market(self) -> None:
        for trade in self.store.list_trades(self.session_date):
            if trade.status not in ACTIVE_STATES:
                continue
            mark = self.broker.ltp(trade.symbol)
            pnl = open_pnl(
                direction=trade.direction,
                qty=trade.qty,
                entry_fill=trade.entry_fill,
                mark=mark,
            )
            if abs(pnl - trade.open_pnl) > 1e-9:
                self.store.update_trade(trade.trade_id, open_pnl=pnl)

    def write_status(self) -> None:
        if self.status_file is None:
            return
        snap = snapshot_dict(
            self.store,
            session_date=self.session_date,
            total_capital=self.total_capital(),
            leverage_factor=self.leverage_factor,
            live_orders_enabled=self.live_orders_enabled,
            running=self.running,
            last_error=self.last_error,
        )
        snap["updated_at"] = _now()
        write_heartbeat(self.status_file, snap)
