"""
Intraday spike detector: quality gate → baseline → features → rules → persist/emit.

Non-fatal to market data: writer failures are counted, not re-raised.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Callable, Mapping, Optional

from baseline_store import BaselineStore
from candle_aggregation import CompletedOneMinuteCandle, minute_of_day_from_datetime
from candle_quality_gate import evaluate_candle_quality, primary_quality_skip_reason
from intraday_spike_config import IntradaySpikeRuleConfig
from intraday_spike_rules import IntradaySpikeRuleEngine
from intraday_spike_writer import IntradaySpikeWriter, SpikeConflictError
from spike_features import compute_spike_features
from spike_metrics import SpikeMetrics
from spike_types import IntradaySpikeEvent

logger = logging.getLogger(__name__)

OnSpikeCallback = Callable[[IntradaySpikeEvent], None]


class IntradaySpikeDetector:
    def __init__(
        self,
        *,
        baseline_store: BaselineStore,
        writer: IntradaySpikeWriter,
        token_to_symbol: Mapping[int, str],
        config: Optional[IntradaySpikeRuleConfig] = None,
        on_spike: Optional[OnSpikeCallback] = None,
        metrics: Optional[SpikeMetrics] = None,
    ) -> None:
        self._baselines = baseline_store
        self._writer = writer
        self._token_to_symbol = dict(token_to_symbol)
        self._config = config if config is not None else IntradaySpikeRuleConfig()
        self._rules = IntradaySpikeRuleEngine(self._config)
        self._on_spike = on_spike
        self._metrics = metrics if metrics is not None else SpikeMetrics()

    @property
    def metrics(self) -> SpikeMetrics:
        return self._metrics

    @property
    def config(self) -> IntradaySpikeRuleConfig:
        return self._config

    def on_candle(self, candle: CompletedOneMinuteCandle) -> None:
        self._metrics.candles_seen += 1

        quality = evaluate_candle_quality(candle, self._config)
        if not quality.eligible:
            self._metrics.partial_skipped += 1
            logger.debug(
                "spike quality skip token=%s reasons=%s",
                candle.instrument_token,
                sorted(quality.reasons),
            )
            _ = primary_quality_skip_reason(quality)
            return

        self._metrics.eligible_candles += 1

        minute = minute_of_day_from_datetime(candle.candle_time)
        lookup = self._baselines.lookup(candle.instrument_token, minute)
        if lookup.status == "miss":
            self._metrics.baseline_miss += 1
            return
        if lookup.status == "unreliable" and self._config.require_reliable_baseline:
            self._metrics.baseline_unreliable += 1
            return

        snapshot = lookup.snapshot
        if snapshot is None:
            self._metrics.baseline_miss += 1
            return

        features, feature_skip = compute_spike_features(candle, snapshot)
        if features is None:
            self._metrics.feature_skipped += 1
            logger.debug(
                "spike feature skip token=%s reason=%s",
                candle.instrument_token,
                feature_skip,
            )
            return

        decision = self._rules.evaluate(features)
        if not decision.accepted:
            self._metrics.rejected_spikes += 1
            return

        tradingsymbol = self._token_to_symbol.get(candle.instrument_token)
        if tradingsymbol is None:
            self._metrics.writer_failures += 1
            logger.error(
                "spike accepted but unknown instrument_token=%s",
                candle.instrument_token,
            )
            return

        event = IntradaySpikeEvent(
            instrument_token=candle.instrument_token,
            tradingsymbol=tradingsymbol,
            candle_time=candle.candle_time,
            session_date=features.session_date,
            rule_version=decision.rule_version,
            direction=features.direction,
            open=features.open,
            high=features.high,
            low=features.low,
            close=features.close,
            volume=features.volume,
            features=features,
            detected_at=datetime.now(timezone.utc),
            decision=decision,
        )

        try:
            self._writer.on_spike(event)
        except (SpikeConflictError, ValueError, RuntimeError, sqlite3.OperationalError) as exc:
            self._metrics.writer_failures += 1
            logger.exception("spike writer failure: %s", exc)
            return
        except Exception as exc:  # noqa: BLE001 — strategy path must stay non-fatal
            self._metrics.writer_failures += 1
            logger.exception("unexpected spike writer failure: %s", exc)
            return

        self._metrics.accepted_spikes += 1

        if self._on_spike is not None:
            try:
                self._on_spike(event)
            except Exception:  # noqa: BLE001
                logger.exception("on_spike callback failed")
