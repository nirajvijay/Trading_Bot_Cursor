"""
Sole Kite historical_data gateway for VWAP v2 warm-up and gap repair.

No other module may call Kite historical for VWAP.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Deque, Dict, List, Optional, Protocol, Sequence, Tuple

from candle_aggregation import OneMinuteCandle, ensure_ist
from vwap_session_cache import SessionVwapCache, session_open_dt

logger = logging.getLogger(__name__)


class HistoricalOneMinuteFetcher(Protocol):
    def fetch_one_minute(
        self,
        instrument_token: int,
        start: datetime,
        end_exclusive: datetime,
    ) -> List[OneMinuteCandle]: ...


class SharedRateLimiter:
    """Sleeping limiter for Kite historical REST."""

    def __init__(self, requests_per_second: float) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be > 0")
        self._min_interval = 1.0 / requests_per_second
        self._lock = threading.Lock()
        self._last = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()


def kite_row_to_one_minute(row: dict) -> Optional[OneMinuteCandle]:
    raw_dt = row.get("date")
    if raw_dt is None:
        return None
    if isinstance(raw_dt, datetime):
        ts = ensure_ist(raw_dt)
    else:
        ts = ensure_ist(datetime.fromisoformat(str(raw_dt)))
    ts = ts.replace(second=0, microsecond=0)
    try:
        return OneMinuteCandle(
            candle_time=ts.isoformat(timespec="seconds"),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=int(row["volume"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


class KiteHistoricalFetcher:
    def __init__(
        self,
        *,
        kite: Optional[object] = None,
        rate_limiter: Optional[SharedRateLimiter] = None,
        kite_factory: Optional[Callable[[], object]] = None,
    ) -> None:
        self._kite = kite
        self._rate_limiter = rate_limiter
        self._kite_factory = kite_factory

    def fetch_one_minute(
        self,
        instrument_token: int,
        start: datetime,
        end_exclusive: datetime,
    ) -> List[OneMinuteCandle]:
        if self._rate_limiter is not None:
            self._rate_limiter.acquire()
        kite = self._kite
        if kite is None:
            factory = self._kite_factory
            if factory is None:
                from login import _get_kite

                kite = _get_kite()
            else:
                kite = factory()
            self._kite = kite
        start_ist = ensure_ist(start)
        end_ist = ensure_ist(end_exclusive)
        to_date = end_ist - timedelta(seconds=1)
        raw = kite.historical_data(  # type: ignore[union-attr]
            instrument_token=instrument_token,
            from_date=start_ist,
            to_date=to_date,
            interval="minute",
            continuous=False,
            oi=False,
        )
        out: List[OneMinuteCandle] = []
        for row in raw or []:
            candle = kite_row_to_one_minute(row)
            if candle is None:
                continue
            ts = ensure_ist(datetime.fromisoformat(candle.candle_time))
            if start_ist <= ts < end_ist:
                out.append(candle)
        return out


@dataclass(frozen=True)
class _WarmupJob:
    token: int
    end_exclusive: datetime
    enqueued_at: float


@dataclass
class SchedulerMetrics:
    requests: int = 0
    minutes_applied: int = 0
    failures: int = 0
    queue_wait_ms_total: int = 0
    warm_up_duration_ms_total: int = 0


def _candles_to_rows(
    candles: Sequence[OneMinuteCandle],
) -> List[Tuple[datetime, float, float, float, float, int]]:
    rows: List[Tuple[datetime, float, float, float, float, int]] = []
    for candle in candles:
        ts = ensure_ist(datetime.fromisoformat(candle.candle_time))
        rows.append(
            (ts, candle.open, candle.high, candle.low, candle.close, candle.volume)
        )
    return rows


class VwapHistoricalScheduler:
    def __init__(
        self,
        *,
        cache: SessionVwapCache,
        session_date: str,
        fetcher: HistoricalOneMinuteFetcher,
        on_ready: Optional[Callable[[int], None]] = None,
        on_degraded: Optional[Callable[[int], None]] = None,
        max_failures: int = 3,
    ) -> None:
        self._cache = cache
        self._session_date = session_date
        self._fetcher = fetcher
        self._on_ready = on_ready
        self._on_degraded = on_degraded
        self._max_failures = max_failures
        self._lock = threading.RLock()
        self._pending: Dict[int, _WarmupJob] = {}
        self._queue: Deque[int] = deque()
        self._failures: Dict[int, int] = {}
        self._metrics = SchedulerMetrics()
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None

    @property
    def metrics(self) -> SchedulerMetrics:
        with self._lock:
            return SchedulerMetrics(
                requests=self._metrics.requests,
                minutes_applied=self._metrics.minutes_applied,
                failures=self._metrics.failures,
                queue_wait_ms_total=self._metrics.queue_wait_ms_total,
                warm_up_duration_ms_total=self._metrics.warm_up_duration_ms_total,
            )

    def start(self) -> None:
        if self._worker is not None:
            return
        self._worker = threading.Thread(
            target=self._loop,
            name="vwap-historical-scheduler",
            daemon=True,
        )
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout=5.0)

    def request_warmup(self, token: int, *, end_exclusive: datetime) -> None:
        job = _WarmupJob(
            token=token,
            end_exclusive=ensure_ist(end_exclusive),
            enqueued_at=time.monotonic(),
        )
        with self._lock:
            self._pending[token] = job
            if token not in self._queue:
                self._queue.append(token)

    def _loop(self) -> None:
        while not self._stop.is_set():
            token: Optional[int] = None
            with self._lock:
                if self._queue:
                    token = self._queue.popleft()
            if token is None:
                time.sleep(0.05)
                continue
            self._process_token(token)

    def _process_token(self, token: int) -> None:
        with self._lock:
            job = self._pending.pop(token, None)
        if job is None:
            return
        wait_ms = int((time.monotonic() - job.enqueued_at) * 1000)
        start = time.monotonic()
        start_dt = session_open_dt(self._session_date)
        end = job.end_exclusive
        try:
            candles = self._fetcher.fetch_one_minute(token, start_dt, end)
            rows = _candles_to_rows(candles)
            applied = self._cache.apply_historical_minutes(token, rows)
            with self._lock:
                self._metrics.requests += 1
                self._metrics.minutes_applied += applied
                self._metrics.queue_wait_ms_total += wait_ms
                self._metrics.warm_up_duration_ms_total += int(
                    (time.monotonic() - start) * 1000
                )
                self._failures[token] = 0
            if self._on_ready is not None:
                self._on_ready(token)
        except Exception:  # noqa: BLE001
            logger.exception("vwap historical warm-up failed token=%s", token)
            with self._lock:
                self._metrics.failures += 1
                count = self._failures.get(token, 0) + 1
                self._failures[token] = count
            if count >= self._max_failures:
                if self._on_degraded is not None:
                    self._on_degraded(token)
            else:
                with self._lock:
                    self._pending[token] = job
                    self._queue.append(token)

    def process_sync_for_tests(self, token: int) -> None:
        """Drain one job synchronously (tests only)."""
        self._process_token(token)
