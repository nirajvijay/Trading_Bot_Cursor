"""
Live trading-engine process. Consumes continuation TRIGGERED rows and
owns demo (or gated live) trade state. Observation runner is untouched.

Default: durable PaperBroker with read-only quotes. No kite.place_order.
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
from api.admin_config.store import AdminConfigStore
from api.services.observation_runner import seconds_until_session_close
from trading_engine_broker import FakeBroker, KiteBroker
from trading_engine_cycle import (
    TradingEngineCycle,
    VWAP_PENDING_RETRY_SECONDS,
    feed_age_seconds_from_runner_status,
)
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
    parser.add_argument(
        "--runner-status-file",
        default=None,
        help="Observation runner_status.json for feed freshness (default: RUNNER_STATUS_FILE)",
    )
    return parser.parse_args(argv)


def _request_stop(*_args: object) -> None:
    global _STOP
    _STOP = True


def _stop_requested(stop_file: Optional[Path]) -> bool:
    if _STOP:
        return True
    return bool(stop_file and stop_file.exists())


def _require_vwap_accept_from_env() -> bool:
    """VWAP qualification required (ACCEPT or LIMITED) when enabled (default on)."""
    raw = os.environ.get("TRADING_ENGINE_REQUIRE_VWAP_ACCEPT", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def next_loop_sleep_seconds(
    *,
    has_pending: bool,
    poll_seconds: float,
    elapsed_since_full: float,
    pending_retry: float = VWAP_PENDING_RETRY_SECONDS,
) -> float:
    poll_seconds = max(0.2, float(poll_seconds))
    until_full = max(0.0, poll_seconds - elapsed_since_full)
    if has_pending:
        if until_full <= 0.0:
            return 0.0
        return min(pending_retry, until_full)
    return until_full


def _make_broker(live: bool, total_capital: float, *, paper_db: Optional[Path] = None):
    """Runtime PAPER uses a durable local account; omitted path is isolated-test mode."""
    if not live:
        if paper_db is not None:
            from trading_engine_paper import PaperBroker
            # Lazily obtain only a quote function. PAPER never delegates order methods.
            def quote(symbol):
                from login import _get_kite
                return KiteBroker(_get_kite(), live_orders_enabled=False).touch_quote(symbol)
            return PaperBroker(paper_db, quote_provider=quote, total_capital=total_capital)
        return FakeBroker(
            auto_fill_entry=True,
            auto_confirm_sl=True,
            remaining_capital=total_capital,
        )
    if os.environ.get("NIFTY_RADAR_LIVE_WRITES_AUTHORIZED") != "1":
        raise RuntimeError("LIVE execution is locked; supervised live authorization required")
    from login import _get_kite

    kite = _get_kite()
    return KiteBroker(kite, live_orders_enabled=True)


def _runner_status_path(args: argparse.Namespace) -> Path:
    raw = args.runner_status_file or os.environ.get("RUNNER_STATUS_FILE")
    if raw:
        return Path(raw)
    return Path(config.RUNNER_STATUS_FILE)


def run(args: argparse.Namespace) -> int:
    session_date = args.session_date or _today_ist()
    live = bool(args.live_orders)
    status_file = Path(args.status_file or config.trading_engine_status_file())
    stop_file = Path(args.stop_file or config.trading_engine_stop_file())
    trading_db = Path(args.trading_db or config.trading_engine_db_path())
    live_db = Path(args.live_db or config.live_db_path())
    runner_status_file = _runner_status_path(args)

    if stop_file.exists():
        try:
            stop_file.unlink()
        except OSError:
            pass

    require_vwap = _require_vwap_accept_from_env()
    store = TradingEngineStore(trading_db)
    admin_store = AdminConfigStore(config.admin_config_db_path(), read_only=True)
    entries_paused = admin_store.read_entries_paused()
    consume = not entries_paused
    started_at = _utc_now()
    run_id = store.start_run(
        session_date=session_date,
        live_orders_enabled=live,
        pid=os.getpid(),
        total_capital=float(args.total_capital),
        require_vwap_accept=require_vwap,
    )
    store.set_consume_triggers(run_id, consume)
    broker = _make_broker(live, float(args.total_capital),
                          paper_db=trading_db.with_name(trading_db.stem + "_paper_account.db"))

    def _feed_age() -> Optional[float]:
        return feed_age_seconds_from_runner_status(
            runner_status_file,
            now=datetime.now(IST),
            expected_session_date=session_date,
        )

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
        require_vwap_accept=require_vwap,
        admin_config_db=config.admin_config_db_path(),
        feed_age_seconds_fn=_feed_age,
    )
    cycle.consume_new_triggers = consume
    try:
        cycle.enforce_restart_recovery()
    except Exception:  # noqa: BLE001
        pass

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    deadline = None
    if args.until_session_close:
        deadline = time.monotonic() + seconds_until_session_close()

    poll_seconds = max(0.2, float(args.poll_seconds))
    last_full: Optional[float] = None
    failed = False
    try:
        drain_requested = False
        while cycle.running:
            now = time.monotonic()
            if (_stop_requested(stop_file) or (deadline is not None and now >= deadline)) and not drain_requested:
                store.enqueue_command("stop_engine", payload={"run_id":run_id})
                drain_requested = True
            do_full = last_full is None or (now - last_full) >= poll_seconds
            try:
                if do_full:
                    cycle.tick()
                    last_full = time.monotonic()
                elif cycle.has_pending_vwap():
                    cycle.poll_pending_vwap()
            except Exception as exc:  # noqa: BLE001
                failed = True
                cycle.last_error = str(exc)
                store.set_run_status(run_id, "error", last_error=str(exc))
                cycle.write_status()
                raise
            elapsed = time.monotonic() - (last_full or time.monotonic())
            sleep_for = next_loop_sleep_seconds(
                has_pending=cycle.has_pending_vwap(),
                poll_seconds=poll_seconds,
                elapsed_since_full=elapsed,
            )
            if sleep_for > 0:
                time.sleep(sleep_for)
    finally:
        cycle.drain_pending_vwap()
        cycle.running = False
        cycle.consume_new_triggers = False
        store.set_consume_triggers(run_id, False)
        # Never acknowledge a drain as successful merely because the process exits.
        if failed:
            for pending in store.pending_commands():
                if pending.kind == "stop_engine":
                    store.set_command_state(pending.command_id, "unknown_needs_reconcile",
                                            {"reason": "engine_failed_before_confirmed_drain"})
        store.set_run_status(run_id, "error" if failed else "stopped", stopped=True)
        cycle.write_status()
        cycle.close()
        if hasattr(broker, "close"):
            broker.close()
        admin_store.close()
        store.close()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
