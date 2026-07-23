"""
Live manual smoke test: TickReceiver -> OneMinuteCandleBuilder -> LiveOneMinuteCandleWriter.

Usage:
  python3 smoke_test_candle_writer.py
  python3 smoke_test_candle_writer.py --symbols RELIANCE,TCS,INFY --min-candles 3
"""

from __future__ import annotations

import argparse
import logging
import signal
import sqlite3
import sys
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import patch
from zoneinfo import ZoneInfo

from candle_aggregation import CompletedOneMinuteCandle, ensure_ist
from historical_collector import (
    DEFAULT_INSTRUMENTS_DB_PATH,
    StockToken,
    load_nifty50_tokens,
)
from live_one_minute_candle_writer import LiveOneMinuteCandleWriter
from one_minute_candle_builder import BuilderMetrics, OneMinuteCandleBuilder
from tick_event import IST
from tick_receiver import TickReceiver

logger = logging.getLogger(__name__)

_IST = ZoneInfo(IST)
DEFAULT_SYMBOLS = ("RELIANCE", "TCS", "INFY")


@dataclass
class SmokeState:
    token_to_symbol: Dict[int, str]
    target_candles: int
    db_path: Path
    candles_by_token: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    emitted: List[CompletedOneMinuteCandle] = field(default_factory=list)
    stop_lock: threading.Lock = field(default_factory=threading.Lock)
    stop_requested: bool = False
    receiver: Optional[TickReceiver] = None
    writer: Optional[LiveOneMinuteCandleWriter] = None

    def all_targets_met(self) -> bool:
        return all(
            self.candles_by_token.get(token, 0) >= self.target_candles
            for token in self.token_to_symbol
        )


def _format_candle(candle: CompletedOneMinuteCandle, symbol: str) -> str:
    candle_time = ensure_ist(candle.candle_time)
    time_label = candle_time.strftime("%H:%M")
    return (
        "--------------------------------------------------\n"
        f"{symbol}\n"
        f"Time: {time_label}\n"
        f"Open : {candle.open:.2f}\n"
        f"High : {candle.high:.2f}\n"
        f"Low  : {candle.low:.2f}\n"
        f"Close: {candle.close:.2f}\n"
        f"Volume: {candle.volume}\n"
        f"Volume Reliable: {candle.volume_reliable}\n"
        f"Tick Count: {candle.tick_count}\n"
        f"Partial: {candle.is_partial}\n"
        f"Full Coverage: {candle.has_full_minute_coverage}\n"
        f"Reason: {candle.completion_reason}\n"
        "--------------------------------------------------"
    )


def _load_smoke_symbols(
    instruments_db: Path,
    symbols: tuple[str, ...],
    kite=None,
) -> list[StockToken]:
    all_tokens = load_nifty50_tokens(instruments_db, kite=kite)
    by_symbol = {stock.tradingsymbol: stock for stock in all_tokens}
    missing = [symbol for symbol in symbols if symbol not in by_symbol]
    if missing:
        raise RuntimeError("Symbols not found in instruments DB: %s" % ", ".join(missing))
    return [by_symbol[symbol] for symbol in symbols]


def _make_on_candle(state: SmokeState):
    def on_candle(candle: CompletedOneMinuteCandle) -> None:
        symbol = state.token_to_symbol.get(candle.instrument_token, "unknown")
        print(_format_candle(candle, symbol), flush=True)
        state.emitted.append(candle)
        state.candles_by_token[candle.instrument_token] += 1
        if state.writer is not None:
            state.writer.on_candle(candle)
        with state.stop_lock:
            if state.all_targets_met() and not state.stop_requested:
                state.stop_requested = True
                logger.info("Target candle count reached; stopping smoke test.")
                if state.receiver is not None:
                    state.receiver.stop()

    return on_candle


def _verify_db(state: SmokeState) -> int:
    conn = sqlite3.connect(state.db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM live_1m_candles").fetchone()[0]
        complete = conn.execute(
            "SELECT COUNT(*) FROM live_1m_candles WHERE is_partial = 0"
        ).fetchone()[0]
        duplicates = conn.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT instrument_token, candle_time, COUNT(*) AS c
                FROM live_1m_candles
                GROUP BY instrument_token, candle_time
                HAVING c > 1
            )
            """
        ).fetchone()[0]
    finally:
        conn.close()

    print("\nDatabase verification:", flush=True)
    print("  total rows: %d" % total, flush=True)
    print("  complete rows (is_partial=0): %d" % complete, flush=True)
    print("  duplicate PK groups: %d" % duplicates, flush=True)

    if duplicates != 0:
        print("FAIL: duplicate primary keys found.", flush=True)
        return 1
    if total < len(state.emitted):
        print("FAIL: DB has fewer rows than emitted candles.", flush=True)
        return 1
    print("PASS: database checks.", flush=True)
    return 0


def _report_metrics(builder_metrics: BuilderMetrics, writer: LiveOneMinuteCandleWriter) -> None:
    wm = writer.metrics
    print("\nBuilder metrics:", flush=True)
    print("  candles_emitted: %d" % builder_metrics.candles_emitted, flush=True)
    print("\nWriter metrics:", flush=True)
    print("  candles_inserted: %d" % wm.candles_inserted, flush=True)
    print("  duplicates_ignored: %d" % wm.duplicates_ignored, flush=True)
    print("  conflicting_duplicates: %d" % wm.conflicting_duplicates, flush=True)
    print("  validation_errors: %d" % wm.validation_errors, flush=True)
    print("  write_retries: %d" % wm.write_retries, flush=True)
    print("  write_failures: %d" % wm.write_failures, flush=True)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Live smoke test for candle builder + SQLite writer",
    )
    parser.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help="Comma-separated symbols (default: %s)" % ",".join(DEFAULT_SYMBOLS),
    )
    parser.add_argument("--min-candles", type=int, default=2)
    parser.add_argument(
        "--db",
        default=str(Path(__file__).resolve().parent / "data" / "nifty50_live_1m.db"),
    )
    parser.add_argument("--instruments-db", default=str(DEFAULT_INSTRUMENTS_DB_PATH))
    parser.add_argument("--queue-maxsize", type=int, default=10000)
    parser.add_argument("--stale-seconds", type=float, default=30.0)
    parser.add_argument("--health-interval", type=float, default=10.0)
    args = parser.parse_args()

    symbols = tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip())
    if not symbols:
        print("No symbols specified.", file=sys.stderr)
        return 2

    instruments_db = Path(args.instruments_db)
    db_path = Path(args.db)
    stocks = _load_smoke_symbols(instruments_db, symbols)
    token_to_symbol = {stock.instrument_token: stock.tradingsymbol for stock in stocks}

    state = SmokeState(
        token_to_symbol=token_to_symbol,
        target_candles=args.min_candles,
        db_path=db_path,
    )
    writer = LiveOneMinuteCandleWriter(
        db_path=db_path,
        token_to_symbol=token_to_symbol,
    )
    state.writer = writer
    builder = OneMinuteCandleBuilder(on_candle=_make_on_candle(state))

    smoke_symbols = symbols

    def patched_load(instruments_db_path, kite=None, symbols=()):  # type: ignore
        return _load_smoke_symbols(instruments_db_path, smoke_symbols, kite=kite)

    with patch("tick_receiver.load_nifty50_tokens", side_effect=patched_load):
        receiver = TickReceiver(
            on_tick=builder.on_tick,
            on_feed_ready=builder.mark_feed_restored,
            on_feed_interrupted=builder.mark_feed_interrupted,
            instruments_db=instruments_db,
            queue_maxsize=args.queue_maxsize,
            stale_seconds=args.stale_seconds,
            health_interval=args.health_interval,
        )
        state.receiver = receiver

        def _handle_signal(signum: int, frame) -> None:  # type: ignore
            logger.info("Received signal %s; stopping smoke test.", signum)
            receiver.stop()

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

        print(
            "Starting live candle writer smoke test for: %s" % ", ".join(symbols),
            flush=True,
        )
        print("Database: %s" % db_path, flush=True)
        print(
            "Waiting for %d completed candle(s) per symbol. Press Ctrl+C to stop.\n"
            % args.min_candles,
            flush=True,
        )

        exit_code = 0
        try:
            receiver.start()
        except KeyboardInterrupt:
            logger.info("Interrupted by user.")
            receiver.stop()
            exit_code = 1
        except Exception:
            logger.exception("Smoke test failed.")
            exit_code = 1
        finally:
            print("\nFlushing in-progress candles...", flush=True)
            try:
                builder.flush()
            except Exception:
                logger.exception("builder.flush() failed.")
                exit_code = 1
            try:
                writer.close()
            except Exception:
                logger.exception("writer.close() failed.")
                exit_code = 1

            _report_metrics(builder.metrics, writer)
            verify_code = _verify_db(state)
            if verify_code != 0:
                exit_code = verify_code

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
