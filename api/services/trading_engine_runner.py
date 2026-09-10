"""Start/stop/status for the trading-engine child process."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from api import config
from api.services.trading_engine_start_lock import (
    TradingEngineStartBusy,
    acquire_start_lock,
    is_start_lease_active,
    reconcile_start_lock_with_heartbeat,
    release_start_lock,
    update_start_lock_pid,
)
from trading_engine_types import DEFAULT_TOTAL_CAPITAL

IST = ZoneInfo("Asia/Kolkata")
STALE_SECONDS = 30


def _today_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def _status_file() -> Path:
    return config.trading_engine_status_file()


def _stop_file() -> Path:
    return config.trading_engine_stop_file()


def read_heartbeat() -> Optional[dict]:
    path = _status_file()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def is_heartbeat_fresh(*, expected_session_date: Optional[str] = None) -> bool:
    data = read_heartbeat()
    if not data:
        return False
    if expected_session_date and data.get("session_date") != expected_session_date:
        return False
    updated_at = data.get("updated_at")
    if not updated_at:
        return False
    try:
        updated = datetime.fromisoformat(str(updated_at))
    except ValueError:
        return False
    if updated.tzinfo is None:
        return False
    age = (datetime.now(IST) - updated.astimezone(IST)).total_seconds()
    return 0 <= age < STALE_SECONDS


def heartbeat_indicates_running(*, expected_session_date: Optional[str] = None) -> bool:
    if not is_heartbeat_fresh(expected_session_date=expected_session_date):
        return False
    data = read_heartbeat()
    if not data:
        return False
    if data.get("running") is False:
        return False
    if data.get("running") is True:
        return True
    return str(data.get("state") or "") not in {"stopped", "error"}


def is_engine_running(*, session_date: Optional[str] = None) -> bool:
    date = session_date or _today_ist()
    hb_running = heartbeat_indicates_running(expected_session_date=date)
    reconcile_start_lock_with_heartbeat(heartbeat_fresh=hb_running)
    if hb_running:
        return True
    return is_start_lease_active(date)


def _build_command(*, live_orders: bool, session_date: str, total_capital: float) -> list[str]:
    cmd = [
        sys.executable,
        "live_trading_engine.py",
        "--status-file",
        str(_status_file()),
        "--stop-file",
        str(_stop_file()),
        "--trading-db",
        str(config.trading_engine_db_path()),
        "--live-db",
        str(config.live_db_path()),
        "--session-date",
        session_date,
        "--until-session-close",
        "--total-capital",
        str(total_capital),
    ]
    if live_orders:
        cmd.append("--live-orders")
    return cmd


def start_trading_engine(
    session_date: Optional[str] = None,
    *,
    confirm_live_orders: bool = False,
    total_capital: float = DEFAULT_TOTAL_CAPITAL,
) -> Tuple[bool, str, Optional[int]]:
    date = session_date or _today_ist()
    if is_engine_running(session_date=date):
        return False, "Trading engine is already running", None
    live_wanted = bool(confirm_live_orders)
    if live_wanted and os.environ.get("NIFTY_RADAR_LIVE_WRITES_AUTHORIZED") != "1":
        return False, "LIVE execution is locked; supervised live authorization required", None

    try:
        lock_file = acquire_start_lock(date)
    except TradingEngineStartBusy as exc:
        return False, str(exc), None

    stop = _stop_file()
    if stop.exists():
        try:
            stop.unlink()
        except OSError:
            pass
    _ack_pending_stop_engine()

    env = os.environ.copy()
    env["TRADING_ENGINE_STATUS_FILE"] = str(_status_file())
    if live_wanted:
        env["TRADING_ENGINE_LIVE_ORDERS"] = "true"
    else:
        env["TRADING_ENGINE_LIVE_ORDERS"] = "false"

    try:
        proc = subprocess.Popen(
            _build_command(
                live_orders=live_wanted, session_date=date, total_capital=total_capital
            ),
            cwd=str(config.ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        release_start_lock(lock_file)
        return False, f"Failed to start trading engine: {exc}", None

    try:
        update_start_lock_pid(lock_file, pid=proc.pid, session_date=date)
    except OSError:
        pass
    return True, f"Trading engine started (pid {proc.pid})", proc.pid


def _ack_pending_stop_engine() -> None:
    try:
        from trading_engine_store import TradingEngineStore

        db = config.trading_engine_db_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        store = TradingEngineStore(db)
        try:
            store.ack_pending_commands("stop_engine")
        finally:
            store.close()
    except OSError:
        pass


def stop_trading_engine() -> Tuple[bool, str]:
    # A normal stop must drain, not terminate the process with live exposure.
    if is_engine_running():
        try:
            from trading_engine_store import TradingEngineStore

            db = config.trading_engine_db_path()
            if db.exists():
                store = TradingEngineStore(db)
                try:
                    store.enqueue_command("stop_engine")
                finally:
                    store.close()
        except OSError:
            pass
    return True, "Drain requested. Entries disarm; protection continues until flat and reconciled."
