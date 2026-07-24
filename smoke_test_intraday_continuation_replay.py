"""
Offline smoke: pullback READY → continuation arm → TRIGGERED/REJECTED determinism.

No Kite. Exercises active-clear and immediate re-spike after REJECTED.
"""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from candle_aggregation import CompletedFiveMinuteCandle, CompletedOneMinuteCandle
from continuation_tick_size import TickSizeMap
from continuation_types import ContinuationRejectedEvent, ContinuationTriggeredEvent
from intraday_continuation_engine import IntradayContinuationEngine
from intraday_continuation_writer import IntradayContinuationWriter
from intraday_pullback_engine import IntradayPullbackEngine
from intraday_pullback_writer import IntradayPullbackWriter
from spike_types import IntradaySpikeEvent, SpikeDecision, SpikeFeatures
from tick_event import IST, Ohlc, TickEvent

_IST = ZoneInfo(IST)
TOKEN = 42
SYMBOL = "RELIANCE"
SESSION = "2026-07-22"
TICK_SIZE = 0.05


def _spike(minute: int = 10 * 60 + 32) -> IntradaySpikeEvent:
    hour, mins = divmod(minute, 60)
    ct = datetime(2026, 7, 22, hour, mins, 0, tzinfo=_IST)
    features = SpikeFeatures(
        instrument_token=TOKEN,
        minute_of_day=minute,
        session_date=SESSION,
        baseline_as_of_date="2026-07-21",
        open=101.0,
        high=110.0,
        low=100.0,
        close=109.0,
        volume=1000,
        tick_count=20,
        volume_reliable=True,
        absolute_return=0.08,
        signed_return=0.08,
        direction="UP",
        relative_volume_median=3.0,
        relative_volume_trimmed=3.0,
        abs_return_vs_baseline=3.0,
        body_ratio=0.8,
        close_location=0.9,
        median_volume=100.0,
        trimmed_mean_volume=100.0,
        median_abs_return=0.001,
        valid_session_count=20,
        is_reliable=True,
    )
    return IntradaySpikeEvent(
        instrument_token=TOKEN,
        tradingsymbol=SYMBOL,
        candle_time=ct,
        session_date=SESSION,
        rule_version="intraday_spike_v1",
        direction="UP",
        open=101.0,
        high=110.0,
        low=100.0,
        close=109.0,
        volume=1000,
        features=features,
        detected_at=datetime.now(timezone.utc),
        decision=SpikeDecision(
            accepted=True, rule_version="intraday_spike_v1", reasons=frozenset()
        ),
    )


def _5m(hour: int, minute: int, o: float, h: float, l: float, c: float) -> CompletedFiveMinuteCandle:
    return CompletedFiveMinuteCandle(
        instrument_token=TOKEN,
        candle_time=datetime(2026, 7, 22, hour, minute, 0, tzinfo=_IST),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=500,
        session_date=SESSION,
        constituent_count=5,
        all_volume_reliable=True,
        any_partial=False,
        all_full_coverage=True,
        tick_count=25,
    )


def _1m(hour: int, minute: int, volume: int) -> CompletedOneMinuteCandle:
    return CompletedOneMinuteCandle(
        instrument_token=TOKEN,
        candle_time=datetime(2026, 7, 22, hour, minute, 0, tzinfo=_IST),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=volume,
        tick_count=10,
        volume_reliable=True,
        is_partial=False,
        has_full_minute_coverage=True,
        completion_reason="minute_transition",
    )


def _tick(seq: int, price: float, hour: int, minute: int, second: int, vol: int) -> TickEvent:
    ts = datetime(2026, 7, 22, hour, minute, second, tzinfo=_IST)
    return TickEvent(
        sequence=seq,
        instrument_token=TOKEN,
        last_price=price,
        exchange_timestamp=ts,
        received_at=ts,
        volume_traded=vol,
        last_traded_quantity=1,
        average_traded_price=price,
        ohlc=Ohlc(open=price, high=price, low=price, close=price),
    )


def _drive_ready(pullback: IntradayPullbackEngine) -> None:
    pullback.on_spike(_spike())
    pullback.on_five_minute_candle(_5m(10, 30, 100, 110, 100, 109))
    pullback.on_five_minute_candle(_5m(10, 35, 109, 109, 104, 105))
    pullback.on_five_minute_candle(_5m(10, 40, 105, 108, 104, 106))


def main() -> int:
    checks: list[tuple[str, bool]] = []

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "live.db"
        pb_writer = IntradayPullbackWriter(db_path=db)
        cont_writer = IntradayContinuationWriter(db_path=db)
        tick_sizes = TickSizeMap(by_token={TOKEN: TICK_SIZE}, by_symbol={SYMBOL: TICK_SIZE})
        triggered: list[ContinuationTriggeredEvent] = []
        rejected: list[ContinuationRejectedEvent] = []

        cont_holder: dict = {}

        def on_ready(c):
            cont_holder["c"].on_pullback_ready(c)

        pullback = IntradayPullbackEngine(
            writer=pb_writer,
            on_pullback_ready=on_ready,
            on_setup_terminal=lambda sid, reason: cont_holder["c"].on_setup_terminal(
                sid, reason
            ),
        )
        continuation = IntradayContinuationEngine(
            writer=cont_writer,
            tick_sizes=tick_sizes,
            pullback_closer=pullback,
            on_triggered=triggered.append,
            on_rejected=rejected.append,
        )
        cont_holder["c"] = continuation

        _drive_ready(pullback)
        checks.append(("armed after READY", continuation.metrics.arms_created == 1))
        setup_id = pullback.active_setup_id_for_token(TOKEN)
        checks.append(("pullback still active while armed", setup_id is not None))

        for i, vol in enumerate((100, 100, 100)):
            continuation.on_one_minute(_1m(10, 41 + i, vol))
        continuation.on_tick(_tick(1, 100.0, 10, 44, 50, 1000))
        continuation.on_tick(_tick(2, 100.0, 10, 45, 1, 1000))
        continuation.on_tick(_tick(3, 109.05, 10, 45, 10, 1200))

        checks.append(("TRIGGERED once", len(triggered) == 1))
        checks.append(("active cleared after TRIGGERED", not pullback.is_setup_active(setup_id or "")))

        # Replay identical sequence into fresh engines → same decision.
        db2 = Path(tmp) / "live2.db"
        pb2 = IntradayPullbackWriter(db_path=db2)
        cw2 = IntradayContinuationWriter(db_path=db2)
        trig2: list = []
        holder2: dict = {}
        pb_eng2 = IntradayPullbackEngine(
            writer=pb2,
            on_pullback_ready=lambda c: holder2["c"].on_pullback_ready(c),
        )
        cont2 = IntradayContinuationEngine(
            writer=cw2,
            tick_sizes=tick_sizes,
            pullback_closer=pb_eng2,
            on_triggered=trig2.append,
        )
        holder2["c"] = cont2
        _drive_ready(pb_eng2)
        for i, vol in enumerate((100, 100, 100)):
            cont2.on_one_minute(_1m(10, 41 + i, vol))
        cont2.on_tick(_tick(1, 100.0, 10, 44, 50, 1000))
        cont2.on_tick(_tick(2, 100.0, 10, 45, 1, 1000))
        cont2.on_tick(_tick(3, 109.05, 10, 45, 10, 1200))
        checks.append(("replay TRIGGERED once", len(trig2) == 1))
        checks.append(
            (
                "replay same setup_id",
                trig2[0].setup_id == triggered[0].setup_id if trig2 and triggered else False,
            )
        )

        # REJECTED path + immediate re-spike
        db3 = Path(tmp) / "live3.db"
        pb3w = IntradayPullbackWriter(db_path=db3)
        cw3 = IntradayContinuationWriter(db_path=db3)
        rej: list = []
        holder3: dict = {}
        pb3 = IntradayPullbackEngine(
            writer=pb3w,
            on_pullback_ready=lambda c: holder3["c"].on_pullback_ready(c),
        )
        cont3 = IntradayContinuationEngine(
            writer=cw3,
            tick_sizes=tick_sizes,
            pullback_closer=pb3,
            on_rejected=rej.append,
        )
        holder3["c"] = cont3
        _drive_ready(pb3)
        sid = pb3.active_setup_id_for_token(TOKEN)
        for i, vol in enumerate((100, 100, 100)):
            cont3.on_one_minute(_1m(10, 41 + i, vol))
        cont3.on_tick(_tick(1, 100.0, 10, 44, 50, 1000))
        cont3.on_tick(_tick(2, 100.0, 10, 45, 1, 1000))
        cont3.on_tick(_tick(3, 109.05, 10, 45, 10, 1050))  # weak volume
        checks.append(("REJECTED once", len(rej) == 1))
        checks.append(("active cleared after REJECTED", sid is not None and not pb3.is_setup_active(sid)))
        ignored_before = pb3.metrics.spike_ignored_while_active
        pb3.on_spike(_spike(minute=11 * 60 + 2))
        checks.append(
            (
                "immediate re-spike allowed",
                pb3.metrics.spike_ignored_while_active == ignored_before
                and pb3.active_setup_id_for_token(TOKEN) is not None,
            )
        )

        conn = sqlite3.connect(db)
        n_dec = conn.execute("SELECT COUNT(*) FROM live_continuation_decisions").fetchone()[0]
        conn.close()
        checks.append(("decision rows written", n_dec == 1))

        pb_writer.close()
        cont_writer.close()
        pb2.close()
        cw2.close()
        pb3w.close()
        cw3.close()

    print("=== Continuation replay smoke ===")
    failed = 0
    for name, ok in checks:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")
        if not ok:
            failed += 1
    print("Result: %s (%d/%d)" % ("OK" if failed == 0 else "FAILED", len(checks) - failed, len(checks)))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
