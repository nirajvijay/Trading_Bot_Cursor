#!/usr/bin/env python3
"""Replay smoke test for vwap_qualifier_v2 on synthetic 1m candles."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from continuation_types import ContinuationArmedEvent, ContinuationTriggeredEvent
from vwap_qualifier_v2 import VwapQualifierV2

IST = ZoneInfo("Asia/Kolkata")
TOKEN = 1


def main() -> int:
    engine = VwapQualifierV2(
        session_date="2026-09-01",
        token_to_symbol={TOKEN: "TEST"},
        writer=None,
        start_scheduler=False,
    )
    engine.on_armed(
        ContinuationArmedEvent(
            setup_id="s1",
            instrument_token=TOKEN,
            tradingsymbol="TEST",
            session_date="2026-09-01",
            direction="UP",
            pullback_swing_high=None,
            pullback_swing_low=90.0,
            tick_size=0.05,
            buffer_ticks=1,
            trigger_price=100.0,
            trigger_price_ticks=2000,
            continuation_rule_version="continuation_v1",
            ready_5m_candle_time=None,
            armed_at=datetime(2026, 9, 1, 9, 20, tzinfo=IST),
        )
    )
    engine.registry.set_cache_state(TOKEN, "ready")
    from candle_aggregation import CompletedOneMinuteCandle
    from vwap_session_cache import session_open_dt

    m = session_open_dt("2026-09-01")
    for i in range(10):
        engine.on_one_minute(
            CompletedOneMinuteCandle(
                instrument_token=TOKEN,
                candle_time=m + timedelta(minutes=i),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=1000,
                tick_count=5,
                volume_reliable=True,
                completion_reason="minute_transition",
                has_full_minute_coverage=True,
                is_partial=False,
            )
        )
    trigger_ts = m + timedelta(minutes=10, seconds=30)
    event = ContinuationTriggeredEvent(
        setup_id="s1",
        instrument_token=TOKEN,
        tradingsymbol="TEST",
        direction="UP",
        trigger_price=100.0,
        trigger_price_ticks=2000,
        last_price=100.0,
        last_price_ticks=2000,
        tick_sequence=1,
        exchange_timestamp=trigger_ts,
        breakout_candle_time=trigger_ts - timedelta(minutes=1),
        breakout_candle_volume=1000,
        avg_prior_3_1m_volume=500.0,
        continuation_rule_version="continuation_v1",
        detected_at=trigger_ts,
    )
    result = engine.on_triggered(event)
    if result is None:
        print("FAIL: no qualification", file=sys.stderr)
        return 1
    print(
        "OK classification=%s gap=%s vwap=%s"
        % (result.classification, result.gap, result.vwap)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
