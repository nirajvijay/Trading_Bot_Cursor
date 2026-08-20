"""
Live trading-engine process. Consumes continuation TRIGGERED rows and
owns demo (or gated live) trade state. Observation runner is untouched.

Default: FakeBroker, TRADING_ENGINE_LIVE_ORDERS=false. No kite.place_order.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from api import config
from api.services.observation_runner import seconds_until_session_close
from trading_engine_broker import FakeBroker, KiteBroker
from trading_engine_cycle import TradingEngineCycle
from trading_engine_store import TradingEngineStore
from trading_engine_types import DEFAULT_TOTAL_CAPITAL, DEMO_LEVERAGE_FACTOR

IST = ZoneInfo("Asia/Kolkata")
_STOP = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NIFTY RADAR trading engine (V1)")
    parser.add_argument("--status-file", default=None)
    parser.add_argument("--stop-file", default=None)
    parser.add_argument("--trading-db", default=None)
    parser.add_argument("--live-db", default=None)
    parser.add_argument("--session-date", default=None)
    parser.add_argument("--until-session-close", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--live-orders", action="store_true")
    parser.add_argument("--total-capital", type=float, default=DEFAULT_TOTAL_CAPITAL)
    return parser.parse_args(argv)


def _request_stop(*_args: object) -> None:
    global _STOP
    _STOP = True


def _stop_requested(stop_file: Optional[Path]) -> bool:
    if _STOP:
        return True
    return bool(stop_file and stop_file.exists())


def _make_broker(live: bool, total_capital: float):
    if not live:
        return FakeBroker(
            auto_fill_entry=True,
            auto_confirm_sl=True,
            remaining_capital=total_capital,
        )
    from login import _get_kite

    kite = _get_kite()
    return KiteBroker(kite, live_orders_enabled=True)


def run(args: argparse.Namespace) -> int:
    session_date = args.session_date or _today_ist()
    live = bool(args.live_orders)
    status_file = Path(args.status_file or config.trading_engine_status_file())
    stop_file = Path(args.stop_file or config.trading_engine_stop_file())
    trading_db = Path(args.trading_db or config.trading_engine_db_path())
    live_db = Path(args.live_db or config.live_db_path())

    if stop_file.exists():
        try:
            stop_file.unlink()
        except OSError:
            pass

    store = TradingEngineStore(trading_db)
    started_at = _utc_now()
    run_id = store.start_run(
        session_date=session_date,
        live_orders_enabled=live,
        pid=os.getpid(),
        total_capital=float(args.total_capital),
    )
    broker = _make_broker(live, float(args.total_capital))
    cycle = TradingEngineCycle(
        store,
        broker,
        live_db=live_db,
        session_date=session_date,
        started_at=started_at,
        run_id=run_id,
        live_orders_enabled=live,
        leverage_factor=DEMO_LEVERAGE_FACTOR,
        status_file=status_file,
    )

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    deadline = None
    if args.until_session_close:
        deadline = time.monotonic() + seconds_until_session_close()

    try:
        while not _stop_requested(stop_file):
            if deadline is not None and time.monotonic() >= deadline:
                break
            try:
                cycle.tick()
            except Exception as exc:  # noqa: BLE001
                cycle.last_error = str(exc)
                store.set_run_status(run_id, "error", last_error=str(exc))
                cycle.write_status()
                raise
            time.sleep(max(0.2, float(args.poll_seconds)))
    finally:
        cycle.running = False
        cycle.consume_new_triggers = False
        store.set_consume_triggers(run_id, False)
        store.ack_pending_commands("stop_engine")
        store.set_run_status(run_id, "stopped", stopped=True)
        cycle.write_status()
        store.close()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
