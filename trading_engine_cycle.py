"""One engine cycle: ingest triggers, drive state machine, commands, heartbeat."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from continuation_features import price_to_ticks, ticks_to_price

from api import config
from api.admin_config.store import AdminConfigStore
from nse_trading_calendar import (
    SpecialSessionSchedule,
    entry_calendar_block_reason,
    is_special_session_day,
    parse_hhmm,
    validate_session_gate_hhmm_pair,
)
from trading_engine_broker import BrokerPort, FakeBroker, _is_stop_order, parse_timestamp_text
from trading_engine_handoff import (
    VWAP_RULE_VERSION,
    VwapLookupError,
    fetch_triggered_since,
    fetch_vwap_classification,
)
from trading_engine_risk import (
    blocks_new_entries,
    capital_snapshot,
    fold_exit_costs_into_loss,
    freeze_r_value,
    is_unprotected,
    live_pnl,
    margin_blocked,
    open_pnl,
    realised_pnl,
    realised_pnl_from_values,
    remaining_downside_risk,
    risk_snapshot,
    size_new_trade,
    staged_r_desired_stop,
    trade_cost_profile,
    trail_crosses_last_price,
    trail_improvement_ticks,
    trail_is_tighten_only,
    update_trail_extreme,
)
from trading_engine_store import TradingEngineStore
from trading_engine_types import (
    ACTIVE_STATES,
    DAILY_LOSS_CAP,
    DEFAULT_ENTRY_CUTOFF_IST_HHMM,
    DEFAULT_ENTRY_REMAINDER_CANCEL_SECONDS,
    DEFAULT_ESTIMATED_SLIPPAGE_BPS,
    DEFAULT_PROTECTION_CONFIRM_DEADLINE_SECONDS,
    DEFAULT_ROUND_TRIP_CHARGE_BPS,
    DEFAULT_SQUARE_OFF_IST_HHMM,
    DEMO_LEVERAGE_FACTOR,
    LIMITED_PER_TRADE_RISK_CAP,
    MAX_CONCURRENT_POSITIONS,
    MAX_FILLED_SETUPS_PER_DAY,
    PER_TRADE_RISK_CAP,
    SKIPPED_STATES,
    STOP_ORDER_TYPES,
    TRAIL_MIN_IMPROVEMENT_TICKS,
    TRAIL_MIN_MODIFY_INTERVAL_SECONDS,
    UNPROTECTED_STATES,
    EngineState,
    PositionQuote,
    TradeRecord,
    TriggerCandidate,
    broker_order_filled_qty,
    broker_order_pending_qty,
)

ENTRY_COMPLETE = {"COMPLETE"}
ENTRY_REJECTED = {"REJECTED", "CANCELLED"}
ENTRY_WORKING = {"OPEN", "TRIGGER PENDING", "AMO REQ RECEIVED"}
SL_WORKING = {"TRIGGER PENDING"}  # waiting for trigger (not yet executed)
SL_TRIGGERED_WORKING = {"OPEN"}  # triggered; may have partial fills + OPEN remainder
SL_FILLED = {"COMPLETE"}
SL_CANCELLED = {"CANCELLED", "REJECTED"}
# Exit-side order types that may close exposure (stops, market flatten, limit flatten).
EXIT_ORDER_TYPES = frozenset(set(STOP_ORDER_TYPES) | {"MARKET", "LIMIT"})

VWAP_WAIT_SECONDS = 2.0
VWAP_PENDING_RETRY_SECONDS = 0.25
VWAP_SKIP_BY_CLASS = {
    "LIMITED": "vwap_limited",
    "REJECT": "vwap_reject",
    "UNAVAILABLE": "vwap_unavailable",
}
# NSE cash session calendar day for attribution checks.
_SESSION_TZ = ZoneInfo("Asia/Kolkata")


def _parse_aware_instant(raw: object) -> Optional[datetime]:
    """Parse an application/broker timestamp that already carries an explicit timezone.

    Timezone-less values are rejected (return None). Broker adapters must normalize
    naive exchange stamps with their verified convention before attribution sees them.
    """
    parsed = parse_timestamp_text(raw)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _resolve_admin_config_db(
    admin_config_db: Optional[Path],
    trading_store_db: Path,
) -> Path:
    if admin_config_db is not None:
        return Path(admin_config_db)
    preferred = config.admin_config_db_path()
    try:
        preferred.parent.mkdir(parents=True, exist_ok=True)
        return preferred
    except OSError:
        return Path(trading_store_db).parent / "admin_config.db"


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
    if any(is_unprotected(t) for t in trades):
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
    daily_loss_cap: float = DAILY_LOSS_CAP,
    per_trade_cap: float = PER_TRADE_RISK_CAP,
) -> dict[str, Any]:
    trades = store.list_trades(session_date)
    risk = risk_snapshot(
        trades,
        daily_loss_cap=daily_loss_cap,
        per_trade_cap=per_trade_cap,
    )
    capital = capital_snapshot(
        trades, total_capital=total_capital, leverage_factor=leverage_factor
    )
    state = engine_ui_state(trades, running=running, last_error=last_error)
    pnl = live_pnl(trades)

    def trade_json(t: TradeRecord) -> dict[str, Any]:
        exposure_qty = int(t.filled_qty or 0) if int(t.filled_qty or 0) > 0 else int(t.qty or 0)
        return {
            "trade_id": t.trade_id,
            "setup_id": t.setup_id,
            "symbol": t.symbol,
            "direction": t.direction,
            "qty": t.qty,
            "intended_qty": int(t.intended_qty or t.qty or 0),
            "filled_qty": int(t.filled_qty or 0),
            "exited_qty": int(getattr(t, "exited_qty", 0) or 0),
            "remaining_entry_qty": int(t.remaining_entry_qty or 0),
            "remaining_position_qty": int(t.remaining_position_qty or 0),
            "protected_qty": int(t.protected_qty or 0),
            "run_id": t.run_id,
            "entry_live_orders_enabled": t.entry_live_orders_enabled,
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
            "remaining_downside_risk": remaining_downside_risk(
                direction=t.direction,
                qty=exposure_qty,
                entry=t.entry_fill if t.entry_fill is not None else t.entry_estimate,
                current_stop=t.current_stop if t.current_stop is not None else t.initial_stop,
            ),
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
        admin_config_db: Optional[Path] = None,
        clock_fn: Optional[Callable[[], datetime]] = None,
        special_session_schedule: Optional[SpecialSessionSchedule] = None,
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
        self._clock_fn = clock_fn
        self._special_session_schedule = special_session_schedule
        self._vwap_rule_version = vwap_rule_version
        db_path = _resolve_admin_config_db(admin_config_db, store.db_path)
        # Saved→Effective promotion is never automatic on cycle construction.
        # Promote only via AdminConfigStore.arm_effective_config() at an explicit
        # validated arm boundary.
        self._admin_store = AdminConfigStore(db_path, read_only=True)
        self.consume_new_triggers = True
        self.last_error: Optional[str] = None
        self.running = True
        self._ltp_cache: dict[str, Optional[float]] = {}
        self._pos_cache: dict[str, Optional[PositionQuote]] = {}
        self._pending_vwap: dict[tuple[str, str, str, str], _PendingVwap] = {}

    def total_capital(self) -> float:
        return self.store.get_total_capital(self.run_id)

    def _active_admin_snapshot(self):
        return self._admin_store.capture_snapshot()

    def _sync_pause_from_canonical(self) -> None:
        paused = self._admin_store.read_entries_paused()
        consume = not paused
        if self.consume_new_triggers != consume:
            self.consume_new_triggers = consume
            self.store.set_consume_triggers(self.run_id, consume)

    def _entries_paused(self) -> bool:
        return self._admin_store.read_entries_paused()

    def _cancel_pending_vwap_on_pause(self) -> None:
        for pending in list(self._pending_vwap.values()):
            key = _vwap_pending_key(pending.candidate, pending.vwap_rule_version)
            self._skip_vwap(pending.candidate, "entries_paused")
            self._pending_vwap.pop(key, None)

    def close(self) -> None:
        self._admin_store.close()

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
        self._sync_pause_from_canonical()
        # Session/cutoff gates before any new-entry ingest or drive.
        try:
            self.enforce_entry_session_calendar()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
        try:
            self.enforce_entry_cutoff()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
        if not self._entries_paused():
            try:
                self.ingest_triggers()
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
        try:
            self.drive_open()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
        try:
            self.enforce_entry_remainder_cancels()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
        try:
            self.enforce_protection_deadlines()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
        try:
            self.enforce_square_off()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
        try:
            self.resume_pending_market_exits()
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
        if self._entries_paused():
            return
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
        if self._entries_paused():
            return
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
            admin = self._admin_store.load_effective_payload()
            self._place_candidate(
                candidate,
                per_trade_risk_cap=float(admin["per_trade_risk_cap_inr"]),
            )
            return
        if classification == "LIMITED":
            self._pending_vwap.pop(key, None)
            admin = self._admin_store.load_effective_payload()
            self._place_candidate(
                candidate,
                per_trade_risk_cap=float(admin["limited_per_trade_risk_cap_inr"]),
            )
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
        if self._new_entry_block_reason() is not None:
            return
        snapshot = self._admin_store.capture_snapshot(
            risk_cap_used_inr=float(per_trade_risk_cap)
        )
        if self._new_entry_block_reason() is not None:
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
        admin_payload = self._admin_store.load_effective_payload()
        # Effective allocated capital under frozen apply policy (post-arm).
        allocated = float(
            admin_payload.get("allocated_capital_inr", self.total_capital())
        )
        available_margin: Optional[float] = None
        if self.live_orders_enabled:
            try:
                available_margin = self.broker.available_margins()
            except Exception:  # noqa: BLE001
                available_margin = None
        notional_cap = (
            allocated
            if bool(int(admin_payload.get("aggregate_notional_cap_equals_allocated_capital", 1)))
            else None
        )
        decision = size_new_trade(
            candidate,
            peers,
            total_capital=allocated,
            leverage_factor=self.leverage_factor,
            per_trade_risk_cap=per_trade_risk_cap,
            daily_loss_cap=snapshot.daily_loss_cap_inr,
            max_concurrent_positions=int(
                admin_payload.get("max_concurrent_positions", MAX_CONCURRENT_POSITIONS)
            ),
            max_filled_setups_per_day=int(
                admin_payload.get("max_filled_setups_per_day", MAX_FILLED_SETUPS_PER_DAY)
            ),
            one_per_symbol=bool(
                int(admin_payload.get("one_position_or_unresolved_entry_per_symbol", 1))
            ),
            aggregate_notional_cap=notional_cap,
            charge_bps=float(
                admin_payload.get("round_trip_charge_bps", DEFAULT_ROUND_TRIP_CHARGE_BPS)
            ),
            slippage_bps=float(
                admin_payload.get("estimated_slippage_bps", DEFAULT_ESTIMATED_SLIPPAGE_BPS)
            ),
            # PAPER may use demo leverage; LIVE sizes against verified available margin only.
            use_demo_leverage=not bool(self.live_orders_enabled),
            available_broker_margin=available_margin,
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

        stamp_charge = float(
            admin_payload.get("round_trip_charge_bps", DEFAULT_ROUND_TRIP_CHARGE_BPS)
        )
        stamp_slip = float(
            admin_payload.get("estimated_slippage_bps", DEFAULT_ESTIMATED_SLIPPAGE_BPS)
        )
        trade = self.store.update_trade(
            trade.trade_id,
            qty=decision.qty,
            intended_qty=decision.qty,
            filled_qty=0,
            exited_qty=0,
            remaining_entry_qty=decision.qty,
            remaining_position_qty=0,
            protected_qty=0,
            qty_model_version=1,
            initial_stop=decision.initial_stop,
            current_stop=decision.initial_stop,
            notional=decision.notional,
            margin_blocked=decision.margin_blocked,
            status="entry_submitting",
            run_id=self.run_id,
            entry_live_orders_enabled=1 if self.live_orders_enabled else 0,
            charge_bps=stamp_charge,
            slippage_bps=stamp_slip,
            **snapshot.provenance_fields(),
        )
        self.store.append_event(trade.trade_id, "entry_submitting")
        self._submit_entry(trade)

    def _has_entry_intent(self, trade_id: str) -> bool:
        return any(str(r["action"]) == "entry_intent" for r in self.store.list_events(trade_id))

    def _reconcile_entry_from_broker(self, trade: TradeRecord) -> bool:
        """Apply any visible broker entry order. Returns True if an order was found."""
        if trade.entry_order_id:
            polled = self.broker.poll_order(trade.entry_order_id)
            if polled is not None:
                self._apply_entry_order(trade, polled)
                return True
        existing = [
            o for o in self.broker.orders_by_tag(trade.broker_tag) if o.order_type == "MARKET"
        ]
        if existing:
            self.store.append_event(
                trade.trade_id, "reconcile", payload={"order_id": existing[0].order_id}
            )
            self._apply_entry_order(trade, existing[0])
            return True
        return False

    def _link_order(
        self,
        trade: TradeRecord,
        order_id: str,
        *,
        role: str,
        kind: str,
    ) -> bool:
        """Persist order→trade attribution. False on cross-trade conflict."""
        if not order_id:
            return False
        result = self.store.attribute_order(
            trade.trade_id,
            order_id,
            role=role,
            attribution_kind=kind,
            session_date=trade.session_date,
        )
        if result == "conflict":
            self.store.append_event(
                trade.trade_id,
                "order_attribution_conflict",
                payload={"order_id": order_id, "role": role, "kind": kind},
            )
            return False
        if result == "linked":
            self.store.append_event(
                trade.trade_id,
                "order_attributed",
                payload={"order_id": order_id, "role": role, "kind": kind},
            )
        return True

    def _order_owned_by_other_trade(self, trade: TradeRecord, order_id: str) -> bool:
        link = self.store.get_order_link(order_id)
        return link is not None and str(link["trade_id"]) != str(trade.trade_id)

    def _is_exit_order_type(self, order: Any) -> bool:
        return str(order.order_type).upper() in EXIT_ORDER_TYPES

    def _exit_side_matches(self, trade: TradeRecord, order: Any) -> bool:
        return str(order.transaction_type).upper() == _stop_side(trade.direction)

    def _order_product_exchange_ok(self, trade: TradeRecord, order: Any) -> bool:
        product = str(getattr(order, "product", None) or "MIS").upper()
        exchange = str(getattr(order, "exchange", None) or "NSE").upper()
        return product == "MIS" and exchange == "NSE"

    def _order_timing_ok_for_trade(self, trade: TradeRecord, order: Any) -> bool:
        """True only when untagged exit timing is fully resolved for this trade.

        Missing or invalid timestamps leave the order unresolved (False). Instants are
        compared as timezone-aware UTC values; the order must also fall on the trade's
        session calendar day in Asia/Kolkata. Application times and broker adapter
        outputs must carry an explicit timezone — naive values are rejected.
        """
        order_dt = _parse_aware_instant(getattr(order, "order_timestamp", None))
        if order_dt is None:
            return False

        session_raw = str(trade.session_date or "").strip()
        if not session_raw:
            return False
        try:
            session_day = date.fromisoformat(session_raw)
        except ValueError:
            return False
        if order_dt.astimezone(_SESSION_TZ).date() != session_day:
            return False

        entry_dt = _parse_aware_instant(trade.entry_time or trade.created_at)
        if entry_dt is None:
            return False
        if order_dt < entry_dt:
            return False

        if trade.close_time:
            close_dt = _parse_aware_instant(trade.close_time)
            if close_dt is None:
                return False
            if order_dt > close_dt:
                return False
        return True

    def _resolve_order(self, order_id: str, symbol: str) -> Optional[Any]:
        polled = self.broker.poll_order(order_id)
        if polled is not None:
            return polled
        for order in self.broker.orders_for_symbol(symbol):
            if str(order.order_id) == str(order_id):
                return order
        return None

    def _attributed_exit_qty(self, trade: TradeRecord) -> int:
        """Cumulative exit fills from durable per-order snapshots (not live visibility)."""
        _cv, _ev, confirmed, estimated = self.store.exit_execution_totals(trade.trade_id)
        return max(0, int(confirmed) + int(estimated))

    def _try_attribute_external_exits(self, trade: TradeRecord) -> list[Any]:
        """Attribute untagged exits only when ownership is unique; else leave unresolved."""
        side = _stop_side(trade.direction)
        peers = [
            t
            for t in self.store.list_trades(trade.session_date)
            if t.trade_id != trade.trade_id
            and t.symbol == trade.symbol
            and t.status in ACTIVE_STATES
        ]
        candidates: list[Any] = []
        for order in self.broker.orders_for_symbol(trade.symbol):
            oid = str(order.order_id)
            if self.store.get_order_link(oid) is not None:
                continue
            if str(order.tag or "") and str(order.tag) != str(trade.broker_tag):
                # Tagged for a different engine trade — never steal.
                continue
            if str(order.tag or "") == str(trade.broker_tag):
                continue  # handled via tag path
            if str(order.transaction_type).upper() != side:
                continue
            if not self._is_exit_order_type(order):
                continue
            if not self._order_product_exchange_ok(trade, order):
                continue
            if not self._order_timing_ok_for_trade(trade, order):
                continue
            if broker_order_filled_qty(order) <= 0:
                continue
            candidates.append(order)

        if not candidates:
            return []

        if peers:
            # Concurrent live exposure in the same symbol — never guess.
            if self._broker_is_flat(trade):
                self.store.append_event(
                    trade.trade_id,
                    "exit_attribution_ambiguous",
                    payload={
                        "reason": "concurrent_active_same_symbol",
                        "candidate_order_ids": [str(o.order_id) for o in candidates],
                        "peer_trade_ids": [p.trade_id for p in peers],
                    },
                )
            return []

        # Only auto-bind untagged exits when the broker is flat (external flatten).
        if not self._broker_is_flat(trade):
            return []

        filled = int(trade.filled_qty or 0)
        already = self._attributed_exit_qty(trade)
        gap = max(0, filled - already)
        if gap <= 0:
            return []

        # Prefer unresolved over subset matching: a single exact-qty order is not unique
        # ownership when other untagged candidates also exist (e.g. 10 vs 4+6).
        if len(candidates) == 1 and broker_order_filled_qty(candidates[0]) == gap:
            chosen = candidates[0]
            if self._link_order(trade, str(chosen.order_id), role="exit", kind="external"):
                return [chosen]
            return []

        self.store.append_event(
            trade.trade_id,
            "exit_attribution_ambiguous",
            payload={
                "reason": "untagged_exit_not_unique",
                "gap_qty": gap,
                "candidate_order_ids": [str(o.order_id) for o in candidates],
                "candidate_qtys": [broker_order_filled_qty(o) for o in candidates],
            },
        )
        return []

    def _iter_exit_orders(self, trade: TradeRecord) -> list[Any]:
        """Exit-side orders owned by this trade only (explicit attribution)."""
        seen: set[str] = set()
        out: list[Any] = []

        def _add(order: Any) -> None:
            oid = str(order.order_id)
            if oid in seen:
                return
            if self._order_owned_by_other_trade(trade, oid):
                return
            if not self._exit_side_matches(trade, order):
                return
            if not self._is_exit_order_type(order):
                return
            seen.add(oid)
            out.append(order)

        # 1) Already persisted links for this trade.
        for link in self.store.list_order_links(trade.trade_id):
            if str(link["role"]) not in {"exit", "stop"}:
                continue
            order = self._resolve_order(str(link["order_id"]), trade.symbol)
            if order is not None:
                _add(order)

        # 2) Known protective order id.
        if trade.sl_order_id:
            order = self._resolve_order(str(trade.sl_order_id), trade.symbol)
            if order is not None:
                self._link_order(trade, str(order.order_id), role="stop", kind="explicit")
                _add(order)

        # 3) Tag-owned exit/stop orders (engine-placed).
        for order in self.broker.orders_by_tag(trade.broker_tag):
            if not self._exit_side_matches(trade, order):
                continue
            if not self._is_exit_order_type(order):
                continue
            role = "stop" if _is_stop_order(order) else "exit"
            if self._link_order(trade, str(order.order_id), role=role, kind="tag"):
                _add(order)

        # 4) Untagged external exits — only when uniquely attributable.
        for order in self._try_attribute_external_exits(trade):
            _add(order)

        return out

    def _broker_exit_filled_total(self, trade: TradeRecord) -> int:
        """Cumulative exit fills from the durable per-order reconciliation snapshot."""
        _cv, _ev, confirmed, estimated = self._broker_exit_execution_values(trade)
        return max(0, int(confirmed) + int(estimated))

    def _broker_exit_execution_values(
        self, trade: TradeRecord
    ) -> tuple[float, float, int, int]:
        """Per-order exit notionals: (confirmed_value, estimated_value, confirmed_qty, est_qty).

        Visible broker orders update the durable trade_order_links snapshot. Qty and ₹
        totals are then read from that same snapshot so a temporarily missing order
        cannot drop previously confirmed execution value. Authoritative priced
        observations overwrite the snapshot when the order reappears.
        """
        est_px = float(
            trade.current_stop
            or trade.exit_fill
            or trade.entry_fill
            or trade.entry_estimate
            or 0.0
        )
        for order in self._iter_exit_orders(trade):
            filled = broker_order_filled_qty(order)
            avg = order.average_price
            self.store.record_order_execution(
                str(order.order_id),
                filled_qty=filled,
                average_price=None if avg is None else float(avg),
                estimated_px=est_px,
            )
        return self.store.exit_execution_totals(trade.trade_id, estimated_px=est_px)

    def _broker_exit_value_total(self, trade: TradeRecord) -> float:
        """Confirmed exit notional only (no price substitution)."""
        confirmed, _est, _cq, _eq = self._broker_exit_execution_values(trade)
        return confirmed

    def _entry_execution_values_from_order(
        self, order: Any, filled_qty: int, fallback_px: float
    ) -> tuple[float, float]:
        """Return (confirmed_value, estimated_value) for an entry order."""
        if filled_qty <= 0:
            return 0.0, 0.0
        if order.average_price is not None:
            return float(order.average_price) * float(filled_qty), 0.0
        return 0.0, float(fallback_px) * float(filled_qty)

    def _entry_value_from_order(self, order: Any, filled_qty: int, fallback_px: float) -> float:
        confirmed, _est = self._entry_execution_values_from_order(order, filled_qty, fallback_px)
        return confirmed

    def _sync_exit_values(self, trade: TradeRecord) -> dict[str, Any]:
        confirmed, estimated, confirmed_qty, est_qty = self._broker_exit_execution_values(trade)
        provisional = est_qty > 0
        weighted = None
        total_qty = confirmed_qty + est_qty
        if confirmed_qty > 0 and confirmed > 0:
            weighted = confirmed / float(confirmed_qty)
        elif est_qty > 0 and estimated > 0 and not provisional:
            weighted = estimated / float(est_qty)
        updates: dict[str, Any] = {
            "exit_value": confirmed,
            "exit_value_est": estimated,
            "pnl_provisional": 1 if provisional else 0,
            "exited_qty": max(int(trade.exited_qty or 0), confirmed_qty + est_qty),
            "exit_confirmed_qty": int(confirmed_qty),
            "exit_est_qty": int(est_qty),
        }
        if weighted is not None and not provisional:
            updates["exit_fill"] = weighted
        elif weighted is not None and provisional and trade.exit_fill is None:
            # Keep a display estimate without treating it as confirmed fill.
            updates["exit_fill"] = (
                (confirmed + estimated) / float(total_qty) if total_qty > 0 else trade.exit_fill
            )
        return updates

    def _exit_prices_fully_confirmed(self, trade: TradeRecord) -> bool:
        _c, _e, confirmed_qty, est_qty = self._broker_exit_execution_values(trade)
        exited = max(int(trade.exited_qty or 0), confirmed_qty + est_qty)
        if exited <= 0:
            return True
        return est_qty == 0 and confirmed_qty >= exited

    def _remaining_position_from_executions(self, filled_qty: int, exited_qty: int) -> int:
        return max(0, int(filled_qty) - int(exited_qty))

    def _broker_is_flat(self, trade: TradeRecord) -> bool:
        net = self.broker.net_position_qty(trade.symbol)
        if net is None:
            return False
        return abs(int(net)) == 0

    def _stop_is_working_cover(self, order: Any) -> bool:
        """True when stop still has confirmed waiting or triggered executable cover."""
        status = str(order.status).upper()
        if status in SL_CANCELLED | SL_FILLED:
            return False
        pending = broker_order_pending_qty(order)
        filled = broker_order_filled_qty(order)
        if status in SL_WORKING:
            return True
        # OPEN with fills = triggered partial; OPEN with zero fills = unconfirmed placement.
        if status in SL_TRIGGERED_WORKING:
            return filled > 0 and pending > 0
        return False

    def _submit_entry(self, trade: TradeRecord) -> None:
        # Always reconcile before pause/skip or any new write — lost responses may already be filled.
        if self._reconcile_entry_from_broker(trade):
            return

        paused = self._entries_paused()
        uncertain = (
            trade.status == "submission_unknown"
            or bool(trade.entry_order_id)
            or self._has_entry_intent(trade.trade_id)
        )
        # Any persisted submission intent must enter reconciliation before another broker write,
        # regardless of pause state (crash after accept, before local response record).
        if uncertain:
            if trade.status in {"entry_submitting", "submission_unknown"}:
                self.store.update_trade(trade.trade_id, status="submission_unknown")
            self.store.append_event(
                trade.trade_id,
                "pause_hold_unresolved" if paused else "reconcile_waiting",
                payload={
                    "reason": (
                        "entries_paused_pending_reconcile"
                        if paused
                        else "broker_visibility_pending"
                    ),
                    "had_entry_intent": self._has_entry_intent(trade.trade_id),
                    "entry_order_id": trade.entry_order_id,
                },
            )
            return

        # Block new broker writes on pause / calendar / cutoff before any intent.
        block = self._new_entry_block_reason()
        if block is not None:
            self.store.update_trade(
                trade.trade_id,
                status="skipped",
                skip_reason=block,
                qty=0,
                intended_qty=0,
                filled_qty=0,
                exited_qty=0,
                remaining_entry_qty=0,
                remaining_position_qty=0,
                protected_qty=0,
                notional=0,
                margin_blocked=0,
                qty_model_version=1,
            )
            self.store.append_event(
                trade.trade_id,
                "skipped",
                payload={"reason": block, "phase": "pre_entry_intent"},
            )
            return

        if self.live_orders_enabled:
            available = None
            try:
                available = self.broker.available_margins()
            except Exception:  # noqa: BLE001
                available = None
            if available is None:
                self.store.update_trade(
                    trade.trade_id,
                    status="rejected",
                    reject_reason="margin_unavailable",
                    qty=0,
                    intended_qty=0,
                    filled_qty=0,
                    exited_qty=0,
                    remaining_entry_qty=0,
                    remaining_position_qty=0,
                    protected_qty=0,
                    notional=0,
                    margin_blocked=0,
                    qty_model_version=1,
                )
                self.store.append_event(
                    trade.trade_id, "rejected", payload={"reason": "margin_unavailable"}
                )
                return
            quote = self.broker.order_margins(
                tradingsymbol=trade.symbol,
                transaction_type=_entry_side(trade.direction),
                quantity=trade.qty,
            )
            if not quote.ok or quote.required > float(available):
                reason = quote.reason or "insufficient_margin"
                if not quote.ok and "mis" in reason.lower():
                    reason = "mis_unavailable"
                self.store.update_trade(
                    trade.trade_id, status="rejected", reject_reason=reason, qty=0,
                    intended_qty=0, filled_qty=0, exited_qty=0, remaining_entry_qty=0,
                    remaining_position_qty=0, protected_qty=0,
                    notional=0, margin_blocked=0, qty_model_version=1,
                )
                self.store.append_event(trade.trade_id, "rejected", payload={"reason": reason})
                return

        # Persist intent before any broker write (durable command boundary).
        submitted_at = _now()
        self.store.append_event(
            trade.trade_id,
            "entry_intent",
            payload={
                "symbol": trade.symbol,
                "intended_qty": int(trade.intended_qty or trade.qty),
                "side": _entry_side(trade.direction),
                "run_id": trade.run_id or self.run_id,
                "live_orders": bool(self.live_orders_enabled),
                "submitted_at": submitted_at,
            },
        )
        if not trade.entry_submitted_at:
            self.store.update_trade(trade.trade_id, entry_submitted_at=submitted_at)
            trade = self.store.get_trade(trade.trade_id) or trade
        # Recheck entry gates immediately before the broker write.
        block = self._new_entry_block_reason()
        if block is not None:
            self.store.update_trade(
                trade.trade_id,
                status="skipped",
                skip_reason=block,
                qty=0,
                intended_qty=0,
                filled_qty=0,
                exited_qty=0,
                remaining_entry_qty=0,
                remaining_position_qty=0,
                protected_qty=0,
                notional=0,
                margin_blocked=0,
                qty_model_version=1,
            )
            self.store.append_event(
                trade.trade_id,
                "skipped",
                payload={"reason": block, "phase": "pre_broker_submit"},
            )
            return
        try:
            order = self.broker.place_market_mis(
                tradingsymbol=trade.symbol,
                transaction_type=_entry_side(trade.direction),
                quantity=trade.qty,
                tag=trade.broker_tag,
            )
        except Exception as exc:  # noqa: BLE001
            self.store.update_trade(trade.trade_id, status="submission_unknown")
            self.store.append_event(
                trade.trade_id,
                "submission_unknown",
                payload={"error": str(exc)},
            )
            return
        self.store.append_event(
            trade.trade_id, "entry_placed", payload={"order_id": order.order_id}
        )
        self._apply_entry_order(trade, order)

    def _confirmed_stop_cover_qty(self, order: Any) -> int:
        """Broker-confirmed protective size still working on the live stop order."""
        status = str(order.status).upper()
        if status in SL_CANCELLED | SL_FILLED:
            return 0
        pending = broker_order_pending_qty(order)
        filled = broker_order_filled_qty(order)
        if status in SL_WORKING:
            # Waiting stop: full pending (or quantity − filled) is cover.
            if pending > 0:
                return pending
            return max(0, int(order.quantity or 0) - filled)
        if status in SL_TRIGGERED_WORKING and filled > 0 and pending > 0:
            # Triggered partial: only the OPEN executable remainder is cover.
            return pending
        return 0

    def _apply_entry_order(self, trade: TradeRecord, order: Any) -> None:
        self._link_order(trade, str(order.order_id), role="entry", kind="explicit")
        status = str(order.status).upper()
        filled_qty = broker_order_filled_qty(order)
        pending_qty = broker_order_pending_qty(order)
        intended = int(trade.intended_qty or trade.qty or order.quantity or 0)
        exited_qty = max(int(trade.exited_qty or 0), self._broker_exit_filled_total(trade))

        if status in ENTRY_REJECTED and filled_qty <= 0:
            self.store.update_trade(
                trade.trade_id,
                status="rejected",
                reject_reason="entry_rejected",
                entry_order_id=order.order_id,
                qty=0,
                intended_qty=0,
                filled_qty=0,
                exited_qty=0,
                remaining_entry_qty=0,
                remaining_position_qty=0,
                protected_qty=0,
                notional=0,
                margin_blocked=0,
                qty_model_version=1,
            )
            self.store.append_event(trade.trade_id, "rejected", payload={"reason": "entry_rejected"})
            return

        if filled_qty <= 0:
            phase = "entry_submitting"
            if status not in ENTRY_WORKING and status not in ENTRY_COMPLETE and status not in ENTRY_REJECTED:
                phase = "submission_unknown"
            self.store.update_trade(
                trade.trade_id,
                entry_order_id=order.order_id,
                remaining_entry_qty=max(pending_qty, intended),
                remaining_position_qty=0,
                exited_qty=exited_qty,
                status=phase,
                qty_model_version=1,
            )
            return

        fill_px = float(order.average_price or trade.entry_estimate)
        # LIVE must not restore demo leverage into margin_blocked accounting.
        lev = 1.0 if self.live_orders_enabled else self.leverage_factor
        if status in ENTRY_COMPLETE or pending_qty <= 0:
            phase = "entry_filled"
            remaining = 0
        else:
            phase = "partial_entry"
            remaining = pending_qty
        reserved_for_margin = filled_qty + remaining
        blocked = margin_blocked(
            qty=reserved_for_margin, entry=fill_px, leverage_factor=lev
        )

        rem_pos = self._remaining_position_from_executions(filled_qty, exited_qty)
        if rem_pos <= 0 and exited_qty > 0 and remaining > 0:
            phase = "reconciliation_required"
        elif rem_pos <= 0 and exited_qty > 0 and remaining <= 0:
            phase = "reconciliation_required"
        elif rem_pos > 0 and exited_qty > 0 and remaining > 0:
            phase = "partial_entry"
        elif rem_pos > 0 and exited_qty > 0 and remaining <= 0:
            phase = "partial_exit" if rem_pos < filled_qty else phase

        entry_confirmed, entry_est = self._entry_execution_values_from_order(
            order, filled_qty, fill_px
        )
        reserved_qty = filled_qty + remaining
        self.store.update_trade(
            trade.trade_id,
            status=phase,
            entry_fill=(
                float(order.average_price)
                if order.average_price is not None
                else (entry_confirmed / float(filled_qty) if entry_confirmed > 0 else fill_px)
            ),
            entry_order_id=order.order_id,
            entry_time=trade.entry_time or _now(),
            filled_qty=filled_qty,
            exited_qty=exited_qty,
            entry_value=entry_confirmed,
            entry_value_est=entry_est,
            remaining_entry_qty=remaining,
            remaining_position_qty=rem_pos,
            intended_qty=intended,
            qty=intended,
            notional=float(reserved_qty) * float(order.average_price or fill_px),
            margin_blocked=blocked,
            pnl_provisional=1 if entry_est > 0 else int(bool(getattr(trade, "pnl_provisional", False))),
            qty_model_version=1,
        )
        if remaining == 0 and filled_qty > 0:
            mid = self.store.get_trade(trade.trade_id)
            if mid is not None:
                freeze = self._maybe_freeze_r_fields(mid)
                if freeze:
                    self.store.update_trade(trade.trade_id, **freeze)
        self.store.append_event(
            trade.trade_id,
            "entry_fill",
            payload={
                "fill": float(order.average_price or fill_px),
                "filled_qty": filled_qty,
                "entry_value": entry_confirmed,
                "entry_value_est": entry_est,
                "exited_qty": exited_qty,
                "remaining_entry_qty": remaining,
                "remaining_position_qty": rem_pos,
                "order_status": status,
            },
        )
        refreshed = self.store.get_trade(trade.trade_id)
        if refreshed and rem_pos > 0:
            self._ensure_protection(refreshed)

    def _ensure_protection(self, trade: TradeRecord) -> None:
        pos = int(trade.remaining_position_qty or 0)
        if pos <= 0:
            pos = self._remaining_position_from_executions(
                int(trade.filled_qty or 0), int(trade.exited_qty or 0)
            )
        if pos <= 0:
            return
        if trade.current_stop is None:
            self.store.update_trade(
                trade.trade_id, status="rejected", reject_reason="missing_stop"
            )
            return
        terminal_sl = SL_CANCELLED | SL_FILLED
        existing = [
            o
            for o in self.broker.orders_by_tag(trade.broker_tag)
            if _is_stop_order(o) and str(o.status).upper() not in terminal_sl
        ]
        if existing:
            order = existing[0]
            pending = broker_order_pending_qty(order)
            if pending != pos:
                try:
                    order = self.broker.modify_slm(
                        order.order_id,
                        float(trade.current_stop),
                        tick_size=trade.tick_size,
                        transaction_type=_stop_side(trade.direction),
                        quantity=pos,
                    )
                    self.store.append_event(
                        trade.trade_id,
                        "sl_qty_adjust_requested",
                        payload={
                            "order_id": order.order_id,
                            "requested_qty": pos,
                            "broker_qty": int(order.quantity or 0),
                            "broker_pending": broker_order_pending_qty(order),
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    # Ambiguous modify: reconcile before another write.
                    self.store.append_event(
                        trade.trade_id,
                        "sl_modify_ambiguous",
                        payload={"error": str(exc), "order_id": order.order_id},
                    )
                    polled = self.broker.poll_order(order.order_id)
                    if polled is not None:
                        self._apply_sl_order(trade, polled)
                    return
            self._apply_sl_order(trade, order)
            return
        order = self.broker.place_slm(
            tradingsymbol=trade.symbol,
            transaction_type=_stop_side(trade.direction),
            quantity=pos,
            trigger_price=trade.current_stop,
            tag=trade.broker_tag,
            tick_size=trade.tick_size,
        )
        self.store.append_event(
            trade.trade_id,
            "sl_placed",
            payload={"order_id": order.order_id, "requested_qty": pos},
        )
        self._apply_sl_order(trade, order)

    def _place_stop(self, trade: TradeRecord) -> None:
        """Backward-compatible alias: protect current remaining position."""
        self._ensure_protection(trade)

    def _apply_sl_order(self, trade: TradeRecord, order: Any) -> None:
        self._link_order(trade, str(order.order_id), role="stop", kind="explicit")
        status = str(order.status).upper()
        stop_filled = broker_order_filled_qty(order)
        prev_exited = int(trade.exited_qty or 0)
        exited_total = max(prev_exited, self._broker_exit_filled_total(trade))
        filled_qty = int(trade.filled_qty or 0)
        rem_pos = self._remaining_position_from_executions(filled_qty, exited_total)
        cover = self._confirmed_stop_cover_qty(order)
        new_exit_fills = exited_total > prev_exited

        # Persist newly observed exit executions (partial or complete).
        if new_exit_fills:
            exit_px = float(order.average_price or trade.current_stop or 0)
            incremental = exited_total - prev_exited
            self._handle_protective_exit(
                trade,
                exit_px=exit_px,
                exit_qty=incremental,
                reason="sl_hit",
                exited_qty_absolute=exited_total,
                stop_complete=status in SL_FILLED,
                stop_order=order,
            )
            return

        if status in SL_FILLED:
            # Exits already booked; drain entry remainder and require broker flat before close.
            rem_entry = int(trade.remaining_entry_qty or 0)
            if rem_entry > 0:
                rem_entry = self._cancel_entry_remainder(trade)
                refreshed = self.store.get_trade(trade.trade_id)
                if refreshed is not None:
                    trade = refreshed
                    filled_qty = max(filled_qty, int(trade.filled_qty or 0))
                    exited_total = max(exited_total, int(trade.exited_qty or 0))
                    rem_entry = int(trade.remaining_entry_qty or 0)
                    rem_pos = self._remaining_position_from_executions(filled_qty, exited_total)
            if rem_pos <= 0 and rem_entry <= 0 and self._broker_is_flat(trade):
                exit_updates = self._sync_exit_values(trade)
                self.store.update_trade(
                    trade.trade_id,
                    **{k: v for k, v in exit_updates.items() if k != "exited_qty"},
                )
                refreshed = self.store.get_trade(trade.trade_id)
                trade = refreshed if refreshed is not None else trade
                if not self._exit_prices_fully_confirmed(trade):
                    self.store.update_trade(
                        trade.trade_id,
                        status="reconciliation_required",
                        pnl_provisional=1,
                        exited_qty=exited_total,
                        filled_qty=filled_qty,
                        remaining_position_qty=0,
                        remaining_entry_qty=0,
                    )
                    self.store.append_event(
                        trade.trade_id,
                        "pnl_provisional_waiting_prices",
                        payload={
                            "exit_value": float(getattr(trade, "exit_value", 0) or 0),
                            "exit_value_est": float(getattr(trade, "exit_value_est", 0) or 0),
                        },
                    )
                elif trade.status != "closed":
                    self._close_trade(
                        trade,
                        float(order.average_price or trade.current_stop or trade.exit_fill or 0),
                        "sl_hit",
                        closed_qty=max(exited_total, filled_qty),
                        exited_qty=exited_total,
                        filled_qty=filled_qty,
                    )
                return
            if trade.status != "reconciliation_required":
                self.store.update_trade(
                    trade.trade_id,
                    status="reconciliation_required",
                    exit_fill=float(order.average_price or trade.exit_fill or trade.current_stop or 0),
                    filled_qty=filled_qty,
                    exited_qty=exited_total,
                    remaining_position_qty=rem_pos,
                    remaining_entry_qty=rem_entry,
                    protected_qty=0,
                    sl_order_id=None if rem_pos > 0 else trade.sl_order_id,
                    qty_model_version=1,
                )
            if rem_pos > 0:
                again = self.store.get_trade(trade.trade_id)
                if again is not None:
                    self._ensure_protection(again)
            return

        if status in SL_WORKING or (
            status in SL_TRIGGERED_WORKING
            and broker_order_filled_qty(order) > 0
            and broker_order_pending_qty(order) > 0
        ):
            confirmed = min(cover, rem_pos) if rem_pos > 0 else cover
            if int(trade.remaining_entry_qty or 0) > 0 and rem_pos > 0:
                next_status = "partial_entry"
            elif rem_pos > 0 and exited_total > 0 and confirmed >= rem_pos:
                next_status = (
                    "partial_exit" if int(trade.remaining_entry_qty or 0) <= 0 else "partial_entry"
                )
            elif rem_pos > 0 and confirmed >= rem_pos:
                next_status = "protected_open"
            elif rem_pos > 0:
                next_status = "protection_pending"
            else:
                next_status = "reconciliation_required"
            exit_updates = self._sync_exit_values(trade)
            exit_value = float(exit_updates["exit_value"])
            # Avoid event spam / rearm loops when nothing material changed.
            if (
                trade.status == next_status
                and int(trade.protected_qty or 0) == confirmed
                and int(trade.exited_qty or 0) == exited_total
                and int(trade.remaining_position_qty or 0) == rem_pos
                and trade.sl_order_id == order.order_id
                and abs(float(getattr(trade, "exit_value", 0) or 0) - exit_value) < 1e-9
                and abs(float(getattr(trade, "exit_value_est", 0) or 0) - float(exit_updates.get("exit_value_est", 0)))
                < 1e-9
            ):
                return
            self.store.update_trade(
                trade.trade_id,
                status=next_status,
                sl_order_id=order.order_id,
                protected_qty=confirmed if rem_pos > 0 else 0,
                exited_qty=exited_total,
                remaining_position_qty=rem_pos,
                qty_model_version=1,
                **{k: v for k, v in exit_updates.items() if k != "exited_qty"},
                **self._protection_state_fields(trade, next_status=next_status),
            )
            self.store.append_event(
                trade.trade_id,
                "protected",
                payload={
                    "protected_qty": confirmed,
                    "broker_stop_qty": cover,
                    "remaining_position_qty": rem_pos,
                    "exited_qty": exited_total,
                    "exit_value": exit_value,
                    "exit_value_est": float(exit_updates.get("exit_value_est", 0)),
                    "entry_remaining": int(trade.remaining_entry_qty or 0),
                    "stop_status": status,
                },
            )
            return

        # Unconfirmed OPEN (no fills yet) or other non-terminal: order exists but not covering.
        pending_status = "protection_pending" if rem_pos > 0 else "stop_pending"
        self.store.update_trade(
            trade.trade_id,
            status=pending_status,
            sl_order_id=order.order_id,
            protected_qty=0,
            exited_qty=exited_total,
            remaining_position_qty=rem_pos,
            qty_model_version=1,
            **self._protection_state_fields(trade, next_status=pending_status),
        )
        self.store.append_event(trade.trade_id, "protection_pending")

    def _cancel_entry_remainder(self, trade: TradeRecord) -> int:
        """Cancel unfilled entry; persist cancellation-time fills. Returns pending qty."""
        remaining = int(trade.remaining_entry_qty or 0)
        if remaining <= 0:
            return 0
        if not trade.entry_order_id:
            return remaining
        cancelled = self.broker.cancel_order(trade.entry_order_id)
        if cancelled is None:
            return remaining
        pending = broker_order_pending_qty(cancelled)
        filled = broker_order_filled_qty(cancelled)
        status = str(cancelled.status).upper()
        if status in {"CANCELLED", "REJECTED", "COMPLETE"}:
            pending = 0 if status != "COMPLETE" else broker_order_pending_qty(cancelled)
            if status == "COMPLETE":
                pending = 0
        exited = max(int(trade.exited_qty or 0), self._broker_exit_filled_total(trade))
        prev_filled = int(trade.filled_qty or 0)
        new_filled = max(prev_filled, filled)
        rem_pos = self._remaining_position_from_executions(new_filled, exited)
        entry_confirmed, entry_est = self._entry_execution_values_from_order(
            cancelled,
            new_filled,
            float(cancelled.average_price or trade.entry_fill or trade.entry_estimate),
        )
        exit_updates = self._sync_exit_values(trade)
        updates: dict[str, Any] = {
            "remaining_entry_qty": pending,
            "filled_qty": new_filled,
            "exited_qty": exited,
            "entry_value": entry_confirmed,
            "entry_value_est": entry_est,
            "remaining_position_qty": rem_pos,
            "qty_model_version": 1,
            **{k: v for k, v in exit_updates.items() if k != "exited_qty"},
        }
        if new_filled > 0 and cancelled.average_price is not None:
            updates["entry_fill"] = float(cancelled.average_price)
        elif new_filled > 0 and entry_confirmed > 0:
            updates["entry_fill"] = entry_confirmed / float(new_filled)
        if new_filled > prev_filled or pending == 0:
            avg = float(updates.get("entry_fill") or trade.entry_fill or trade.entry_estimate)
            reserved = rem_pos + max(0, pending)
            updates["notional"] = float(reserved) * avg
            lev = 1.0 if self.live_orders_enabled else self.leverage_factor
            updates["margin_blocked"] = margin_blocked(
                qty=reserved,
                entry=avg,
                leverage_factor=lev,
            )
        self.store.update_trade(trade.trade_id, **updates)
        self.store.append_event(
            trade.trade_id,
            "entry_cancel_attempt",
            payload={
                "status": cancelled.status,
                "pending": pending,
                "filled": filled,
                "prev_filled": prev_filled,
                "remaining_position_qty": rem_pos,
                "exited_qty": exited,
                "entry_value": entry_confirmed,
                "entry_value_est": entry_est,
            },
        )
        if pending == 0 and new_filled > 0:
            refreshed = self.store.get_trade(trade.trade_id)
            if refreshed is not None:
                freeze = self._maybe_freeze_r_fields(refreshed)
                if freeze:
                    self.store.update_trade(trade.trade_id, **freeze)
        return pending

    def _handle_protective_exit(
        self,
        trade: TradeRecord,
        *,
        exit_px: float,
        exit_qty: int,
        reason: str,
        exited_qty_absolute: Optional[int] = None,
        stop_complete: bool = True,
        stop_order: Any = None,
    ) -> None:
        prev_exited = int(trade.exited_qty or 0)
        if exited_qty_absolute is not None:
            new_exited = max(prev_exited, int(exited_qty_absolute))
        else:
            new_exited = prev_exited + max(0, int(exit_qty))
        filled = int(trade.filled_qty or 0)
        rem_entry = self._cancel_entry_remainder(trade)
        refreshed = self.store.get_trade(trade.trade_id)
        if refreshed is not None:
            trade = refreshed
            filled = max(filled, int(trade.filled_qty or 0))
            rem_entry = int(trade.remaining_entry_qty or 0)
            new_exited = max(new_exited, int(trade.exited_qty or 0), self._broker_exit_filled_total(trade))

        # Re-poll entry after cancel for late fills (also covered by cancel path).
        if trade.entry_order_id:
            polled = self.broker.poll_order(trade.entry_order_id)
            if polled is not None:
                late_filled = broker_order_filled_qty(polled)
                rem_entry = broker_order_pending_qty(polled)
                st = str(polled.status).upper()
                if st in {"CANCELLED", "REJECTED", "COMPLETE"}:
                    rem_entry = 0 if st != "OPEN" else rem_entry
                    if st == "COMPLETE":
                        rem_entry = 0
                if late_filled > filled:
                    bump = late_filled - filled
                    filled = late_filled
                    entry_confirmed, entry_est = self._entry_execution_values_from_order(
                        polled,
                        filled,
                        float(polled.average_price or trade.entry_fill or exit_px),
                    )
                    self.store.update_trade(
                        trade.trade_id,
                        filled_qty=filled,
                        entry_fill=(
                            float(polled.average_price)
                            if polled.average_price is not None
                            else trade.entry_fill
                        ),
                        entry_value=entry_confirmed,
                        entry_value_est=entry_est,
                    )
                    self.store.append_event(
                        trade.trade_id,
                        "late_entry_fill_during_exit",
                        payload={
                            "bump": bump,
                            "filled": late_filled,
                            "entry_value": entry_confirmed,
                            "entry_value_est": entry_est,
                        },
                    )

        new_exited = max(new_exited, self._broker_exit_filled_total(trade))
        refreshed_for_vals = self.store.get_trade(trade.trade_id)
        if refreshed_for_vals is not None:
            trade = refreshed_for_vals
        exit_updates = self._sync_exit_values(trade)
        exit_value = float(exit_updates["exit_value"])
        exit_value_est = float(exit_updates.get("exit_value_est", 0) or 0)
        provisional = bool(int(exit_updates.get("pnl_provisional", 0) or 0))
        entry_confirmed = float(getattr(trade, "entry_value", 0) or 0)
        entry_est = float(getattr(trade, "entry_value_est", 0) or 0)
        if filled > 0 and trade.entry_order_id:
            polled_entry = self.broker.poll_order(trade.entry_order_id)
            if polled_entry is not None:
                c, e = self._entry_execution_values_from_order(
                    polled_entry,
                    filled,
                    float(polled_entry.average_price or trade.entry_fill or trade.entry_estimate),
                )
                entry_confirmed, entry_est = c, e
                if e > 0:
                    provisional = True
        new_pos = self._remaining_position_from_executions(filled, new_exited)
        cover = 0
        if stop_order is not None and not stop_complete:
            cover = min(self._confirmed_stop_cover_qty(stop_order), new_pos)

        if new_pos > 0 or rem_entry > 0 or not self._broker_is_flat(trade):
            next_status = "reconciliation_required"
            if new_pos > 0 and rem_entry <= 0 and not stop_complete and cover >= new_pos:
                next_status = "partial_exit"
            elif new_pos > 0 and rem_entry > 0:
                next_status = "partial_entry" if not stop_complete else "reconciliation_required"
            elif new_pos > 0 and stop_complete:
                next_status = "reconciliation_required"
            display_exit = exit_px
            if exit_value > 0 and new_exited > 0 and not provisional:
                display_exit = exit_value / float(max(1, int(exit_updates.get("exited_qty", new_exited))))
            elif exit_value + exit_value_est > 0 and new_exited > 0:
                display_exit = (exit_value + exit_value_est) / float(new_exited)
            prev_loss = float(getattr(trade, "closed_loss_contribution", 0) or 0)
            prev_pnl = float(getattr(trade, "realised_pnl", 0) or 0)
            partial_loss = prev_loss
            partial_pnl = prev_pnl
            if (
                not provisional
                and new_exited > 0
                and filled > 0
                and entry_confirmed > 0
                and exit_value > 0
            ):
                entry_slice = entry_confirmed * (float(new_exited) / float(filled))
                partial_pnl = realised_pnl_from_values(
                    direction=trade.direction,
                    entry_value=entry_slice,
                    exit_value=exit_value,
                    qty=new_exited,
                )
                # Price loss only here; residual exit costs stay in exited_cost_reservation
                # until authoritative close (avoids wiping costs at gross BE).
                partial_loss = abs(partial_pnl) if partial_pnl < 0 else 0.0
            elif provisional:
                # Missing prices must not zero an existing confirmed loss.
                partial_loss = prev_loss
                partial_pnl = prev_pnl
            self.store.update_trade(
                trade.trade_id,
                status=next_status,
                exit_fill=display_exit,
                filled_qty=filled,
                exited_qty=new_exited,
                entry_value=entry_confirmed,
                entry_value_est=entry_est,
                remaining_position_qty=new_pos,
                remaining_entry_qty=rem_entry,
                protected_qty=cover if next_status == "partial_exit" else 0,
                close_reason=None,
                realised_pnl=partial_pnl,
                closed_loss_contribution=partial_loss,
                sl_order_id=(
                    trade.sl_order_id
                    if not stop_complete
                    else (None if new_pos > 0 else trade.sl_order_id)
                ),
                qty_model_version=1,
                **{k: v for k, v in exit_updates.items() if k not in {"exited_qty", "exit_fill"}},
            )
            self.store.append_event(
                trade.trade_id,
                "exit_partial_or_unreconciled",
                payload={
                    "reason": reason,
                    "exit_qty": exit_qty,
                    "exited_qty": new_exited,
                    "exit_value": exit_value,
                    "exit_value_est": exit_value_est,
                    "pnl_provisional": provisional,
                    "remaining_position_qty": new_pos,
                    "remaining_entry_qty": rem_entry,
                    "stop_complete": stop_complete,
                    "broker_flat": self._broker_is_flat(trade),
                },
            )
            # Residual exposure after a completed stop (e.g. late entry fill) needs new protection.
            if new_pos > 0 and stop_complete:
                again = self.store.get_trade(trade.trade_id)
                if again is not None:
                    self._ensure_protection(again)
            elif new_pos > 0 and not stop_complete and cover < new_pos:
                again = self.store.get_trade(trade.trade_id)
                if again is not None:
                    self._ensure_protection(again)
            return

        # Local flat AND broker flat — require confirmed exit prices before final close.
        self.store.update_trade(
            trade.trade_id,
            filled_qty=filled,
            exited_qty=new_exited,
            entry_value=entry_confirmed,
            entry_value_est=entry_est,
            **{k: v for k, v in exit_updates.items() if k != "exited_qty"},
        )
        refreshed = self.store.get_trade(trade.trade_id)
        trade = refreshed if refreshed is not None else trade
        if not self._exit_prices_fully_confirmed(trade) or entry_est > 0:
            display_exit = exit_px
            if exit_value + exit_value_est > 0 and new_exited > 0:
                display_exit = (exit_value + exit_value_est) / float(new_exited)
            self.store.update_trade(
                trade.trade_id,
                status="reconciliation_required",
                exit_fill=display_exit,
                pnl_provisional=1,
                close_reason=None,
            )
            self.store.append_event(
                trade.trade_id,
                "pnl_provisional_waiting_prices",
                payload={
                    "exit_value": exit_value,
                    "exit_value_est": exit_value_est,
                    "entry_value": entry_confirmed,
                    "entry_value_est": entry_est,
                },
            )
            return
        weighted_exit = (
            exit_value / float(new_exited) if new_exited > 0 and exit_value > 0 else exit_px
        )
        self._close_trade(
            trade,
            weighted_exit,
            reason,
            closed_qty=max(exit_qty, new_exited, filled),
            exited_qty=new_exited,
            filled_qty=filled,
        )

    def _refresh_closed_exit_snapshot(self, trade: TradeRecord) -> None:
        """Re-sync durable exit qty/₹ after close when hidden orders reappear/correct.

        Temporarily missing broker orders must not erase the snapshot. When a priced
        observation returns, apply the authoritative correction once (no double-count).
        """
        prev_value = float(getattr(trade, "exit_value", 0) or 0)
        prev_est = float(getattr(trade, "exit_value_est", 0) or 0)
        prev_cq = int(getattr(trade, "exit_confirmed_qty", 0) or 0)
        prev_eq = int(getattr(trade, "exit_est_qty", 0) or 0)
        prev_exited = int(getattr(trade, "exited_qty", 0) or 0)
        updates = self._sync_exit_values(trade)
        new_value = float(updates.get("exit_value", 0) or 0)
        new_est = float(updates.get("exit_value_est", 0) or 0)
        new_cq = int(updates.get("exit_confirmed_qty", 0) or 0)
        new_eq = int(updates.get("exit_est_qty", 0) or 0)
        new_exited = int(updates.get("exited_qty", prev_exited) or prev_exited)
        if (
            abs(new_value - prev_value) < 1e-9
            and abs(new_est - prev_est) < 1e-9
            and new_cq == prev_cq
            and new_eq == prev_eq
            and new_exited == prev_exited
        ):
            return
        filled = int(trade.filled_qty or trade.qty or 0)
        pnl_qty = min(filled, new_exited) if filled and new_exited else max(filled, new_exited)
        entry_value = float(getattr(trade, "entry_value", 0) or 0)
        provisional = bool(int(updates.get("pnl_provisional", 0) or 0))
        if entry_value > 0 and new_value > 0 and pnl_qty > 0 and not provisional:
            entry_for_pnl = (
                entry_value * (float(pnl_qty) / float(filled)) if filled else entry_value
            )
            pnl = realised_pnl_from_values(
                direction=trade.direction,
                entry_value=entry_for_pnl,
                exit_value=new_value,
                qty=pnl_qty,
            )
            entry_px = entry_for_pnl / float(pnl_qty)
            charge_bps, _ = trade_cost_profile(
                trade,
                charge_bps=float(
                    self._admin_store.load_effective_payload().get(
                        "round_trip_charge_bps", DEFAULT_ROUND_TRIP_CHARGE_BPS
                    )
                ),
            )
            loss = fold_exit_costs_into_loss(
                price_pnl=pnl,
                qty=pnl_qty,
                entry=float(entry_px),
                charge_bps=charge_bps,
            )
            exit_avg = new_value / float(new_cq) if new_cq > 0 else trade.exit_fill
        else:
            pnl = float(trade.realised_pnl or 0)
            loss = float(trade.closed_loss_contribution or 0)
            exit_avg = trade.exit_fill
        self.store.update_trade(
            trade.trade_id,
            exit_value=new_value,
            exit_value_est=new_est,
            exit_confirmed_qty=new_cq,
            exit_est_qty=new_eq,
            exited_qty=max(prev_exited, new_exited),
            pnl_provisional=1 if provisional else 0,
            exit_fill=exit_avg,
            realised_pnl=pnl,
            closed_loss_contribution=loss,
        )
        self.store.append_event(
            trade.trade_id,
            "closed_exit_snapshot_refreshed",
            payload={
                "exit_value": new_value,
                "exit_value_est": new_est,
                "exit_confirmed_qty": new_cq,
                "exit_est_qty": new_eq,
                "pnl": pnl,
            },
        )

    def drive_open(self) -> None:
        for trade in self.store.list_trades(self.session_date):
            if trade.status == "closed":
                self._refresh_closed_exit_snapshot(trade)
                continue
            if trade.status in {
                "entry_submitting",
                "submission_unknown",
                "partial_entry",
                "reconciliation_required",
                "exit_pending",
                "partial_exit",
            }:
                if trade.status == "submission_unknown":
                    found = self._reconcile_entry_from_broker(trade)
                    if not found:
                        self.store.append_event(
                            trade.trade_id,
                            "reconcile_waiting",
                            payload={"reason": "broker_visibility_pending"},
                        )
                    refreshed = self.store.get_trade(trade.trade_id)
                    trade = refreshed if refreshed is not None else trade
                elif trade.status in {"entry_submitting", "partial_entry", "partial_exit"}:
                    if trade.status == "entry_submitting" and (
                        self._has_entry_intent(trade.trade_id) or bool(trade.entry_order_id)
                    ):
                        # Crash-recovery path: never place another entry while intent exists.
                        if trade.entry_order_id:
                            polled = self.broker.poll_order(trade.entry_order_id)
                            if polled:
                                self._apply_entry_order(trade, polled)
                            else:
                                self._submit_entry(trade)  # reconcile-only when intent present
                        else:
                            self._submit_entry(trade)
                    elif trade.entry_order_id:
                        polled = self.broker.poll_order(trade.entry_order_id)
                        if polled:
                            self._apply_entry_order(trade, polled)
                    else:
                        self._submit_entry(trade)
                    refreshed = self.store.get_trade(trade.trade_id)
                    trade = refreshed if refreshed is not None else trade
                elif trade.status in {"reconciliation_required", "exit_pending"}:
                    if int(trade.remaining_entry_qty or 0) > 0:
                        self._cancel_entry_remainder(trade)
                    refreshed = self.store.get_trade(trade.trade_id)
                    trade = refreshed if refreshed is not None else trade
                    exited = max(int(trade.exited_qty or 0), self._broker_exit_filled_total(trade))
                    filled = int(trade.filled_qty or 0)
                    rem_pos = self._remaining_position_from_executions(filled, exited)
                    rem_entry = int(trade.remaining_entry_qty or 0)
                    self.store.update_trade(
                        trade.trade_id,
                        filled_qty=filled,
                        exited_qty=exited,
                        remaining_position_qty=rem_pos,
                        remaining_entry_qty=rem_entry,
                    )
                    refreshed = self.store.get_trade(trade.trade_id)
                    trade = refreshed if refreshed is not None else trade
                    exit_updates = self._sync_exit_values(trade)
                    self.store.update_trade(
                        trade.trade_id,
                        **{k: v for k, v in exit_updates.items() if k != "exited_qty"},
                    )
                    refreshed = self.store.get_trade(trade.trade_id)
                    trade = refreshed if refreshed is not None else trade
                    if rem_pos <= 0 and rem_entry <= 0 and self._broker_is_flat(trade):
                        if not self._exit_prices_fully_confirmed(trade):
                            self.store.update_trade(
                                trade.trade_id,
                                status="reconciliation_required",
                                pnl_provisional=1,
                            )
                            self.store.append_event(
                                trade.trade_id,
                                "pnl_provisional_waiting_prices",
                                payload={
                                    "exit_value": float(getattr(trade, "exit_value", 0) or 0),
                                    "exit_value_est": float(
                                        getattr(trade, "exit_value_est", 0) or 0
                                    ),
                                },
                            )
                        else:
                            self._close_trade(
                                trade,
                                float(
                                    trade.exit_fill
                                    or trade.current_stop
                                    or trade.entry_fill
                                    or 0
                                ),
                                trade.close_reason or "reconciled_flat",
                                exited_qty=exited,
                                filled_qty=filled,
                            )
                    elif rem_pos > 0:
                        if not self._blocking_exit_in_progress(trade):
                            self._ensure_protection(trade)
                    refreshed = self.store.get_trade(trade.trade_id)
                    trade = refreshed if refreshed is not None else trade

            if trade.status in {
                "entry_filled",
                "partial_entry",
                "partial_exit",
                "stop_pending",
                "protection_pending",
                "protected_open",
                "reconciliation_required",
            }:
                if self._reconcile_external_exit(trade):
                    continue
                refreshed = self.store.get_trade(trade.trade_id)
                trade = refreshed if refreshed is not None else trade

            pos = int(trade.remaining_position_qty or 0)
            if pos <= 0:
                pos = self._remaining_position_from_executions(
                    int(trade.filled_qty or 0), int(trade.exited_qty or 0)
                )
            prot = int(trade.protected_qty or 0)
            if trade.status in {
                "entry_filled",
                "partial_entry",
                "partial_exit",
                "protection_pending",
                "stop_pending",
                "protected_open",
                "reconciliation_required",
            }:
                # Always poll working/terminal stops for partial fills before resizing.
                if trade.sl_order_id:
                    polled = self.broker.poll_order(trade.sl_order_id)
                    if polled is not None:
                        self._apply_sl_order(trade, polled)
                        refreshed = self.store.get_trade(trade.trade_id)
                        trade = refreshed if refreshed is not None else trade
                        pos = int(trade.remaining_position_qty or 0)
                        prot = int(trade.protected_qty or 0)
                if pos > prot:
                    if not self._blocking_exit_in_progress(trade):
                        self._ensure_protection(trade)
                elif trade.status == "protected_open":
                    self._drive_protected(trade)

    def _reconcile_external_exit(self, trade: TradeRecord) -> bool:
        if self._blocking_exit_in_progress(trade):
            # Outstanding emergency/trail/close/square-off exit owns reconciliation.
            return False
        filled = int(trade.filled_qty or 0)
        exited = max(int(trade.exited_qty or 0), self._broker_exit_filled_total(trade))
        pos = self._remaining_position_from_executions(filled, exited)
        if pos <= 0:
            pos = int(trade.remaining_position_qty or 0)
        if trade.entry_fill is None or (pos <= 0 and int(trade.remaining_entry_qty or 0) <= 0):
            return False
        if int(trade.remaining_entry_qty or 0) > 0:
            net = self.broker.net_position_qty(trade.symbol)
            if net is None:
                return False
            if abs(int(net)) != 0:
                return False
            rem = self._cancel_entry_remainder(trade)
            refreshed = self.store.get_trade(trade.trade_id)
            if refreshed is not None:
                trade = refreshed
                filled = int(trade.filled_qty or 0)
                exited = max(int(trade.exited_qty or 0), self._broker_exit_filled_total(trade))
                pos = self._remaining_position_from_executions(filled, exited)
                rem = int(trade.remaining_entry_qty or 0)
            self.store.update_trade(
                trade.trade_id,
                status="reconciliation_required",
                filled_qty=filled,
                exited_qty=exited,
                remaining_position_qty=pos,
                remaining_entry_qty=rem,
                protected_qty=0 if pos <= 0 else int(trade.protected_qty or 0),
            )
            if rem == 0 and pos <= 0 and self._broker_is_flat(trade):
                self._close_trade(
                    trade,
                    float(trade.current_stop or trade.entry_fill or 0),
                    "external_exit",
                    exited_qty=exited,
                    filled_qty=filled,
                )
                return True
            if pos > 0:
                again = self.store.get_trade(trade.trade_id)
                if again is not None:
                    self._ensure_protection(again)
            return True
        net = self.broker.net_position_qty(trade.symbol)
        if net is None:
            return False
        if abs(net) not in {0, pos} and net != 0:
            self.store.append_event(
                trade.trade_id,
                "qty_mismatch",
                payload={"net": net, "engine_qty": pos},
            )
        if net != 0:
            return False
        if trade.sl_order_id:
            sl = self.broker.poll_order(trade.sl_order_id)
            if sl is not None and str(sl.status).upper() in SL_FILLED:
                self._handle_protective_exit(
                    trade,
                    exit_px=float(sl.average_price or trade.current_stop or 0),
                    exit_qty=broker_order_filled_qty(sl) or pos,
                    reason="sl_hit",
                    exited_qty_absolute=max(exited, broker_order_filled_qty(sl)),
                    stop_complete=True,
                    stop_order=sl,
                )
                return True
            if sl is not None and str(sl.status).upper() not in SL_CANCELLED | SL_FILLED:
                self.broker.cancel_order(trade.sl_order_id)
        exit_px = self._exit_fill_from_broker(trade)
        self._handle_protective_exit(
            trade,
            exit_px=exit_px,
            exit_qty=pos,
            reason="external_exit",
            exited_qty_absolute=max(exited, self._broker_exit_filled_total(trade)),
            stop_complete=True,
        )
        return True

    def _exit_fill_from_broker(self, trade: TradeRecord) -> float:
        fills = [
            o
            for o in self._iter_exit_orders(trade)
            if str(o.status).upper() in SL_FILLED and o.average_price is not None
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
        if status in SL_FILLED or broker_order_filled_qty(polled) > int(trade.exited_qty or 0):
            self._apply_sl_order(trade, polled)
            return
        if status in SL_CANCELLED:
            self.store.update_trade(trade.trade_id, status="stop_pending", protected_qty=0)
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
                self._cancel_pending_vwap_on_pause()
                continue
            if command.kind == "pause_entries":
                self.consume_new_triggers = False
                self.store.set_consume_triggers(self.run_id, False)
                self._cancel_pending_vwap_on_pause()
                self.store.mark_command_processed(command.command_id)
                continue
            if command.kind == "resume_entries":
                self.consume_new_triggers = True
                self.store.set_consume_triggers(self.run_id, True)
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
                continue
            if command.kind == "close_position" and command.trade_id:
                self.close_position(command.trade_id, actor="user")
                self.store.mark_command_processed(command.command_id)
                continue
            if command.kind == "close_all":
                self.close_all(actor="user")
                self.store.mark_command_processed(command.command_id)
                continue

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
            modified = self.broker.modify_slm(
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
        confirmed = self._confirmed_trail_trigger(modified, requested=aligned, tick_size=trade.tick_size)
        if confirmed is None:
            self.store.append_event(
                trade.trade_id,
                "sl_modify_unconfirmed",
                actor=actor,
                old_stop=old,
                new_stop=aligned,
                payload={
                    "broker_trigger": getattr(modified, "trigger_price", None),
                    "broker_status": getattr(modified, "status", None),
                },
            )
            return trade
        if not trail_is_tighten_only(
            direction=trade.direction,
            current_stop=float(old),
            new_stop=float(confirmed),
        ):
            self.store.append_event(
                trade.trade_id,
                "sl_modify_unchanged",
                actor=actor,
                old_stop=old,
                new_stop=aligned,
                payload={"broker_trigger": confirmed},
            )
            return trade
        fields: dict[str, Any] = {"current_stop": float(confirmed)}
        if actor == "user" and trade.auto_trail_enabled:
            mark_for_trail = mark if mark is not None else self._last_price(trade.symbol)
            if mark_for_trail is not None:
                fields["auto_trail_ticks"] = max(
                    1,
                    abs(
                        price_to_ticks(float(mark_for_trail), trade.tick_size)
                        - price_to_ticks(float(confirmed), trade.tick_size)
                    ),
                )
                fields["auto_trail_extreme"] = float(mark_for_trail)
        updated = self.store.update_trade(trade.trade_id, **fields)
        self.store.append_event(
            trade.trade_id,
            "sl_modified",
            actor=actor,
            old_stop=old,
            new_stop=float(confirmed),
        )
        return updated

    def _confirmed_trail_trigger(
        self,
        order: Any,
        *,
        requested: float,
        tick_size: float,
    ) -> Optional[float]:
        """Return broker-confirmed stop only when waiting-SL trigger advanced to request."""
        if order is None:
            return None
        status = str(getattr(order, "status", "") or "").upper()
        if status not in SL_WORKING:
            # Triggered/uncertain states must not advance confirmed protection from modify.
            return None
        raw = getattr(order, "trigger_price", None)
        if raw is None:
            return None
        confirmed = _align_stop(float(raw), tick_size)
        req = _align_stop(float(requested), tick_size)
        if abs(confirmed - req) > max(float(tick_size) * 0.5, 1e-9):
            return None
        return confirmed

    def set_auto_trail(self, trade_id: str, *, enabled: bool) -> TradeRecord:
        trade = self.store.get_trade(trade_id)
        if trade is None:
            raise ValueError("trade_not_found")
        if not enabled:
            updated = self.store.update_trade(
                trade_id,
                auto_trail_enabled=0,
                auto_trail_owner_disabled=1,
            )
            self.store.append_event(trade_id, "auto_trail_off", actor="user")
            return updated
        protected = int(trade.protected_qty or 0)
        if trade.status == "protected_open":
            pass
        elif trade.status == "partial_entry" and protected > 0:
            pass
        else:
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
            auto_trail_owner_disabled=0,
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

    def _now_ist(self) -> datetime:
        if self._clock_fn is not None:
            now = self._clock_fn()
        else:
            now = datetime.now(_SESSION_TZ)
        if now.tzinfo is None:
            now = now.replace(tzinfo=_SESSION_TZ)
        return now.astimezone(_SESSION_TZ)

    @staticmethod
    def _hhmm_to_minutes(hhmm: object) -> Optional[int]:
        return parse_hhmm(hhmm)

    def _configured_special_schedule(self) -> Optional[SpecialSessionSchedule]:
        """Return the active special schedule only when calendar-valid for this session."""
        sched = self._special_session_schedule
        if sched is None:
            return None
        if str(sched.session_date).strip() != str(self.session_date).strip():
            return None
        try:
            day = date.fromisoformat(str(self.session_date).strip())
        except ValueError:
            return None
        if not is_special_session_day(day):
            return None
        # Schedule is active only when date matches, day is special, and schedule validates.
        if sched.validation_error() is not None:
            return None
        # Session-date must match the IST clock day (same rule as entry calendar).
        now = self._now_ist()
        if now.astimezone(_SESSION_TZ).date() != day:
            return None
        return sched

    def _session_gate_minutes(self, key: str, default_hhmm: float) -> Optional[int]:
        """Cutoff / square-off minutes: special schedule overrides normal-day admin times.

        Returns None when the configured HHMM is malformed (caller must block safely).
        """
        sched = self._configured_special_schedule()
        if sched is not None:
            raw = (
                sched.entry_cutoff_ist
                if key == "entry_cutoff_ist"
                else sched.square_off_ist
            )
            return parse_hhmm(raw)
        admin = self._admin_store.load_effective_payload()
        minutes = parse_hhmm(admin.get(key, default_hhmm))
        if minutes is not None:
            return minutes
        return parse_hhmm(default_hhmm)

    def _admin_session_gate_block_reason(self) -> Optional[str]:
        """Block new entries when normal-session Admin HHMM gates are malformed."""
        if self._configured_special_schedule() is not None:
            return None
        admin = self._admin_store.load_effective_payload()
        err = validate_session_gate_hhmm_pair(
            admin.get("entry_cutoff_ist", DEFAULT_ENTRY_CUTOFF_IST_HHMM),
            admin.get("square_off_ist", DEFAULT_SQUARE_OFF_IST_HHMM),
        )
        if err is None:
            return None
        return "session_gate_invalid"

    def _ist_minutes_now(self) -> int:
        now = self._now_ist()
        return now.hour * 60 + now.minute

    def entry_cutoff_reached(self) -> bool:
        """True at/after configured entry_cutoff_ist on a valid session calendar day."""
        if self._entry_calendar_block_reason() is not None:
            return True
        if self._admin_session_gate_block_reason() is not None:
            return True
        gate = self._session_gate_minutes(
            "entry_cutoff_ist", DEFAULT_ENTRY_CUTOFF_IST_HHMM
        )
        if gate is None:
            return True
        return self._ist_minutes_now() >= gate

    def square_off_reached(self) -> bool:
        """True at/after configured square_off_ist (management continues regardless)."""
        gate = self._session_gate_minutes(
            "square_off_ist", DEFAULT_SQUARE_OFF_IST_HHMM
        )
        if gate is None:
            return False
        return self._ist_minutes_now() >= gate

    def _entry_calendar_block_reason(self) -> Optional[str]:
        return entry_calendar_block_reason(
            self.session_date,
            self._now_ist(),
            special_session_schedule=self._special_session_schedule,
        )

    def _new_entry_block_reason(self) -> Optional[str]:
        """Combined gate for new entry risk (pause, calendar, open, cutoff)."""
        if self._entries_paused():
            return "entries_paused"
        cal = self._entry_calendar_block_reason()
        if cal is not None:
            return cal
        admin_gate = self._admin_session_gate_block_reason()
        if admin_gate is not None:
            return admin_gate
        sched = self._configured_special_schedule()
        if sched is not None:
            open_m = parse_hhmm(sched.session_open_ist)
            if open_m is None:
                return "special_session_schedule_incomplete"
            if self._ist_minutes_now() < open_m:
                return "special_session_not_open"
        gate = self._session_gate_minutes(
            "entry_cutoff_ist", DEFAULT_ENTRY_CUTOFF_IST_HHMM
        )
        if gate is None or self._ist_minutes_now() >= gate:
            return "entry_cutoff"
        return None

    def enforce_entry_session_calendar(self) -> None:
        """Invalid/unconfigured sessions: pause new entries; keep managing exposure."""
        reason = self._entry_calendar_block_reason()
        if reason is None:
            return
        if self._entries_paused():
            return
        try:
            pause_store = AdminConfigStore(Path(self._admin_store.db_path), read_only=False)
            pause_store.set_entries_paused(True)
            pause_store.append_control_log(
                actor_username="engine",
                action="pause_entries",
                result="ok",
                detail=reason,
                version_id=pause_store.active_version_id(),
            )
            pause_store.close()
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"session_calendar_pause_failed:{exc}"
            return
        self.consume_new_triggers = False
        self.store.set_consume_triggers(self.run_id, False)
        self._cancel_pending_vwap_on_pause()

    def enforce_entry_cutoff(self) -> None:
        """Past entry cutoff → pause new entries (management continues)."""
        if self._entry_calendar_block_reason() is not None:
            # Calendar gate owns pause; do not also stamp cutoff.
            return
        if not self.entry_cutoff_reached():
            return
        if self._entries_paused():
            return
        try:
            pause_store = AdminConfigStore(Path(self._admin_store.db_path), read_only=False)
            pause_store.set_entries_paused(True)
            pause_store.append_control_log(
                actor_username="engine",
                action="pause_entries",
                result="ok",
                detail="entry_cutoff",
                version_id=pause_store.active_version_id(),
            )
            pause_store.close()
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"entry_cutoff_pause_failed:{exc}"
            return
        self.consume_new_triggers = False
        self.store.set_consume_triggers(self.run_id, False)
        self._cancel_pending_vwap_on_pause()

    def enforce_square_off(self) -> None:
        """Calendar square-off: durable flatten of remaining engine-managed exposure."""
        if not self.square_off_reached():
            return
        if not self._entries_paused():
            try:
                pause_store = AdminConfigStore(
                    Path(self._admin_store.db_path), read_only=False
                )
                pause_store.set_entries_paused(True)
                pause_store.append_control_log(
                    actor_username="engine",
                    action="pause_entries",
                    result="ok",
                    detail="square_off",
                    version_id=pause_store.active_version_id(),
                )
                pause_store.close()
            except Exception:  # noqa: BLE001
                pass
            self.consume_new_triggers = False
            self.store.set_consume_triggers(self.run_id, False)
        # Serialized: one trade at a time through the durable exit path.
        for trade in self.store.list_trades(self.session_date):
            if trade.status in {"closed", "skipped", "rejected"}:
                continue
            if int(trade.remaining_position_qty or 0) <= 0 and int(
                trade.remaining_entry_qty or 0
            ) <= 0:
                continue
            self._request_market_exit(
                trade, reason="square_off", kind="square_off", actor="engine"
            )

    def close_position(self, trade_id: str, *, actor: str = "user") -> None:
        trade = self.store.get_trade(trade_id)
        if trade is None:
            return
        self.store.append_event(
            trade_id, "close_position_requested", actor=actor
        )
        self._request_market_exit(
            trade, reason="close_position", kind="user_close", actor=actor
        )

    def close_all(self, *, actor: str = "user") -> None:
        """Pause entries and serially liquidate engine-managed positions."""
        try:
            pause_store = AdminConfigStore(Path(self._admin_store.db_path), read_only=False)
            pause_store.set_entries_paused(True)
            pause_store.append_control_log(
                actor_username=actor,
                action="pause_entries",
                result="ok",
                detail="close_all",
                version_id=pause_store.active_version_id(),
            )
            pause_store.close()
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"close_all_pause_failed:{exc}"
        self.consume_new_triggers = False
        self.store.set_consume_triggers(self.run_id, False)
        self._cancel_pending_vwap_on_pause()
        for trade in self.store.list_trades(self.session_date):
            if trade.status in {"closed", "skipped", "rejected"}:
                continue
            if int(trade.remaining_position_qty or 0) <= 0 and int(
                trade.remaining_entry_qty or 0
            ) <= 0:
                continue
            self.store.append_event(
                trade.trade_id, "close_all_requested", actor=actor
            )
            self._request_market_exit(
                trade, reason="close_all", kind="close_all", actor=actor
            )

    def _request_market_exit(
        self,
        trade: TradeRecord,
        *,
        reason: str,
        kind: str,
        actor: str = "engine",
    ) -> None:
        """Start or resume a durable market exit; never compete with an in-flight exit."""
        active = self._active_exit_reason_kind(trade)
        if active is not None:
            self.store.append_event(
                trade.trade_id,
                "exit_reason_deferred",
                actor=actor,
                payload={
                    "requested_reason": reason,
                    "requested_kind": kind,
                    "active_reason": active[0],
                    "active_kind": active[1],
                },
            )
            self._durable_market_exit(trade, reason=active[0], kind=active[1])
            return
        self._durable_market_exit(trade, reason=reason, kind=kind)

    def _active_exit_reason_kind(
        self, trade: TradeRecord
    ) -> Optional[tuple[str, str]]:
        for reason, kind in (
            ("protection_deadline", "emergency"),
            ("trail_through_exit", "trail_through"),
            ("close_position", "user_close"),
            ("close_all", "close_all"),
            ("square_off", "square_off"),
        ):
            if not self._has_exit_intent(trade.trade_id, reason=reason):
                continue
            linked = self._linked_exit_order_id(trade, kind=kind)
            cleared = self._exit_submit_cleared(
                trade.trade_id, reason=reason, kind=kind
            )
            rem = int(trade.remaining_position_qty or 0)
            if linked is None:
                if cleared:
                    continue
                return reason, kind
            polled = self.broker.poll_order(linked)
            if polled is None:
                return reason, kind
            status = str(polled.status).upper()
            pending = broker_order_pending_qty(polled)
            filled = broker_order_filled_qty(polled)
            if status in ENTRY_WORKING or (
                status in SL_TRIGGERED_WORKING and pending > 0
            ):
                return reason, kind
            if status in ENTRY_COMPLETE and rem > 0:
                return reason, kind
            # Terminal reject/cancel: residual may retry after clear.
            if status in {"REJECTED", "CANCELLED"} and rem > 0 and cleared:
                continue
            if rem > 0 and filled >= 0 and not cleared:
                return reason, kind
        return None

    def _exit_submit_cleared(
        self, trade_id: str, *, reason: str, kind: str
    ) -> bool:
        """True when the latest matching attempt was cleared after terminal fail."""
        last_attempt = -1
        last_clear = -1
        for idx, row in enumerate(self.store.list_events(trade_id)):
            action = str(row["action"])
            payload = self._parse_event_payload(row)
            if action in {"exit_submit_attempt", "exit_submission_unknown"}:
                if (
                    str(payload.get("reason") or "") == reason
                    or str(payload.get("kind") or "") == kind
                ):
                    last_attempt = idx
            if action == "exit_submit_cleared":
                if (
                    str(payload.get("reason") or "") == reason
                    or str(payload.get("kind") or "") == kind
                ):
                    last_clear = idx
        return last_clear > last_attempt >= 0

    def _protection_deadline_seconds(self) -> float:
        admin = self._admin_store.load_effective_payload()
        return float(
            admin.get(
                "protection_confirm_deadline_seconds",
                DEFAULT_PROTECTION_CONFIRM_DEADLINE_SECONDS,
            )
        )

    def _remainder_cancel_seconds(self) -> float:
        admin = self._admin_store.load_effective_payload()
        return float(
            admin.get(
                "entry_remainder_cancel_seconds",
                DEFAULT_ENTRY_REMAINDER_CANCEL_SECONDS,
            )
        )

    def _deadline_iso(self, *, seconds: float) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=float(seconds))).isoformat(
            timespec="seconds"
        )

    def _iso_expired(self, raw: Optional[str]) -> bool:
        if not raw:
            return False
        try:
            deadline = datetime.fromisoformat(str(raw))
        except ValueError:
            return False
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= deadline.astimezone(timezone.utc)

    def _parse_event_payload(self, row: Any) -> dict[str, Any]:
        raw = row["payload_json"] if "payload_json" in row.keys() else None
        if not raw:
            return {}
        try:
            data = json.loads(str(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _entry_submission_anchor(self, trade: TradeRecord) -> Optional[datetime]:
        """Clock for remainder cancel: persisted submission intent, not fill time."""
        if trade.entry_submitted_at:
            try:
                started = datetime.fromisoformat(str(trade.entry_submitted_at))
            except ValueError:
                started = None
            else:
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                return started.astimezone(timezone.utc)
        for row in self.store.list_events(trade.trade_id):
            if str(row["action"]) != "entry_intent":
                continue
            payload = self._parse_event_payload(row)
            stamp = payload.get("submitted_at") or row["at"]
            try:
                started = datetime.fromisoformat(str(stamp))
            except ValueError:
                continue
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            return started.astimezone(timezone.utc)
        return None

    def _maybe_freeze_r_fields(self, trade: TradeRecord) -> dict[str, Any]:
        """Freeze R only after entry completion or confirmed remainder cancellation."""
        if trade.r_value is not None:
            return {}
        if int(trade.remaining_entry_qty or 0) > 0:
            return {}
        if int(trade.filled_qty or 0) <= 0:
            return {}
        entry = float(trade.entry_fill or trade.entry_estimate or 0)
        stop = float(trade.initial_stop or trade.current_stop or 0)
        if entry <= 0 or stop <= 0:
            return {}
        return {"r_value": freeze_r_value(entry=entry, initial_stop=stop)}

    def _protection_state_fields(
        self, trade: TradeRecord, *, next_status: str
    ) -> dict[str, Any]:
        """Stamp protection deadline / clear it / freeze R when entry is complete."""
        fields: dict[str, Any] = {}
        if next_status == "protection_pending":
            if not trade.protection_deadline_at:
                fields["protection_deadline_at"] = self._deadline_iso(
                    seconds=self._protection_deadline_seconds()
                )
        elif next_status in {"protected_open", "partial_exit", "partial_entry"}:
            fields["protection_deadline_at"] = None
            fields.update(self._maybe_freeze_r_fields(trade))
            if (
                next_status == "protected_open"
                and not bool(trade.auto_trail_enabled)
                and not bool(trade.auto_trail_owner_disabled)
            ):
                fields["auto_trail_enabled"] = 1
                mark = self._last_price(trade.symbol)
                if mark is not None:
                    fields["auto_trail_extreme"] = float(mark)
        return fields

    def enforce_entry_remainder_cancels(self) -> None:
        """Cancel working entry remainders after submission-intent deadline."""
        limit = self._remainder_cancel_seconds()
        for trade in self.store.list_trades(self.session_date):
            rem = int(trade.remaining_entry_qty or 0)
            if rem <= 0 or not trade.entry_order_id:
                continue
            if trade.status in {"closed", "skipped", "rejected"}:
                continue
            started = self._entry_submission_anchor(trade)
            if started is None:
                continue
            age = (datetime.now(timezone.utc) - started).total_seconds()
            if age < float(limit):
                continue
            self.store.append_event(
                trade.trade_id,
                "entry_remainder_cancel_deadline",
                payload={
                    "age_seconds": age,
                    "limit_seconds": limit,
                    "anchor": "entry_submitted_at",
                },
            )
            self._cancel_entry_remainder(trade)

    def enforce_protection_deadlines(self) -> None:
        """Unprotected past deadline → pause entries + durable emergency flatten."""
        for trade in self.store.list_trades(self.session_date):
            if trade.status in {"closed", "skipped", "rejected"}:
                continue
            pos = int(trade.remaining_position_qty or 0)
            if pos <= 0:
                continue
            if not is_unprotected(trade):
                continue
            deadline = trade.protection_deadline_at
            if not deadline:
                self.store.update_trade(
                    trade.trade_id,
                    protection_deadline_at=self._deadline_iso(
                        seconds=self._protection_deadline_seconds()
                    ),
                )
                continue
            if not self._iso_expired(deadline):
                continue
            self._protection_deadline_breach(trade)

    def _protection_deadline_breach(self, trade: TradeRecord) -> None:
        self.store.append_event(
            trade.trade_id,
            "protection_deadline_breach",
            payload={"deadline_at": trade.protection_deadline_at},
        )
        try:
            pause_store = AdminConfigStore(Path(self._admin_store.db_path), read_only=False)
            pause_store.set_entries_paused(True)
            pause_store.append_control_log(
                actor_username="engine",
                action="pause_entries",
                result="ok",
                detail="protection_deadline_breach",
                version_id=pause_store.active_version_id(),
            )
            pause_store.close()
        except Exception as exc:  # noqa: BLE001
            self.store.append_event(
                trade.trade_id,
                "error",
                payload={"reason": f"pause_failed:{exc}"},
            )
        self.consume_new_triggers = False
        self._durable_market_exit(
            trade,
            reason="protection_deadline",
            kind="emergency",
        )

    def _exit_order_tag(self, trade: TradeRecord, kind: str) -> str:
        base = str(trade.broker_tag or "NR")[:12]
        suffix = {
            "emergency": "E",
            "trail_through": "T",
            "user_close": "C",
            "close_all": "A",
            "square_off": "S",
        }.get(kind, "X")
        return f"{base}{suffix}"[:20]

    def _has_exit_intent(self, trade_id: str, *, reason: str) -> bool:
        for row in self.store.list_events(trade_id):
            if str(row["action"]) != "exit_intent":
                continue
            payload = self._parse_event_payload(row)
            if str(payload.get("reason") or "") == reason:
                return True
        return False

    def _has_exit_submit_attempt(
        self, trade_id: str, *, reason: str, kind: str
    ) -> bool:
        if self._exit_submit_cleared(trade_id, reason=reason, kind=kind):
            return False
        for row in self.store.list_events(trade_id):
            action = str(row["action"])
            if action not in {"exit_submit_attempt", "exit_submission_unknown"}:
                continue
            payload = self._parse_event_payload(row)
            if str(payload.get("reason") or "") == reason:
                return True
            if str(payload.get("kind") or "") == kind:
                return True
        return False

    def _blocking_exit_in_progress(self, trade: TradeRecord) -> bool:
        """Exit intent active without attributed exit — block re-protect / external close."""
        if int(trade.remaining_position_qty or 0) <= 0:
            return False
        return self._active_exit_reason_kind(trade) is not None

    def _linked_exit_order_id(self, trade: TradeRecord, *, kind: str) -> Optional[str]:
        """Return the active exit order for ``kind``, never an older failed attempt."""
        if trade.active_exit_order_id and str(trade.active_exit_kind or "") == kind:
            return str(trade.active_exit_order_id)
        # Fallback: newest matching link (oldest-first listing would resurrect failures).
        newest: Optional[str] = None
        for link in self.store.list_order_links(trade.trade_id):
            if str(link["role"]) != "exit":
                continue
            if str(link["attribution_kind"] or "") == kind:
                newest = str(link["order_id"])
        return newest

    def _set_active_exit_order(
        self, trade: TradeRecord, *, order_id: str, kind: str
    ) -> TradeRecord:
        updated = self.store.update_trade(
            trade.trade_id,
            active_exit_order_id=str(order_id),
            active_exit_kind=str(kind),
        )
        return updated

    def _clear_active_exit_order(
        self, trade: TradeRecord, *, order_id: Optional[str] = None
    ) -> TradeRecord:
        if order_id is not None and trade.active_exit_order_id:
            if str(trade.active_exit_order_id) != str(order_id):
                return trade
        updated = self.store.update_trade(
            trade.trade_id,
            active_exit_order_id=None,
            active_exit_kind=None,
        )
        return updated

    def _cancel_stop_confirmed(self, trade: TradeRecord) -> str:
        """Cancel working stop and account cancel-race fills. Returns terminal state class.

        Returns one of: ``cancelled``, ``filled``, ``still_working``, ``absent``,
        ``visibility_unknown``.

        ``absent`` means no stop id is recorded (never placed / already cleared).
        ``visibility_unknown`` means a known ``sl_order_id`` cannot be polled — that
        stop may still execute, so a competing flatten must not proceed.
        """
        if not trade.sl_order_id:
            return "absent"
        oid = str(trade.sl_order_id)
        try:
            cancelled = self.broker.cancel_order(oid)
        except Exception as exc:  # noqa: BLE001
            self.store.append_event(
                trade.trade_id,
                "error",
                payload={"reason": f"stop_cancel_failed:{exc}"},
            )
            cancelled = self.broker.poll_order(oid)
        polled = cancelled if cancelled is not None else self.broker.poll_order(oid)
        if polled is None:
            self.store.append_event(
                trade.trade_id,
                "stop_visibility_unknown",
                payload={"order_id": oid},
            )
            return "visibility_unknown"
        status = str(polled.status).upper()
        # Persist any cancel-time stop fills through the durable stop path.
        if broker_order_filled_qty(polled) > 0 or status in SL_FILLED:
            refreshed = self.store.get_trade(trade.trade_id)
            if refreshed is not None:
                self._apply_sl_order(refreshed, polled)
            if status in SL_FILLED:
                return "filled"
            if status in SL_CANCELLED:
                return "cancelled"
            return "still_working"
        if status in SL_CANCELLED:
            self.store.update_trade(
                trade.trade_id,
                protected_qty=0,
                sl_order_id=None,
                status=(
                    "stop_pending"
                    if int(trade.remaining_position_qty or 0) > 0
                    else trade.status
                ),
            )
            self.store.append_event(trade.trade_id, "sl_cancelled")
            return "cancelled"
        self.store.append_event(
            trade.trade_id,
            "stop_cancel_unconfirmed",
            payload={"status": status, "order_id": oid},
        )
        return "still_working"

    def resume_pending_market_exits(self) -> None:
        """Continue durable emergency/trail-through exits after intent is persisted."""
        for trade in self.store.list_trades(self.session_date):
            if trade.status in {"closed", "skipped", "rejected"}:
                continue
            rem_entry = int(trade.remaining_entry_qty or 0)
            if (
                int(trade.remaining_position_qty or 0) <= 0
                and rem_entry <= 0
                and self._broker_is_flat(trade)
            ):
                continue
            for reason, kind in (
                ("protection_deadline", "emergency"),
                ("trail_through_exit", "trail_through"),
                ("close_position", "user_close"),
                ("close_all", "close_all"),
                ("square_off", "square_off"),
            ):
                if self._has_exit_intent(trade.trade_id, reason=reason):
                    self._durable_market_exit(trade, reason=reason, kind=kind)

    def _durable_market_exit(
        self,
        trade: TradeRecord,
        *,
        reason: str,
        kind: str,
    ) -> None:
        """Emergency/trail-through exit: intent → confirmed stop cancel → port flatten.

        Exited qty advances only from durable confirmed fills (never assumes full size).
        Exit market writes require a fresh ``exit_submit_attempt``; empty broker lookup
        after an attempt means reconcile-wait, never permission to resubmit.
        Working entry remainders (including zero-fill) are cancelled before completion.
        """
        refreshed = self.store.get_trade(trade.trade_id)
        trade = refreshed if refreshed is not None else trade
        if trade.status in {"closed", "skipped", "rejected"}:
            return
        pos = int(trade.remaining_position_qty or 0)

        if not self._has_exit_intent(trade.trade_id, reason=reason):
            self.store.append_event(
                trade.trade_id,
                "exit_intent",
                payload={
                    "reason": reason,
                    "kind": kind,
                    "qty": pos,
                    "symbol": trade.symbol,
                    "side": _stop_side(trade.direction),
                    "remaining_entry_qty": int(trade.remaining_entry_qty or 0),
                },
            )
            self.store.update_trade(trade.trade_id, status="exit_pending", protected_qty=0)

        # Always cancel working entries before declaring flat/complete — even when
        # local position qty is already zero (zero-fill working entry).
        if int(trade.remaining_entry_qty or 0) > 0:
            self._cancel_entry_remainder(trade)
            refreshed = self.store.get_trade(trade.trade_id)
            trade = refreshed if refreshed is not None else trade

        rem_entry = int(trade.remaining_entry_qty or 0)
        if rem_entry > 0:
            self.store.update_trade(
                trade.trade_id,
                status="exit_pending",
                protected_qty=0,
            )
            self.store.append_event(
                trade.trade_id,
                "entry_remainder_cancel_unconfirmed",
                payload={"remaining_entry_qty": rem_entry, "reason": reason},
            )
            return

        pos = int(trade.remaining_position_qty or 0)
        if pos <= 0 and self._broker_is_flat(trade) and int(trade.filled_qty or 0) <= 0:
            # Zero-fill entry cancelled — close-out complete with no exposure.
            self._clear_active_exit_order(trade)
            self.store.update_trade(
                trade.trade_id,
                status="skipped",
                skip_reason=reason,
                qty=0,
                intended_qty=0,
                filled_qty=0,
                exited_qty=0,
                remaining_entry_qty=0,
                remaining_position_qty=0,
                protected_qty=0,
                notional=0,
                margin_blocked=0,
                qty_model_version=1,
            )
            self.store.append_event(
                trade.trade_id,
                "skipped",
                payload={"reason": reason, "phase": "zero_fill_entry_cancelled"},
            )
            return

        if trade.sl_order_id:
            stop_state = self._cancel_stop_confirmed(trade)
            refreshed = self.store.get_trade(trade.trade_id)
            trade = refreshed if refreshed is not None else trade
            if stop_state in {"still_working", "visibility_unknown"}:
                self.store.update_trade(
                    trade.trade_id,
                    status="reconciliation_required",
                    protected_qty=0,
                )
                return
            if stop_state == "filled" and int(trade.remaining_position_qty or 0) <= 0:
                return

        refreshed = self.store.get_trade(trade.trade_id)
        trade = refreshed if refreshed is not None else trade
        pos = int(trade.remaining_position_qty or 0)
        if pos <= 0:
            if self._broker_is_flat(trade):
                exit_updates = self._sync_exit_values(trade)
                exited = max(
                    int(trade.exited_qty or 0),
                    int(exit_updates.get("exited_qty", 0) or 0),
                )
                if exited > 0 and self._exit_prices_fully_confirmed(trade):
                    self._close_trade(
                        trade,
                        float(trade.exit_fill or trade.current_stop or trade.entry_fill or 0),
                        reason,
                        exited_qty=exited,
                        filled_qty=int(trade.filled_qty or 0),
                    )
            return

        exit_tag = self._exit_order_tag(trade, kind)
        existing_oid = self._linked_exit_order_id(trade, kind=kind)
        order: Any = None
        cleared = self._exit_submit_cleared(
            trade.trade_id, reason=reason, kind=kind
        )
        if existing_oid:
            order = self.broker.poll_order(existing_oid)
            if order is None:
                # Known exit order id with lost visibility — wait; do not resubmit.
                self.store.update_trade(
                    trade.trade_id,
                    status="reconciliation_required",
                    protection_deadline_at=None,
                    protected_qty=0,
                )
                self.store.append_event(
                    trade.trade_id,
                    "exit_reconcile_waiting",
                    payload={
                        "reason": reason,
                        "kind": kind,
                        "order_id": existing_oid,
                        "tag": exit_tag,
                    },
                )
                return
            st_existing = str(order.status).upper()
            if st_existing in {"REJECTED", "CANCELLED"} and cleared:
                # Prior attempt exhausted — allow a new market write for residual.
                if (
                    trade.active_exit_order_id
                    and str(trade.active_exit_order_id) == str(existing_oid)
                ):
                    trade = self._clear_active_exit_order(trade, order_id=existing_oid)
                order = None
            elif st_existing in {"REJECTED", "CANCELLED"} and not cleared:
                # Still need terminal handling below (may clear + residual).
                pass
        if order is None:
            tagged = [
                o
                for o in self.broker.orders_by_tag(exit_tag)
                if str(o.order_type).upper() == "MARKET"
                and str(o.status).upper() not in {"REJECTED", "CANCELLED"}
            ]
            order = tagged[0] if tagged else None
            if order is not None:
                trade = self._set_active_exit_order(
                    trade, order_id=str(order.order_id), kind=kind
                )

        # Broker already flat with an outstanding submit attempt and no visible exit
        # order yet → wait (do not invent an external close / re-protect).
        if (
            order is None
            and self._broker_is_flat(trade)
            and self._has_exit_submit_attempt(trade.trade_id, reason=reason, kind=kind)
            and max(int(trade.exited_qty or 0), self._broker_exit_filled_total(trade))
            <= 0
        ):
            self.store.update_trade(
                trade.trade_id,
                status="reconciliation_required",
                protected_qty=0,
                protection_deadline_at=None,
            )
            self.store.append_event(
                trade.trade_id,
                "exit_reconcile_waiting",
                payload={
                    "reason": reason,
                    "kind": kind,
                    "tag": exit_tag,
                    "broker_flat": True,
                },
            )
            return

        if order is None and self._broker_is_flat(trade):
            exited_seen = max(
                int(trade.exited_qty or 0), self._broker_exit_filled_total(trade)
            )
            self._handle_protective_exit(
                trade,
                exit_px=float(
                    trade.exit_fill or trade.current_stop or trade.entry_fill or 0
                ),
                exit_qty=max(0, exited_seen - int(trade.exited_qty or 0)),
                reason=reason,
                exited_qty_absolute=exited_seen,
                stop_complete=True,
            )
            return

        if order is None:
            if self._has_exit_submit_attempt(
                trade.trade_id, reason=reason, kind=kind
            ):
                # Prior submit attempt with empty lookup → reconcile-wait only.
                self.store.update_trade(
                    trade.trade_id,
                    status="reconciliation_required",
                    protection_deadline_at=None,
                )
                self.store.append_event(
                    trade.trade_id,
                    "exit_reconcile_waiting",
                    payload={
                        "reason": reason,
                        "kind": kind,
                        "tag": exit_tag,
                        "qty": pos,
                    },
                )
                return
            # Persist submission attempt before any broker write.
            self.store.append_event(
                trade.trade_id,
                "exit_submit_attempt",
                payload={
                    "reason": reason,
                    "kind": kind,
                    "tag": exit_tag,
                    "qty": pos,
                    "side": _stop_side(trade.direction),
                    "symbol": trade.symbol,
                },
            )
            self.store.update_trade(
                trade.trade_id,
                status="exit_pending",
                protected_qty=0,
                protection_deadline_at=None,
            )
            try:
                order = self.broker.flatten_mis(
                    tradingsymbol=trade.symbol,
                    transaction_type=_stop_side(trade.direction),
                    quantity=pos,
                    tag=exit_tag,
                )
            except Exception as exc:  # noqa: BLE001
                self.store.update_trade(
                    trade.trade_id,
                    status="reconciliation_required",
                    protection_deadline_at=None,
                )
                self.store.append_event(
                    trade.trade_id,
                    "exit_submission_unknown",
                    payload={
                        "reason": reason,
                        "kind": kind,
                        "tag": exit_tag,
                        "error": str(exc),
                    },
                )
                return
        status = str(order.status).upper()
        # Historical failures must not clear or replace a newer active attempt.
        if status in {"REJECTED", "CANCELLED"}:
            active_oid = trade.active_exit_order_id
            if active_oid is not None and str(active_oid) != str(order.order_id):
                self.store.append_event(
                    trade.trade_id,
                    "exit_stale_terminal_ignored",
                    payload={
                        "reason": reason,
                        "kind": kind,
                        "order_id": order.order_id,
                        "active_exit_order_id": active_oid,
                        "status": status,
                    },
                )
                return
            self._link_order(trade, str(order.order_id), role="exit", kind=kind)
            filled = broker_order_filled_qty(order)
            if filled > 0:
                prev_exited = int(trade.exited_qty or 0)
                self._broker_exit_execution_values(trade)
                exited_total = max(
                    prev_exited, self._broker_exit_filled_total(trade), filled
                )
                exit_px = float(
                    order.average_price
                    or self._last_price(trade.symbol)
                    or trade.entry_fill
                    or trade.entry_estimate
                    or 0
                )
                self._handle_protective_exit(
                    trade,
                    exit_px=exit_px,
                    exit_qty=max(0, exited_total - prev_exited),
                    reason=reason,
                    exited_qty_absolute=exited_total,
                    stop_complete=True,
                    stop_order=order,
                )
            self.store.append_event(
                trade.trade_id,
                "exit_order_terminal_failed",
                payload={
                    "reason": reason,
                    "kind": kind,
                    "order_id": order.order_id,
                    "status": status,
                    "filled": filled,
                },
            )
            # Allow a fresh submit for any residual exposure.
            self.store.append_event(
                trade.trade_id,
                "exit_submit_cleared",
                payload={"reason": reason, "kind": kind, "tag": exit_tag},
            )
            self._clear_active_exit_order(trade, order_id=str(order.order_id))
            refreshed = self.store.get_trade(trade.trade_id)
            if refreshed is not None and int(refreshed.remaining_position_qty or 0) > 0:
                self.store.update_trade(
                    refreshed.trade_id,
                    status="reconciliation_required",
                    protection_deadline_at=None,
                    protected_qty=0,
                )
            return

        self._link_order(trade, str(order.order_id), role="exit", kind=kind)
        trade = self._set_active_exit_order(
            trade, order_id=str(order.order_id), kind=kind
        )
        filled = broker_order_filled_qty(order)
        pending = broker_order_pending_qty(order)
        complete = status in ENTRY_COMPLETE and pending <= 0
        if filled <= 0 and not complete:
            self.store.update_trade(
                trade.trade_id,
                status="exit_pending" if status in ENTRY_WORKING else "reconciliation_required",
                protection_deadline_at=None,
                active_exit_order_id=str(order.order_id),
                active_exit_kind=kind,
            )
            self.store.append_event(
                trade.trade_id,
                "exit_partial_or_unreconciled",
                payload={
                    "reason": reason,
                    "exit_qty": 0,
                    "order_id": order.order_id,
                    "status": status,
                    "pending": pending,
                },
            )
            return
        # Advance only by confirmed fills; sync durable link snapshot.
        prev_exited = int(trade.exited_qty or 0)
        self._broker_exit_execution_values(trade)
        exited_total = max(prev_exited, self._broker_exit_filled_total(trade), filled)
        exit_px = float(
            order.average_price
            or self._last_price(trade.symbol)
            or trade.entry_fill
            or trade.entry_estimate
            or 0
        )
        self._handle_protective_exit(
            trade,
            exit_px=exit_px,
            exit_qty=max(0, exited_total - prev_exited),
            reason=reason,
            exited_qty_absolute=exited_total,
            stop_complete=complete,
            stop_order=order if complete else None,
        )
        if complete:
            refreshed = self.store.get_trade(trade.trade_id)
            if refreshed is not None:
                self._clear_active_exit_order(refreshed, order_id=str(order.order_id))

    def apply_auto_trails(self) -> None:
        for trade in self.store.list_trades(self.session_date):
            if not trade.auto_trail_enabled:
                continue
            if bool(trade.auto_trail_owner_disabled):
                continue
            if trade.status == "protected_open":
                pass
            elif trade.status == "partial_entry" and int(trade.protected_qty or 0) > 0:
                pass
            else:
                continue
            if trade.current_stop is None or trade.initial_stop is None:
                continue
            # Staged-R requires frozen R after entry completion / remainder cancel.
            if trade.r_value is None or int(trade.remaining_entry_qty or 0) > 0:
                continue
            entry = float(trade.entry_fill or trade.entry_estimate or 0)
            if entry <= 0:
                continue
            mark = self._last_price(trade.symbol)
            if mark is None:
                continue
            r_value = float(trade.r_value)
            if r_value <= 1e-12:
                continue
            extreme = update_trail_extreme(
                direction=trade.direction,
                last_price=float(mark),
                current_extreme=(
                    float(trade.auto_trail_extreme)
                    if trade.auto_trail_extreme is not None
                    else None
                ),
            )
            charge_bps, _ = trade_cost_profile(trade)
            desired = staged_r_desired_stop(
                direction=trade.direction,
                entry=entry,
                initial_stop=float(trade.initial_stop),
                current_stop=float(trade.current_stop),
                extreme=extreme,
                last_price=float(mark),
                r_value=r_value,
                charge_bps=charge_bps,
                tick_size=trade.tick_size,
            )
            aligned = _align_stop(desired, trade.tick_size)
            self.store.update_trade(
                trade.trade_id,
                auto_trail_extreme=extreme,
            )
            if trail_crosses_last_price(
                direction=trade.direction, new_stop=aligned, last_price=mark
            ):
                self._durable_market_exit(
                    trade,
                    reason="trail_through_exit",
                    kind="trail_through",
                )
                continue
            if not trail_is_tighten_only(
                direction=trade.direction,
                current_stop=float(trade.current_stop),
                new_stop=aligned,
            ):
                continue
            improve = trail_improvement_ticks(
                direction=trade.direction,
                current_stop=float(trade.current_stop),
                new_stop=aligned,
                tick_size=trade.tick_size,
            )
            if improve < TRAIL_MIN_IMPROVEMENT_TICKS:
                continue
            if trade.last_trail_modify_at:
                try:
                    last = datetime.fromisoformat(str(trade.last_trail_modify_at))
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    age = (
                        datetime.now(timezone.utc) - last.astimezone(timezone.utc)
                    ).total_seconds()
                    if age < TRAIL_MIN_MODIFY_INTERVAL_SECONDS:
                        continue
                except ValueError:
                    pass
            # Stamp throttle before write so retracement during the interval keeps extreme stage.
            self.store.update_trade(
                trade.trade_id,
                last_trail_modify_at=_now(),
                auto_trail_extreme=extreme,
            )
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
                qty=int(trade.filled_qty or trade.qty or 0),
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
        if quote.quantity != 0 and abs(int(quote.quantity)) != int(trade.filled_qty or trade.qty):
            self.store.append_event(
                trade.trade_id,
                "qty_mismatch",
                payload={"net": quote.quantity, "engine_qty": int(trade.filled_qty or trade.qty)},
            )
        current = self.store.get_trade(trade.trade_id)
        if current is None:
            return
        if abs(float(pnl) - current.open_pnl) > 1e-9 or "entry_fill" in fields:
            self.store.update_trade(trade.trade_id, **fields)

    def _close_trade(
        self,
        trade: TradeRecord,
        exit_fill: float,
        reason: str,
        *,
        closed_qty: Optional[int] = None,
        exited_qty: Optional[int] = None,
        filled_qty: Optional[int] = None,
    ) -> None:
        if trade.status == "closed":
            return
        filled_keep = int(
            filled_qty if filled_qty is not None else (trade.filled_qty or trade.qty or 0)
        )
        exited_keep = int(
            exited_qty
            if exited_qty is not None
            else max(int(trade.exited_qty or 0), filled_keep)
        )
        close_qty = int(
            closed_qty
            if closed_qty is not None
            else (min(filled_keep, exited_keep) or exited_keep or filled_keep or trade.qty or 0)
        )
        # Authoritative per-order broker values replace stored estimates — never max().
        exit_updates = self._sync_exit_values(trade)
        entry_value = float(getattr(trade, "entry_value", 0) or 0)
        entry_value_est = float(getattr(trade, "entry_value_est", 0) or 0)
        if trade.entry_order_id and filled_keep > 0:
            polled_entry = self.broker.poll_order(trade.entry_order_id)
            if polled_entry is not None:
                entry_value, entry_value_est = self._entry_execution_values_from_order(
                    polled_entry,
                    filled_keep,
                    float(polled_entry.average_price or trade.entry_fill or trade.entry_estimate),
                )
        exit_value = float(exit_updates["exit_value"])
        exit_value_est = float(exit_updates.get("exit_value_est", 0) or 0)
        provisional = bool(int(exit_updates.get("pnl_provisional", 0) or 0)) or entry_value_est > 0
        pnl_qty = min(filled_keep, exited_keep) if filled_keep and exited_keep else close_qty

        if provisional:
            est_entry = entry_value + entry_value_est
            est_exit = exit_value + exit_value_est
            if est_entry > 0 and est_exit > 0 and pnl_qty > 0 and filled_keep > 0 and exited_keep > 0:
                entry_for_pnl = est_entry * (float(pnl_qty) / float(filled_keep))
                exit_for_pnl = est_exit * (float(pnl_qty) / float(exited_keep))
                pnl = realised_pnl_from_values(
                    direction=trade.direction,
                    entry_value=entry_for_pnl,
                    exit_value=exit_for_pnl,
                    qty=pnl_qty,
                )
                entry = entry_for_pnl / float(pnl_qty)
                exit_avg = exit_for_pnl / float(pnl_qty)
            else:
                entry = trade.entry_fill if trade.entry_fill is not None else trade.entry_estimate
                exit_avg = float(exit_fill)
                pnl = realised_pnl(
                    direction=trade.direction, qty=close_qty, entry_fill=entry, exit_fill=exit_avg
                )
            # Do not zero an existing confirmed loss while prices are unresolved.
            loss = float(getattr(trade, "closed_loss_contribution", 0) or 0)
        elif entry_value > 0 and exit_value > 0 and pnl_qty > 0:
            entry_for_pnl = (
                entry_value * (float(pnl_qty) / float(filled_keep)) if filled_keep else entry_value
            )
            exit_for_pnl = (
                exit_value * (float(pnl_qty) / float(exited_keep)) if exited_keep else exit_value
            )
            pnl = realised_pnl_from_values(
                direction=trade.direction,
                entry_value=entry_for_pnl,
                exit_value=exit_for_pnl,
                qty=pnl_qty,
            )
            entry = entry_for_pnl / float(pnl_qty)
            exit_avg = exit_for_pnl / float(pnl_qty)
            charge_bps, _ = trade_cost_profile(
                trade,
                charge_bps=float(
                    self._admin_store.load_effective_payload().get(
                        "round_trip_charge_bps", DEFAULT_ROUND_TRIP_CHARGE_BPS
                    )
                ),
            )
            loss = fold_exit_costs_into_loss(
                price_pnl=pnl,
                qty=pnl_qty,
                entry=float(entry),
                charge_bps=charge_bps,
            )
        else:
            entry = trade.entry_fill if trade.entry_fill is not None else trade.entry_estimate
            exit_avg = float(exit_fill)
            pnl = realised_pnl(
                direction=trade.direction, qty=close_qty, entry_fill=entry, exit_fill=exit_avg
            )
            provisional = True
            loss = float(getattr(trade, "closed_loss_contribution", 0) or 0)

        if (
            not provisional
            and reason in {"sl_hit", "external_exit", "reconciled_flat"}
            and self.live_orders_enabled
        ):
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
                    charge_bps, _ = trade_cost_profile(
                        trade,
                        charge_bps=float(
                            self._admin_store.load_effective_payload().get(
                                "round_trip_charge_bps", DEFAULT_ROUND_TRIP_CHARGE_BPS
                            )
                        ),
                    )
                    loss = fold_exit_costs_into_loss(
                        price_pnl=pnl,
                        qty=pnl_qty,
                        entry=float(entry or 0.0),
                        charge_bps=charge_bps,
                    )
        self.store.update_trade(
            trade.trade_id,
            status="closed",
            exit_fill=exit_avg,
            entry_fill=float(entry) if entry is not None else trade.entry_fill,
            entry_value=entry_value,
            entry_value_est=entry_value_est,
            exit_value=exit_value,
            exit_value_est=exit_value_est,
            pnl_provisional=1 if provisional else 0,
            close_reason=reason,
            close_time=_now(),
            realised_pnl=pnl,
            open_pnl=0.0,
            closed_loss_contribution=loss,
            margin_blocked=0.0,
            remaining_entry_qty=0,
            remaining_position_qty=0,
            protected_qty=0,
            filled_qty=filled_keep,
            exited_qty=max(exited_keep, filled_keep),
            qty_model_version=1,
        )
        self.store.append_event(
            trade.trade_id,
            "sl_filled" if reason == "sl_hit" else "closed",
            payload={
                "exit": exit_avg,
                "pnl": pnl,
                "reason": reason,
                "qty": pnl_qty,
                "filled_qty": filled_keep,
                "exited_qty": max(exited_keep, filled_keep),
                "entry_value": entry_value,
                "exit_value": exit_value,
                "entry_value_est": entry_value_est,
                "exit_value_est": exit_value_est,
                "pnl_provisional": provisional,
            },
        )

    def write_status(self) -> None:
        if self.status_file is None:
            return
        admin = self._admin_store.load_effective_payload()
        snap = snapshot_dict(
            self.store,
            session_date=self.session_date,
            total_capital=self.total_capital(),
            leverage_factor=self.leverage_factor,
            live_orders_enabled=self.live_orders_enabled,
            running=self.running,
            last_error=self.last_error,
            accepting_triggers=bool(self.running and not self._entries_paused()),
            require_vwap_accept=self.require_vwap_accept,
            daily_loss_cap=float(admin["daily_loss_cap_inr"]),
            per_trade_cap=float(admin["per_trade_risk_cap_inr"]),
        )
        snap["updated_at"] = _now()
        snap["running"] = self.running
        snap["consume_new_triggers"] = self.consume_new_triggers
        write_heartbeat(self.status_file, snap)
