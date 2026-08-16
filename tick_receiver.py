"""
Live Kite WebSocket tick receiver for Nifty 100 instruments.

Connect -> Receive -> Normalize -> Assign sequence -> Enqueue

Usage:
  python tick_receiver.py
  python tick_receiver.py --stale-seconds 30 --queue-maxsize 10000
"""

from __future__ import annotations

import argparse
import logging
import queue
import signal
import threading
import time
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from kiteconnect import KiteTicker

from historical_collector import DEFAULT_INSTRUMENTS_DB_PATH, load_nifty50_tokens
from kite_tick_normalizer import normalize_kite_tick, to_tick_event
from login import _get_kite, _require_env, check_access_token
from candle_emission import CandleEmissionError
from tick_event import IST, FeedLifecycleCallback, TickCallback, TickEvent

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
_IST = ZoneInfo(IST)

_SENTINEL = object()

DEFAULT_QUEUE_MAXSIZE = 10_000
DEFAULT_STALE_SECONDS = 30.0
DEFAULT_HEALTH_INTERVAL = 10.0
DEFAULT_WORKER_POLL_SECONDS = 0.5


class QueueOverloadError(RuntimeError):
    """Raised when the tick queue is full and the market-data stream is invalid."""


class FeedContinuityState(Enum):
    STARTING = "starting"
    HEALTHY = "healthy"
    STALE = "stale"
    RESTORING = "restoring"
    STOPPED = "stopped"


class TickReceiver:
    def __init__(
        self,
        on_tick: TickCallback,
        *,
        on_feed_ready: Optional[FeedLifecycleCallback] = None,
        on_feed_interrupted: Optional[FeedLifecycleCallback] = None,
        instruments_db: Path = DEFAULT_INSTRUMENTS_DB_PATH,
        queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
        stale_seconds: float = DEFAULT_STALE_SECONDS,
        health_interval: float = DEFAULT_HEALTH_INTERVAL,
        worker_poll_seconds: float = DEFAULT_WORKER_POLL_SECONDS,
        api_key: Optional[str] = None,
        access_token: Optional[str] = None,
        ticker_factory: Any = None,
    ) -> None:
        self._on_tick = on_tick
        self._on_feed_ready = on_feed_ready
        self._on_feed_interrupted = on_feed_interrupted
        self._instruments_db = instruments_db
        self._queue_maxsize = queue_maxsize
        self._stale_seconds = stale_seconds
        self._health_interval = health_interval
        self._worker_poll_seconds = worker_poll_seconds
        self._ticker_factory = ticker_factory or KiteTicker

        valid, message = check_access_token(access_token)
        if not valid:
            raise RuntimeError(message)

        env = _require_env("KITE_API_KEY", "KITE_ACCESS_TOKEN")
        self._api_key = api_key or env["KITE_API_KEY"]
        self._access_token = access_token or env["KITE_ACCESS_TOKEN"]

        kite = _get_kite(api_key=self._api_key, access_token=self._access_token)
        stocks = load_nifty50_tokens(self._instruments_db, kite=kite)
        if not stocks:
            raise RuntimeError(
                "No Nifty 100 instrument tokens found in %s. Run instrument_collector.py first."
                % self._instruments_db
            )

        self._instrument_tokens: List[int] = [stock.instrument_token for stock in stocks]
        self._token_to_symbol: Dict[int, str] = {
            stock.instrument_token: stock.tradingsymbol for stock in stocks
        }

        if len(stocks) < 100:
            logger.warning(
                "Loaded %d/100 Nifty 100 tokens (%d missing).",
                len(stocks),
                100 - len(stocks),
            )

        self._queue: queue.Queue = queue.Queue(maxsize=queue_maxsize)
        self._stop_event = threading.Event()
        self._fatal_error: Optional[BaseException] = None
        self._accepting_ticks = True
        self._connected = False
        self._feed_stale = False
        self._latest_by_token: Dict[int, TickEvent] = {}

        self._next_sequence = 1
        self._ticks_enqueued = 0
        self._ticks_invalid = 0
        self._last_tick_monotonic: Optional[float] = None
        self._last_tick_at: Optional[datetime] = None

        self._ticker: Optional[KiteTicker] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._health_thread: Optional[threading.Thread] = None
        self._state_lock = threading.Lock()
        self._state_cond = threading.Condition(self._state_lock)
        self._continuity_state = FeedContinuityState.STARTING
        self._restoring_from: Optional[FeedContinuityState] = None

    @property
    def fatal_error(self) -> Optional[BaseException]:
        return self._fatal_error

    def is_accepting_ticks(self) -> bool:
        return self._accepting_ticks

    def is_feed_stale(self, max_age_seconds: Optional[float] = None) -> bool:
        threshold = self._stale_seconds if max_age_seconds is None else max_age_seconds
        last_tick = self._last_tick_monotonic
        if last_tick is None:
            return True
        return (time.monotonic() - last_tick) > threshold

    @property
    def last_tick_at(self) -> Optional[datetime]:
        return self._last_tick_at

    def start(self) -> None:
        """Block until shutdown, then re-raise fatal errors in the caller thread."""
        if self._worker_thread is not None:
            raise RuntimeError("TickReceiver is already running")

        self._ticker = self._ticker_factory(self._api_key, self._access_token)
        self._wire_ticker_callbacks(self._ticker)

        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="tick-receiver-worker",
            daemon=True,
        )
        self._health_thread = threading.Thread(
            target=self._health_loop,
            name="tick-receiver-health",
            daemon=True,
        )
        self._worker_thread.start()
        self._health_thread.start()

        logger.info(
            "Starting tick receiver for %d instruments (queue_maxsize=%d).",
            len(self._instrument_tokens),
            self._queue_maxsize,
        )
        self._ticker.connect(threaded=True)

        self._stop_event.wait()
        self._cleanup()

        if self._fatal_error is not None:
            raise self._fatal_error

    def stop(self) -> None:
        self._accepting_ticks = False
        self._transition_to_stopped()
        self._stop_event.set()
        self._close_ticker_safe()
        self._try_enqueue_sentinel()

    def _wire_ticker_callbacks(self, ticker: KiteTicker) -> None:
        ticker.on_ticks = self._on_ticks
        ticker.on_connect = self._on_connect
        ticker.on_close = self._on_close
        ticker.on_error = self._on_error
        ticker.on_reconnect = self._on_reconnect
        ticker.on_noreconnect = self._on_noreconnect

    def _on_connect(self, ws: Any, response: Any) -> None:
        self._connected = True
        logger.info("KiteTicker connected; subscribing to %d tokens.", len(self._instrument_tokens))
        ws.subscribe(self._instrument_tokens)
        ws.set_mode(ws.MODE_FULL, self._instrument_tokens)

    def _on_reconnect(self, ws: Any, attempts_count: int) -> None:
        self._connected = True
        logger.warning("KiteTicker reconnect attempt %d.", attempts_count)

    def _on_close(self, ws: Any, code: int, reason: str) -> None:
        self._connected = False
        logger.warning("KiteTicker closed (code=%s reason=%s).", code, reason)
        self._transition_to_stale(datetime.now(_IST))

    def _on_error(self, ws: Any, code: int, reason: str) -> None:
        self._connected = False
        logger.error("KiteTicker error (code=%s reason=%s).", code, reason)
        self._transition_to_stale(datetime.now(_IST))

    def _on_noreconnect(self, ws: Any) -> None:
        logger.critical("KiteTicker exceeded reconnect attempts; stopping receiver.")
        self._set_fatal(
            RuntimeError("KiteTicker exceeded maximum reconnect attempts"),
            accepting=False,
        )

    def _on_ticks(self, ws: Any, ticks: List[Dict[str, Any]]) -> None:
        if not self._accepting_ticks or self._fatal_error is not None:
            return

        received_at = datetime.now(_IST)

        for raw in ticks:
            if not self._accepting_ticks or self._fatal_error is not None:
                return

            try:
                normalized = normalize_kite_tick(raw, received_at=received_at)
                if normalized is None:
                    self._ticks_invalid += 1
                    continue

                sequence = self._next_sequence
                self._next_sequence += 1
                event = to_tick_event(normalized, sequence)
                self._enqueue_tick_with_restoration(event, datetime.now(_IST))

            except Exception:
                self._ticks_invalid += 1
                logger.exception("Unexpected error while processing tick; skipping.")

    def _handle_queue_overload(self, event: TickEvent) -> None:
        symbol = self._token_to_symbol.get(event.instrument_token, "unknown")
        message = (
            "Tick queue full (maxsize=%d); could not enqueue tick seq=%d token=%d (%s). "
            "Market-data stream is invalid. Halting receiver and downstream trading."
        )
        logger.critical(
            message,
            self._queue_maxsize,
            event.sequence,
            event.instrument_token,
            symbol,
        )
        error = QueueOverloadError(
            message
            % (
                self._queue_maxsize,
                event.sequence,
                event.instrument_token,
                symbol,
            )
        )
        self._set_fatal(error, accepting=False)
        threading.Thread(
            target=self._close_ticker_safe,
            name="tick-receiver-close",
            daemon=True,
        ).start()

    def _set_fatal(self, error: BaseException, *, accepting: bool) -> None:
        with self._state_lock:
            if self._fatal_error is None:
                self._fatal_error = error
            self._accepting_ticks = accepting
            self._feed_stale = True
        self._transition_to_stopped()
        self._stop_event.set()

    def _enqueue_tick_with_restoration(
        self,
        event: TickEvent,
        restored_at: datetime,
    ) -> None:
        ready_callback: Optional[FeedLifecycleCallback] = None
        overload_event: Optional[TickEvent] = None

        with self._state_cond:
            while self._continuity_state == FeedContinuityState.RESTORING:
                self._state_cond.wait()
            if self._continuity_state == FeedContinuityState.STOPPED:
                return
            if self._queue.full():
                overload_event = event
            elif self._continuity_state in (
                FeedContinuityState.STARTING,
                FeedContinuityState.STALE,
            ):
                self._restoring_from = self._continuity_state
                self._continuity_state = FeedContinuityState.RESTORING
                ready_callback = self._on_feed_ready

        if overload_event is not None:
            self._handle_queue_overload(overload_event)
            return

        if ready_callback is not None:
            try:
                ready_callback(restored_at)
            except Exception:
                logger.exception("on_feed_ready callback failed")
                self._abort_restoration()
                return

        self._queue.put_nowait(event)

        with self._state_cond:
            self._ticks_enqueued += 1
            self._last_tick_monotonic = time.monotonic()
            self._last_tick_at = event.exchange_timestamp
            if self._continuity_state == FeedContinuityState.RESTORING:
                self._continuity_state = FeedContinuityState.HEALTHY
                self._restoring_from = None
            self._state_cond.notify_all()

        self._latest_by_token[event.instrument_token] = event

    def _abort_restoration(self) -> None:
        with self._state_cond:
            if self._continuity_state != FeedContinuityState.RESTORING:
                return
            self._continuity_state = self._restoring_from or FeedContinuityState.STARTING
            self._restoring_from = None
            self._state_cond.notify_all()

    def _transition_to_stale(self, interrupted_at: datetime) -> None:
        interrupted_callback: Optional[FeedLifecycleCallback] = None
        with self._state_cond:
            if self._continuity_state != FeedContinuityState.HEALTHY:
                return
            self._continuity_state = FeedContinuityState.STALE
            interrupted_callback = self._on_feed_interrupted

        if interrupted_callback is not None:
            try:
                interrupted_callback(interrupted_at)
            except Exception:
                logger.exception("on_feed_interrupted callback failed")

    def _transition_to_stopped(self) -> None:
        interrupted_callback: Optional[FeedLifecycleCallback] = None
        with self._state_cond:
            if self._continuity_state == FeedContinuityState.STOPPED:
                return
            if self._continuity_state == FeedContinuityState.HEALTHY:
                interrupted_callback = self._on_feed_interrupted
            self._continuity_state = FeedContinuityState.STOPPED
            self._state_cond.notify_all()

        if interrupted_callback is not None:
            try:
                interrupted_callback(datetime.now(_IST))
            except Exception:
                logger.exception("on_feed_interrupted callback failed")

    def _close_ticker_safe(self) -> None:
        ticker = self._ticker
        if ticker is None:
            return
        try:
            ticker.close()
        except Exception:
            logger.exception("Error while closing KiteTicker.")

    def _try_enqueue_sentinel(self) -> None:
        try:
            self._queue.put_nowait(_SENTINEL)
        except queue.Full:
            pass

    def _worker_loop(self) -> None:
        while True:
            if self._stop_event.is_set() and self._queue.empty():
                break

            try:
                item = self._queue.get(timeout=self._worker_poll_seconds)
            except queue.Empty:
                continue

            if item is _SENTINEL:
                continue

            if self._fatal_error is not None:
                self._queue.task_done()
                continue

            try:
                self._on_tick(item)
            except CandleEmissionError as exc:
                self._set_fatal(exc, accepting=False)
                threading.Thread(
                    target=self._close_ticker_safe,
                    name="tick-receiver-close",
                    daemon=True,
                ).start()
                break
            except Exception:
                logger.exception(
                    "Downstream tick callback failed for seq=%s token=%s.",
                    item.sequence,
                    item.instrument_token,
                )
            finally:
                self._queue.task_done()

            if self._stop_event.is_set() and self._queue.empty():
                break

    def _run_health_check(self) -> None:
        stale = self.is_feed_stale()
        self._feed_stale = stale

        seconds_since_last = None
        if self._last_tick_monotonic is not None:
            seconds_since_last = time.monotonic() - self._last_tick_monotonic

        logger.info(
            "health connected=%s accepting=%s enqueued=%d invalid=%d queue=%d/%d "
            "seconds_since_last_tick=%s feed_stale=%s fatal=%s continuity=%s",
            self._connected,
            self._accepting_ticks,
            self._ticks_enqueued,
            self._ticks_invalid,
            self._queue.qsize(),
            self._queue_maxsize,
            (
                "%.1f" % seconds_since_last
                if seconds_since_last is not None
                else "n/a"
            ),
            stale,
            self._fatal_error is not None,
            self._continuity_state.value,
        )

        if stale and self._accepting_ticks and self._fatal_error is None:
            logger.warning(
                "Feed stale: no tick enqueued in the last %.1f seconds.",
                self._stale_seconds,
            )

        with self._state_cond:
            if self._continuity_state != FeedContinuityState.HEALTHY:
                return
        if stale:
            self._transition_to_stale(datetime.now(_IST))

    def _health_loop(self) -> None:
        while not self._stop_event.wait(self._health_interval):
            self._run_health_check()

    def _cleanup(self) -> None:
        self._close_ticker_safe()
        self._try_enqueue_sentinel()

        if self._worker_thread is not None:
            self._worker_thread.join(timeout=5.0)
        if self._health_thread is not None:
            self._health_thread.join(timeout=5.0)


def _default_tick_callback(event: TickEvent) -> None:
    return None


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Live Kite WebSocket tick receiver for Nifty 100")
    parser.add_argument(
        "--instruments-db",
        default=str(DEFAULT_INSTRUMENTS_DB_PATH),
        help="Instruments SQLite DB (default: %s)" % DEFAULT_INSTRUMENTS_DB_PATH,
    )
    parser.add_argument(
        "--queue-maxsize",
        type=int,
        default=DEFAULT_QUEUE_MAXSIZE,
        help="Bounded tick queue size (default: %d)" % DEFAULT_QUEUE_MAXSIZE,
    )
    parser.add_argument(
        "--stale-seconds",
        type=float,
        default=DEFAULT_STALE_SECONDS,
        help="Stale feed threshold in seconds (default: %.1f)" % DEFAULT_STALE_SECONDS,
    )
    parser.add_argument(
        "--health-interval",
        type=float,
        default=DEFAULT_HEALTH_INTERVAL,
        help="Health log interval in seconds (default: %.1f)" % DEFAULT_HEALTH_INTERVAL,
    )
    args = parser.parse_args()

    receiver = TickReceiver(
        on_tick=_default_tick_callback,
        instruments_db=Path(args.instruments_db),
        queue_maxsize=args.queue_maxsize,
        stale_seconds=args.stale_seconds,
        health_interval=args.health_interval,
    )

    def _handle_signal(signum: int, frame: Any) -> None:
        logger.info("Received signal %s; stopping tick receiver.", signum)
        receiver.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    receiver.start()


if __name__ == "__main__":
    main()
