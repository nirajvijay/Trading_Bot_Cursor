"""One engine cycle: ingest triggers, drive state machine, commands, heartbeat."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from continuation_features import price_to_ticks, ticks_to_price

from trading_engine_broker import BrokerPort, FakeBroker, _is_stop_order
from trading_engine_handoff import (
    VWAP_RULE_VERSION,
    VwapLookupError,
    fetch_triggered_since,
    fetch_vwap_classification,
)
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
    LIMITED_PER_TRADE_RISK_CAP,
    PER_TRADE_RISK_CAP,
    SKIPPED_STATES,
    UNPROTECTED_STATES,
    EngineState,
    PositionQuote,
    TradeRecord,
    TriggerCandidate,
)

ENTRY_COMPLETE = {"COMPLETE"}
ENTRY_REJECTED = {"REJECTED", "CANCELLED"}
SL_WORKING = {"TRIGGER PENDING"}
SL_FILLED = {"COMPLETE"}
SL_CANCELLED = {"CANCELLED", "REJECTED"}

VWAP_WAIT_SECONDS = 2.0
VWAP_PENDING_RETRY_SECONDS = 0.25
VWAP_SKIP_BY_CLASS = {
    "LIMITED": "vwap_limited",
    "REJECT": "vwap_reject",
    "UNAVAILABLE": "vwap_unavailable",
}


@dataclass
class _PendingVwap:
    candidate: TriggerCandidate
    first_seen_monotonic: float
    vwap_rule_version: str


def _vwap_pending_key(
    candidate: TriggerCandidate,
    vwap_rule_version: str = VWAP_RULE_VERSION,
) -> tuple[str, str, str, str]:
    return (
        candidate.session_date,
        candidate.setup_id,
        candidate.continuation_rule_version,
        vwap_rule_version,
    )


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
    accepting_triggers: Optional[bool] = None,
    require_vwap_accept: bool = True,
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
            "tick_size": t.tick_size,
            "auto_trail_enabled": bool(t.auto_trail_enabled),
            "auto_trail_ticks": t.auto_trail_ticks,
            "stop_revised": (
                t.initial_stop is not None
                and t.current_stop is not None
                and abs(t.initial_stop - t.current_stop) > 1e-9
            ),
        }

    active = [trade_json(t) for t in trades if t.status in ACTIVE_STATES]
    closed = [trade_json(t) for t in trades if t.status == "closed"]
    skipped = [trade_json(t) for t in trades if t.status in SKIPPED_STATES]
    accepting = running if accepting_triggers is None else bool(accepting_triggers)
    return {
        "state": state,
        "session_date": session_date,
        "live_orders_enabled": live_orders_enabled,
        "accepting_triggers": accepting,
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
        "require_vwap_accept": bool(require_vwap_accept),
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
        require_vwap_accept: bool = True,
        monotonic_fn: Callable[[], float] = time.monotonic,
        vwap_rule_version: str = VWAP_RULE_VERSION,
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
        self.require_vwap_accept = bool(require_vwap_accept)
        self._monotonic = monotonic_fn
        self._vwap_rule_version = vwap_rule_version
        self.consume_new_triggers = True
        self.last_error: Optional[str] = None
        self.running = True
        self._ltp_cache: dict[str, Optional[float]] = {}
        self._pos_cache: dict[str, Optional[PositionQuote]] = {}
        self._pending_vwap: dict[tuple[str, str, str, str], _PendingVwap] = {}

    def total_capital(self) -> float:
        return self.store.get_total_capital(self.run_id)

    def _reset_quote_cache(self) -> None:
        self._ltp_cache = {}
        self._pos_cache = {}
        clearer = getattr(self.broker, "clear_quote_cache", None)
        if callable(clearer):
            clearer()

    def _ltp(self, symbol: str) -> Optional[float]:
        if symbol in self._ltp_cache:
            return self._ltp_cache[symbol]
        try:
            mark = self.broker.ltp(symbol)
        except Exception:  # noqa: BLE001
            mark = None
        self._ltp_cache[symbol] = mark
        return mark

    def _position_quote(self, symbol: str) -> Optional[PositionQuote]:
        if symbol in self._pos_cache:
            return self._pos_cache[symbol]
        getter = getattr(self.broker, "position_quote", None)
        if not callable(getter):
            self._pos_cache[symbol] = None
            return None
        try:
            quote = getter(symbol)
        except Exception:  # noqa: BLE001
            quote = None
        self._pos_cache[symbol] = quote
        return quote

    def _last_price(self, symbol: str) -> Optional[float]:
        quote = self._position_quote(symbol) if self.live_orders_enabled else None
        if quote is not None and quote.last_price is not None:
            return float(quote.last_price)
        return self._ltp(symbol)

    def tick(self) -> None:
        self._reset_quote_cache()
        if self.consume_new_triggers:
            try:
                self.ingest_triggers()
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
        try:
            self.drive_open()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
        try:
            self.process_commands()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
        try:
            self.apply_auto_trails()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
        try:
            self.mark_to_market()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
        self.write_status()

    def has_pending_vwap(self) -> bool:
        return bool(self._pending_vwap)

    def ingest_triggers(self) -> None:
        candidates = fetch_triggered_since(self.live_db, created_at_gte=self.started_at)
        for candidate in candidates:
            self._handle_candidate(candidate)

    def poll_pending_vwap(self) -> None:
        for pending in list(self._pending_vwap.values()):
            existing = self.store.find_trade(
                pending.candidate.setup_id, pending.candidate.continuation_rule_version
            )
            if existing is not None:
                key = _vwap_pending_key(pending.candidate, pending.vwap_rule_version)
                self._pending_vwap.pop(key, None)
                continue
            expired = (
                self._monotonic() - pending.first_seen_monotonic
            ) >= VWAP_WAIT_SECONDS
            self._apply_vwap_gate(
                pending.candidate,
                first_seen=pending.first_seen_monotonic,
                deadline_expired=expired,
            )

    def drain_pending_vwap(self) -> None:
        for pending in list(self._pending_vwap.values()):
            existing = self.store.find_trade(
                pending.candidate.setup_id, pending.candidate.continuation_rule_version
            )
            if existing is not None:
                key = _vwap_pending_key(pending.candidate, pending.vwap_rule_version)
                self._pending_vwap.pop(key, None)
                continue
            self._apply_vwap_gate(
                pending.candidate,
                first_seen=pending.first_seen_monotonic,
                deadline_expired=True,
            )

    def _handle_candidate(self, candidate: TriggerCandidate) -> None:
        existing = self.store.find_trade(
            candidate.setup_id, candidate.continuation_rule_version
        )
        if existing is not None:
            return
        if not self.require_vwap_accept:
            self._place_candidate(candidate)
            return
        key = _vwap_pending_key(candidate, self._vwap_rule_version)
        if key in self._pending_vwap:
            return
        self._apply_vwap_gate(candidate, first_seen=self._monotonic())

    def _apply_vwap_gate(
        self,
        candidate: TriggerCandidate,
        *,
        first_seen: float,
        deadline_expired: bool = False,
    ) -> None:
        """When require_vwap_accept is set, placement requires ACCEPT or LIMITED."""
        key = _vwap_pending_key(candidate, self._vwap_rule_version)
        try:
            classification = fetch_vwap_classification(
                self.live_db,
                session_date=candidate.session_date,
                setup_id=candidate.setup_id,
                continuation_rule_version=candidate.continuation_rule_version,
                vwap_rule_version=self._vwap_rule_version,
            )
        except VwapLookupError:
            self._pending_vwap.pop(key, None)
            self._skip_vwap(candidate, "vwap_unavailable")
            return
        if classification is None:
            if deadline_expired or (self._monotonic() - first_seen) >= VWAP_WAIT_SECONDS:
                self._pending_vwap.pop(key, None)
                self._skip_vwap(candidate, "vwap_unavailable")
                return
            self._pending_vwap.setdefault(
                key,
                _PendingVwap(
                    candidate=candidate,
                    first_seen_monotonic=first_seen,
                    vwap_rule_version=self._vwap_rule_version,
                ),
            )
            return
        if classification == "ACCEPT":
            self._pending_vwap.pop(key, None)
            self._place_candidate(candidate, per_trade_risk_cap=PER_TRADE_RISK_CAP)
            return
        if classification == "LIMITED":
            self._pending_vwap.pop(key, None)
            self._place_candidate(candidate, per_trade_risk_cap=LIMITED_PER_TRADE_RISK_CAP)
            return
        reason = VWAP_SKIP_BY_CLASS.get(classification, "vwap_unavailable")
        self._pending_vwap.pop(key, None)
        self._skip_vwap(candidate, reason)

    def _skip_vwap(self, candidate: TriggerCandidate, reason: str) -> None:
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
        self.store.append_event(
            trade.trade_id, "candidate", payload={"setup_id": candidate.setup_id}
        )
        self.store.update_trade(trade.trade_id, status="skipped", skip_reason=reason)
        self.store.append_event(trade.trade_id, "skipped", payload={"reason": reason})

    def _place_candidate(
        self,
        candidate: TriggerCandidate,
        *,
        per_trade_risk_cap: float = PER_TRADE_RISK_CAP,
    ) -> None:
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
            per_trade_risk_cap=per_trade_risk_cap,
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
            o
            for o in self.broker.orders_by_tag(trade.broker_tag)
            if _is_stop_order(o) and str(o.status).upper() not in SL_CANCELLED
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
            tick_size=trade.tick_size,
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
                continue
            if trade.status in {"entry_filled", "stop_pending", "protected_open"}:
                if self._reconcile_external_exit(trade):
                    continue
                refreshed = self.store.get_trade(trade.trade_id)
                trade = refreshed if refreshed is not None else trade
            if trade.status == "entry_filled":
                self._place_stop(trade)
            elif trade.status == "stop_pending":
                if trade.sl_order_id:
                    polled = self.broker.poll_order(trade.sl_order_id)
                    if polled:
                        self._apply_sl_order(trade, polled)
                else:
                    self._place_stop(trade)
            elif trade.status == "protected_open":
                self._drive_protected(trade)

    def _reconcile_external_exit(self, trade: TradeRecord) -> bool:
        if trade.entry_fill is None or trade.qty <= 0:
            return False
        net = self.broker.net_position_qty(trade.symbol)
        if net is None:
            return False
        if abs(net) not in {0, trade.qty} and net != 0:
            self.store.append_event(
                trade.trade_id,
                "qty_mismatch",
                payload={"net": net, "engine_qty": trade.qty},
            )
        if net != 0:
            return False
        if trade.sl_order_id:
            sl = self.broker.poll_order(trade.sl_order_id)
            if sl is not None and str(sl.status).upper() in SL_FILLED:
                self._close_trade(
                    trade,
                    float(sl.average_price or trade.current_stop or 0),
                    "sl_hit",
                )
                return True
            if sl is not None and str(sl.status).upper() not in SL_CANCELLED | SL_FILLED:
                self.broker.cancel_order(trade.sl_order_id)
        exit_px = self._exit_fill_from_broker(trade)
        self._close_trade(trade, exit_px, "external_exit")
        return True

    def _exit_fill_from_broker(self, trade: TradeRecord) -> float:
        side = _stop_side(trade.direction)
        fills = [
            o
            for o in self.broker.orders_for_symbol(trade.symbol)
            if str(o.transaction_type).upper() == side
            and str(o.status).upper() in SL_FILLED
            and o.average_price is not None
        ]
        if fills:
            return float(fills[-1].average_price or 0)
        mark = self._ltp(trade.symbol)
        if mark is not None:
            return float(mark)
        return float(trade.current_stop or trade.entry_fill or trade.entry_estimate)

    def _drive_protected(self, trade: TradeRecord) -> None:
        if not trade.sl_order_id:
            self._place_stop(trade)
            return
        polled = self.broker.poll_order(trade.sl_order_id)
        if polled is None:
            return
        status = str(polled.status).upper()
        if status in SL_FILLED:
            self._close_trade(
                trade,
                float(polled.average_price or trade.current_stop or 0),
                "sl_hit",
            )
            return
        if status in SL_CANCELLED:
            self.store.update_trade(trade.trade_id, status="stop_pending")
            self.store.append_event(
                trade.trade_id, "stop_pending", payload={"reason": "sl_cancelled"}
            )
            return
        if status in SL_WORKING and polled.trigger_price is not None:
            try:
                aligned = _align_stop(float(polled.trigger_price), trade.tick_size)
            except ValueError:
                return
            if trade.current_stop is None:
                self.store.update_trade(trade.trade_id, current_stop=aligned)
                return
            if abs(aligned - trade.current_stop) <= 1e-9:
                return
            tighter = trail_is_tighten_only(
                direction=trade.direction,
                current_stop=trade.current_stop,
                new_stop=aligned,
            )
            if not tighter:
                return
            old = trade.current_stop
            self.store.update_trade(trade.trade_id, current_stop=aligned)
            self.store.append_event(
                trade.trade_id,
                "stop_adopted",
                old_stop=old,
                new_stop=aligned,
                payload={"source": "broker"},
            )

    def process_commands(self) -> None:
        for command in self.store.pending_commands():
            if command.kind == "stop_engine":
                if command.created_at < self.started_at:
                    self.store.mark_command_processed(command.command_id)
                    continue
                self.consume_new_triggers = False
                self.store.set_consume_triggers(self.run_id, False)
                self.store.mark_command_processed(command.command_id)
                self.drain_pending_vwap()
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
                continue
            if command.kind == "set_auto_trail" and command.trade_id:
                payload = json.loads(command.payload_json or "{}")
                try:
                    self.set_auto_trail(
                        command.trade_id, enabled=bool(payload.get("enabled"))
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
        mark = last_price if last_price is not None else self._last_price(trade.symbol)
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
        try:
            self.broker.modify_slm(
                trade.sl_order_id,
                aligned,
                tick_size=trade.tick_size,
                transaction_type=_stop_side(trade.direction),
            )
        except Exception as exc:  # noqa: BLE001
            self.store.append_event(
                trade.trade_id,
                "error",
                actor=actor,
                payload={"reason": f"modify_failed:{exc}"},
            )
            raise ValueError(f"modify_failed:{exc}") from exc
        fields: dict[str, Any] = {"current_stop": aligned}
        if actor == "user" and trade.auto_trail_enabled:
            mark_for_trail = mark if mark is not None else self._last_price(trade.symbol)
            if mark_for_trail is not None:
                fields["auto_trail_ticks"] = max(
                    1,
                    abs(
                        price_to_ticks(float(mark_for_trail), trade.tick_size)
                        - price_to_ticks(aligned, trade.tick_size)
                    ),
                )
                fields["auto_trail_extreme"] = float(mark_for_trail)
        updated = self.store.update_trade(trade.trade_id, **fields)
        self.store.append_event(
            trade.trade_id,
            "sl_modified",
            actor=actor,
            old_stop=old,
            new_stop=aligned,
        )
        return updated

    def set_auto_trail(self, trade_id: str, *, enabled: bool) -> TradeRecord:
        trade = self.store.get_trade(trade_id)
        if trade is None:
            raise ValueError("trade_not_found")
        if not enabled:
            updated = self.store.update_trade(trade_id, auto_trail_enabled=0)
            self.store.append_event(trade_id, "auto_trail_off", actor="user")
            return updated
        if trade.status != "protected_open":
            raise ValueError("trail_only_protected_open")
        if trade.current_stop is None:
            raise ValueError("missing_stop")
        mark = self._last_price(trade.symbol)
        if mark is None:
            raise ValueError("ltp_unavailable")
        ticks = max(
            1,
            abs(
                price_to_ticks(float(mark), trade.tick_size)
                - price_to_ticks(trade.current_stop, trade.tick_size)
            ),
        )
        updated = self.store.update_trade(
            trade_id,
            auto_trail_enabled=1,
            auto_trail_ticks=ticks,
            auto_trail_extreme=float(mark),
        )
        self.store.append_event(
            trade_id,
            "auto_trail_on",
            actor="user",
            payload={"ticks": ticks},
        )
        return updated

    def apply_auto_trails(self) -> None:
        for trade in self.store.list_trades(self.session_date):
            if not trade.auto_trail_enabled or trade.status != "protected_open":
                continue
            if trade.current_stop is None or not trade.auto_trail_ticks:
                continue
            mark = self._last_price(trade.symbol)
            if mark is None:
                continue
            tick_size = trade.tick_size
            gap = int(trade.auto_trail_ticks)
            if trade.direction == "UP":
                proposed = ticks_to_price(price_to_ticks(mark, tick_size) - gap, tick_size)
            else:
                proposed = ticks_to_price(price_to_ticks(mark, tick_size) + gap, tick_size)
            aligned = _align_stop(proposed, tick_size)
            if trail_crosses_last_price(
                direction=trade.direction, new_stop=aligned, last_price=mark
            ):
                continue
            if not trail_is_tighten_only(
                direction=trade.direction,
                current_stop=trade.current_stop,
                new_stop=aligned,
            ):
                continue
            if abs(aligned - trade.current_stop) < 1e-9:
                continue
            try:
                self.apply_trail(
                    trade.trade_id,
                    aligned,
                    last_price=mark,
                    actor="engine",
                )
            except ValueError:
                continue
            except Exception as exc:  # noqa: BLE001
                self.store.append_event(
                    trade.trade_id,
                    "error",
                    payload={"reason": f"auto_trail:{exc}"},
                )

    def mark_to_market(self) -> None:
        for trade in self.store.list_trades(self.session_date):
            if trade.status not in ACTIVE_STATES:
                continue
            if self.live_orders_enabled:
                self._mark_live_trade(trade)
                continue
            mark = self._ltp(trade.symbol)
            if mark is None:
                continue
            pnl = open_pnl(
                direction=trade.direction,
                qty=trade.qty,
                entry_fill=trade.entry_fill,
                mark=mark,
            )
            if abs(pnl - trade.open_pnl) > 1e-9:
                self.store.update_trade(trade.trade_id, open_pnl=pnl)

    def _mark_live_trade(self, trade: TradeRecord) -> None:
        quote = self._position_quote(trade.symbol)
        if quote is None:
            return
        pnl = quote.unrealised
        if pnl is None:
            pnl = quote.pnl
        if pnl is None and quote.average_price is not None and quote.last_price is not None:
            pnl = float(quote.quantity) * (float(quote.last_price) - float(quote.average_price))
        if pnl is None:
            return
        fields: dict[str, Any] = {"open_pnl": float(pnl)}
        if quote.average_price is not None and (
            trade.entry_fill is None or abs(float(quote.average_price) - float(trade.entry_fill)) > 1e-9
        ):
            fields["entry_fill"] = float(quote.average_price)
        if quote.quantity != 0 and abs(int(quote.quantity)) != int(trade.qty):
            self.store.append_event(
                trade.trade_id,
                "qty_mismatch",
                payload={"net": quote.quantity, "engine_qty": trade.qty},
            )
        current = self.store.get_trade(trade.trade_id)
        if current is None:
            return
        if abs(float(pnl) - current.open_pnl) > 1e-9 or "entry_fill" in fields:
            self.store.update_trade(trade.trade_id, **fields)

    def _close_trade(self, trade: TradeRecord, exit_fill: float, reason: str) -> None:
        if trade.status == "closed":
            return
        entry = trade.entry_fill if trade.entry_fill is not None else trade.entry_estimate
        pnl = realised_pnl(
            direction=trade.direction, qty=trade.qty, entry_fill=entry, exit_fill=exit_fill
        )
        if reason in {"sl_hit", "external_exit"} and self.live_orders_enabled:
            quote = self._position_quote(trade.symbol)
            if quote is not None and quote.quantity == 0 and quote.realised is not None:
                others = [
                    t
                    for t in self.store.list_trades(self.session_date)
                    if t.symbol == trade.symbol and t.status == "closed"
                ]
                if not others:
                    pnl = float(quote.realised)
                    if quote.average_price is not None:
                        entry = float(quote.average_price)
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
            trade.trade_id,
            "sl_filled" if reason == "sl_hit" else "closed",
            payload={"exit": exit_fill, "pnl": pnl, "reason": reason},
        )

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
            accepting_triggers=bool(self.running and self.consume_new_triggers),
            require_vwap_accept=self.require_vwap_accept,
        )
        snap["updated_at"] = _now()
        snap["running"] = self.running
        snap["consume_new_triggers"] = self.consume_new_triggers
        write_heartbeat(self.status_file, snap)
