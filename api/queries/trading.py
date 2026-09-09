"""Read snapshots from trading_engine.db."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from api import config
from api.admin_config.store import AdminConfigStore
from trading_engine_cycle import snapshot_dict
from trading_engine_store import TradingEngineStore
from trading_engine_types import DEFAULT_TOTAL_CAPITAL, DEMO_LEVERAGE_FACTOR


def empty_snapshot(session_date: str) -> dict[str, Any]:
    return {
        "state": "stopped",
        "session_date": session_date,
        "live_orders_enabled": False,
        "unprotected_count": 0,
        "limits_protected": True,
        "closed_loss_today": 0.0,
        "committed_risk": 0.0,
        "remaining_daily": 3000.0,
        "live_pnl": 0.0,
        "total_capital": DEFAULT_TOTAL_CAPITAL,
        "leverage_factor": DEMO_LEVERAGE_FACTOR,
        "margin_used": 0.0,
        "remaining_capital": DEFAULT_TOTAL_CAPITAL,
        "buying_power": DEFAULT_TOTAL_CAPITAL * DEMO_LEVERAGE_FACTOR,
        "last_error": None,
        "accepting_triggers": False,
        "require_vwap_accept": True,
        "active": [],
        "closed": [],
        "skipped": [],
    }


def load_snapshot(db_path: Path, session_date: str, *, running: bool) -> dict[str, Any]:
    if not db_path.exists():
        return empty_snapshot(session_date)
    store = TradingEngineStore(db_path)
    try:
        run = store.latest_run()
        if run is None:
            return empty_snapshot(session_date)
        date = session_date
        admin_store = AdminConfigStore(config.admin_config_db_path(), read_only=True)
        try:
            admin_payload = admin_store.load_effective_payload()
            entries_paused = admin_store.read_entries_paused()
        finally:
            admin_store.close()
        require_vwap = True
        if "require_vwap_accept" in run.keys() and run["require_vwap_accept"] is not None:
            require_vwap = bool(run["require_vwap_accept"])
        arm = store.session_arm(date)
        return snapshot_dict(
            store,
            session_date=date,
            total_capital=float(run["total_capital"]),
            leverage_factor=float(run["leverage"] or DEMO_LEVERAGE_FACTOR),
            live_orders_enabled=bool(run["live_orders_enabled"]),
            running=running,
            last_error=None if run["last_error"] is None else str(run["last_error"]),
            accepting_triggers=bool(running and not entries_paused and arm and arm["armed"] and arm["run_id"] == run["run_id"]),
            require_vwap_accept=require_vwap,
            daily_loss_cap=float(admin_payload["daily_loss_cap_inr"]),
            per_trade_cap=float(admin_payload["per_trade_risk_cap_inr"]),
        )
    finally:
        store.close()
