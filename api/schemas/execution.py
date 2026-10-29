"""Request/response shapes for the /execution API."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from engine_config import (
    ARB_DEFAULT_TOTAL_CAPITAL_RUPEES,
    ARB_UNLOCKED_SCALE,
    DEFAULT_DAILY_LOSS_CAP_RUPEES,
    DEFAULT_LEVERAGE_FACTOR,
    DEFAULT_PER_TRADE_CAP_RUPEES,
    DEFAULT_PER_TRADE_CAP_VWAP_LIMITED_RUPEES,
    DEFAULT_TOTAL_CAPITAL_RUPEES,
    STRATEGY_LEGACY_VWAP,
)


class SessionCaps(BaseModel):
    """The editable caps and capital, read once at start and then fixed.

    Small values validate cheaply against real Kite calls; normal values trade
    for real. Same code path either way, which is the point.
    """

    per_trade_cap_rupees: float = DEFAULT_PER_TRADE_CAP_RUPEES
    per_trade_cap_vwap_limited_rupees: float = DEFAULT_PER_TRADE_CAP_VWAP_LIMITED_RUPEES
    daily_loss_cap_rupees: float = DEFAULT_DAILY_LOSS_CAP_RUPEES
    total_capital_rupees: float = DEFAULT_TOTAL_CAPITAL_RUPEES
    leverage_factor: float = DEFAULT_LEVERAGE_FACTOR


class ExecutionStartRequest(BaseModel):
    caps: SessionCaps = Field(default_factory=SessionCaps)
    session_date: Optional[str] = None
    live_orders: bool = False
    # "legacy_vwap" (default) or "arb". In ARB only the stage and the capital
    # are chosen at start; the per-trade and daily caps above are not used.
    strategy_mode: str = STRATEGY_LEGACY_VWAP
    arb_risk_scale: float = ARB_UNLOCKED_SCALE
    # None = the saved setting (execution_settings.json); False = ORB only.
    arb_funnel_enabled: Optional[bool] = None


class ArbStageView(BaseModel):
    """One rollout stage, with the rupee figures it trades at."""

    key: str
    risk_scale: float
    unlocked: bool
    orb_risk: float
    funnel_risks: List[float]
    open_risk_cap: float
    min_risk: float
    hard_day_stop: float
    day_lock_arm: float
    closed_loss_backstop: float


class ExecutionSettingsView(BaseModel):
    """What the 09:15 autostart launches, and the stages ARB can run at."""

    autostart_strategy: str = STRATEGY_LEGACY_VWAP
    arb_risk_scale: float = ARB_UNLOCKED_SCALE
    arb_total_capital_rupees: float = ARB_DEFAULT_TOTAL_CAPITAL_RUPEES
    arb_funnel_enabled: bool = True
    updated_at: Optional[str] = None
    updated_by: Optional[str] = None
    unlocked_scale: float = ARB_UNLOCKED_SCALE
    day_lock_keep: float = 0.0
    stages: List[ArbStageView] = Field(default_factory=list)


class ExecutionSettingsRequest(BaseModel):
    autostart_strategy: str
    arb_risk_scale: float = ARB_UNLOCKED_SCALE
    arb_total_capital_rupees: float = ARB_DEFAULT_TOTAL_CAPITAL_RUPEES
    # None keeps the saved switch, so a desk that does not send it never
    # turns the funnel back on by accident.
    arb_funnel_enabled: Optional[bool] = None


class ExecutionStartResponse(BaseModel):
    success: bool
    message: str
    pid: Optional[int] = None


class PreconditionView(BaseModel):
    key: str
    ok: bool
    detail: str


class PreflightResponse(BaseModel):
    can_start: bool
    checks: List[PreconditionView]
    engine_state: str
    engine_reason: Optional[str] = None
    refusals: List[str] = Field(default_factory=list)


class CapitalView(BaseModel):
    total_capital_rupees: float
    leverage_factor: float
    buying_power_rupees: float
    margin_used_rupees: float
    remaining_capital_rupees: float
    remaining_buying_power_rupees: float


class ExecutionStatusResponse(BaseModel):
    # "running" | "stopped" | "crashed" | "absent"
    engine_state: str
    engine_reason: Optional[str] = None
    heartbeat_age_seconds: Optional[float] = None
    stopped_on_purpose: bool = False
    stop_reason: Optional[str] = None
    run_id: Optional[str] = None
    session_date: Optional[str] = None
    is_live: bool = False
    tick_count: int = 0
    entries_allowed: bool = False
    entries_stopped: bool = False
    entries_paused: bool = False
    pause_reason: Optional[str] = None
    open_positions: int = 0
    unprotected: int = 0
    realised_loss_today: float = 0.0
    daily_loss_cap: float = 0.0
    remaining_daily: float = 0.0
    caps: SessionCaps = Field(default_factory=SessionCaps)
    capital: Optional[CapitalView] = None
    total_live_pnl: Optional[float] = None
    live_pnl_as_of: Optional[str] = None
    live_pnl_complete: bool = False
    # Desk summary: Total Day = Total Realised + Total Ongoing.
    total_day_pnl: Optional[float] = None
    total_realised_pnl: Optional[float] = None
    total_ongoing_pnl: Optional[float] = None
    # The live Open P&L feed: "live" (WebSocket), "fallback" (Kite REST,
    # feed stale), "off" (PAPER), or None before the engine has written any.
    live_pnl_feed_state: Optional[str] = None
    live_pnl_feed_reason: Optional[str] = None
    live_pnl_last_tick_at: Optional[str] = None
    # Kite order-update pushes the engine's WebSocket has received this run.
    live_pnl_order_updates: Optional[int] = None
    last_error: Optional[str] = None
    escalations: Dict[str, str] = Field(default_factory=dict)
    # "legacy_vwap" or "arb": what the running (or last) session was started as.
    strategy_mode: str = STRATEGY_LEGACY_VWAP
    # ARB's day state, written by the engine every tick (engine_core.arb_snapshot).
    arb: Optional[Dict[str, Any]] = None


class PositionView(BaseModel):
    trade_id: str
    setup_id: str
    session_date: str
    tradingsymbol: str
    direction: str
    vwap_classification: Optional[str] = None
    state: str
    qty: int = 0
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    # Risk at the fill (what the slippage check judged); never changes.
    risk_taken_rupees: Optional[float] = None
    # Loss if the current stop filled now: follows every stop move. Negative
    # once profit is locked in. Open positions only.
    current_risk_rupees: Optional[float] = None
    realised_pnl: Optional[float] = None
    live_pnl: Optional[float] = None
    # "ws" (live WebSocket tick) or "kite_rest" (fallback), with why.
    live_pnl_source: Optional[str] = None
    live_pnl_reason: Optional[str] = None
    # This trade's P&L plus earlier closed trades in the same stock today.
    stock_day_total: Optional[float] = None
    # Kite's day figure for the stock vs our per-trade sum, when over Rs 1 apart.
    pnl_mismatch: Optional[Dict[str, float]] = None
    # Closed but no closing order found at Kite: "unattributed — check Kite".
    realised_unattributed: bool = False
    entry_order_id: Optional[str] = None
    stop_order_id: Optional[str] = None
    exit_order_id: Optional[str] = None
    is_live: bool = False
    run_id: Optional[str] = None
    close_reason: Optional[str] = None
    skip_reason: Optional[str] = None
    stop_adopted_from_broker: bool = False
    # The structural stop the trade was first protected with (the trailing
    # floor). stop_price is the current stop while open, the final one once closed.
    initial_stop_price: Optional[float] = None
    # Auto-trail on/off, and Kite modifications counted on the live stop order.
    trail_enabled: bool = False
    stop_mod_count: int = 0
    # An exit is under way (waiting on its stop cancel, or a safety exit).
    exiting: bool = False
    manual_review: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    # --- ARB (None / False on a trade ARB did not take) ---
    # "ORB", or "F0".."F3" by funnel score.
    source: Optional[str] = None
    # What ARB decided at entry: base and used risk, half size, score parts.
    arb: Optional[Dict[str, Any]] = None
    # The price the backtest assumed (trigger price; the level for ORB), and
    # how much worse the fill was, in basis points.
    signal_price: Optional[float] = None
    entry_slippage_bps: Optional[float] = None
    # "initial" | "locked" (+0.25R) | "giveback" (25% of the peak gain), read
    # from the stop Kite holds.
    lock_stage: Optional[str] = None
    # The stop is at or beyond entry: the trade no longer uses the open-risk budget.
    risk_freed: bool = False
    # Closed trades: the result in R before charges.
    r_gross: Optional[float] = None


class PositionsResponse(BaseModel):
    session_date: str
    open: List[PositionView] = Field(default_factory=list)
    closed: List[PositionView] = Field(default_factory=list)
    rejected: List[PositionView] = Field(default_factory=list)
    total_live_pnl: Optional[float] = None
    live_pnl_as_of: Optional[str] = None
    live_pnl_complete: bool = False
    # Desk summary: Total Day = Total Realised + Total Ongoing.
    total_day_pnl: Optional[float] = None
    total_realised_pnl: Optional[float] = None
    total_ongoing_pnl: Optional[float] = None
    # The live Open P&L feed: "live" (WebSocket), "fallback" (Kite REST,
    # feed stale), "off" (PAPER), or None before the engine has written any.
    live_pnl_feed_state: Optional[str] = None
    live_pnl_feed_reason: Optional[str] = None
    live_pnl_last_tick_at: Optional[str] = None
    # Kite order-update pushes the engine's WebSocket has received this run.
    live_pnl_order_updates: Optional[int] = None


class EventView(BaseModel):
    event_id: int
    trade_id: str
    at: str
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class EventsResponse(BaseModel):
    trade_id: str
    events: List[EventView] = Field(default_factory=list)


class ExecutionCommandRequest(BaseModel):
    # "stop" | "start" | "close_position" | "kill_all" | "move_stop" | "set_trail"
    kind: str
    trade_id: Optional[str] = None
    # move_stop: ticks = +1 / -1 (price terms). set_trail: enabled = true / false.
    ticks: Optional[int] = None
    enabled: Optional[bool] = None


class ExecutionCommandResponse(BaseModel):
    command_id: int
    kind: str
    status: str
    trade_id: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    at: Optional[str] = None
    applied_at: Optional[str] = None
