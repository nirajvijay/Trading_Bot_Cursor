"""
Live observation runner: Kite ticks → 1m/5m candles → spike → pullback → continuation.

Observation only. No orders, risk, or execution.
Default run duration: 60 minutes (or --until-session-close for full session to 15:30 IST).
"""

from __future__ import annotations

import argparse
import logging
import signal
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Set
from zoneinfo import ZoneInfo

from api.runner_status import write_runner_status
from api.services.observation_runner import (
    seconds_until_session_close,
    session_close_datetime,
)
from baseline_store import BaselineStore, DEFAULT_BASELINES_DB_PATH
from candle_aggregation import CompletedOneMinuteCandle
from candle_emission import CandleEmissionError
from continuation_tick_size import TickSizePreflightError, preflight_tick_sizes
from continuation_types import ContinuationRejectedEvent, ContinuationTriggeredEvent
from historical_collector import DEFAULT_INSTRUMENTS_DB_PATH, load_nifty50_tokens
from intraday_continuation_engine import IntradayContinuationEngine
from intraday_continuation_writer import IntradayContinuationWriter
from intraday_pullback_engine import IntradayPullbackEngine
from intraday_pullback_writer import IntradayPullbackWriter
from intraday_spike_detector import IntradaySpikeDetector
from intraday_spike_writer import IntradaySpikeWriter
from live_candle_pipeline import LiveCandlePipeline
from live_five_minute_candle_builder import LiveFiveMinuteCandleBuilder
from live_five_minute_candle_writer import LiveFiveMinuteCandleWriter
from live_one_minute_candle_writer import DEFAULT_DB_PATH, LiveOneMinuteCandleWriter
from login import check_access_token
from market_data_coordinator import MarketDataCoordinator
from pullback_ema_seed import DEFAULT_HISTORICAL_DB, PullbackEmaSeedStore
from pullback_indicators import Ema20State
from spike_types import IntradaySpikeEvent
from tick_event import IST
from tick_receiver import TickReceiver

logger = logging.getLogger(__name__)
_IST = ZoneInfo(IST)

ROOT = Path(__file__).resolve().parent


@dataclass
class ObservationState:
    session_date: str
    subscribed_tokens: int
    tokens_with_1m: Set[int] = field(default_factory=set)
    tokens_with_5m: Set[int] = field(default_factory=set)
    spikes_accepted: int = 0
    spikes_to_pullback: int = 0
    continuation_triggered: int = 0
    continuation_rejected: int = 0
    lifecycle_rows: list = field(default_factory=list)
    one_m_logged: int = 0
    five_m_logged: int = 0


def _ist_today() -> str:
    return datetime.now(_IST).date().isoformat()


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _baseline_token_coverage(
    store: BaselineStore,
    tokens: Set[int],
) -> int:
    covered: Set[int] = set()
    # Snapshot keys are (instrument_token, minute_of_day).
    for key in store._snapshots.keys():  # noqa: SLF001 — coverage report only
        token = key[0]
        if token in tokens:
            covered.add(token)
    return len(covered)


def _ema_seed_coverage(
    seeds: PullbackEmaSeedStore,
    tokens: list[int],
    *,
    period: int = 20,
) -> tuple[int, int, list[int]]:
    """Return (ready_count, missing_count, missing_token_sample)."""
    ready = 0
    missing_tokens: list[int] = []
    for token in tokens:
        seed = seeds.get(token)
        if seed is None or seed.seed_session_date is None or not seed.closes:
            missing_tokens.append(token)
            continue
        ema = Ema20State(period=period)
        applied = seeds.apply_to(token, ema)
        if not applied or not ema.available:
            missing_tokens.append(token)
            continue
        ready += 1
    return ready, len(missing_tokens), missing_tokens[:10]


def _print_coverage(
    *,
    label: str,
    session_date: str,
    subscribed: int,
    tokens_1m: int,
    tokens_5m: int,
    baseline_as_of: Optional[str],
    baseline_tokens: int,
    ema_ready: int,
    ema_missing: int,
    ema_missing_sample: list[int],
    five_incomplete: int,
) -> None:
    print(flush=True)
    print("=== Coverage (%s) ===" % label, flush=True)
    print("session_date (IST):          %s" % session_date, flush=True)
    print("subscribed tokens (expected): %d" % subscribed, flush=True)
    print("instruments with 1m candles:  %d / %d" % (tokens_1m, subscribed), flush=True)
    print("instruments with 5m candles:  %d / %d" % (tokens_5m, subscribed), flush=True)
    print(
        "baseline_as_of (strict prior): %s"
        % (baseline_as_of if baseline_as_of is not None else "NONE (explicit miss)"),
        flush=True,
    )
    print(
        "baseline token coverage:      %d / %d" % (baseline_tokens, subscribed),
        flush=True,
    )
    print(
        "EMA seed ready coverage:      %d / %d (missing=%d)"
        % (ema_ready, subscribed, ema_missing),
        flush=True,
    )
    if ema_missing_sample:
        print(
            "EMA missing token sample:     %s" % ema_missing_sample,
            flush=True,
        )
    print("5m incomplete buckets discarded: %d" % five_incomplete, flush=True)
    print(flush=True)


def _db_counts(db_path: Path, session_date: str) -> Dict[str, int]:
    if not db_path.exists():
        return {
            "1m": 0,
            "5m": 0,
            "spikes": 0,
            "setups": 0,
            "events": 0,
            "cont_arms": 0,
            "cont_decisions": 0,
        }
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        def _count(sql: str, params: tuple = ()) -> int:
            row = conn.execute(sql, params).fetchone()
            return int(row[0]) if row else 0

        return {
            "1m": _count(
                "SELECT COUNT(*) FROM live_1m_candles WHERE session_date = ?",
                (session_date,),
            ),
            "5m": _count(
                "SELECT COUNT(*) FROM live_5m_candles WHERE session_date = ?",
                (session_date,),
            ),
            "spikes": _count(
                "SELECT COUNT(*) FROM live_intraday_spikes WHERE session_date = ?",
                (session_date,),
            ),
            "setups": _count(
                "SELECT COUNT(*) FROM live_pullback_setups WHERE session_date = ?",
                (session_date,),
            ),
            "events": _count(
                """
                SELECT COUNT(*)
                FROM live_pullback_setup_events e
                JOIN live_pullback_setups s ON s.setup_id = e.setup_id
                WHERE s.session_date = ?
                """,
                (session_date,),
            ),
            "cont_arms": _count(
                "SELECT COUNT(*) FROM live_continuation_arms WHERE session_date = ?",
                (session_date,),
            ),
            "cont_decisions": _count(
                """
                SELECT COUNT(*)
                FROM live_continuation_decisions d
                JOIN live_continuation_arms a
                  ON a.setup_id = d.setup_id
                 AND a.continuation_rule_version = d.continuation_rule_version
                WHERE a.session_date = ?
                """,
                (session_date,),
            ),
        }
    except sqlite3.OperationalError:
        # Tables may not exist yet on a brand-new DB before writers open.
        return {
            "1m": 0,
            "5m": 0,
            "spikes": 0,
            "setups": 0,
            "events": 0,
            "cont_arms": 0,
            "cont_decisions": 0,
        }
    finally:
        conn.close()


def _verify_integrity(db_path: Path, session_date: str) -> list[str]:
    failures: list[str] = []
    if not db_path.exists():
        return ["live DB missing"]
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        for label, sql in (
            (
                "1m_dup",
                """
                SELECT COUNT(*) FROM (
                  SELECT instrument_token, candle_time, COUNT(*) c
                  FROM live_1m_candles
                  WHERE session_date = ?
                  GROUP BY 1, 2 HAVING c > 1
                )
                """,
            ),
            (
                "5m_dup",
                """
                SELECT COUNT(*) FROM (
                  SELECT instrument_token, candle_time, COUNT(*) c
                  FROM live_5m_candles
                  WHERE session_date = ?
                  GROUP BY 1, 2 HAVING c > 1
                )
                """,
            ),
            (
                "spike_dup",
                """
                SELECT COUNT(*) FROM (
                  SELECT instrument_token, candle_time, rule_version, COUNT(*) c
                  FROM live_intraday_spikes
                  WHERE session_date = ?
                  GROUP BY 1, 2, 3 HAVING c > 1
                )
                """,
            ),
            (
                "partial_5m",
                """
                SELECT COUNT(*) FROM live_5m_candles
                WHERE session_date = ? AND constituent_count != 5
                """,
            ),
        ):
            try:
                n = int(conn.execute(sql, (session_date,)).fetchone()[0])
            except sqlite3.OperationalError:
                continue
            if n:
                failures.append("%s=%d" % (label, n))
    finally:
        conn.close()
    return failures


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Live observation runner (1m/5m/spike/pullback/continuation). No orders."
    )
    p.add_argument(
        "--duration-minutes",
        type=float,
        default=60.0,
        help="Auto-stop after N minutes (default: 60; ignored with --until-session-close)",
    )
    p.add_argument(
        "--until-session-close",
        action="store_true",
        help="Auto-stop at 15:30 IST (overrides --duration-minutes)",
    )
    p.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="Live SQLite path (default: %s)" % DEFAULT_DB_PATH,
    )
    p.add_argument(
        "--instruments-db",
        type=Path,
        default=DEFAULT_INSTRUMENTS_DB_PATH,
        help="Instruments DB (default: %s)" % DEFAULT_INSTRUMENTS_DB_PATH,
    )
    p.add_argument(
        "--baselines-db",
        type=Path,
        default=DEFAULT_BASELINES_DB_PATH,
        help="Baselines DB (default: %s)" % DEFAULT_BASELINES_DB_PATH,
    )
    p.add_argument(
        "--historical-db",
        type=Path,
        default=DEFAULT_HISTORICAL_DB,
        help="Historical DB for EMA seeds (default: %s)" % DEFAULT_HISTORICAL_DB,
    )
    p.add_argument("--queue-maxsize", type=int, default=10_000)
    p.add_argument("--stale-seconds", type=float, default=30.0)
    p.add_argument("--health-interval", type=float, default=10.0)
    p.add_argument(
        "--status-file",
        type=Path,
        default=None,
        help="Optional path to write runner status JSON for the read API",
    )
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    _configure_logging()

    ok, msg = check_access_token()
    if not ok:
        print("Access token check failed: %s" % msg, flush=True)
        return 1
    print("Access token OK: %s" % msg, flush=True)

    session_date = _ist_today()
    stocks = load_nifty50_tokens(args.instruments_db)
    if not stocks:
        print("No Nifty 100 tokens loaded from %s" % args.instruments_db, flush=True)
        return 1
    token_to_symbol = {s.instrument_token: s.tradingsymbol for s in stocks}
    tokens = list(token_to_symbol.keys())
    token_set = set(tokens)

    if args.until_session_close:
        stop_at = session_close_datetime()
        print(
            "Observation-only live runner | session_date=%s | until_session_close=%s | tokens=%d"
            % (session_date, stop_at.isoformat(timespec="seconds"), len(tokens)),
            flush=True,
        )
    else:
        print(
            "Observation-only live runner | session_date=%s | duration=%.1f min | tokens=%d"
            % (session_date, args.duration_minutes, len(tokens)),
            flush=True,
        )
    print("DB: %s" % args.db, flush=True)
    print(
        "Restored 0 active setup(s) (restore deferred / none present)",
        flush=True,
    )
    print(
        "NOTE: Chart review is out of scope for this integration run. "
        "A later full-session strategy-validation run must compare detections "
        "against 1m and 5m market charts.",
        flush=True,
    )

    # Strictly prior baselines only — never generate/load today's session.
    baseline_store = BaselineStore.load(session_date, db_path=args.baselines_db)
    if baseline_store.baseline_as_of_date is None:
        print(
            "WARNING: No baseline_as_of_date strictly before %s — "
            "spike accepts will miss baselines (explicit; no fabricated defaults)."
            % session_date,
            flush=True,
        )
    elif baseline_store.baseline_as_of_date >= session_date:
        print(
            "FATAL: baseline_as_of_date=%s is not strictly before session_date=%s"
            % (baseline_store.baseline_as_of_date, session_date),
            flush=True,
        )
        return 1

    ema_seeds = PullbackEmaSeedStore.load(
        session_date,
        tokens,
        db_path=args.historical_db,
    )
    ema_ready, ema_missing, ema_sample = _ema_seed_coverage(ema_seeds, tokens)
    baseline_tokens = _baseline_token_coverage(baseline_store, token_set)

    _print_coverage(
        label="startup",
        session_date=session_date,
        subscribed=len(tokens),
        tokens_1m=0,
        tokens_5m=0,
        baseline_as_of=baseline_store.baseline_as_of_date,
        baseline_tokens=baseline_tokens,
        ema_ready=ema_ready,
        ema_missing=ema_missing,
        ema_missing_sample=ema_sample,
        five_incomplete=0,
    )

    state = ObservationState(
        session_date=session_date,
        subscribed_tokens=len(tokens),
    )

    candle_writer = LiveOneMinuteCandleWriter(
        db_path=args.db,
        token_to_symbol=token_to_symbol,
    )
    five_writer = LiveFiveMinuteCandleWriter(
        db_path=args.db,
        token_to_symbol=token_to_symbol,
    )
    five_builder = LiveFiveMinuteCandleBuilder()
    spike_writer = IntradaySpikeWriter(
        db_path=args.db,
        token_to_symbol=token_to_symbol,
    )
    pullback_writer = IntradayPullbackWriter(db_path=args.db)
    continuation_writer = IntradayContinuationWriter(db_path=args.db)

    try:
        tick_sizes = preflight_tick_sizes(args.instruments_db, tokens)
    except TickSizePreflightError as exc:
        print("FATAL tick_size preflight failed: %s" % exc, flush=True)
        return 1
    print("tick_size preflight OK for %d tokens" % len(tokens), flush=True)

    def on_lifecycle(
        tradingsymbol: str,
        setup_id: str,
        event_type: str,
        resulting_state: str,
        evaluation_candle_time: Optional[datetime],
    ) -> None:
        when = (
            evaluation_candle_time.isoformat(timespec="seconds")
            if evaluation_candle_time is not None
            else "-"
        )
        line = "%s | %s | %s -> %s | %s" % (
            when,
            tradingsymbol,
            event_type,
            resulting_state,
            setup_id,
        )
        state.lifecycle_rows.append(line)
        print("LIFECYCLE %s" % line, flush=True)

    # Forwarders so pullback and continuation can cross-wire without circular ctor.
    continuation_holder: Dict[str, IntradayContinuationEngine] = {}

    def on_pullback_ready(candidate) -> None:
        cont = continuation_holder.get("engine")
        if cont is not None:
            cont.on_pullback_ready(candidate)

    def on_setup_terminal(setup_id: str, reason: str) -> None:
        cont = continuation_holder.get("engine")
        if cont is not None:
            cont.on_setup_terminal(setup_id, reason)

    def on_triggered(event: ContinuationTriggeredEvent) -> None:
        state.continuation_triggered += 1
        print(
            "CONTINUATION_TRIGGERED %s setup=%s px=%.2f vol=%d avg3=%.1f"
            % (
                event.tradingsymbol,
                event.setup_id,
                event.last_price,
                event.breakout_candle_volume,
                event.avg_prior_3_1m_volume,
            ),
            flush=True,
        )

    def on_rejected(event: ContinuationRejectedEvent) -> None:
        state.continuation_rejected += 1
        print(
            "CONTINUATION_REJECTED %s setup=%s reason=%s"
            % (event.tradingsymbol, event.setup_id, event.reason),
            flush=True,
        )

    engine = IntradayPullbackEngine(
        writer=pullback_writer,
        ema_seeds=ema_seeds,
        on_lifecycle_event=on_lifecycle,
        on_pullback_ready=on_pullback_ready,
        on_setup_terminal=on_setup_terminal,
    )

    continuation = IntradayContinuationEngine(
        writer=continuation_writer,
        tick_sizes=tick_sizes,
        pullback_closer=engine,
        on_triggered=on_triggered,
        on_rejected=on_rejected,
    )
    continuation_holder["engine"] = continuation

    def on_spike(event: IntradaySpikeEvent) -> None:
        state.spikes_accepted += 1
        print(
            "SPIKE_ACCEPTED %s %s dir=%s vol_med=%.2f abs_ret=%.4f"
            % (
                event.tradingsymbol,
                event.candle_time.isoformat(timespec="seconds"),
                event.direction,
                event.features.relative_volume_median,
                event.features.absolute_return,
            ),
            flush=True,
        )
        before = engine.metrics.spikes_received
        engine.on_spike(event)
        if engine.metrics.spikes_received == before + 1:
            state.spikes_to_pullback += 1

    detector = IntradaySpikeDetector(
        baseline_store=baseline_store,
        writer=spike_writer,
        token_to_symbol=token_to_symbol,
        on_spike=on_spike,
    )

    def track_1m(candle: CompletedOneMinuteCandle) -> None:
        state.tokens_with_1m.add(candle.instrument_token)
        state.one_m_logged += 1
        if state.one_m_logged <= 5 or state.one_m_logged % 500 == 0:
            symbol = token_to_symbol.get(candle.instrument_token, "?")
            print(
                "1m candle #%d %s %s partial=%s"
                % (
                    state.one_m_logged,
                    symbol,
                    candle.candle_time.isoformat(timespec="seconds"),
                    candle.is_partial,
                ),
                flush=True,
            )
        detector.on_candle(candle)
        continuation.on_one_minute(candle)

    def track_5m(candle) -> None:
        state.tokens_with_5m.add(candle.instrument_token)
        state.five_m_logged += 1
        if state.five_m_logged <= 5 or state.five_m_logged % 100 == 0:
            symbol = token_to_symbol.get(candle.instrument_token, "?")
            print(
                "5m candle #%d %s %s constituents=%d"
                % (
                    state.five_m_logged,
                    symbol,
                    candle.candle_time.isoformat(timespec="seconds"),
                    candle.constituent_count,
                ),
                flush=True,
            )
        engine.on_five_minute_candle(candle)

    coordinator = MarketDataCoordinator(
        candle_writer=candle_writer,
        strategy_consumers=[track_1m],
        five_minute_builder=five_builder,
        five_minute_writer=five_writer,
        five_minute_consumers=[track_5m],
        closeables=[spike_writer, pullback_writer, continuation_writer],
    )
    pipeline = LiveCandlePipeline(
        coordinator=coordinator,
        tick_consumers=[continuation.on_tick],
    )

    receiver = TickReceiver(
        on_tick=pipeline.on_tick,
        on_feed_ready=pipeline.builder.mark_feed_restored,
        on_feed_interrupted=pipeline.builder.mark_feed_interrupted,
        instruments_db=args.instruments_db,
        queue_maxsize=args.queue_maxsize,
        stale_seconds=args.stale_seconds,
        health_interval=args.health_interval,
    )
    pipeline.attach_receiver(receiver)

    stop_reason = {"value": "unknown"}

    def _request_stop(reason: str) -> None:
        if stop_reason["value"] not in ("unknown",):
            return
        stop_reason["value"] = reason
        logger.info("Stopping observation runner: %s", reason)
        try:
            receiver.stop()
        except Exception:  # noqa: BLE001
            logger.exception("receiver.stop() failed")

    def _handle_signal(signum: int, frame) -> None:  # type: ignore[no-untyped-def]
        _request_stop("signal_%s" % signum)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if args.until_session_close:
        duration_s = seconds_until_session_close()
        stop_reason_name = "session_close"
    else:
        duration_s = max(1.0, float(args.duration_minutes) * 60.0)
        stop_reason_name = "duration_elapsed"
    timer = threading.Timer(duration_s, lambda: _request_stop(stop_reason_name))
    timer.daemon = True
    timer.start()

    metrics_stop = threading.Event()

    def _metrics_loop() -> None:
        while not metrics_stop.wait(max(args.health_interval, 5.0)):
            sm = detector.metrics.snapshot()
            pm = engine.metrics.snapshot()
            cm = continuation.metrics.snapshot()
            print(
                "METRICS 1m_tokens=%d 5m_tokens=%d spikes_acc=%d pb_recv=%d "
                "setups=%d ready_ema=%d ready_shallow=%d inv=%d exp=%d "
                "cont_trig=%d cont_rej=%d "
                "writer_fail=%d strat_fail=%d degraded=%d 5m_incomplete=%d"
                % (
                    len(state.tokens_with_1m),
                    len(state.tokens_with_5m),
                    state.spikes_accepted,
                    pm.spikes_received,
                    pm.setups_created,
                    pm.pullback_ready_ema,
                    pm.pullback_ready_shallow,
                    pm.invalidated,
                    pm.expired,
                    cm.triggered,
                    cm.rejected_volume
                    + cm.rejected_insufficient_history
                    + cm.rejected_unreliable_volume,
                    pm.writer_failure + sm.writer_failures + cm.writer_failures,
                    pm.strategy_failure + cm.strategy_failure,
                    pm.subsystem_degraded + cm.degraded,
                    five_builder.buckets_incomplete,
                ),
                flush=True,
            )
            if args.status_file is not None:
                last_tick = receiver.last_tick_at
                if last_tick is None:
                    feed_status = "DISCONNECTED"
                elif receiver.is_feed_stale():
                    feed_status = "STALE"
                else:
                    feed_status = "STABLE"
                write_runner_status(
                    args.status_file,
                    session_date=session_date,
                    subscribed_tokens=len(tokens),
                    feed_status=feed_status,
                    last_tick_time=last_tick.isoformat() if last_tick else None,
                )

    metrics_thread = threading.Thread(
        target=_metrics_loop, name="observation-metrics", daemon=True
    )
    metrics_thread.start()

    if args.until_session_close:
        print(
            "Starting live feed until 15:30 IST (%s). Ctrl+C to stop early.\n"
            % session_close_datetime().isoformat(timespec="seconds"),
            flush=True,
        )
    else:
        print(
            "Starting live feed for %.1f minutes. Ctrl+C to stop early.\n"
            % args.duration_minutes,
            flush=True,
        )

    exit_code = 0
    try:
        pipeline.run()
    except KeyboardInterrupt:
        _request_stop("keyboard_interrupt")
        exit_code = 1
    except CandleEmissionError as exc:
        logger.error(
            "Fatal candle persistence token=%s time=%s: %s",
            exc.candle.instrument_token,
            exc.candle.candle_time,
            exc.cause,
        )
        exit_code = 1
    except Exception:
        logger.exception("Observation runner failed")
        exit_code = 1
    finally:
        timer.cancel()
        metrics_stop.set()
        metrics_thread.join(timeout=2.0)
        print("\nShutting down pipeline (reason=%s)..." % stop_reason["value"], flush=True)
        if stop_reason["value"] == "session_close":
            try:
                engine.on_session_closed(session_date)
            except Exception:
                logger.exception("engine.on_session_closed() failed")
        try:
            pipeline.shutdown()
        except Exception:
            logger.exception("pipeline.shutdown() failed")
            exit_code = 1

    ema_ready, ema_missing, ema_sample = _ema_seed_coverage(ema_seeds, tokens)
    _print_coverage(
        label="end-of-run",
        session_date=session_date,
        subscribed=len(tokens),
        tokens_1m=len(state.tokens_with_1m),
        tokens_5m=len(state.tokens_with_5m),
        baseline_as_of=baseline_store.baseline_as_of_date,
        baseline_tokens=baseline_tokens,
        ema_ready=ema_ready,
        ema_missing=ema_missing,
        ema_missing_sample=ema_sample,
        five_incomplete=five_builder.buckets_incomplete,
    )

    counts = _db_counts(args.db, session_date)
    print("=== DB counts (session_date=%s) ===" % session_date, flush=True)
    for key in ("1m", "5m", "spikes", "setups", "events", "cont_arms", "cont_decisions"):
        print("  %s: %d" % (key, counts.get(key, 0)), flush=True)

    sm = detector.metrics.snapshot()
    pm = engine.metrics.snapshot()
    cm = continuation.metrics.snapshot()
    print("=== Spike metrics ===", flush=True)
    print(
        "  candles_seen=%d eligible=%d accepted=%d rejected=%d "
        "baseline_miss=%d writer_failures=%d"
        % (
            sm.candles_seen,
            sm.eligible_candles,
            sm.accepted_spikes,
            sm.rejected_spikes,
            sm.baseline_miss,
            sm.writer_failures,
        ),
        flush=True,
    )
    print("=== Pullback metrics ===", flush=True)
    print(
        "  spikes_received=%d setups_created=%d spike_ignored_while_active=%d "
        "ready_ema=%d ready_shallow=%d invalidated=%d expired=%d "
        "writer_failure=%d strategy_failure=%d subsystem_degraded=%d warmup_unavailable=%d"
        % (
            pm.spikes_received,
            pm.setups_created,
            pm.spike_ignored_while_active,
            pm.pullback_ready_ema,
            pm.pullback_ready_shallow,
            pm.invalidated,
            pm.expired,
            pm.writer_failure,
            pm.strategy_failure,
            pm.subsystem_degraded,
            pm.warmup_unavailable,
        ),
        flush=True,
    )
    print("=== Continuation metrics ===", flush=True)
    print(
        "  arms=%d triggered=%d rejected_vol=%d rejected_hist=%d "
        "rejected_unrel=%d disarmed=%d audit_sync_fail=%d degraded=%d"
        % (
            cm.arms_created,
            cm.triggered,
            cm.rejected_volume,
            cm.rejected_insufficient_history,
            cm.rejected_unreliable_volume,
            cm.disarmed_pullback_structural,
            cm.audit_sync_failures,
            cm.degraded,
        ),
        flush=True,
    )

    print("=== Spike → pullback once check ===", flush=True)
    print(
        "  spikes_accepted(callback)=%d spikes_received(engine)=%d"
        % (state.spikes_accepted, pm.spikes_received),
        flush=True,
    )
    if state.spikes_accepted == 0:
        print("  OK: zero accepted spikes (valid for this integration run)", flush=True)
    elif state.spikes_accepted == pm.spikes_received:
        print("  OK: each accepted spike reached pullback exactly once", flush=True)
    else:
        print(
            "  FAIL: accepted=%d engine_received=%d"
            % (state.spikes_accepted, pm.spikes_received),
            flush=True,
        )
        exit_code = 1

    integrity = _verify_integrity(args.db, session_date)
    if integrity:
        print("Integrity failures: %s" % ", ".join(integrity), flush=True)
        exit_code = 1
    else:
        print("Integrity OK (no duplicate PKs / no partial 5m rows)", flush=True)

    if pm.writer_failure or pm.strategy_failure or pm.subsystem_degraded or sm.writer_failures:
        print(
            "WARNING: non-zero writer/strategy/degraded counters — investigate before next run",
            flush=True,
        )

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
