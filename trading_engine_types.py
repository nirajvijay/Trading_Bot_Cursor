"""Shared types and constants for the Version 1 trading engine.

No I/O. Observation runner must not import this for orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, Literal, Optional

PER_TRADE_RISK_CAP = 900.0
DAILY_LOSS_CAP = 3000.0
DEFAULT_TOTAL_CAPITAL = 300_000.0
DEMO_LEVERAGE_FACTOR = 5.0

TradeStatus = Literal[
    "candidate",
    "entry_submitting",
    "entry_filled",
    "stop_pending",
    "protected_open",
    "closed",
    "skipped",
    "rejected",
]

RISK_CONSUMING_STATES: FrozenSet[str] = frozenset(
    {"entry_submitting", "entry_filled", "stop_pending", "protected_open"}
)
UNPROTECTED_STATES: FrozenSet[str] = frozenset({"entry_filled", "stop_pending"})
ACTIVE_STATES: FrozenSet[str] = frozenset(
    {"entry_submitting", "entry_filled", "stop_pending", "protected_open"}
)
IN_FLIGHT_BLOCK_STATES: FrozenSet[str] = frozenset(
    {"entry_submitting", "entry_filled", "stop_pending"}
)
SKIPPED_STATES: FrozenSet[str] = frozenset({"skipped", "rejected"})

EngineState = Literal["stopped", "starting", "running", "error", "critical"]

CommandKind = Literal["trail_stop", "stop_engine", "set_auto_trail"]

STOP_ORDER_TYPES: FrozenSet[str] = frozenset({"SL", "SL-M"})


@dataclass(frozen=True)
class TriggerCandidate:
    setup_id: str
    continuation_rule_version: str
    session_date: str
    tradingsymbol: str
    instrument_token: int
    direction: str
    trigger_price: float
    pullback_swing_high: Optional[float]
    pullback_swing_low: Optional[float]
    tick_size: float
    buffer_ticks: int
    trigger_exchange_ts: Optional[str]
    created_at: str
    last_price: Optional[float] = None


@dataclass
class TradeRecord:
    trade_id: str
    setup_id: str
    continuation_rule_version: str
    broker_tag: str
    session_date: str
    symbol: str
    instrument_token: int
    direction: str
    qty: int
    entry_estimate: float
    entry_fill: Optional[float]
    initial_stop: Optional[float]
    current_stop: Optional[float]
    exit_fill: Optional[float]
    tick_size: float
    notional: float
    margin_blocked: float
    status: str
    skip_reason: Optional[str]
    reject_reason: Optional[str]
    close_reason: Optional[str]
    entry_order_id: Optional[str]
    sl_order_id: Optional[str]
    realised_pnl: float
    open_pnl: float
    closed_loss_contribution: float
    trigger_time: Optional[str]
    entry_time: Optional[str]
    close_time: Optional[str]
    created_at: str
    updated_at: str
    auto_trail_enabled: bool = False
    auto_trail_ticks: Optional[int] = None
    auto_trail_extreme: Optional[float] = None


@dataclass
class RiskSnapshot:
    closed_loss_today: float
    committed_risk: float
    remaining_daily: float
    per_trade_cap: float = PER_TRADE_RISK_CAP
    daily_cap: float = DAILY_LOSS_CAP
    unprotected_count: int = 0
    in_flight_count: int = 0
    limits_protected: bool = True


@dataclass
class CapitalSnapshot:
    total_capital: float
    leverage_factor: float
    margin_used: float
    remaining_capital: float
    buying_power: float


@dataclass
class SizeDecision:
    allow: bool
    qty: int
    initial_stop: Optional[float]
    risk_per_share: float
    proposed_risk: float
    notional: float
    margin_blocked: float
    reason: str = ""
    kind: Literal["accept", "skipped", "rejected"] = "accept"


@dataclass
class BrokerOrder:
    order_id: str
    tag: str
    tradingsymbol: str
    transaction_type: str
    order_type: str
    quantity: int
    status: str
    average_price: Optional[float] = None
    trigger_price: Optional[float] = None
    price: Optional[float] = None
    product: str = "MIS"
    variety: str = "regular"
    exchange: str = "NSE"


@dataclass
class MarginQuote:
    ok: bool
    required: float
    reason: str = ""


@dataclass
class PositionQuote:
    quantity: int
    average_price: Optional[float] = None
    last_price: Optional[float] = None
    pnl: Optional[float] = None
    unrealised: Optional[float] = None
    realised: Optional[float] = None


@dataclass
class EngineCommand:
    command_id: int
    kind: str
    trade_id: Optional[str]
    payload_json: str
    created_at: str
    processed_at: Optional[str] = None


@dataclass
class Heartbeat:
    state: EngineState
    session_date: str
    live_orders_enabled: bool
    unprotected_count: int
    committed_risk: float
    live_pnl: float
    updated_at: str
    last_error: Optional[str] = None
    open_count: int = 0
    extra: dict = field(default_factory=dict)
