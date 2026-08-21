"""
Live 5-minute candle-based session VWAP qualifier engine.

In-memory tick updates, classify on raw TRIGGERED, async persist.
Manual-start bucket B0 is UNCERTAIN until reconstructed from five historical 1m bars.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence, Set
from zoneinfo import ZoneInfo

from candle_aggregation import (
    OneMinuteCandle,
    ensure_ist,
    is_in_session,
    minute_of_day_from_datetime,
)
from continuation_types import ContinuationTriggeredEvent
from tick_event import IST, TickEvent
from vwap_qualifier_bootstrap import (
    HistoricalOneMinuteFetcher,
    KiteHistoricalFetcher,
    SharedRateLimiter,
    seed_closed_five_minute_contributions,
)
from vwap_qualifier_config import VwapQualifierConfig, load_vwap_qualifier_config
from vwap_qualifier_features import classify_gap, directional_gap, hlc3_pv
from vwap_qualifier_repair import (
    RepairValidateError,
    backoff_delay_seconds,
    can_fetch_bucket,
    provenance_for_bucket,
    reconstruct_five_minute_hlc3,
)
from vwap_qualifier_state import (
    InProgressFiveMinute,
    TokenVwapState,
    bucket_end_dt,
    bucket_start_dt,
    freeze_cutoff,
    session_open_dt,
    startup_bucket,
)
from vwap_qualifier_types import (
    VwapProvenance,
    VwapQualification,
    VwapQualityReason,
    VwapUiState,
)
from vwap_qualifier_writer import VwapQualifierWriter

logger = logging.getLogger(__name__)
_IST = ZoneInfo(IST)

_REPAIR_SENTINEL = object()


@dataclass(frozen=True)
class VwapEngineMetrics:
    ticks_seen: int
    ticks_buffered: int
    buffer_overflows: int
    classified: int
    accept: int
    limited: int
    reject: int
    unavailable: int
    reconstruct_ok: int
    reconstruct_fail: int
    callback_failures: int


@dataclass(frozen=True)
class _RepairJob:
    instrument_token: int
    bucket_start: datetime


class VwapQualifierEngine:
    def __init__(
        self,
        *,
        tokens: Sequence[int],
        token_to_symbol: Dict[int, str],
        session_date: str,
        writer: Optional[VwapQualifierWriter] = None,
        config: Optional[VwapQualifierConfig] = None,
        fetcher: Optional[HistoricalOneMinuteFetcher] = None,
        now_fn: Optional[Callable[[], datetime]] = None,
        manual_start: bool = True,
        start_workers: bool = False,
    ) -> None:
        self._config = config or load_vwap_qualifier_config()
        self._tokens: List[int] = list(tokens)
        self._token_to_symbol = dict(token_to_symbol)
        self._session_date = session_date
        self._writer = writer
        self._now_fn = now_fn or (lambda: datetime.now(_IST))
        self._manual_start = manual_start
        self._fetcher = fetcher
        self._lock = threading.RLock()
        self._cutoff: Optional[datetime] = None
        self._b0: Optional[datetime] = None
        self._bootstrap_ready = False
        self._bootstrap_failed = False
        self._feed_stale = False
        self._states: Dict[int, TokenVwapState] = {}
        self._buffers: Dict[int, Deque[TickEvent]] = {}
        self._queued_repairs: Set[tuple[int, datetime]] = set()
        self._ticks_seen = 0
        self._ticks_buffered = 0
        self._buffer_overflows = 0
        self._classified = 0
        self._accept = 0
        self._limited = 0
        self._reject = 0
        self._unavailable = 0
        self._reconstruct_ok = 0
        self._reconstruct_fail = 0
        self._callback_failures = 0
        self._stop = threading.Event()
        self._repair_queue: queue.Queue = queue.Queue()
        self._bootstrap_thread: Optional[threading.Thread] = None
        self._repair_thread: Optional[threading.Thread] = None
        if self._fetcher is None:
            limiter = SharedRateLimiter(self._config.historical_max_requests_per_second)
            self._fetcher = KiteHistoricalFetcher(rate_limiter=limiter)
        if start_workers:
            self.start()

    @property
    def config(self) -> VwapQualifierConfig:
        return self._config

    @property
    def cutoff(self) -> Optional[datetime]:
        return self._cutoff

    @property
    def b0(self) -> Optional[datetime]:
        return self._b0

    @property
    def metrics(self) -> VwapEngineMetrics:
        with self._lock:
            return VwapEngineMetrics(
                ticks_seen=self._ticks_seen,
                ticks_buffered=self._ticks_buffered,
                buffer_overflows=self._buffer_overflows,
                classified=self._classified,
                accept=self._accept,
                limited=self._limited,
                reject=self._reject,
                unavailable=self._unavailable,
                reconstruct_ok=self._reconstruct_ok,
                reconstruct_fail=self._reconstruct_fail,
                callback_failures=self._callback_failures,
            )

    def status_snapshot(self) -> Dict[str, Any]:
        """Derived UI snapshot. No I/O. First-match-wins state order is locked."""
        with self._lock:
            token_count = len(self._tokens)
            failed_token_count = sum(
                1 for token in self._tokens if self._states.get(token) is not None
                and self._states[token].failed_buckets
            )
            uncertain_bucket_count = sum(
                len(state.uncertain_buckets) for state in self._states.values()
            )
            repair_queued = len(self._queued_repairs)
            state, reason = self._derive_ui_state_locked(
                token_count=token_count,
                failed_token_count=failed_token_count,
                uncertain_bucket_count=uncertain_bucket_count,
                repair_queued=repair_queued,
            )
            return {
                "state": state,
                "bootstrap_ready": self._bootstrap_ready,
                "bootstrap_failed": self._bootstrap_failed,
                "feed_stale": self._feed_stale,
                "repair_queued": repair_queued,
                "uncertain_bucket_count": uncertain_bucket_count,
                "failed_token_count": failed_token_count,
                "token_count": token_count,
                "accept": self._accept,
                "limited": self._limited,
                "reject": self._reject,
                "unavailable": self._unavailable,
                "reason": reason,
            }

    def _derive_ui_state_locked(
        self,
        *,
        token_count: int,
        failed_token_count: int,
        uncertain_bucket_count: int,
        repair_queued: int,
    ) -> tuple[VwapUiState, str]:
        if self._bootstrap_failed:
            return "unavailable", "bootstrap failed"
        if self._feed_stale:
            return "unavailable", "feed stale"
        if token_count == 0:
            return "unavailable", "no subscribed tokens"
        if not self._bootstrap_ready:
            return "bootstrapping", "bootstrapping historical 1m"
        if failed_token_count == token_count:
            return "unavailable", "all tokens unavailable"
        b0_unresolved = False
        if self._b0 is not None:
            b0_unresolved = any(
                self._b0 not in state.committed and self._b0 not in state.failed_buckets
                for state in self._states.values()
            )
        partial_failures = 0 < failed_token_count < token_count
        if b0_unresolved or repair_queued > 0 or uncertain_bucket_count > 0 or partial_failures:
            if partial_failures and not b0_unresolved and repair_queued == 0 and uncertain_bucket_count == 0:
                return (
                    "repairing",
                    "%d token%s unavailable; remaining tokens active"
                    % (failed_token_count, "" if failed_token_count == 1 else "s"),
                )
            if partial_failures:
                reason = "%d token%s unavailable; remaining tokens active" % (
                    failed_token_count,
                    "" if failed_token_count == 1 else "s",
                )
            elif b0_unresolved and self._b0 is not None:
                reason = "reconstructing %s" % self._b0.strftime("%H:%M")
            else:
                reason = "repair pending"
            return "repairing", reason
        return "ready", "qualifier ready"

    def mark_bootstrap_failed(self, reason: str = "bootstrap failed") -> None:
        del reason
        with self._lock:
            self._bootstrap_failed = True
        self.mark_bootstrap_ready()

    def freeze_cutoff(self, now: Optional[datetime] = None) -> datetime:
        with self._lock:
            if self._cutoff is not None:
                return self._cutoff
            cutoff = freeze_cutoff(now or self._now_fn())
            self._cutoff = cutoff
            if self._manual_start:
                self._b0 = startup_bucket(cutoff, self._session_date)
            else:
                # Feed was subscribed before B0: no forced UNCERTAIN on boundary.
                bucket = bucket_start_dt(cutoff)
                self._b0 = bucket or startup_bucket(cutoff, self._session_date)
            for token in self._tokens:
                self._states[token] = TokenVwapState(b0=self._b0)
                if not self._manual_start:
                    self._states[token].uncertain_buckets.discard(self._b0)
            return cutoff

    def start(self) -> None:
        """Freeze cutoff and start bootstrap + repair workers. Observation is not blocked."""
        self.freeze_cutoff()
        if self._bootstrap_thread is not None:
            return
        self._bootstrap_thread = threading.Thread(
            target=self._bootstrap_loop,
            name="vwap-qualifier-bootstrap",
            daemon=True,
        )
        self._repair_thread = threading.Thread(
            target=self._repair_loop,
            name="vwap-qualifier-repair",
            daemon=True,
        )
        self._bootstrap_thread.start()
        self._repair_thread.start()
        logger.info(
            "VWAP qualifier started cutoff=%s b0=%s manual_start=%s tokens=%d",
            self._cutoff.isoformat(timespec="seconds") if self._cutoff else None,
            self._b0.isoformat(timespec="seconds") if self._b0 else None,
            self._manual_start,
            len(self._tokens),
        )

    def close(self) -> None:
        self._stop.set()
        try:
            self._repair_queue.put_nowait(_REPAIR_SENTINEL)
        except queue.Full:
            pass
        if self._bootstrap_thread is not None:
            self._bootstrap_thread.join(timeout=5.0)
        if self._repair_thread is not None:
            self._repair_thread.join(timeout=5.0)

    def mark_feed_interrupted(self, interrupted_at: datetime) -> None:
        del interrupted_at
        with self._lock:
            self._feed_stale = True
            for token, state in self._states.items():
                ip = state.in_progress
                if ip is None:
                    continue
                bucket = ip.bucket_start
                if bucket == state.b0:
                    state.in_progress = None
                    continue
                state.mark_uncertain(bucket)
                self._enqueue_repair_locked(token, bucket)

    def mark_feed_restored(self, restored_at: datetime) -> None:
        del restored_at
        with self._lock:
            self._feed_stale = False

    def on_tick(self, tick: TickEvent) -> None:
        try:
            self._on_tick_impl(tick)
        except Exception:  # noqa: BLE001
            self._callback_failures += 1
            logger.exception(
                "vwap qualifier on_tick failed token=%s seq=%s",
                tick.instrument_token,
                tick.sequence,
            )

    def _on_tick_impl(self, tick: TickEvent) -> None:
        with self._lock:
            self._ticks_seen += 1
            if self._cutoff is None:
                self.freeze_cutoff(tick.exchange_timestamp)
            token = tick.instrument_token
            state = self._states.get(token)
            if state is None:
                if self._b0 is None:
                    return
                state = TokenVwapState(b0=self._b0)
                self._states[token] = state
                if self._bootstrap_ready:
                    state.bootstrap_ready = True
                    self._enqueue_repair_locked(token, self._b0)
            ts = ensure_ist(tick.exchange_timestamp)
            minute = minute_of_day_from_datetime(ts)
            if not is_in_session(minute):
                return
            state.last_sequence = max(state.last_sequence, tick.sequence)

            if not state.bootstrap_ready:
                buf = self._buffers.setdefault(token, deque())
                max_n = self._config.bootstrap_tick_buffer_max_per_token
                if len(buf) >= max_n:
                    state.overflowed = True
                    buf.clear()
                    self._buffer_overflows += 1
                    return
                buf.append(tick)
                self._ticks_buffered += 1
                return

            self._apply_live_tick_locked(state, tick)

    def _apply_live_tick_locked(self, state: TokenVwapState, tick: TickEvent) -> None:
        ts = ensure_ist(tick.exchange_timestamp)
        if self._cutoff is not None and ts <= self._cutoff:
            return
        bucket = bucket_start_dt(ts)
        if bucket is None:
            return
        if bucket in state.committed:
            return
        if state.is_uncertain(bucket) or bucket == state.b0:
            return

        tick_minute = minute_of_day_from_datetime(ts)
        ip = state.in_progress
        if ip is not None and ip.bucket_start != bucket:
            self._close_or_gap_locked(tick.instrument_token, state, ip, bucket)
            ip = state.in_progress

        if state.is_uncertain(bucket):
            return

        if ip is None or ip.bucket_start != bucket:
            state.in_progress = InProgressFiveMinute(
                bucket_start=bucket,
                open=tick.last_price,
                high=tick.last_price,
                low=tick.last_price,
                close=tick.last_price,
            )
            ip = state.in_progress

        if ip.last_tick_minute is not None and tick_minute > ip.last_tick_minute + 1:
            state.mark_uncertain(bucket)
            self._enqueue_repair_locked(tick.instrument_token, bucket)
            return

        ip.seen_minutes.add(tick_minute)
        ip.last_tick_minute = tick_minute
        ip.high = max(ip.high, tick.last_price)
        ip.low = min(ip.low, tick.last_price)
        ip.close = tick.last_price

        cum = int(tick.volume_traded)
        if state.last_cumulative_volume is None:
            state.last_cumulative_volume = cum
            return
        if cum < state.last_cumulative_volume:
            state.volume_reset = True
            state.mark_uncertain(bucket)
            self._enqueue_repair_locked(tick.instrument_token, bucket)
            state.last_cumulative_volume = cum
            return
        delta = cum - state.last_cumulative_volume
        state.last_cumulative_volume = cum
        ip.volume += delta

    def _close_or_gap_locked(
        self,
        token: int,
        state: TokenVwapState,
        ip: InProgressFiveMinute,
        next_bucket: datetime,
    ) -> None:
        expected_minutes = {
            minute_of_day_from_datetime(ip.bucket_start) + offset for offset in range(5)
        }
        complete = ip.seen_minutes == expected_minutes
        if complete and ip.bucket_start != state.b0 and not state.is_uncertain(ip.bucket_start):
            pv = hlc3_pv(ip.high, ip.low, ip.close, ip.volume)
            state.commit_if_absent(
                ip.bucket_start,
                pv=pv,
                volume=ip.volume,
                provenance="live",
            )
        else:
            state.mark_uncertain(ip.bucket_start)
            self._enqueue_repair_locked(token, ip.bucket_start)
        # Skipped whole buckets between ip and next_bucket.
        skipped = ip.bucket_start
        while True:
            nxt = bucket_end_dt(skipped)
            if nxt >= next_bucket:
                break
            state.mark_uncertain(nxt)
            self._enqueue_repair_locked(token, nxt)
            skipped = nxt
        state.in_progress = None

    def seed_closed_bars(
        self,
        token: int,
        candles: Sequence[OneMinuteCandle],
    ) -> int:
        """Apply bootstrap 1m candles (test and worker). Returns bars committed."""
        with self._lock:
            if self._cutoff is None or self._b0 is None:
                self.freeze_cutoff()
            state = self._ensure_state_locked(token)
            contribs = seed_closed_five_minute_contributions(
                candles, cutoff=self._cutoff, b0=self._b0
            )
            n = 0
            for start, pv, volume, provenance in contribs:
                if state.commit_if_absent(start, pv=pv, volume=volume, provenance=provenance):
                    n += 1
            return n

    def mark_bootstrap_ready(self) -> None:
        with self._lock:
            self._bootstrap_ready = True
            for token, state in self._states.items():
                state.bootstrap_ready = True
                buf = self._buffers.pop(token, deque())
                if state.overflowed:
                    buf.clear()
                    continue
                for tick in buf:
                    self._apply_live_tick_locked(state, tick)
            if self._b0 is not None:
                for token in list(self._states.keys()):
                    self._enqueue_repair_locked(token, self._b0)

    def reconstruct_bucket(
        self,
        token: int,
        bucket_start: datetime,
        candles: Sequence[OneMinuteCandle],
    ) -> bool:
        """Synchronous reconstruct for tests / repair worker."""
        with self._lock:
            state = self._ensure_state_locked(token)
            b0 = state.b0
            provenance = provenance_for_bucket(bucket_start, b0)
            try:
                pv, volume = reconstruct_five_minute_hlc3(candles, bucket_start)
            except RepairValidateError:
                self._reconstruct_fail += 1
                return False
            inserted = state.commit_if_absent(
                bucket_start, pv=pv, volume=volume, provenance=provenance
            )
            if inserted:
                self._reconstruct_ok += 1
                state.volume_reset = False
            self._queued_repairs.discard((token, ensure_ist(bucket_start)))
            return inserted

    def on_raw_trigger(self, event: ContinuationTriggeredEvent) -> Optional[VwapQualification]:
        try:
            result = self._classify_triggered(event)
        except Exception:  # noqa: BLE001
            self._callback_failures += 1
            logger.exception("vwap qualifier classify failed setup=%s", event.setup_id)
            return None
        if result is None:
            return None
        if self._writer is not None:
            try:
                self._writer.enqueue_priority(result)
            except Exception:  # noqa: BLE001
                logger.exception("vwap qualifier enqueue failed setup=%s", event.setup_id)
        return result

    def _classify_triggered(self, event: ContinuationTriggeredEvent) -> VwapQualification:
        with self._lock:
            if self._cutoff is None:
                self.freeze_cutoff(event.exchange_timestamp)
            state = self._ensure_state_locked(event.instrument_token)
            trigger_ts = ensure_ist(event.exchange_timestamp)
            trigger_bucket = bucket_start_dt(trigger_ts)
            quality_reason: Optional[VwapQualityReason] = None
            quality_ok = True

            if not state.bootstrap_ready:
                quality_ok = False
                quality_reason = "bootstrap_not_ready"
            elif self._feed_stale:
                quality_ok = False
                quality_reason = "feed_stale"
            elif state.overflowed and (
                trigger_bucket == state.b0 or not state.bootstrap_ready
            ):
                quality_ok = False
                quality_reason = "bootstrap_buffer_overflow"
            elif trigger_bucket is not None and (
                trigger_bucket == state.b0 or state.is_uncertain(trigger_bucket)
            ):
                quality_ok = False
                quality_reason = (
                    "bootstrap_open_bucket"
                    if trigger_bucket == state.b0
                    else "gap_in_current_bucket"
                )
            elif trigger_bucket is not None and trigger_bucket in state.failed_buckets:
                quality_ok = False
                quality_reason = "repair_attempts_exhausted"
            elif state.b0 not in state.committed and trigger_bucket != state.b0:
                quality_ok = False
                if state.b0 in state.failed_buckets:
                    quality_reason = "repair_attempts_exhausted"
                else:
                    quality_reason = "repair_pending"
            elif event.tick_sequence > state.last_sequence:
                quality_ok = False
                quality_reason = "trigger_tick_not_applied"
            elif state.volume_reset:
                quality_ok = False
                quality_reason = "volume_reset"

            vwap: Optional[float] = None
            gap: Optional[float] = None
            in_progress_start = None
            in_progress_volume = 0
            contributions = state.snapshot_contributions()
            include_ip = (
                quality_ok
                and state.in_progress is not None
                and trigger_bucket == state.in_progress.bucket_start
                and not state.is_uncertain(state.in_progress.bucket_start)
            )
            ip_pv, ip_vol = (0.0, 0)
            if include_ip:
                ip_pv, ip_vol = state.live_in_progress_pv()
                in_progress_start = state.in_progress.bucket_start
                in_progress_volume = ip_vol
            total_pv = state.cum_pv + ip_pv
            total_vol = state.cum_vol + ip_vol
            if quality_ok:
                if total_vol <= 0:
                    quality_ok = False
                    quality_reason = "vwap_unavailable"
                else:
                    vwap = total_pv / float(total_vol)
                    gap = directional_gap(event.direction, event.trigger_price, vwap)
                    if gap is None:
                        quality_ok = False
                        quality_reason = "non_finite_gap"

            classification, reason = classify_gap(
                gap,
                quality_ok=quality_ok,
                quality_reason=quality_reason,
                config=self._config,
            )
            if classification == "UNAVAILABLE" and reason is not None:
                quality_reason = reason
                quality_ok = False

            provenance = _snapshot_provenance(contributions, include_repaired_ip=False)
            self._classified += 1
            if classification == "ACCEPT":
                self._accept += 1
            elif classification == "LIMITED":
                self._limited += 1
            elif classification == "REJECT":
                self._reject += 1
            else:
                self._unavailable += 1

            return VwapQualification(
                setup_id=event.setup_id,
                continuation_rule_version=event.continuation_rule_version,
                vwap_rule_version=self._config.rule_version,
                instrument_token=event.instrument_token,
                tradingsymbol=event.tradingsymbol,
                session_date=self._session_date,
                direction=event.direction,
                trigger_price=event.trigger_price,
                last_price=event.last_price,
                trigger_tick_sequence=event.tick_sequence,
                trigger_exchange_ts=trigger_ts,
                vwap=vwap,
                gap=gap,
                classification=classification,
                quality_ok=quality_ok,
                quality_reason=quality_reason,
                vwap_provenance=provenance if quality_ok else None,
                bootstrap_cutoff_exchange_ts=self._cutoff,
                completed_5m_count=len(state.committed),
                in_progress_bucket_start=in_progress_start,
                in_progress_volume=in_progress_volume,
                contributions=contributions,
                detected_at=datetime.now(timezone.utc),
            )

    def token_state(self, token: int) -> TokenVwapState:
        with self._lock:
            return self._ensure_state_locked(token)

    def _ensure_state_locked(self, token: int) -> TokenVwapState:
        state = self._states.get(token)
        if state is None:
            if self._b0 is None:
                self.freeze_cutoff()
            state = TokenVwapState(b0=self._b0)  # type: ignore[arg-type]
            self._states[token] = state
            if self._bootstrap_ready:
                state.bootstrap_ready = True
                self._enqueue_repair_locked(token, self._b0)
        return state

    def _enqueue_repair_locked(self, token: int, bucket_start: datetime) -> None:
        key = (token, ensure_ist(bucket_start))
        if key in self._queued_repairs:
            return
        self._queued_repairs.add(key)
        try:
            self._repair_queue.put_nowait(_RepairJob(token, key[1]))
        except queue.Full:
            self._queued_repairs.discard(key)
            logger.error("vwap repair queue full token=%s bucket=%s", token, key[1])

    def _bootstrap_loop(self) -> None:
        try:
            cutoff = self._cutoff
            b0 = self._b0
            if cutoff is None or b0 is None:
                cutoff = self.freeze_cutoff()
                b0 = self._b0
            assert cutoff is not None and b0 is not None
            start = session_open_dt(self._session_date)
            fetcher = self._fetcher
            if fetcher is None:
                logger.error("vwap bootstrap missing historical fetcher")
                self.mark_bootstrap_failed()
                return
            for token in self._tokens:
                if self._stop.is_set():
                    return
                try:
                    candles = fetcher.fetch_one_minute(token, start, cutoff)
                    self.seed_closed_bars(token, candles)
                except Exception:  # noqa: BLE001
                    logger.exception("vwap bootstrap fetch failed token=%s", token)
            self.mark_bootstrap_ready()
        except Exception:  # noqa: BLE001
            logger.exception("vwap bootstrap loop failed")
            try:
                self.mark_bootstrap_failed()
            except Exception:  # noqa: BLE001
                logger.exception("vwap mark_bootstrap_failed failed")

    def _repair_loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._repair_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if item is _REPAIR_SENTINEL:
                return
            if not isinstance(item, _RepairJob):
                continue
            self._run_repair_job(item)

    def _run_repair_job(self, job: _RepairJob) -> None:
        while not self._stop.is_set() and not can_fetch_bucket(job.bucket_start, self._now_fn()):
            remaining = (
                bucket_end_dt(job.bucket_start) - ensure_ist(self._now_fn())
            ).total_seconds()
            self._stop.wait(max(0.05, min(remaining, 1.0)))
        if self._stop.is_set() or not can_fetch_bucket(job.bucket_start, self._now_fn()):
            return
        max_attempts = self._config.repair_max_attempts
        schedule = self._config.repair_backoff_seconds
        fetcher = self._fetcher
        for attempt in range(max_attempts):
            if self._stop.is_set():
                return
            try:
                if fetcher is None:
                    raise RepairValidateError("no fetcher")
                candles = fetcher.fetch_one_minute(
                    job.instrument_token,
                    job.bucket_start,
                    bucket_end_dt(job.bucket_start),
                )
                ok = self.reconstruct_bucket(job.instrument_token, job.bucket_start, candles)
                if ok:
                    logger.info(
                        "vwap bucket reconstructed token=%s bucket=%s provenance=%s",
                        job.instrument_token,
                        job.bucket_start.isoformat(timespec="seconds"),
                        provenance_for_bucket(
                            job.bucket_start, self._b0 or job.bucket_start
                        ),
                    )
                    with self._lock:
                        self._queued_repairs.discard(
                            (job.instrument_token, ensure_ist(job.bucket_start))
                        )
                    return
                raise RepairValidateError("reconstruct returned false")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "vwap reconstruct attempt %d/%d failed token=%s bucket=%s: %s",
                    attempt + 1,
                    max_attempts,
                    job.instrument_token,
                    job.bucket_start.isoformat(timespec="seconds"),
                    exc,
                )
                if attempt + 1 >= max_attempts:
                    with self._lock:
                        state = self._ensure_state_locked(job.instrument_token)
                        state.failed_buckets.add(ensure_ist(job.bucket_start))
                        self._queued_repairs.discard(
                            (job.instrument_token, ensure_ist(job.bucket_start))
                        )
                    logger.error(
                        "vwap reconstruct exhausted token=%s bucket=%s",
                        job.instrument_token,
                        job.bucket_start.isoformat(timespec="seconds"),
                    )
                    return
                delay = backoff_delay_seconds(attempt, schedule)
                self._stop.wait(delay)

    def enqueue_repair(self, token: int, bucket_start: datetime) -> None:
        with self._lock:
            self._enqueue_repair_locked(token, bucket_start)


def _snapshot_provenance(contributions, include_repaired_ip: bool) -> Optional[VwapProvenance]:
    del include_repaired_ip
    if not contributions:
        return "live"
    provenances = {c.provenance for c in contributions}
    if "repaired" in provenances:
        return "repaired"
    if "bootstrap" in provenances:
        return "bootstrap"
    return "live"
