"""The execution engine as a real, long-running process.

Launched by the API when start is clicked; it must keep ticking for hours
afterwards, so it cannot run inline as part of handling one click.

Only triggers from the moment of start onward are eligible: the candidate
floor is this process's own start instant. Anything that triggered before
then is ignored even if it is still sitting in the database, which prevents an
accidental late start from immediately firing on a stale, already-passed
opportunity.

The three ways this process ends itself -- a daily-loss breach, the 14:50 EOD
cutoff, and a manual Kill-It-All-Now -- all converge on the same sequence:
close everything, confirm every position is actually CLOSED, write the
stopping-on-purpose note, then exit. A plain STOP does not end the process; it
only stops new entries.
"""
from __future__ import annotations

import argparse
import signal
import sys
import time
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

import engine_clock
from engine_commands import CommandQueue
from engine_config import SessionRiskConfig, validate
from engine_core import ExecutionEngine
from engine_feed import FeedMonitor
from engine_live_marks import feed_state, first_reason
from engine_live_ticks import LiveTickFeed, NullTickFeed
from engine_risk import RiskPolicy
from engine_runloop import (
    TICK_INTERVAL_SECONDS,
    LoopWake,
    accumulated_drift_seconds,
    next_sleep_seconds,
    wait_for_next_tick,
)
from engine_sizing import RiskCappedSizing
from engine_status import EngineStatus, HeartbeatWriter, LiveMarkWriter
from engine_store import SqlitePositionStore
from engine_types import ExecutionState
from trading_engine_handoff import fetch_triggered_with_vwap_since

# Feed staleness. The engine has no market connection of its own -- everything
# it knows comes from what the observation runner writes -- so "feed stale" and
# "observation runner down" are the same underlying fact. This stays the safety
# net for the runner dying after a successful start, when no click happens that
# could be refused.
FEED_STALE_SECONDS = 5.0

# Kite reads (positions, orders, margins, profile) answer in well under a
# second when healthy. 3s bounds a hung read, so one tick's two reads stall at
# most ~6s instead of ~14s at the SDK's 7s default.
KITE_READ_TIMEOUT_SECONDS = 3.0

_STOP_REQUESTED = False


def _request_stop(*_args: object) -> None:
    global _STOP_REQUESTED
    _STOP_REQUESTED = True


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NIFTY RADAR execution engine")
    parser.add_argument("--session-date", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--engine-db", required=True, help="positions/events/commands store")
    parser.add_argument("--live-db", required=True, help="observation DB with triggers")
    parser.add_argument("--status-file", required=True)
    parser.add_argument("--live-mark-file", default=None)
    parser.add_argument("--runner-status-file", default=None)
    parser.add_argument("--per-trade-cap", type=float, default=900.0)
    parser.add_argument("--limited-per-trade-cap", type=float, default=450.0)
    parser.add_argument("--daily-loss-cap", type=float, default=3000.0)
    parser.add_argument("--total-capital", type=float, default=300_000.0)
    parser.add_argument("--leverage", type=float, default=5.0)
    parser.add_argument("--live-orders", action="store_true")
    parser.add_argument("--tick-seconds", type=float, default=TICK_INTERVAL_SECONDS)
    parser.add_argument(
        "--max-ticks",
        type=int,
        default=0,
        help="stop after N ticks (testing only; 0 means run until a stop trigger)",
    )
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> SessionRiskConfig:
    config = SessionRiskConfig(
        per_trade_cap_rupees=args.per_trade_cap,
        per_trade_cap_vwap_limited_rupees=args.limited_per_trade_cap,
        daily_loss_cap_rupees=args.daily_loss_cap,
        total_capital_rupees=args.total_capital,
        leverage_factor=args.leverage,
    )
    # Refuse an unsafe configuration here too, not only at the API. A process
    # launched by hand must not bypass the guard.
    validate(config)
    return config


def build_broker(*, live_orders: bool):
    """FakeBroker is an internal testing tool, never a user-facing paper mode.

    There is no UI switch for it: real validation happens through genuine Kite
    calls sized tiny via the editable caps, because a simulator can only prove
    our own logic and never the actual integration.
    """
    if not live_orders:
        from trading_engine_broker import FakeBroker

        return FakeBroker()
    from login import _get_kite
    from trading_engine_broker import KiteBroker

    return KiteBroker(
        _get_kite(),  # writes: SDK default timeout
        live_orders_enabled=True,
        read_kite=_get_kite(timeout=KITE_READ_TIMEOUT_SECONDS),
    )


def build_tick_feed(*, live_orders: bool, on_order_update=None):
    """The live Open P&L feed: a dedicated KiteTicker in LIVE, nothing in PAPER.

    Display only. Building it never fails the engine: missing credentials just
    mean every mark stays on Kite REST pnl with the reason shown on the desk.
    """
    if not live_orders:
        return NullTickFeed()
    from login import _read_env_merged

    env = _read_env_merged()
    return LiveTickFeed(
        api_key=str(env.get("KITE_API_KEY") or ""),
        access_token=str(env.get("KITE_ACCESS_TOKEN") or ""),
        on_order_update=on_order_update,
    )


def live_mark_feed_summary(engine: ExecutionEngine) -> dict:
    health = engine.live_feed_health
    return {
        "state": feed_state(
            feed_health=health, sources=engine.live_pnl_source.values()
        ),
        "connected": health.connected,
        "reason": health.reason or first_reason(engine.live_pnl_reason),
        "last_tick_at": health.last_tick_at.isoformat() if health.last_tick_at else None,
        "order_updates": int(getattr(health, "order_updates", 0) or 0),
    }


def build_feed_monitor(runner_status_file: Optional[str]) -> FeedMonitor:
    """Staleness is judged against the actual last-tick timestamp.

    Never against a status file's write cadence, which was the old engine's
    structural bug: a 5s threshold compared against a file written every ~10s
    made a healthy feed look stale on every check.
    """
    if not runner_status_file:
        return FeedMonitor(
            last_tick_at=lambda: datetime.now(timezone.utc),
            stale_after_seconds=FEED_STALE_SECONDS,
        )

    path = Path(runner_status_file)

    def last_tick_at() -> Optional[datetime]:
        from engine_status import read_json
        from trading_engine_broker import parse_timestamp_text

        data = read_json(path)
        if not data:
            return None
        for key in ("last_tick_at", "last_tick", "updated_at"):
            parsed = parse_timestamp_text(data.get(key))
            if parsed is not None:
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        return None

    return FeedMonitor(
        last_tick_at=last_tick_at, stale_after_seconds=FEED_STALE_SECONDS
    )


def build_engine(
    args: argparse.Namespace, *, store, queue, floor_iso: str, wake: Optional[LoopWake] = None
):
    config = build_config(args)
    session_date = args.session_date or engine_clock.to_ist().strftime("%Y-%m-%d")
    return ExecutionEngine(
        broker=build_broker(live_orders=args.live_orders),
        store=store,
        feed_monitor=build_feed_monitor(args.runner_status_file),
        risk_policy=RiskPolicy(config.to_risk_limits()),
        sizing_policy=RiskCappedSizing(),
        candidate_source=partial(
            fetch_triggered_with_vwap_since,
            Path(args.live_db),
            created_at_gte=floor_iso,
        ),
        session_config=config,
        session_date=session_date,
        is_live=bool(args.live_orders),
        run_id=args.run_id or uuid4().hex[:12],
        command_source=queue.take_pending,
        tick_feed=build_tick_feed(
            live_orders=bool(args.live_orders),
            on_order_update=wake.notify if wake is not None else None,
        ),
    )


def snapshot_status(engine: ExecutionEngine, *, state: str = "running") -> EngineStatus:
    open_positions = engine.store.open_positions()
    config = engine.session_config
    return EngineStatus(
        run_id=str(engine.run_id or ""),
        session_date=engine.session_date,
        state=state,
        tick_count=engine.tick_count,
        open_positions=len(open_positions),
        unprotected=sum(
            1
            for p in open_positions
            if p.state in (ExecutionState.ENTERED, ExecutionState.ENTRY_SUBMITTED)
        ),
        entries_allowed=engine.entries_allowed,
        entries_stopped=engine.entries_stopped,
        entries_paused=engine.entries_paused,
        pause_reason=engine.pause_reason,
        realised_loss_today=float(getattr(engine, "realised_loss_today", 0.0)),
        daily_loss_cap=config.daily_loss_cap_rupees,
        remaining_daily=max(
            0.0,
            config.daily_loss_cap_rupees
            - float(getattr(engine, "realised_loss_today", 0.0)),
        ),
        is_live=engine.is_live,
        last_error=engine.failures.last_error,
        step_failures=engine.failures.snapshot(),
        escalations=dict(engine.failures.escalated),
    )


def run(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    # Clean-start floor: only triggers from this instant onward are eligible.
    floor_iso = datetime.now(timezone.utc).isoformat()

    store = SqlitePositionStore(args.engine_db)
    queue = CommandQueue(args.engine_db)
    heartbeat = HeartbeatWriter(Path(args.status_file))
    marks = LiveMarkWriter(
        Path(args.live_mark_file)
        if args.live_mark_file
        else Path(args.status_file).with_name("engine_live_marks.json")
    )

    wake = LoopWake()
    try:
        engine = build_engine(args, store=store, queue=queue, floor_iso=floor_iso, wake=wake)
    except Exception as exc:  # noqa: BLE001 - a failed start must be visible
        heartbeat.write(
            EngineStatus(
                run_id=args.run_id or "",
                session_date=args.session_date or "",
                state="error",
                last_error=str(exc),
                stopped_on_purpose=True,
                stop_reason="start_failed",
            )
        )
        store.close()
        queue.close()
        return 2

    # The live P&L feed lives exactly as long as this process: started here,
    # stopped in the finally below on every way out. A failed start is
    # recorded on the feed and the desk falls back to Kite REST; it never
    # stops the engine.
    engine.tick_feed.start()

    loop_started = time.monotonic()
    interval = float(args.tick_seconds or TICK_INTERVAL_SECONDS)
    stop_reason: Optional[str] = None

    try:
        while True:
            tick_started = time.monotonic()
            # Before the REST reads: a push landing during this tick re-arms
            # the next wait instead of being cleared away unseen.
            wake.clear()
            engine.tick()
            heartbeat.write(snapshot_status(engine))
            marks.write(
                engine.live_pnl,
                sources=engine.live_pnl_source,
                reasons=engine.live_pnl_reason,
                feed=live_mark_feed_summary(engine),
            )

            if engine.shutdown_reason is not None and engine.shutdown_complete:
                # Everything is confirmed CLOSED: there is no useful work left
                # for the rest of the day.
                stop_reason = engine.shutdown_reason.value
                break

            if _STOP_REQUESTED:
                # An external signal. Not one of the three designed triggers,
                # so it is recorded under its own reason.
                stop_reason = "signal"
                break

            if args.max_ticks and engine.tick_count >= args.max_ticks:
                stop_reason = "max_ticks"
                break

            if not engine_clock.market_session_open(datetime.now(timezone.utc)):
                if engine_clock.eod_squareoff_due(datetime.now(timezone.utc)):
                    # Past the close with nothing left to square off.
                    stop_reason = "session_over"
                    break

            tick_finished = time.monotonic()
            sleep_for = next_sleep_seconds(
                tick_started_at=tick_started,
                tick_finished_at=tick_finished,
                interval=interval,
            )
            wait_for_next_tick(
                sleep_for=sleep_for,
                tick_started_at=tick_started,
                wait_for_wake=wake.wait,
                sleep=time.sleep,
                monotonic=time.monotonic,
            )
    except BaseException as exc:  # noqa: BLE001
        # An unhandled failure escaped every per-step guard. Do not write a
        # stop note: this is a crash, and the heartbeat going silent without a
        # note is exactly how it must be reported.
        status = snapshot_status(engine, state="error")
        status.last_error = f"unhandled: {exc}"
        heartbeat.write(status)
        raise
    finally:
        drift = accumulated_drift_seconds(
            started_at=loop_started,
            now=time.monotonic(),
            ticks_completed=engine.tick_count,
            interval=interval,
        )
        if stop_reason is not None:
            # The last thing written before a deliberate exit, so silence with
            # this note reads as healthy rather than as a crash.
            final = snapshot_status(engine, state="stopped")
            final.escalations = dict(engine.failures.escalated)
            final.step_failures = {"accumulated_drift_seconds": int(drift)}
            heartbeat.write_stop_note(final, stop_reason)
        try:
            engine.tick_feed.stop()
        except Exception:  # noqa: BLE001 - never mask the real exit
            pass
        store.close()
        queue.close()

    return 0


if __name__ == "__main__":
    sys.exit(run())
