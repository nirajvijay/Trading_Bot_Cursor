"""One engine cycle: ingest triggers, drive state machine, commands, heartbeat."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4
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
from trading_engine_broker import (
    EntryAcceptedVisibilityUnknown,
    BrokerOrder,
    BrokerPort,
    FakeBroker,
    SlPlaceAcceptedVisibilityUnknown,
    _is_stop_order,
    parse_timestamp_text,
)
from trading_engine_quotes import EntryLimitDecision, bounded_entry_limit
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
    open_notional_total,
    post_fill_risk_decision,
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
    DEFAULT_SESSION_OPEN_IST_HHMM,
    DEFAULT_SQUARE_OFF_IST_HHMM,
    DEMO_LEVERAGE_FACTOR,
    FEED_STALE_EXIT_SECONDS,
    FEED_STALE_PAUSE_SECONDS,
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
# Durable market-exit owners: (reason, kind). Shared by request serialization,
# active-exit ownership, and resume — keep a single source of truth.
DURABLE_MARKET_EXIT_OWNERS: tuple[tuple[str, str], ...] = (
    ("post_fill_risk_breach", "risk_breach"),
    ("protection_deadline", "emergency"),
    ("feed_stale", "emergency"),
    ("trail_through_exit", "trail_through"),
    ("close_position", "user_close"),
    ("close_all", "close_all"),
    ("square_off", "square_off"),
)

VWAP_WAIT_SECONDS = 2.0
VWAP_PENDING_RETRY_SECONDS = 0.25
VWAP_SKIP_BY_CLASS = {
    "LIMITED": "vwap_limited",
    "REJECT": "vwap_reject",
    "UNAVAILABLE": "vwap_unavailable",
}
# NSE cash session calendar day for attribution checks.
_SESSION_TZ = ZoneInfo("Asia/Kolkata")
# Durable orphan / recovery findings without a local trade row (no FK on events).
RECOVERY_EVENTS_TRADE_ID = "__recovery__"


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


def feed_age_seconds_from_runner_status(
    status_path: Optional[Path | str],
    *,
    now: Optional[datetime] = None,
    expected_session_date: Optional[str] = None,
) -> Optional[float]:
    """Age of observation ``last_tick_time`` in seconds, or None if unknown/invalid.

    Missing file, missing/invalid tick, wrong session, non-finite ages, and read
    errors all return None (callers must treat None as blocked during trading hours).
    """
    if status_path is None:
        return None
    path = Path(status_path)
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if expected_session_date is not None:
        file_session = data.get("session_date")
        if file_session is not None and str(file_session) != str(expected_session_date):
            return None
    tick_raw = data.get("last_tick_time")
    if tick_raw is None or str(tick_raw).strip() == "":
        return None
    try:
        tick = datetime.fromisoformat(str(tick_raw).strip())
    except ValueError:
        return None
    if tick.tzinfo is None:
        tick = tick.replace(tzinfo=_SESSION_TZ)
    clock = now if now is not None else datetime.now(_SESSION_TZ)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=_SESSION_TZ)
    age = (clock.astimezone(timezone.utc) - tick.astimezone(timezone.utc)).total_seconds()
    if not math.isfinite(age) or age < 0:
        return None
    return float(age)


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
        feed_age_seconds_fn: Optional[Callable[[], Optional[float]]] = None,
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
        self._feed_age_seconds_fn = feed_age_seconds_fn
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
        self._restart_recovery_done = False
        # Local entry lock survives pause-store failures (fail-closed for new risk).
        self._local_entries_lock = False
        self._local_entries_lock_reason: Optional[str] = None
        # Idempotent keys for recovery findings already durably surfaced this process.
        self._recovery_finding_keys: set[str] = set()
        self._postfill_active: set[str] = set()

    def total_capital(self) -> float:
        return self.store.get_total_capital(self.run_id)

    def _active_admin_snapshot(self):
        return self._admin_store.capture_snapshot()

    def _sync_pause_from_canonical(self) -> None:
        if self._local_entries_lock:
            # Fail-closed: never re-arm entries from canonical while local lock holds.
            self.consume_new_triggers = False
            self.store.set_consume_triggers(self.run_id, False)
            return
        paused = self._admin_store.read_entries_paused()
        consume = not paused
        if self.consume_new_triggers != consume:
            self.consume_new_triggers = consume
            self.store.set_consume_triggers(self.run_id, consume)

    def _entries_paused(self) -> bool:
        if self._local_entries_lock:
            return True
        return self._admin_store.read_entries_paused()

    def _engage_local_entries_lock(self, reason: str) -> None:
        self._local_entries_lock = True
        self._local_entries_lock_reason = reason
        self.consume_new_triggers = False
        self.store.set_consume_triggers(self.run_id, False)

    def _clear_local_entries_lock(self) -> None:
        self._local_entries_lock = False
        self._local_entries_lock_reason = None

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
        try:
            self.enforce_restart_recovery()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            self._engage_local_entries_lock("recovery_failed")
        try:
            self.enforce_postfill_risk()
        except Exception as exc:
            self.last_error = str(exc)
            self._engage_local_entries_lock("post_fill_risk_unknown")
        try:
            self.enforce_feed_staleness()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
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
            self.enforce_postfill_risk()
        except Exception as exc:
            self.last_error = str(exc)
            self._engage_local_entries_lock("post_fill_risk_unknown")
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
            risk_limits_json=json.dumps(admin_payload, sort_keys=True),
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
            o for o in self.broker.orders_by_tag(trade.broker_tag)
            if o.order_type in {"MARKET", "LIMIT"} and o.tradingsymbol == trade.symbol
            and o.transaction_type == _entry_side(trade.direction)
        ]
        if len(existing) == 1:
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

    def _partial_exit_pnl_from_values(
        self,
        trade: TradeRecord,
        *,
        filled_qty: int,
        exited_qty: int,
        entry_confirmed: float,
        entry_est: float,
        exit_value: float,
        exit_provisional: bool,
    ) -> tuple[bool, float, float]:
        """Shared confirmed-vs-provisional partial-exit P&L.

        Returns ``(provisional, realised_pnl, closed_loss_contribution)``.

        Confirmed P&L/loss is replaced only when both entry and exit accounting are
        complete and authoritative. Incomplete entry prices (``entry_est > 0``) keep
        prior confirmed loss/P&L and mark provisional — never slice
        ``entry_confirmed * exited / filled`` as if entry were fully priced.
        """
        provisional = bool(exit_provisional) or float(entry_est or 0) > 0
        prev_loss = float(getattr(trade, "closed_loss_contribution", 0) or 0)
        prev_pnl = float(getattr(trade, "realised_pnl", 0) or 0)
        if (
            not provisional
            and exited_qty > 0
            and filled_qty > 0
            and float(entry_confirmed or 0) > 0
            and float(exit_value or 0) > 0
        ):
            entry_slice = float(entry_confirmed) * (
                float(exited_qty) / float(filled_qty)
            )
            partial_pnl = realised_pnl_from_values(
                direction=trade.direction,
                entry_value=entry_slice,
                exit_value=float(exit_value),
                qty=int(exited_qty),
            )
            # Price loss only; residual exit costs stay in exited_cost_reservation.
            partial_loss = abs(partial_pnl) if partial_pnl < 0 else 0.0
            return False, float(partial_pnl), float(partial_loss)
        # Missing entry/exit prices must not zero an existing confirmed loss.
        return True, prev_pnl, prev_loss

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

    def _entry_limit_decision(self, trade: TradeRecord) -> EntryLimitDecision:
        cfg = self._admin_store.load_effective_payload()
        try:
            quote = self.broker.touch_quote(trade.symbol)
        except Exception:
            quote = None
        return bounded_entry_limit(now=self._now_ist(), trigger_time=trade.trigger_time,
            trigger=trade.entry_estimate, stop=trade.initial_stop, tick=trade.tick_size,
            direction=trade.direction, quote=quote,
            expiry=float(cfg["setup_expiry_seconds"]),
            max_quote_age=float(cfg["max_quote_age_seconds"]),
            drift_r=float(cfg["max_entry_drift_r"]))

    def _reject_entry_gate(self, trade: TradeRecord, reason: str) -> None:
        self.store.update_trade(trade.trade_id, status="skipped", skip_reason=reason,
            qty=0, intended_qty=0, remaining_entry_qty=0, notional=0, margin_blocked=0)
        self.store.append_event(trade.trade_id, "skipped", payload={"reason": reason})

    def _resize_for_limit(self, trade: TradeRecord, price: float) -> Optional[TradeRecord]:
        """Revalidate at the actual limit. Never increase the approved quantity."""
        cfg = self._admin_store.load_effective_payload()
        peers = [t for t in self.store.list_trades(self.session_date) if t.trade_id != trade.trade_id]
        snap = risk_snapshot(peers, daily_loss_cap=float(cfg["daily_loss_cap_inr"]))
        cap = min(float(cfg["per_trade_risk_cap_inr"]), trade.risk_cap_used_inr or float(cfg["per_trade_risk_cap_inr"]))
        charge, slip = trade_cost_profile(trade)
        per_share = abs(price - trade.initial_stop) + price * (charge + slip) / 10000
        capital = max(0., float(cfg["allocated_capital_inr"]) - open_notional_total(peers))
        qty = min(trade.qty, int(min(cap, snap.remaining_daily) / per_share), int(capital / price))
        if qty <= 0:
            self._reject_entry_gate(trade, "limit_price_risk_budget")
            return None
        return self.store.update_trade(trade.trade_id, qty=qty, intended_qty=qty,
            remaining_entry_qty=qty, entry_limit_price=price, notional=qty*price,
            risk_limits_json=trade.risk_limits_json or json.dumps(cfg, sort_keys=True),
            margin_blocked=qty*price/(1 if self.live_orders_enabled else self.leverage_factor))

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

        limit = self._entry_limit_decision(trade)
        if limit.reason:
            self._reject_entry_gate(trade, limit.reason)
            return

        trade = self._resize_for_limit(trade, limit.price)
        if trade is None:
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
                "order_type": "LIMIT",
                "limit_price": limit.price,
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
            # Quote/time can change while checking margin or persisting intent.
            latest = self._entry_limit_decision(trade)
            if latest.reason:
                self._reject_entry_gate(trade, latest.reason)
                return
            trade = self._resize_for_limit(trade, latest.price)
            if trade is None:
                return
            self.store.append_event(trade.trade_id, "entry_limit_validated", payload={
                "price": latest.price, "signal_age": latest.signal_age, "quote_age": latest.quote_age})
            block = self._new_entry_block_reason()
            if block is not None:
                self._reject_entry_gate(trade, block)
                return
            order = self.broker.place_limit_mis(
                tradingsymbol=trade.symbol,
                transaction_type=_entry_side(trade.direction),
                quantity=trade.qty,
                tag=trade.broker_tag,
                price=latest.price,
            )
        except EntryAcceptedVisibilityUnknown as exc:
            self.store.update_trade(trade.trade_id, status="submission_unknown", entry_order_id=exc.order_id)
            self.store.append_event(trade.trade_id, "submission_unknown", payload={"order_id": exc.order_id})
            return
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
            self._revalidate_postfill(self.store.get_trade(trade.trade_id) or refreshed)

    def _revalidate_postfill(self, trade: TradeRecord) -> None:
        if trade.trade_id in self._postfill_active or trade.filled_qty <= 0:
            return
        if trade.status in {"closed", "skipped", "rejected"}:
            return
        self._postfill_active.add(trade.trade_id)
        try:
            limits = json.loads(trade.risk_limits_json or "{}")
            peers = [t for t in self._iter_management_trades() if self._is_positively_owned_engine_trade(t)]
            decision = post_fill_risk_decision(trade, peers, limits)
            breached = self._has_trade_event(trade.trade_id, "post_fill_risk_breach")
            if decision.state == "safe" and not breached:
                return
            reason = "post_fill_risk_breach" if breached or decision.state == "breach" else "post_fill_risk_unknown"
            self._engage_local_entries_lock(reason)
            self._pause_entries_for(reason)
            if not self._has_trade_event(trade.trade_id, reason):
                self.store.append_event(trade.trade_id, reason, payload={"reasons": decision.reasons})
            if reason == "post_fill_risk_breach":
                self._request_market_exit(trade, reason=reason, kind="risk_breach")
            else:
                self.store.update_trade(trade.trade_id, status="reconciliation_required")
        finally:
            self._postfill_active.discard(trade.trade_id)

    def enforce_postfill_risk(self) -> None:
        for trade in self._iter_management_trades():
            if self._is_positively_owned_engine_trade(trade):
                self._revalidate_postfill(trade)

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
        if self._blocking_exit_in_progress(trade):
            return

        terminal_sl = SL_CANCELLED | SL_FILLED

        # Outstanding unknown attempt: reconcile attempt-scoped candidates first.
        if self._sl_submit_unresolved(trade.trade_id):
            outcome = self._reconcile_outstanding_sl_attempt(trade)
            refreshed = self.store.get_trade(trade.trade_id)
            trade = refreshed if refreshed is not None else trade
            pos = int(trade.remaining_position_qty or 0)
            if pos <= 0:
                return
            if outcome == "resolved_working":
                # Fall through so pending cover can be resized to remaining position.
                pass
            elif outcome == "resolved_terminal_open":
                # Cleared reject/cancel with open exposure — may place replacement.
                pass
            else:
                # still_unresolved / ambiguous — book older owned stop executions only;
                # do not place or flatten.
                self._reconcile_owned_prior_stops_during_unresolved(trade)
                return

        # Known stop id with lost poll visibility — do not place a second stop.
        if trade.sl_order_id:
            polled = self.broker.poll_order(str(trade.sl_order_id))
            if polled is None:
                self.store.append_event(
                    trade.trade_id,
                    "stop_visibility_unknown",
                    payload={"order_id": str(trade.sl_order_id)},
                )
                self._mark_unresolved_sl_submit(
                    trade, detail="known_stop_visibility_unknown"
                )
                return
            status = str(polled.status).upper()
            self._apply_sl_order(trade, polled)
            refreshed = self.store.get_trade(trade.trade_id)
            trade = refreshed if refreshed is not None else trade
            pos = int(trade.remaining_position_qty or 0)
            if pos <= 0:
                return
            if self._sl_submit_unresolved(trade.trade_id):
                self._mark_unresolved_sl_submit(trade, detail="terminal_not_cleared")
                return
            if status in SL_FILLED:
                return
            if status not in terminal_sl:
                pending = broker_order_pending_qty(polled)
                if pending == pos:
                    return
                # Pending cover mismatch — fall through to unique working modify.
            # CANCELLED/REJECTED cleared — fall through to replacement place.

        # Adopt a unique working stop (never pick arbitrarily among multiples).
        working = self._working_stops_for_trade(trade, terminal_sl=terminal_sl)
        if len(working) > 1:
            self.store.append_event(
                trade.trade_id,
                "sl_reconcile_ambiguous",
                payload={
                    "detail": "multiple_working_stops",
                    "order_ids": [str(o.order_id) for o in working],
                },
            )
            self._mark_unresolved_sl_submit(trade, detail="multiple_working_stops")
            return
        if len(working) == 1:
            order = working[0]
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

        if self._sl_submit_unresolved(trade.trade_id):
            self._mark_unresolved_sl_submit(
                trade, detail="empty_tag_not_absence"
            )
            return

        # Persist intent before any protective-stop broker write.
        attempt_id = uuid4().hex
        submitted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        attempt_tag = self._sl_attempt_tag(trade, attempt_id)
        stop_side = _stop_side(trade.direction)
        self.store.append_event(
            trade.trade_id,
            "sl_submit_attempt",
            payload={
                "attempt_id": attempt_id,
                "requested_qty": pos,
                "trigger_price": float(trade.current_stop),
                "symbol": trade.symbol,
                "transaction_type": stop_side,
                "tag": attempt_tag,
                "trade_tag": trade.broker_tag,
                "submitted_at": submitted_at,
                "protection_deadline_at": trade.protection_deadline_at,
            },
        )
        pending_fields: dict[str, Any] = {
            "status": "protection_pending",
            "protected_qty": 0,
            "qty_model_version": 1,
        }
        pending_fields.update(
            self._protection_state_fields(trade, next_status="protection_pending")
        )
        self.store.update_trade(trade.trade_id, **pending_fields)
        try:
            order = self.broker.place_slm(
                tradingsymbol=trade.symbol,
                transaction_type=stop_side,
                quantity=pos,
                trigger_price=trade.current_stop,
                tag=attempt_tag,
                tick_size=trade.tick_size,
            )
        except SlPlaceAcceptedVisibilityUnknown as exc:
            accepted_id = str(exc.order_id)
            self.store.append_event(
                trade.trade_id,
                "sl_submission_unknown",
                payload={
                    "error": str(exc),
                    "attempt_id": attempt_id,
                    "order_id": accepted_id,
                    "tag": attempt_tag,
                },
            )
            self.store.update_trade(
                trade.trade_id,
                status="reconciliation_required",
                protected_qty=0,
                sl_order_id=accepted_id,
            )
            self.store.append_event(
                trade.trade_id,
                "protection_emergency_unresolved",
                payload={
                    "reason": "sl_submission_unknown",
                    "attempt_id": attempt_id,
                    "order_id": accepted_id,
                },
            )
            return
        except Exception as exc:  # noqa: BLE001
            self.store.append_event(
                trade.trade_id,
                "sl_submission_unknown",
                payload={"error": str(exc), "attempt_id": attempt_id, "tag": attempt_tag},
            )
            self.store.update_trade(
                trade.trade_id,
                status="reconciliation_required",
                protected_qty=0,
            )
            self.store.append_event(
                trade.trade_id,
                "protection_emergency_unresolved",
                payload={"reason": "sl_submission_unknown", "attempt_id": attempt_id},
            )
            return
        self.store.append_event(
            trade.trade_id,
            "sl_placed",
            payload={
                "order_id": order.order_id,
                "requested_qty": pos,
                "attempt_id": attempt_id,
                "tag": attempt_tag,
            },
        )
        refreshed = self.store.get_trade(trade.trade_id)
        trade = refreshed if refreshed is not None else trade
        self._clear_sl_submit_attempt(
            trade,
            outcome="broker_accepted",
            order_id=str(order.order_id),
            attempt_id=attempt_id,
        )
        refreshed = self.store.get_trade(trade.trade_id)
        trade = refreshed if refreshed is not None else trade
        self._apply_sl_order(trade, order)

    def _place_stop(self, trade: TradeRecord) -> None:
        """Backward-compatible alias: protect current remaining position."""
        self._ensure_protection(trade)

    def _stop_order_engine_owned(
        self,
        trade: TradeRecord,
        order: Any,
        *,
        order_attempt_id: Optional[str],
    ) -> bool:
        """True when this stop is positively attributed to the trade (link or attempt)."""
        oid = str(order.order_id)
        if self._order_owned_by_other_trade(trade, oid):
            return False
        link = self.store.get_order_link(oid)
        if link is not None and str(link["trade_id"]) == str(trade.trade_id):
            return True
        if order_attempt_id is not None and str(order_attempt_id).strip():
            return True
        tag = str(getattr(order, "tag", "") or "").strip()
        if not tag:
            return False
        for row in self.store.list_events(trade.trade_id):
            if str(row["action"]) not in {
                "sl_submit_attempt",
                "sl_submission_unknown",
                "sl_placed",
            }:
                continue
            payload = self._parse_event_payload(row)
            if str(payload.get("tag") or "") == tag:
                return True
        return False

    def _reconcile_prior_stop_executions(
        self,
        trade: TradeRecord,
        order: Any,
        *,
        order_attempt_id: Optional[str],
    ) -> None:
        """Book fills/price corrections from an older stop without mutating attempt B.

        Execution accounting updates remaining position, exit value, and risk P&L.
        Protection identity (``sl_order_id`` / attempt B) is preserved; A never clears B
        and never marks B's protection confirmed. Does not place or flatten.
        """
        if not _is_stop_order(order):
            return
        if not self._stop_order_engine_owned(
            trade, order, order_attempt_id=order_attempt_id
        ):
            return
        if not self._link_order(
            trade, str(order.order_id), role="stop", kind="explicit"
        ):
            return

        # Clear only the prior attempt that owns this order (never the outstanding B).
        if order_attempt_id is not None and str(order_attempt_id).strip():
            self._clear_sl_submit_attempt(
                trade,
                outcome="prior_stop_execution_reconciled",
                order_id=str(order.order_id),
                attempt_id=str(order_attempt_id),
                order=order,
            )

        refreshed = self.store.get_trade(trade.trade_id)
        trade = refreshed if refreshed is not None else trade
        preserve_sl = trade.sl_order_id
        preserve_deadline = trade.protection_deadline_at
        prev_exited = int(trade.exited_qty or 0)
        prev_exit_value = float(getattr(trade, "exit_value", 0) or 0)
        prev_exit_est = float(getattr(trade, "exit_value_est", 0) or 0)

        # Snapshot this order (+ other owned exits) exactly once via durable links.
        exit_updates = self._sync_exit_values(trade)
        exited_total = max(
            prev_exited,
            int(exit_updates.get("exited_qty", 0) or 0),
            self._broker_exit_filled_total(trade),
        )
        new_exit_value = float(exit_updates.get("exit_value", 0) or 0)
        new_exit_est = float(exit_updates.get("exit_value_est", 0) or 0)
        qty_changed = exited_total != prev_exited
        value_changed = (
            abs(new_exit_value - prev_exit_value) > 1e-9
            or abs(new_exit_est - prev_exit_est) > 1e-9
        )

        filled_qty = int(trade.filled_qty or 0)
        entry_confirmed = float(getattr(trade, "entry_value", 0) or 0)
        entry_est = float(getattr(trade, "entry_value_est", 0) or 0)
        if filled_qty > 0 and trade.entry_order_id:
            polled_entry = self.broker.poll_order(trade.entry_order_id)
            if polled_entry is not None:
                entry_confirmed, entry_est = self._entry_execution_values_from_order(
                    polled_entry,
                    filled_qty,
                    float(
                        polled_entry.average_price
                        or trade.entry_fill
                        or trade.entry_estimate
                        or 0.0
                    ),
                )

        exit_provisional = bool(int(exit_updates.get("pnl_provisional", 0) or 0))
        provisional, partial_pnl, partial_loss = self._partial_exit_pnl_from_values(
            trade,
            filled_qty=filled_qty,
            exited_qty=exited_total,
            entry_confirmed=entry_confirmed,
            entry_est=entry_est,
            exit_value=new_exit_value,
            exit_provisional=exit_provisional,
        )

        # Quantity/value/provisional transitions all warrant a durable update.
        prev_provisional = bool(int(getattr(trade, "pnl_provisional", 0) or 0))
        if (
            not qty_changed
            and not value_changed
            and provisional == prev_provisional
            and abs(partial_pnl - float(getattr(trade, "realised_pnl", 0) or 0)) < 1e-9
            and abs(
                partial_loss - float(getattr(trade, "closed_loss_contribution", 0) or 0)
            )
            < 1e-9
        ):
            return

        rem_pos = self._remaining_position_from_executions(filled_qty, exited_total)
        display_exit = trade.exit_fill
        if new_exit_value > 0 and exited_total > 0 and not provisional:
            display_exit = new_exit_value / float(exited_total)
        elif new_exit_value + new_exit_est > 0 and exited_total > 0:
            display_exit = (new_exit_value + new_exit_est) / float(exited_total)

        merged_updates = {
            k: v
            for k, v in exit_updates.items()
            if k not in {"exited_qty", "exit_fill", "pnl_provisional"}
        }
        self.store.update_trade(
            trade.trade_id,
            status="reconciliation_required",
            exit_fill=display_exit,
            filled_qty=filled_qty,
            exited_qty=exited_total,
            entry_value=entry_confirmed,
            entry_value_est=entry_est,
            remaining_position_qty=rem_pos,
            realised_pnl=partial_pnl,
            closed_loss_contribution=partial_loss,
            pnl_provisional=1 if provisional else 0,
            # Lock protection identity to outstanding attempt B.
            sl_order_id=preserve_sl,
            protected_qty=0,
            protection_deadline_at=preserve_deadline,
            qty_model_version=1,
            **merged_updates,
        )
        self.store.append_event(
            trade.trade_id,
            "prior_stop_execution_reconciled",
            payload={
                "order_id": str(order.order_id),
                "order_attempt_id": order_attempt_id,
                "exited_qty": exited_total,
                "exit_value": new_exit_value,
                "exit_value_est": new_exit_est,
                "entry_value": entry_confirmed,
                "entry_value_est": entry_est,
                "pnl_provisional": provisional,
                "remaining_position_qty": rem_pos,
                "preserved_sl_order_id": preserve_sl,
            },
        )

    def _reconcile_owned_prior_stops_during_unresolved(
        self, trade: TradeRecord
    ) -> None:
        """While attempt B is unknown, still reconcile executions on older owned stops."""
        outstanding = self._latest_unresolved_sl_attempt(trade.trade_id)
        out_oid = None
        out_tag = None
        if outstanding is not None:
            out_oid = outstanding[1].get("order_id")
            out_tag = outstanding[1].get("tag")
        for order in list(self._iter_exit_orders(trade)):
            if not _is_stop_order(order):
                continue
            if out_oid is not None and str(order.order_id) == str(out_oid):
                continue
            if (
                out_tag is not None
                and str(out_tag).strip()
                and str(getattr(order, "tag", "") or "") == str(out_tag)
            ):
                continue
            order_aid = self._attempt_id_for_sl_order(
                trade.trade_id, str(order.order_id), order=order
            )
            refreshed = self.store.get_trade(trade.trade_id)
            trade = refreshed if refreshed is not None else trade
            self._reconcile_prior_stop_executions(
                trade, order, order_attempt_id=order_aid
            )

    def _apply_sl_order(self, trade: TradeRecord, order: Any) -> None:
        # Older stop vs newer unknown attempt: still book executions; never mutate B.
        outstanding = self._latest_unresolved_sl_attempt(trade.trade_id)
        if outstanding is not None:
            ap = outstanding[1]
            out_aid = str(ap.get("attempt_id") or "")
            order_aid = self._attempt_id_for_sl_order(
                trade.trade_id, str(order.order_id), order=order
            )
            belongs = False
            if order_aid and out_aid and order_aid == out_aid:
                belongs = True
            elif str(ap.get("order_id") or "") == str(order.order_id):
                belongs = True
            elif (
                str(ap.get("tag") or "").strip()
                and str(getattr(order, "tag", "") or "") == str(ap.get("tag"))
            ):
                belongs = True
            if not belongs:
                self._reconcile_prior_stop_executions(
                    trade, order, order_attempt_id=order_aid
                )
                return

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
            # Always detach filled stop before replacement protect (even if already
            # reconciliation_required — otherwise ensure re-applies the same COMPLETE).
            self.store.update_trade(
                trade.trade_id,
                status="reconciliation_required" if rem_pos > 0 else trade.status,
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
                    self._clear_sl_submit_attempt(
                        again,
                        outcome="stop_filled_reconciled",
                        order_id=str(order.order_id),
                        order=order,
                    )
                    again = self.store.get_trade(trade.trade_id) or again
                    self._ensure_protection(again)
            return

        if status in SL_CANCELLED:
            # Reconcile any cancel-race fills already booked above; then clear the
            # durable submit attempt so open exposure may re-protect or emergency-exit.
            # Do NOT reset protection_deadline_at.
            self.store.update_trade(
                trade.trade_id,
                status="stop_pending" if rem_pos > 0 else "reconciliation_required",
                protected_qty=0,
                exited_qty=exited_total,
                remaining_position_qty=rem_pos,
                sl_order_id=None,
                qty_model_version=1,
            )
            self.store.append_event(
                trade.trade_id, "stop_pending", payload={"reason": "sl_cancelled"}
            )
            self._clear_sl_submit_attempt(
                trade,
                outcome="rejected_or_cancelled",
                order_id=str(order.order_id),
                order=order,
            )
            if rem_pos > 0:
                self._raise_broker_truth_incident(
                    trade,
                    kind="external_stop_cancelled",
                    payload={"sl_order_id": order.order_id},
                )
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
            # Broker-truth: do not clear qty/side mismatch by re-labeling protected.
            if next_status == "protected_open" and rem_pos > 0:
                net = self.broker.net_position_qty(trade.symbol)
                if net is not None and int(net) != 0 and not self._broker_qty_matches_engine(
                    trade, int(net), rem_pos
                ):
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
            event_name = (
                "protected" if next_status == "protected_open" else next_status
            )
            self.store.append_event(
                trade.trade_id,
                event_name,
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
            if confirmed > 0:
                self._clear_sl_submit_attempt(
                    trade,
                    outcome="working_stop_confirmed",
                    order_id=str(order.order_id),
                    order=order,
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
            provisional, partial_pnl, partial_loss = self._partial_exit_pnl_from_values(
                trade,
                filled_qty=filled,
                exited_qty=new_exited,
                entry_confirmed=entry_confirmed,
                entry_est=entry_est,
                exit_value=exit_value,
                exit_provisional=provisional,
            )
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
                **{
                    k: v
                    for k, v in exit_updates.items()
                    if k not in {"exited_qty", "exit_fill", "pnl_provisional"}
                },
                pnl_provisional=1 if provisional else 0,
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
        for trade in self._iter_management_trades():
            # Provenance-unknown / mode-mismatch with filled or prior-session exposure:
            # surface only — never broker-manage. Unstamped current-session entry intents
            # still flow through submit/skip gates.
            if not self._is_positively_owned_engine_trade(trade):
                if self._unknown_requires_reconciliation_hold(trade):
                    if self._trade_has_recoverable_exposure(trade):
                        if trade.status != "reconciliation_required":
                            self.store.update_trade(
                                trade.trade_id, status="reconciliation_required"
                            )
                    continue
                # fall through for current-session zero-fill unsigned intents
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
        if int(net) != 0 and not self._broker_qty_matches_engine(trade, int(net), pos):
            self._raise_broker_truth_incident(
                trade,
                kind="qty_mismatch",
                payload={"net": int(net), "engine_qty": pos},
            )
            return False
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
            if int(trade.remaining_position_qty or 0) > 0:
                self._raise_broker_truth_incident(
                    trade,
                    kind="external_stop_cancelled",
                    payload={"sl_order_id": trade.sl_order_id},
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
                self._raise_broker_truth_incident(
                    trade,
                    kind="external_stop_widen",
                    payload={
                        "engine_stop": trade.current_stop,
                        "broker_trigger": aligned,
                    },
                )
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
                self._clear_local_entries_lock()
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
        feed_block = self._feed_entry_block_reason()
        if feed_block is not None:
            return feed_block
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
        for trade in self._iter_management_trades():
            if not self._is_positively_owned_engine_trade(trade):
                continue
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
        if not self._is_positively_owned_engine_trade(trade):
            self.store.append_event(
                trade_id,
                "close_blocked_unresolved_ownership",
                actor=actor,
                payload={
                    "run_id": trade.run_id,
                    "entry_live_orders_enabled": trade.entry_live_orders_enabled,
                    "engine_live_orders_enabled": bool(self.live_orders_enabled),
                },
            )
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
        for trade in self._iter_management_trades():
            if not self._is_positively_owned_engine_trade(trade):
                continue
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
        for reason, kind in DURABLE_MARKET_EXIT_OWNERS:
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
        for trade in self._iter_management_trades():
            if not self._is_positively_owned_engine_trade(trade):
                continue
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
        for trade in self._iter_management_trades():
            if not self._is_positively_owned_engine_trade(trade):
                continue
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
        self._pause_entries_for("protection_deadline_breach", trade_id=trade.trade_id)
        self._durable_market_exit(
            trade,
            reason="protection_deadline",
            kind="emergency",
        )

    def _has_trade_event(self, trade_id: str, action: str) -> bool:
        for row in self.store.list_events(trade_id):
            if str(row["action"]) == action:
                return True
        return False

    def _pause_entries_for(self, detail: str, *, trade_id: Optional[str] = None) -> bool:
        """Canonical pause + control log. On failure, keep/engage local entry lock.

        Returns True only when the canonical admin pause flag is confirmed True.
        """
        self._engage_local_entries_lock(detail)
        try:
            pause_store = AdminConfigStore(Path(self._admin_store.db_path), read_only=False)
            pause_store.set_entries_paused(True)
            recent = pause_store.list_audit(limit=40)
            logged = any(
                str(r.get("action")) == "pause_entries" and str(r.get("detail") or "") == detail
                for r in recent
            )
            if not logged:
                pause_store.append_control_log(
                    actor_username="engine",
                    action="pause_entries",
                    result="ok",
                    detail=detail,
                    version_id=pause_store.active_version_id(),
                )
            confirmed = bool(pause_store.read_entries_paused())
            pause_store.close()
            if confirmed:
                # Canonical owns the pause; release local so Admin resume can clear it.
                self._clear_local_entries_lock()
                self._sync_pause_from_canonical()
                return True
        except Exception as exc:  # noqa: BLE001
            if trade_id is not None:
                self.store.append_event(
                    trade_id,
                    "error",
                    payload={"reason": f"pause_failed:{exc}"},
                )
            else:
                self.last_error = f"pause_failed:{exc}"
        # Fail-closed: local lock remains; do not reload canonical permission.
        self.consume_new_triggers = False
        self.store.set_consume_triggers(self.run_id, False)
        return False

    def _broker_qty_matches_engine(
        self, trade: TradeRecord, net: int, engine_pos: int
    ) -> bool:
        """Broker net must match engine remaining qty and side (UP long / DOWN short)."""
        if engine_pos <= 0:
            return net == 0
        expected = engine_pos if str(trade.direction).upper() == "UP" else -engine_pos
        return int(net) == int(expected)

    def _raise_broker_truth_incident(
        self,
        trade: TradeRecord,
        *,
        kind: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        """Pause entries, durable event, mark reconciliation_required while exposed."""
        body = dict(payload or {})
        if not self._has_trade_event(trade.trade_id, kind):
            self.store.append_event(trade.trade_id, kind, payload=body)
        self._pause_entries_for(kind, trade_id=trade.trade_id)
        pos = int(trade.remaining_position_qty or 0)
        rem = int(trade.remaining_entry_qty or 0)
        if trade.status not in {"closed", "skipped", "rejected"} and (pos > 0 or rem > 0):
            if trade.status != "reconciliation_required":
                self.store.update_trade(
                    trade.trade_id,
                    status="reconciliation_required",
                )

    def _trade_has_recoverable_exposure(self, trade: TradeRecord) -> bool:
        if trade.status not in ACTIVE_STATES:
            return False
        if int(trade.remaining_position_qty or 0) > 0:
            return True
        if int(trade.remaining_entry_qty or 0) > 0:
            return True
        if trade.status in {
            "entry_submitting",
            "submission_unknown",
            "exit_pending",
            "reconciliation_required",
        }:
            return True
        return False

    def _trade_provenance_known(self, trade: TradeRecord) -> bool:
        run_id = str(trade.run_id or "").strip()
        return bool(run_id) and trade.entry_live_orders_enabled is not None

    def _trade_mode_matches_engine(self, trade: TradeRecord) -> bool:
        if trade.entry_live_orders_enabled is None:
            return False
        return bool(trade.entry_live_orders_enabled) == bool(self.live_orders_enabled)

    def _is_positively_owned_engine_trade(self, trade: TradeRecord) -> bool:
        """True when provenance positively identifies engine ownership in this mode."""
        return self._trade_provenance_known(trade) and self._trade_mode_matches_engine(
            trade
        )

    def _is_manageable_engine_trade(self, trade: TradeRecord) -> bool:
        """Continue protection/exits only for positively identified same-mode exposure."""
        return self._is_positively_owned_engine_trade(
            trade
        ) and self._trade_has_recoverable_exposure(trade)

    def _iter_management_trades(self) -> List[TradeRecord]:
        """Current-session trades plus prior-session positively owned exposure."""
        current: List[TradeRecord] = []
        prior: List[TradeRecord] = []
        for trade in self.store.list_trades(None):
            if trade.session_date == self.session_date:
                current.append(trade)
                continue
            if self._is_manageable_engine_trade(trade):
                prior.append(trade)
        return current + prior

    def _session_has_recoverable_exposure(self) -> bool:
        for trade in self.store.list_trades(self.session_date):
            if self._trade_has_recoverable_exposure(trade):
                return True
        return False

    def _collect_linked_order_ids_and_tags(self) -> Tuple[set[str], set[str]]:
        order_ids: set[str] = set()
        tags: set[str] = set()
        for trade in self.store.list_trades(None):
            for link in self.store.list_order_links(trade.trade_id):
                order_ids.add(str(link["order_id"]))
            if trade.broker_tag:
                tags.add(str(trade.broker_tag))
            for oid in (
                trade.entry_order_id,
                trade.sl_order_id,
                trade.active_exit_order_id,
            ):
                if oid:
                    order_ids.add(str(oid))
            for row in self.store.list_events(trade.trade_id):
                action = str(row["action"] or "")
                try:
                    payload = json.loads(str(row["payload_json"] or "{}"))
                except json.JSONDecodeError:
                    payload = {}
                if not isinstance(payload, dict):
                    continue
                for key in ("order_id", "accepted_order_id", "entry_order_id", "sl_order_id"):
                    val = payload.get(key)
                    if val:
                        order_ids.add(str(val))
                for key in ("tag", "attempt_tag", "trade_tag"):
                    val = payload.get(key)
                    if val:
                        tags.add(str(val))
                if action == "sl_submit_attempt":
                    tag = payload.get("tag")
                    if tag:
                        tags.add(str(tag))
        return order_ids, tags

    def _broker_discovery_snapshot(
        self,
    ) -> Tuple[Optional[List[BrokerOrder]], Optional[Dict[str, int]], Optional[str]]:
        """Return (orders, positions, error). Error ⇒ fail-closed entry block."""
        list_orders = getattr(self.broker, "list_orders", None)
        list_positions = getattr(self.broker, "list_net_positions", None)
        if not callable(list_orders) or not callable(list_positions):
            return None, None, "broker_discovery_unsupported"
        try:
            raw_orders = list_orders()
            if not isinstance(raw_orders, (list, tuple)):
                raise ValueError("invalid_orders_snapshot")
            orders = list(raw_orders)
            if any(not isinstance(order, BrokerOrder) for order in orders):
                raise ValueError("invalid_order_record")
        except Exception as exc:  # noqa: BLE001
            return None, None, f"orders_discovery_failed:{exc}"
        try:
            raw_positions = list_positions()
            if not isinstance(raw_positions, dict):
                raise ValueError("invalid_positions_snapshot")
            positions = dict(raw_positions)
            for symbol, qty in positions.items():
                if not isinstance(symbol, str) or not symbol.strip():
                    raise ValueError("invalid_position_symbol")
                if isinstance(qty, bool) or not isinstance(qty, int):
                    raise ValueError("invalid_position_quantity")
        except Exception as exc:  # noqa: BLE001
            return None, None, f"positions_discovery_failed:{exc}"
        return orders, positions, None

    def _scan_recovery_state(self) -> dict[str, Any]:
        """Cross-session + orphan discovery (never auto-adopt / flatten / delete)."""
        cross_session: List[TradeRecord] = []
        provenance_unknown: List[TradeRecord] = []
        mode_mismatch: List[TradeRecord] = []
        owned_recoverable: List[TradeRecord] = []
        for trade in self.store.list_trades(None):
            if not self._trade_has_recoverable_exposure(trade):
                continue
            if trade.session_date != self.session_date:
                cross_session.append(trade)
            if not self._trade_provenance_known(trade):
                if self._unknown_requires_reconciliation_hold(trade):
                    provenance_unknown.append(trade)
                continue
            if not self._trade_mode_matches_engine(trade):
                if self._unknown_requires_reconciliation_hold(trade):
                    mode_mismatch.append(trade)
                continue
            owned_recoverable.append(trade)

        linked_ids, linked_tags = self._collect_linked_order_ids_and_tags()
        # Any local ACTIVE trade claims its symbol for position orphan checks.
        # Qty fields can lag a broker fill by one tick (cancel-time fill → protect);
        # absence of a local trade is the orphan signal, not a transient qty gap.
        claimed_symbols: set[str] = set()
        for trade in self.store.list_trades(None):
            if trade.status in ACTIVE_STATES:
                claimed_symbols.add(str(trade.symbol))
            elif self._trade_has_recoverable_exposure(trade):
                claimed_symbols.add(str(trade.symbol))

        orphan_orders: List[dict[str, Any]] = []
        orphan_stops: List[dict[str, Any]] = []
        orphan_positions: List[dict[str, Any]] = []
        discovery_error: Optional[str] = None
        orders, positions, discovery_error = self._broker_discovery_snapshot()
        if discovery_error is None and orders is not None and positions is not None:
            for order in orders:
                oid = str(order.order_id or "")
                tag = str(order.tag or "")
                # A tag from another symbol/mode is not ownership. Scope every
                # candidate to a positively identified trade before ignoring it.
                linked = False
                for owner in self.store.list_trades(None):
                    if not self._is_positively_owned_engine_trade(owner):
                        continue
                    if str(order.tradingsymbol) != owner.symbol or order.product != "MIS":
                        continue
                    link = self.store.get_order_link(oid)
                    if link is not None and str(link["trade_id"]) == owner.trade_id:
                        expected_side = _entry_side(owner.direction) if link["role"] == "entry" else _stop_side(owner.direction)
                        linked = str(order.transaction_type) == expected_side
                    elif oid == owner.entry_order_id:
                        linked = str(order.transaction_type) == _entry_side(owner.direction)
                    elif oid in {owner.sl_order_id, owner.active_exit_order_id}:
                        linked = str(order.transaction_type) == _stop_side(owner.direction)
                    elif tag == owner.broker_tag and str(order.transaction_type) == _entry_side(owner.direction):
                        linked = str(order.order_type) in {"MARKET", "LIMIT"}
                    elif _is_stop_order(order) and str(order.transaction_type) == _stop_side(owner.direction):
                        linked = self._attempt_id_for_sl_order(owner.trade_id, oid, order=order) is not None
                    if linked:
                        break
                if linked:
                    continue
                body = {
                    "order_id": oid,
                    "tag": tag,
                    "symbol": str(order.tradingsymbol or ""),
                    "order_type": str(order.order_type or ""),
                    "status": str(order.status or ""),
                    "transaction_type": str(order.transaction_type or ""),
                    "quantity": int(order.quantity or 0),
                    "pending_quantity": broker_order_pending_qty(order),
                    "filled_quantity": broker_order_filled_qty(order),
                }
                if _is_stop_order(order):
                    # Working + terminal unlinked stops (historical-stop discovery).
                    orphan_stops.append(body)
                    continue
                # Non-stop: only working unmatched orders are live orphan risk.
                # COMPLETE day-book fills (e.g. external flatten about to attribute)
                # must not latch entry pause before reconcile.
                st = str(order.status or "").upper()
                pending = broker_order_pending_qty(order)
                if pending > 0 or st in ENTRY_WORKING | SL_TRIGGERED_WORKING | {
                    "TRIGGER PENDING",
                }:
                    orphan_orders.append(body)
            for symbol, qty in positions.items():
                if int(qty) == 0:
                    continue
                if str(symbol) in claimed_symbols:
                    continue
                orphan_positions.append({"symbol": str(symbol), "quantity": int(qty)})

        blocks_entries = bool(
            cross_session
            or provenance_unknown
            or mode_mismatch
            or orphan_orders
            or orphan_stops
            or orphan_positions
            or discovery_error
            or self._session_has_recoverable_exposure()
        )
        ownership_unresolved = bool(
            cross_session
            or provenance_unknown
            or mode_mismatch
            or orphan_orders
            or orphan_stops
            or orphan_positions
            or discovery_error
        )
        return {
            "cross_session": cross_session,
            "provenance_unknown": provenance_unknown,
            "mode_mismatch": mode_mismatch,
            "owned_recoverable": owned_recoverable,
            "orphan_orders": orphan_orders,
            "orphan_stops": orphan_stops,
            "orphan_positions": orphan_positions,
            "discovery_error": discovery_error,
            "blocks_entries": blocks_entries,
            "ownership_unresolved": ownership_unresolved,
            "session_recoverable": self._session_has_recoverable_exposure(),
        }

    def _recovery_pause_detail(self, state: dict[str, Any]) -> str:
        if state.get("discovery_error"):
            return "recovery_discovery_failed"
        if state.get("orphan_stops") or state.get("orphan_orders") or state.get(
            "orphan_positions"
        ):
            return "orphan_recovery"
        if state.get("provenance_unknown") or state.get("mode_mismatch"):
            return "provenance_recovery"
        if state.get("cross_session"):
            return "cross_session_recovery"
        return "restart_recovery"

    def _unknown_requires_reconciliation_hold(self, trade: TradeRecord) -> bool:
        """Hold only filled/prior-session unknown exposure — not unstamped entry intents."""
        if trade.session_date != self.session_date:
            return True
        if int(trade.remaining_position_qty or 0) > 0:
            return True
        if int(trade.filled_qty or 0) > 0:
            return True
        return False

    def _surface_recovery_findings(self, state: dict[str, Any]) -> None:
        """Persist discovery events; never adopt, flatten, or delete orphans."""
        for trade in state["provenance_unknown"]:
            if not self._unknown_requires_reconciliation_hold(trade):
                continue
            key = f"provenance_unknown:{trade.trade_id}"
            if key not in self._recovery_finding_keys and not self._has_trade_event(
                trade.trade_id, "provenance_unknown"
            ):
                self.store.append_event(
                    trade.trade_id,
                    "provenance_unknown",
                    payload={
                        "session_date": trade.session_date,
                        "symbol": trade.symbol,
                        "run_id": trade.run_id,
                        "entry_live_orders_enabled": trade.entry_live_orders_enabled,
                    },
                )
                self._recovery_finding_keys.add(key)
            if trade.status != "reconciliation_required":
                self.store.update_trade(
                    trade.trade_id, status="reconciliation_required"
                )

        for trade in state["mode_mismatch"]:
            if not self._unknown_requires_reconciliation_hold(trade):
                continue
            key = f"mode_mismatch:{trade.trade_id}"
            if key not in self._recovery_finding_keys and not self._has_trade_event(
                trade.trade_id, "mode_mismatch_recovery"
            ):
                self.store.append_event(
                    trade.trade_id,
                    "mode_mismatch_recovery",
                    payload={
                        "session_date": trade.session_date,
                        "symbol": trade.symbol,
                        "entry_live_orders_enabled": trade.entry_live_orders_enabled,
                        "engine_live_orders_enabled": bool(self.live_orders_enabled),
                    },
                )
                self._recovery_finding_keys.add(key)
            if trade.status != "reconciliation_required":
                self.store.update_trade(
                    trade.trade_id, status="reconciliation_required"
                )

        for trade in state["cross_session"]:
            key = f"cross_session:{trade.trade_id}"
            if key not in self._recovery_finding_keys and not self._has_trade_event(
                trade.trade_id, "cross_session_recovery"
            ):
                self.store.append_event(
                    trade.trade_id,
                    "cross_session_recovery",
                    payload={
                        "trade_session_date": trade.session_date,
                        "engine_session_date": self.session_date,
                        "symbol": trade.symbol,
                        "manageable": self._is_manageable_engine_trade(trade),
                    },
                )
                self._recovery_finding_keys.add(key)

        if state.get("discovery_error"):
            key = f"discovery_error:{state['discovery_error']}"
            if key not in self._recovery_finding_keys:
                self.store.append_event(
                    RECOVERY_EVENTS_TRADE_ID,
                    "recovery_discovery_failed",
                    payload={"error": str(state["discovery_error"])},
                )
                self._recovery_finding_keys.add(key)

        for body in state["orphan_positions"]:
            key = f"orphan_position:{body.get('symbol')}:{body.get('quantity')}"
            if key in self._recovery_finding_keys:
                continue
            if any(
                str(r["action"]) == "orphan_broker_position"
                and json.loads(str(r["payload_json"] or "{}")).get("symbol")
                == body.get("symbol")
                for r in self.store.list_events(RECOVERY_EVENTS_TRADE_ID)
            ):
                self._recovery_finding_keys.add(key)
                continue
            self.store.append_event(
                RECOVERY_EVENTS_TRADE_ID,
                "orphan_broker_position",
                payload={**body, "action_policy": "pause_only_no_auto_adopt_flatten"},
            )
            self._recovery_finding_keys.add(key)

        for body in state["orphan_stops"]:
            key = f"orphan_stop:{body.get('order_id') or body.get('tag')}"
            if key in self._recovery_finding_keys:
                continue
            if any(
                str(r["action"]) == "unlinked_historical_stop"
                and json.loads(str(r["payload_json"] or "{}")).get("order_id")
                == body.get("order_id")
                for r in self.store.list_events(RECOVERY_EVENTS_TRADE_ID)
                if body.get("order_id")
            ):
                self._recovery_finding_keys.add(key)
                continue
            self.store.append_event(
                RECOVERY_EVENTS_TRADE_ID,
                "unlinked_historical_stop",
                payload={**body, "action_policy": "pause_only_no_auto_adopt_flatten"},
            )
            self._recovery_finding_keys.add(key)

        for body in state["orphan_orders"]:
            key = f"orphan_order:{body.get('order_id') or body.get('tag')}"
            if key in self._recovery_finding_keys:
                continue
            if any(
                str(r["action"]) == "orphan_broker_order"
                and json.loads(str(r["payload_json"] or "{}")).get("order_id")
                == body.get("order_id")
                for r in self.store.list_events(RECOVERY_EVENTS_TRADE_ID)
                if body.get("order_id")
            ):
                self._recovery_finding_keys.add(key)
                continue
            self.store.append_event(
                RECOVERY_EVENTS_TRADE_ID,
                "orphan_broker_order",
                payload={**body, "action_policy": "pause_only_no_auto_adopt_flatten"},
            )
            self._recovery_finding_keys.add(key)

    def enforce_restart_recovery(self) -> None:
        """WP-1.7: pause entries while cross-session / orphan / restart exposure exists.

        Positively identified same-mode engine trades continue under management.
        Unmatched / provenance-unknown exposure is surfaced only — never auto-adopted,
        flattened, or deleted.

        Latch semantics (WP-1.5 compatible): same-session exposure discovered only after
        the first empty tick does not re-pause mid-run. Ownership/orphan unresolved
        always re-asserts the entry block (including after Admin resume).
        """
        state = self._scan_recovery_state()
        self._surface_recovery_findings(state)

        if state["ownership_unresolved"]:
            detail = self._recovery_pause_detail(state)
            if not self._entries_paused():
                self._engage_local_entries_lock(detail)
                if not self._pause_entries_for(detail):
                    self._restart_recovery_done = False
                    return
            elif not self._restart_recovery_done:
                self._engage_local_entries_lock(detail)
                if not self._pause_entries_for(detail):
                    self._restart_recovery_done = False
                    return
            self._restart_recovery_done = True
            for trade in state["owned_recoverable"]:
                if not self._has_trade_event(trade.trade_id, "restart_recovery"):
                    self.store.append_event(
                        trade.trade_id,
                        "restart_recovery",
                        payload={
                            "entries_paused": True,
                            "session_date": trade.session_date,
                            "engine_session_date": self.session_date,
                        },
                    )
            return

        # Same-session restart latch only (no ownership/orphan issues).
        if self._restart_recovery_done:
            return
        if not state["session_recoverable"]:
            self._restart_recovery_done = True
            return
        self._engage_local_entries_lock("restart_recovery")
        if not self._pause_entries_for("restart_recovery"):
            return
        self._restart_recovery_done = True
        for trade in self.store.list_trades(self.session_date):
            if trade.status in ACTIVE_STATES:
                if not self._has_trade_event(trade.trade_id, "restart_recovery"):
                    self.store.append_event(
                        trade.trade_id,
                        "restart_recovery",
                        payload={"entries_paused": True},
                    )

    def _session_open_minutes(self) -> Optional[int]:
        sched = self._configured_special_schedule()
        if sched is not None:
            return parse_hhmm(sched.session_open_ist)
        return parse_hhmm(DEFAULT_SESSION_OPEN_IST_HHMM)

    def _in_feed_trading_window(self) -> bool:
        """True during cash trading window (open ≤ now < square-off). Pre-open excluded."""
        open_m = self._session_open_minutes()
        end_m = self._session_gate_minutes(
            "square_off_ist", DEFAULT_SQUARE_OFF_IST_HHMM
        )
        if open_m is None or end_m is None:
            return False
        now_m = self._ist_minutes_now()
        return open_m <= now_m < end_m

    def _feed_age_seconds(self) -> Optional[float]:
        """Return finite non-negative feed age, or None when unknown/invalid/unwired."""
        if self._feed_age_seconds_fn is None:
            return None
        try:
            age = self._feed_age_seconds_fn()
        except Exception:  # noqa: BLE001
            return None
        if age is None:
            return None
        try:
            value = float(age)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value) or value < 0:
            return None
        return value

    def _feed_entry_block_reason(self) -> Optional[str]:
        """Block new risk during trading hours when feed is unknown/stale."""
        if not self._in_feed_trading_window():
            return None
        age = self._feed_age_seconds()
        if age is None:
            return "feed_unknown"
        if age >= FEED_STALE_PAUSE_SECONDS:
            return "feed_stale"
        return None

    def _feed_blocks_price_actions(self) -> bool:
        return self._feed_entry_block_reason() is not None

    def enforce_feed_staleness(self) -> None:
        """Trading-window only: unknown/5s pause entries+trails; 30s managed exit."""
        if not self._in_feed_trading_window():
            return
        age = self._feed_age_seconds()
        if age is None:
            self._pause_entries_for("feed_unknown")
            return
        if age >= FEED_STALE_PAUSE_SECONDS:
            self._pause_entries_for("feed_stale")
        if age < FEED_STALE_EXIT_SECONDS:
            return
        for trade in self._iter_management_trades():
            if not self._is_positively_owned_engine_trade(trade):
                continue
            if trade.status in {"closed", "skipped", "rejected"}:
                continue
            pos = int(trade.remaining_position_qty or 0)
            if pos <= 0 and int(trade.remaining_entry_qty or 0) <= 0:
                continue
            quote = self._position_quote(trade.symbol)
            if quote is None and self.live_orders_enabled:
                continue
            net = self.broker.net_position_qty(trade.symbol)
            if net is None:
                continue
            if not self._has_exit_intent(trade.trade_id, reason="feed_stale"):
                self.store.append_event(
                    trade.trade_id,
                    "feed_stale_exit_requested",
                    payload={"feed_age_seconds": age},
                )
            # Shared dispatcher owns serialization vs Close All / square-off / etc.
            self._request_market_exit(
                trade, reason="feed_stale", kind="emergency", actor="engine"
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

    def _sl_attempt_tag(self, trade: TradeRecord, attempt_id: str) -> str:
        """Attempt-specific Kite tag (max 20) — durable identity for SL reconcile."""
        base = str(trade.broker_tag or "NR")[:12]
        return f"{base}P{str(attempt_id)[:7]}"[:20]

    def _protection_lookup_tags(self, trade: TradeRecord) -> list[str]:
        """Tags that may hold this trade's protective stops (trade + latest attempt)."""
        tags: list[str] = []
        seen: set[str] = set()

        def _add(raw: object) -> None:
            text = str(raw or "").strip()
            if not text or text in seen:
                return
            seen.add(text)
            tags.append(text)

        _add(trade.broker_tag)
        for row in reversed(self.store.list_events(trade.trade_id)):
            if str(row["action"]) != "sl_submit_attempt":
                continue
            payload = self._parse_event_payload(row)
            _add(payload.get("tag"))
            break
        return tags

    def _working_stops_for_trade(
        self, trade: TradeRecord, *, terminal_sl: set[str]
    ) -> list[Any]:
        """Unique working stops across trade/attempt tags (deduped by order id)."""
        by_id: dict[str, Any] = {}
        for tag in self._protection_lookup_tags(trade):
            for order in self.broker.orders_by_tag(tag):
                if not _is_stop_order(order):
                    continue
                if str(order.status).upper() in terminal_sl:
                    continue
                if str(getattr(order, "tradingsymbol", "") or "") != str(trade.symbol):
                    continue
                if str(order.transaction_type).upper() != _stop_side(trade.direction):
                    continue
                by_id[str(order.order_id)] = order
        return list(by_id.values())

    def _sl_submit_unresolved(self, trade_id: str) -> bool:
        """True when a durable SL place attempt is outstanding (not cleared)."""
        return self._latest_unresolved_sl_attempt(trade_id) is not None

    def _attempt_ids_cleared(self, trade_id: str) -> set[str]:
        """Attempt ids that have an explicit ``sl_submit_cleared`` event."""
        cleared: set[str] = set()
        for row in self.store.list_events(trade_id):
            if str(row["action"]) != "sl_submit_cleared":
                continue
            payload = self._parse_event_payload(row)
            aid = payload.get("attempt_id")
            if aid is not None and str(aid).strip():
                cleared.add(str(aid))
        return cleared

    def _attempt_id_for_sl_order(
        self,
        trade_id: str,
        order_id: str,
        *,
        order: Any = None,
    ) -> Optional[str]:
        """Map a broker stop order to the durable attempt that owns it."""
        oid = str(order_id)
        tag = str(getattr(order, "tag", "") or "").strip() if order is not None else ""
        matched_tag_id: Optional[str] = None
        for row in self.store.list_events(trade_id):
            action = str(row["action"])
            if action not in {
                "sl_submit_attempt",
                "sl_submission_unknown",
                "sl_placed",
                "sl_submit_cleared",
            }:
                continue
            payload = self._parse_event_payload(row)
            aid = payload.get("attempt_id")
            if aid is None or not str(aid).strip():
                continue
            aid_s = str(aid)
            if payload.get("order_id") is not None and str(payload.get("order_id")) == oid:
                return aid_s
            if tag and str(payload.get("tag") or "") == tag:
                matched_tag_id = aid_s
        return matched_tag_id

    def _latest_unresolved_sl_attempt(
        self, trade_id: str
    ) -> Optional[tuple[int, dict[str, Any]]]:
        """Return (event_index, payload) for the latest uncleared SL submit attempt.

        Clears and unknown merges are attempt-id scoped: a late clear for attempt A
        never resolves a newer outstanding attempt B.

        Missing-ID attempt/unknown records remain fail-closed (never auto-cleared).
        """
        cleared = self._attempt_ids_cleared(trade_id)
        # key -> (idx, merged_payload); key is attempt_id or __missing:<idx>
        by_id: dict[str, tuple[int, dict[str, Any]]] = {}
        order: list[str] = []

        for idx, row in enumerate(self.store.list_events(trade_id)):
            action = str(row["action"])
            payload = self._parse_event_payload(row)
            if action == "sl_submit_attempt":
                aid = payload.get("attempt_id")
                if aid is None or not str(aid).strip():
                    key = f"__missing:{idx}"
                    body = dict(payload)
                    body["malformed_missing_attempt_id"] = True
                    by_id[key] = (idx, body)
                    if key not in order:
                        order.append(key)
                    continue
                aid_s = str(aid)
                by_id[aid_s] = (idx, dict(payload))
                if aid_s not in order:
                    order.append(aid_s)
            elif action == "sl_submission_unknown":
                aid = payload.get("attempt_id")
                if aid is None or not str(aid).strip():
                    key = f"__missing:{idx}"
                    body = dict(payload)
                    body["malformed_missing_attempt_id"] = True
                    by_id[key] = (idx, body)
                    if key not in order:
                        order.append(key)
                    continue
                aid_s = str(aid)
                if aid_s in cleared:
                    continue
                if aid_s not in by_id:
                    by_id[aid_s] = (idx, dict(payload))
                    order.append(aid_s)
                    continue
                prev_idx, prev = by_id[aid_s]
                merged = dict(prev)
                for key_name in ("order_id", "tag", "error"):
                    if (
                        payload.get(key_name) is not None
                        and str(payload.get(key_name)).strip()
                    ):
                        merged[key_name] = payload[key_name]
                by_id[aid_s] = (prev_idx, merged)
            elif action == "sl_submit_cleared":
                aid = payload.get("attempt_id")
                if aid is not None and str(aid).strip():
                    cleared.add(str(aid))

        for aid_s in reversed(order):
            if aid_s.startswith("__missing:"):
                return by_id[aid_s]
            if aid_s in cleared:
                continue
            return by_id[aid_s]
        return None

    def _order_ids_attributed_before_attempt(
        self, trade_id: str, attempt_idx: int
    ) -> set[str]:
        """Stop order ids already attributed to earlier attempts (must not clear newer)."""
        attributed: set[str] = set()
        for idx, row in enumerate(self.store.list_events(trade_id)):
            if idx >= attempt_idx:
                break
            action = str(row["action"])
            if action not in {"sl_placed", "sl_submit_cleared", "sl_submission_unknown"}:
                continue
            payload = self._parse_event_payload(row)
            oid = payload.get("order_id")
            if oid is not None and str(oid).strip():
                attributed.add(str(oid))
        return attributed

    def _order_matches_sl_attempt(
        self,
        trade: TradeRecord,
        order: Any,
        attempt_payload: dict[str, Any],
    ) -> bool:
        """Fail-closed identity checks: stop type, attempt tag, symbol, side."""
        if not _is_stop_order(order):
            return False
        symbol = str(attempt_payload.get("symbol") or trade.symbol)
        if str(getattr(order, "tradingsymbol", "") or "") != symbol:
            return False
        side = str(
            attempt_payload.get("transaction_type") or _stop_side(trade.direction)
        ).upper()
        if str(order.transaction_type).upper() != side:
            return False
        attempt_tag = str(attempt_payload.get("tag") or "").strip()
        if attempt_tag and str(order.tag or "") != attempt_tag:
            return False
        return True

    def _candidates_for_sl_attempt(
        self,
        trade: TradeRecord,
        *,
        attempt_idx: int,
        attempt_payload: dict[str, Any],
    ) -> tuple[str, list[Any]]:
        """Return (status, orders) for attempt-scoped stop reconcile.

        status: ``unique`` | ``none`` | ``ambiguous``

        Fail-closed:
        - Prefer durably captured order_id, else attempt-specific tag.
        - Require symbol + transaction side match.
        - Reject unknown-time (naive/missing) as sole identity; timed matches do not
          suppress competing untimed candidates (ambiguity preserved).
        - Two-second window is supporting evidence only, never unique identity.
        """
        prior_ids = self._order_ids_attributed_before_attempt(
            trade.trade_id, attempt_idx
        )
        captured_oid = attempt_payload.get("order_id")

        # Durable accepted order id — poll that identity only.
        if captured_oid is not None and str(captured_oid).strip():
            oid = str(captured_oid)
            if oid in prior_ids:
                return "none", []
            order = self.broker.poll_order(oid)
            if order is None:
                return "none", []
            if not self._order_matches_sl_attempt(trade, order, attempt_payload):
                return "none", []
            return "unique", [order]

        attempt_tag = str(attempt_payload.get("tag") or "").strip()
        if not attempt_tag:
            # No durable tag/order identity — refuse heuristic clearing.
            return "none", []

        stops = [
            o
            for o in self.broker.orders_by_tag(attempt_tag)
            if str(o.order_id) not in prior_ids
            and self._order_matches_sl_attempt(trade, o, attempt_payload)
        ]
        if not stops:
            return "none", []

        submitted_at = _parse_aware_instant(attempt_payload.get("submitted_at"))
        timed: list[Any] = []
        untimed: list[Any] = []
        skew = timedelta(seconds=2)
        for order in stops:
            ots = _parse_aware_instant(getattr(order, "order_timestamp", None))
            if ots is None:
                untimed.append(order)
                continue
            if submitted_at is None:
                # Without aware attempt time, timestamps cannot support filtering.
                untimed.append(order)
                continue
            if ots >= submitted_at - skew:
                timed.append(order)
            # Older-than-window under the same attempt tag stays excluded from timed.

        # Fail-closed: any unknown-time candidate keeps the attempt unresolved /
        # ambiguous; timed hits never silence untimed peers.
        if untimed:
            if len(stops) == 1 and not timed:
                # Single untimed stop is insufficient identity (reject unknown-time fallback).
                return "none", []
            return "ambiguous", list(stops)
        if not timed:
            return "none", []
        if len(timed) > 1:
            return "ambiguous", timed
        return "unique", timed

    def _reconcile_outstanding_sl_attempt(self, trade: TradeRecord) -> str:
        """Reconcile durable unknown SL attempt against working+terminal tag stops.

        Returns:
          ``resolved_working`` — unique working/cover stop applied
          ``resolved_terminal_open`` — unique reject/cancel cleared; position still open
          ``resolved_flat`` — unique fill/reconcile left no open position
          ``still_unresolved`` — none / ambiguous / visibility gap
        """
        outstanding = self._latest_unresolved_sl_attempt(trade.trade_id)
        if outstanding is None:
            return "still_unresolved"
        attempt_idx, attempt_payload = outstanding
        if attempt_payload.get("malformed_missing_attempt_id"):
            self._mark_unresolved_sl_submit(
                trade, detail="malformed_sl_attempt_missing_id"
            )
            return "still_unresolved"
        attempt_id = attempt_payload.get("attempt_id")
        status, candidates = self._candidates_for_sl_attempt(
            trade, attempt_idx=attempt_idx, attempt_payload=attempt_payload
        )
        if status == "none":
            self._mark_unresolved_sl_submit(
                trade, detail="empty_tag_not_absence"
            )
            return "still_unresolved"
        if status == "ambiguous":
            self.store.append_event(
                trade.trade_id,
                "sl_reconcile_ambiguous",
                payload={
                    "detail": "multiple_attempt_candidates",
                    "attempt_id": attempt_id,
                    "order_ids": [str(o.order_id) for o in candidates],
                },
            )
            self._mark_unresolved_sl_submit(
                trade, detail="ambiguous_attempt_candidates"
            )
            return "still_unresolved"

        order = candidates[0]
        terminal_sl = SL_CANCELLED | SL_FILLED
        st = str(order.status).upper()
        self._apply_sl_order(trade, order)
        refreshed = self.store.get_trade(trade.trade_id)
        trade = refreshed if refreshed is not None else trade
        pos = int(trade.remaining_position_qty or 0)

        if st not in terminal_sl:
            # Working stop uniquely matched this attempt.
            if self._sl_submit_unresolved(trade.trade_id):
                self._clear_sl_submit_attempt(
                    trade,
                    outcome="reconciled_working",
                    order_id=str(order.order_id),
                    attempt_id=str(attempt_id) if attempt_id else None,
                )
            return "resolved_working" if pos > 0 else "resolved_flat"

        # Terminal: _apply_sl_order clears on reject/cancel; fills may leave exposure.
        if pos <= 0:
            if self._sl_submit_unresolved(trade.trade_id):
                self._clear_sl_submit_attempt(
                    trade,
                    outcome="reconciled_terminal_flat",
                    order_id=str(order.order_id),
                    attempt_id=str(attempt_id) if attempt_id else None,
                )
            return "resolved_flat"
        # Reject/cancel with open position should already clear inside apply; if not, clear.
        if self._sl_submit_unresolved(trade.trade_id) and st in SL_CANCELLED:
            self._clear_sl_submit_attempt(
                trade,
                outcome="reconciled_rejected_or_cancelled",
                order_id=str(order.order_id),
                attempt_id=str(attempt_id) if attempt_id else None,
            )
        if self._sl_submit_unresolved(trade.trade_id):
            self._mark_unresolved_sl_submit(trade, detail="terminal_not_cleared")
            return "still_unresolved"
        return "resolved_terminal_open"

    def _clear_sl_submit_attempt(
        self,
        trade: TradeRecord,
        *,
        outcome: str,
        order_id: Optional[str] = None,
        attempt_id: Optional[str] = None,
        order: Any = None,
    ) -> None:
        """Clear one durable SL attempt by verified identity only.

        Requires an explicit ``attempt_id`` or an ``order_id`` that positively maps to
        an attempt. Never infers/clears the latest outstanding attempt. A clear for
        attempt A never resolves a different outstanding attempt B.
        """
        target = str(attempt_id).strip() if attempt_id is not None else ""
        if target.startswith("__missing:"):
            self.store.append_event(
                trade.trade_id,
                "sl_clear_ignored_missing_identity",
                payload={
                    "outcome": outcome,
                    "detail": "refuses_malformed_attempt_key",
                    "order_id": order_id,
                },
            )
            return
        if not target and order_id is not None and str(order_id).strip():
            found = self._attempt_id_for_sl_order(
                trade.trade_id, str(order_id), order=order
            )
            if found:
                target = found
            else:
                outstanding = self._latest_unresolved_sl_attempt(trade.trade_id)
                self.store.append_event(
                    trade.trade_id,
                    "sl_clear_ignored_unmapped_order",
                    payload={
                        "outcome": outcome,
                        "order_id": str(order_id),
                        "outstanding_attempt_id": (
                            None
                            if outstanding is None
                            else outstanding[1].get("attempt_id")
                        ),
                    },
                )
                return
        if not target:
            outstanding = self._latest_unresolved_sl_attempt(trade.trade_id)
            self.store.append_event(
                trade.trade_id,
                "sl_clear_ignored_missing_identity",
                payload={
                    "outcome": outcome,
                    "order_id": order_id,
                    "outstanding_attempt_id": (
                        None
                        if outstanding is None
                        else outstanding[1].get("attempt_id")
                    ),
                },
            )
            return

        if target in self._attempt_ids_cleared(trade.trade_id):
            return

        body: dict[str, Any] = {
            "outcome": outcome,
            "order_id": order_id,
            "attempt_id": target,
            "protection_deadline_at": trade.protection_deadline_at,
        }
        self.store.append_event(
            trade.trade_id,
            "sl_submit_cleared",
            payload=body,
        )

    def _mark_unresolved_sl_submit(
        self, trade: TradeRecord, *, detail: str
    ) -> None:
        """Visibly unresolved protection emergency; preserve protection deadline."""
        self.store.update_trade(
            trade.trade_id,
            status="reconciliation_required",
            protected_qty=0,
        )
        self.store.append_event(
            trade.trade_id,
            "protection_emergency_unresolved",
            payload={"detail": detail},
        )

    def _unresolved_sl_submit_blocks_flatten(self, trade: TradeRecord) -> bool:
        """Unknown stop submit blocks competing flatten even without a known stop id."""
        return self._sl_submit_unresolved(trade.trade_id)

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
        for trade in self._iter_management_trades():
            if not self._is_positively_owned_engine_trade(trade):
                continue
            if trade.status in {"closed", "skipped", "rejected"}:
                continue
            rem_entry = int(trade.remaining_entry_qty or 0)
            if (
                int(trade.remaining_position_qty or 0) <= 0
                and rem_entry <= 0
                and self._broker_is_flat(trade)
            ):
                continue
            for reason, kind in DURABLE_MARKET_EXIT_OWNERS:
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

        # Unknown protective-stop submission blocks competing flatten even without
        # a known stop id — a late stop fill could reverse a flatten.
        if self._unresolved_sl_submit_blocks_flatten(trade):
            self.store.update_trade(
                trade.trade_id,
                status="reconciliation_required",
                protected_qty=0,
            )
            self.store.append_event(
                trade.trade_id,
                "flatten_blocked_unknown_stop_submit",
                payload={
                    "reason": reason,
                    "kind": kind,
                    "sl_order_id": trade.sl_order_id,
                },
            )
            self.store.append_event(
                trade.trade_id,
                "protection_emergency_unresolved",
                payload={"detail": "flatten_blocked", "exit_reason": reason},
            )
            return

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
        if self._feed_blocks_price_actions():
            return
        for trade in self._iter_management_trades():
            if not self._is_positively_owned_engine_trade(trade):
                continue
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
        for trade in self._iter_management_trades():
            if not self._is_positively_owned_engine_trade(trade):
                continue
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
        if quote.quantity != 0:
            remaining = int(trade.remaining_position_qty or 0)
            if remaining > 0 and not self._broker_qty_matches_engine(
                trade, int(quote.quantity), remaining
            ):
                self._raise_broker_truth_incident(
                    trade,
                    kind="qty_mismatch",
                    payload={
                        "net": int(quote.quantity),
                        "engine_qty": remaining,
                        "source": "mark_to_market",
                    },
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
