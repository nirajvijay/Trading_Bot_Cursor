"""
vwap_qualifier_v2 — 1m session VWAP orchestrator.

Classify on TRIGGERED; bounded sync persist; armed-symbol cache only.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo

from pathlib import Path

from api import config as api_config
from api.admin_config.snapshot import AdminConfigSnapshot
from api.admin_config.store import AdminConfigStore
from api.admin_config.vwap_adapter import snapshot_to_vwap_config
from candle_aggregation import CompletedOneMinuteCandle, ensure_ist
from continuation_types import ContinuationArmedEvent, ContinuationTriggeredEvent
from tick_event import IST
from vwap_arm_registry import VwapArmRegistry
from vwap_historical_scheduler import (
    KiteHistoricalFetcher,
    SharedRateLimiter,
    VwapHistoricalScheduler,
)
from vwap_qualifier_v2_config import VwapQualifierV2Config, load_vwap_qualifier_v2_config
from vwap_qualifier_v2_features import classify_gap, directional_gap, risk_for_classification
from vwap_qualifier_v2_types import VwapQualificationV2
from vwap_qualifier_v2_writer import VwapQualifierV2Writer
from vwap_session_cache import SessionVwapCache, last_completed_minute_start

logger = logging.getLogger(__name__)


def _resolve_admin_config_db(
    admin_config_db: Optional[Path],
    *,
    fallback_parent: Path,
) -> Path:
    if admin_config_db is not None:
        return Path(admin_config_db)
    preferred = api_config.admin_config_db_path()
    try:
        preferred.parent.mkdir(parents=True, exist_ok=True)
        return preferred
    except OSError:
        return fallback_parent / "admin_config.db"


_IST = ZoneInfo(IST)


@dataclass(frozen=True)
class VwapV2Metrics:
    classified: int
    accept: int
    limited: int
    reject: int
    unavailable: int
    persist_failures: int
    callback_failures: int


class VwapQualifierV2:
    def __init__(
        self,
        *,
        session_date: str,
        token_to_symbol: Dict[int, str],
        writer: Optional[VwapQualifierV2Writer] = None,
        config: Optional[VwapQualifierV2Config] = None,
        scheduler: Optional[VwapHistoricalScheduler] = None,
        now_fn: Optional[Callable[[], datetime]] = None,
        start_scheduler: bool = True,
        admin_config_db: Optional[Path] = None,
    ) -> None:
        self._config = config or load_vwap_qualifier_v2_config()
        fallback_parent = (
            Path(writer._db_path).parent if writer is not None else Path(".")
        )
        db_path = _resolve_admin_config_db(
            admin_config_db,
            fallback_parent=fallback_parent,
        )
        self._admin_store = AdminConfigStore(db_path, read_only=True)
        self._session_date = session_date
        self._token_to_symbol = dict(token_to_symbol)
        self._writer = writer
        self._now_fn = now_fn or (lambda: datetime.now(_IST))
        self._lock = threading.RLock()
        self._feed_stale = False
        self._classified = 0
        self._accept = 0
        self._limited = 0
        self._reject = 0
        self._unavailable = 0
        self._persist_failures = 0
        self._callback_failures = 0

        self._cache = SessionVwapCache(session_date)
        self._registry = VwapArmRegistry(
            on_warmup=self._on_warmup,
            on_cold=self._on_cold,
        )
        if scheduler is not None:
            self._scheduler = scheduler
        else:
            limiter = SharedRateLimiter(self._config.historical_max_requests_per_second)
            fetcher = KiteHistoricalFetcher(rate_limiter=limiter)
            self._scheduler = VwapHistoricalScheduler(
                cache=self._cache,
                session_date=session_date,
                fetcher=fetcher,
                on_ready=self._on_scheduler_ready,
                on_degraded=self._on_scheduler_degraded,
            )
        if start_scheduler:
            self._scheduler.start()

    @property
    def config(self) -> VwapQualifierV2Config:
        return self._config

    @property
    def cache(self) -> SessionVwapCache:
        return self._cache

    @property
    def registry(self) -> VwapArmRegistry:
        return self._registry

    @property
    def scheduler(self) -> VwapHistoricalScheduler:
        return self._scheduler

    @property
    def metrics(self) -> VwapV2Metrics:
        with self._lock:
            return VwapV2Metrics(
                classified=self._classified,
                accept=self._accept,
                limited=self._limited,
                reject=self._reject,
                unavailable=self._unavailable,
                persist_failures=self._persist_failures,
                callback_failures=self._callback_failures,
            )

    def status_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            m = self.metrics
            state = "ready"
            if self._feed_stale:
                state = "unavailable"
            elif self._registry.armed_tokens():
                warming = any(
                    self._registry.cache_state(t) == "warming"
                    for t in self._registry.armed_tokens()
                )
                if warming:
                    state = "bootstrapping"
            return {
                "state": state,
                "bootstrap_ready": state == "ready",
                "feed_stale": self._feed_stale,
                "token_count": len(self._token_to_symbol),
                "accept": m.accept,
                "limited": m.limited,
                "reject": m.reject,
                "unavailable": m.unavailable,
                "persist_failures": m.persist_failures,
                "reason": "feed_stale" if self._feed_stale else None,
            }

    def _on_warmup(self, token: int) -> None:
        now = self._now_fn()
        end = ensure_ist(now).replace(second=0, microsecond=0) + timedelta(minutes=1)
        self._scheduler.request_warmup(token, end_exclusive=end)

    def _on_cold(self, token: int) -> None:
        self._cache.destroy_token(token)

    def _on_scheduler_ready(self, token: int) -> None:
        self._registry.set_cache_state(token, "ready")

    def _on_scheduler_degraded(self, token: int) -> None:
        self._registry.set_cache_state(token, "degraded")

    def on_armed(self, event: ContinuationArmedEvent) -> None:
        self._registry.arm(event.setup_id, event.instrument_token)

    def on_disarmed(self, setup_id: str, instrument_token: int) -> None:
        self._registry.disarm(setup_id, instrument_token)

    def on_one_minute(self, candle: CompletedOneMinuteCandle) -> None:
        if not self._registry.is_armed(candle.instrument_token):
            return
        self._cache.on_live_candle(candle)

    def mark_feed_restored(self, _restored_at: datetime) -> None:
        with self._lock:
            self._feed_stale = False

    def mark_feed_interrupted(self, _interrupted_at: datetime) -> None:
        with self._lock:
            self._feed_stale = True

    def on_triggered(self, event: ContinuationTriggeredEvent) -> Optional[VwapQualificationV2]:
        try:
            result = self._classify(event)
        except Exception:  # noqa: BLE001
            self._callback_failures += 1
            logger.exception("vwap v2 classify failed setup=%s", event.setup_id)
            return None
        if result is None:
            return None
        if self._writer is not None:
            admin_snapshot = self._admin_store.capture_snapshot(
                risk_cap_used_inr=result.risk_cap_inr
            )
            ok = self._writer.insert_sync(result, provenance=admin_snapshot)
            if not ok and result.classification in ("ACCEPT", "LIMITED", "REJECT"):
                with self._lock:
                    self._persist_failures += 1
                return VwapQualificationV2(
                    setup_id=result.setup_id,
                    continuation_rule_version=result.continuation_rule_version,
                    vwap_rule_version=result.vwap_rule_version,
                    instrument_token=result.instrument_token,
                    tradingsymbol=result.tradingsymbol,
                    session_date=result.session_date,
                    direction=result.direction,
                    trigger_price=result.trigger_price,
                    last_price=result.last_price,
                    trigger_tick_sequence=result.trigger_tick_sequence,
                    trigger_exchange_ts=result.trigger_exchange_ts,
                    vwap=result.vwap,
                    gap=result.gap,
                    classification="UNAVAILABLE",
                    quality_ok=False,
                    quality_reason="persist_failed",
                    risk_profile=None,
                    risk_cap_inr=None,
                    requested_cutoff_minute=result.requested_cutoff_minute,
                    actual_snapshot_cutoff_minute=result.actual_snapshot_cutoff_minute,
                    cache_age_minutes=result.cache_age_minutes,
                    historical_minute_count=result.historical_minute_count,
                    live_minute_count=result.live_minute_count,
                    detected_at=result.detected_at,
                )
        return result

    def _classify(self, event: ContinuationTriggeredEvent) -> VwapQualificationV2:
        admin_snapshot = self._admin_store.capture_snapshot()
        vwap_config = snapshot_to_vwap_config(admin_snapshot)
        trigger_ts = ensure_ist(event.exchange_timestamp)
        requested_cutoff = trigger_ts.replace(second=0, microsecond=0)
        cache_state = self._registry.cache_state(event.instrument_token)
        snap = self._cache.build_snapshot(
            event.instrument_token,
            requested_cutoff=requested_cutoff,
            cache_state=cache_state,
            feed_stale=self._feed_stale,
        )
        quality_ok = snap.quality_ok
        quality_reason = snap.quality_reason
        gap = None
        classification = "UNAVAILABLE"
        risk_profile = None
        risk_cap_inr = None

        if quality_ok and snap.vwap is not None:
            gap = directional_gap(event.direction, event.trigger_price, snap.vwap)
            classification, gap_reason = classify_gap(
                gap,
                quality_ok=True,
                config=vwap_config,
            )
            if gap_reason is not None:
                quality_ok = False
                quality_reason = gap_reason
                classification = "UNAVAILABLE"
        elif not quality_ok:
            classification = "UNAVAILABLE"
        else:
            quality_ok = False
            quality_reason = "non_finite_vwap"
            classification = "UNAVAILABLE"

        if quality_ok:
            risk_profile, risk_cap_inr = risk_for_classification(
                classification, config=vwap_config
            )
        else:
            classification = "UNAVAILABLE"

        row = VwapQualificationV2(
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
            vwap=snap.vwap,
            gap=gap,
            classification=classification,  # type: ignore[arg-type]
            quality_ok=quality_ok,
            quality_reason=quality_reason,
            risk_profile=risk_profile,
            risk_cap_inr=risk_cap_inr,
            requested_cutoff_minute=requested_cutoff,
            actual_snapshot_cutoff_minute=snap.last_committed_minute,
            cache_age_minutes=snap.cache_age_minutes,
            historical_minute_count=snap.historical_minute_count,
            live_minute_count=snap.live_minute_count,
            detected_at=self._now_fn(),
        )
        with self._lock:
            self._classified += 1
            if classification == "ACCEPT":
                self._accept += 1
            elif classification == "LIMITED":
                self._limited += 1
            elif classification == "REJECT":
                self._reject += 1
            else:
                self._unavailable += 1
        return row

    def close(self) -> None:
        self._scheduler.stop()
        if self._writer is not None:
            self._writer.close()
        self._admin_store.close()
