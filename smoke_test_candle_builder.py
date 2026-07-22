"""
Live manual smoke test: TickReceiver -> OneMinuteCandleBuilder -> print candles.

Subscribes to a small symbol set (default: RELIANCE, TCS, INFY) and runs until
each subscribed stock has emitted at least --min-candles completed 1-minute bars.

Usage:
  python3 smoke_test_candle_builder.py
  python3 smoke_test_candle_builder.py --min-candles 3 --symbols RELIANCE,TCS,INFY
"""

from __future__ import annotations

import argparse
import logging
import signal
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
    candles_by_token: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    emitted: List[CompletedOneMinuteCandle] = field(default_factory=list)
    stop_lock: threading.Lock = field(default_factory=threading.Lock)
    stop_requested: bool = False
    receiver: Optional[TickReceiver] = None

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
        "--------------------------------------------------"
    )


def _load_smoke_symbols(
    instruments_db: Path,
    symbols: tuple[str, ...],
    kite=None,
) -> list[StockToken]:
    stocks = load_nifty50_tokens(instruments_db, kite=kite, symbols=symbols)
    found = {stock.tradingsymbol for stock in stocks}
    missing = [symbol for symbol in symbols if symbol not in found]
    if missing:
        raise RuntimeError(
            "Missing instrument tokens for: %s. Run instrument_collector.py first."
            % ", ".join(missing)
        )
    return stocks


def _make_on_candle(state: SmokeState):
    def on_candle(candle: CompletedOneMinuteCandle) -> None:
        symbol = state.token_to_symbol.get(candle.instrument_token, "UNKNOWN")
        print(_format_candle(candle, symbol), flush=True)
        state.emitted.append(candle)
        state.candles_by_token[candle.instrument_token] += 1

        counts = {
            state.token_to_symbol[token]: state.candles_by_token[token]
            for token in state.token_to_symbol
        }
        logger.info("Candle counts: %s (target=%d each)", counts, state.target_candles)

        with state.stop_lock:
            if state.all_targets_met() and not state.stop_requested:
                state.stop_requested = True
                logger.info("Target candle count reached for all symbols; stopping.")
                if state.receiver is not None:
                    state.receiver.stop()

    return on_candle


def _print_metrics(metrics: BuilderMetrics) -> None:
    print("\n=== Builder Metrics ===")
    print(f"late_ticks_dropped         : {metrics.late_ticks_dropped}")
    print(f"duplicate_ticks_ignored    : {metrics.duplicate_ticks_ignored}")
    print(f"out_of_session_ticks         : {metrics.out_of_session_ticks}")
    print(f"invalid_price_ticks          : {metrics.invalid_price_ticks}")
    print(f"cumulative_volume_decreases  : {metrics.cumulative_volume_decreases}")
    print(f"candles_emitted              : {metrics.candles_emitted}")


def _verify_and_report(state: SmokeState, metrics: BuilderMetrics) -> int:
    print("\n=== Smoke Test Verification ===")
    issues: List[str] = []

    for token, symbol in sorted(state.token_to_symbol.items(), key=lambda x: x[1]):
        count = state.candles_by_token.get(token, 0)
        print(f"{symbol}: {count} completed candle(s) emitted")
        if count < state.target_candles:
            issues.append(
                f"{symbol}: only {count}/{state.target_candles} candles before shutdown"
            )

    if not state.emitted:
        issues.append("No completed candles were emitted.")
    else:
        for candle in state.emitted:
            symbol = state.token_to_symbol.get(candle.instrument_token, "UNKNOWN")
            if candle.volume < 0:
                issues.append(f"{symbol} {candle.candle_time}: negative volume")
            if candle.high < candle.low:
                issues.append(f"{symbol} {candle.candle_time}: high < low")
            if candle.open < candle.low or candle.open > candle.high:
                issues.append(f"{symbol} {candle.candle_time}: open outside high/low")
            if candle.close < candle.low or candle.close > candle.high:
                issues.append(f"{symbol} {candle.candle_time}: close outside high/low")
            if not candle.volume_reliable:
                issues.append(
                    f"{symbol} {candle.candle_time}: volume_reliable=False "
                    "(unexpected under normal market conditions)"
                )

    print("\nMetrics summary:")
    _print_metrics(metrics)

    if metrics.candles_emitted != len(state.emitted):
        issues.append(
            "metrics.candles_emitted (%d) != printed candles (%d)"
            % (metrics.candles_emitted, len(state.emitted))
        )

    if issues:
        print("\n=== Issues Observed ===")
        for issue in issues:
            print(f"- {issue}")
        return 1

    print("\nAll smoke checks passed.")
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Live smoke test: TickReceiver -> OneMinuteCandleBuilder",
    )
    parser.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help="Comma-separated symbols (default: RELIANCE,TCS,INFY)",
    )
    parser.add_argument(
        "--min-candles",
        type=int,
        default=3,
        help="Stop after this many completed candles per symbol (default: 3)",
    )
    parser.add_argument(
        "--instruments-db",
        default=str(DEFAULT_INSTRUMENTS_DB_PATH),
        help="Instruments SQLite DB path",
    )
    parser.add_argument(
        "--queue-maxsize",
        type=int,
        default=10_000,
        help="TickReceiver queue size",
    )
    parser.add_argument(
        "--stale-seconds",
        type=float,
        default=30.0,
        help="Stale feed threshold in seconds",
    )
    parser.add_argument(
        "--health-interval",
        type=float,
        default=10.0,
        help="Health log interval in seconds",
    )
    args = parser.parse_args()

    symbols = tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip())
    if not symbols:
        print("No symbols specified.", file=sys.stderr)
        return 2

    instruments_db = Path(args.instruments_db)
    stocks = _load_smoke_symbols(instruments_db, symbols)
    token_to_symbol = {stock.instrument_token: stock.tradingsymbol for stock in stocks}

    state = SmokeState(
        token_to_symbol=token_to_symbol,
        target_candles=args.min_candles,
    )
    builder = OneMinuteCandleBuilder(on_candle=_make_on_candle(state))

    smoke_symbols = symbols

    def patched_load(instruments_db_path, kite=None, symbols=()):  # type: ignore
        return _load_smoke_symbols(instruments_db_path, smoke_symbols, kite=kite)

    with patch("tick_receiver.load_nifty50_tokens", side_effect=patched_load):
        receiver = TickReceiver(
            on_tick=builder.on_tick,
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
            "Starting live candle builder smoke test for: %s"
            % ", ".join(symbols),
            flush=True,
        )
        print(
            "Waiting for %d completed candle(s) per symbol. Press Ctrl+C to stop early.\n"
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
            pre_flush_count = len(state.emitted)
            try:
                builder.flush()
            except Exception:
                logger.exception("builder.flush() failed.")
                exit_code = 1
            post_flush_count = len(state.emitted)
            if post_flush_count > pre_flush_count:
                print(
                    "Flush emitted %d additional in-progress candle(s)."
                    % (post_flush_count - pre_flush_count),
                    flush=True,
                )
            else:
                print("Flush did not emit additional candles.", flush=True)

            verify_code = _verify_and_report(state, builder.metrics)
            if verify_code != 0:
                exit_code = verify_code

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
