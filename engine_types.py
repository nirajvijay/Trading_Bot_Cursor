"""Domain types for the rebuilt execution engine.

Signal detection is untouched: TriggerCandidate is imported from
trading_engine_types and flows in unchanged. Everything below this line is
new engine-side vocabulary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from trading_engine_types import TriggerCandidate  # noqa: F401  (re-exported)


class ExecutionState(str, Enum):
    """Explicit lifecycle states for a position under management.

    Replaces the implicit state (inferred from a scatter of nullable fields
    on TradeRecord) that trading_engine_cycle.py's tick() had to re-derive
    on every call. See engine_orders.ALLOWED_TRANSITIONS for the legal graph.
    """

    PENDING_ENTRY = "pending_entry"      # sized, not yet submitted to broker
    ENTRY_SUBMITTED = "entry_submitted"  # order placed, awaiting fill
    ENTERED = "entered"                  # filled, no protective stop yet
    PROTECTED = "protected"              # stop is live at the broker
    TRAILING = "trailing"                # stop has moved at least once
    EXIT_SUBMITTED = "exit_submitted"    # square-off order placed
    CLOSED = "closed"                    # flat, realised P&L booked
    REJECTED = "rejected"                # broker/risk rejected before fill
    CANCELLED = "cancelled"              # withdrawn before fill (e.g. paused)


@dataclass(frozen=True)
class RiskLimits:
    per_trade_cap_rupees: float
    per_trade_cap_vwap_limited_rupees: float
    daily_loss_cap_rupees: float


@dataclass(frozen=True)
class SizeDecision:
    qty: int
    risk_based_qty: int
    capital_based_qty: int
    binding_constraint: str  # "risk" | "capital"


@dataclass
class Position:
    """The engine's unit of work: one candidate being sized, entered,
    protected, trailed, and exited."""

    trade_id: str
    candidate: TriggerCandidate
    state: ExecutionState
    qty: int = 0
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    entry_order_id: Optional[str] = None
    stop_order_id: Optional[str] = None
    exit_order_id: Optional[str] = None
    realised_pnl: Optional[float] = None
    # The real post-fill risk (qty x |real fill - stop|), compared against
    # 1.5x the tier cap. Top-level rather than buried in extra because it is
    # central to the abnormal-slippage decision.
    risk_taken_rupees: Optional[float] = None
    # Which BrokerPort implementation produced this trade, and which engine
    # run — both needed to keep real trades cleanly separable from test ones
    # and to disambiguate a mid-day restart.
    is_live: bool = False
    run_id: Optional[str] = None
    extra: dict = field(default_factory=dict)
