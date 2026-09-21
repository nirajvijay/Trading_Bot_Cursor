"""Pure ownership / recoverable-exposure predicates shared by engine and control."""

from __future__ import annotations

from typing import Any, Optional

from trading_engine_types import ACTIVE_STATES


def trade_provenance_known(trade: Any) -> bool:
    """True when run_id is non-blank and entry mode provenance is stamped."""
    run_id = str(getattr(trade, "run_id", None) or "").strip()
    return bool(run_id) and getattr(trade, "entry_live_orders_enabled", None) is not None


def trade_has_recoverable_exposure(trade: Any) -> bool:
    """True for active engine states that still imply entry/position/exit risk."""
    status = str(getattr(trade, "status", "") or "")
    if status not in ACTIVE_STATES:
        return False
    if int(getattr(trade, "remaining_position_qty", 0) or 0) > 0:
        return True
    if int(getattr(trade, "remaining_entry_qty", 0) or 0) > 0:
        return True
    if status in {
        "entry_submitting",
        "submission_unknown",
        "exit_pending",
        "reconciliation_required",
    }:
        return True
    return False


def control_incident_trade(trade: Any) -> bool:
    """Recovery/control visibility without waiting for an engine tick.

    Always surface reconciliation_required. Surface provenance-unknown rows that
    still have recoverable exposure (including zero-fill pending/submission_unknown).
    Terminal flat records (closed/skipped/rejected without exposure) stay hidden.
    """
    if str(getattr(trade, "status", "") or "") == "reconciliation_required":
        return True
    if trade_provenance_known(trade):
        return False
    return trade_has_recoverable_exposure(trade)


def unknown_requires_reconciliation_hold(
    trade: Any, *, engine_session_date: Optional[str]
) -> bool:
    """Hold filled/prior-session unknown exposure — not unstamped same-session intents."""
    if engine_session_date is not None and getattr(trade, "session_date", None) != engine_session_date:
        return True
    if int(getattr(trade, "remaining_position_qty", 0) or 0) > 0:
        return True
    if int(getattr(trade, "filled_qty", 0) or 0) > 0:
        return True
    return False
