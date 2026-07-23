#!/usr/bin/env python3
"""
Offline end-to-end intraday spike replay smoke test (market closed).

Feeds realistic completed 1-minute candles through:
  MarketDataCoordinator → candle persist → BaselineStore → features →
  IntradaySpikeRuleEngine → spike persist + metrics

Usage:
  python3 smoke_test_intraday_spike_replay.py
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional
from zoneinfo import ZoneInfo

from baseline_generator import init_baselines_db
from baseline_store import BaselineStore
from candle_aggregation import CompletedOneMinuteCandle
from candle_emission import CandleEmissionError
from intraday_spike_detector import IntradaySpikeDetector
from intraday_spike_writer import IntradaySpikeWriter
from live_candle_pipeline import LiveCandlePipeline
from live_one_minute_candle_writer import LiveOneMinuteCandleWriter
from market_data_coordinator import MarketDataCoordinator
from tick_event import IST

_IST = ZoneInfo(IST)
SESSION_DATE = "2026-07-23"
BASELINE_AS_OF = "2026-07-22"  # strictly prior
TOKEN = 738561
SYMBOL = "RELIANCE"


@dataclass(frozen=True)
class Scenario:
    name: str
    candle: CompletedOneMinuteCandle


def _candle(
    hour: int,
    minute: int,
    *,
    open_: float = 100.0,
    high: float = 100.5,
    low: float = 99.5,
    close: float = 100.2,
    volume: int = 1_000,
    is_partial: bool = False,
    has_full_minute_coverage: bool = True,
    volume_reliable: bool = True,
    completion_reason: str = "minute_transition",
) -> CompletedOneMinuteCandle:
    return CompletedOneMinuteCandle(
        instrument_token=TOKEN,
        candle_time=datetime(2026, 7, 23, hour, minute, 0, tzinfo=_IST),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        tick_count=25,
        volume_reliable=volume_reliable,
        completion_reason=completion_reason,  # type: ignore[arg-type]
        has_full_minute_coverage=has_full_minute_coverage,
        is_partial=is_partial,
    )


def _scenarios() -> List[Scenario]:
    return [
        Scenario(
            "before_0930_rejected_window",
            _candle(9, 15, open_=100, high=103, low=99, close=102.5, volume=20_000),
        ),
        Scenario(
            "exactly_0930_eligible_normal",
            _candle(9, 30),  # small move / low volume → no spike
        ),
        Scenario(
            "normal_1000_no_spike",
            _candle(10, 0),
        ),
        Scenario(
            "valid_bullish_spike_1030",
            _candle(10, 30, open_=100, high=103, low=99, close=102.5, volume=20_000),
        ),
        Scenario(
            "valid_bearish_spike_1100",
            _candle(11, 0, open_=100, high=101, low=97, close=97.5, volume=20_000),
        ),
        Scenario(
            "missing_baseline_1130",
            _candle(11, 30, open_=100, high=103, low=99, close=102.5, volume=20_000),
        ),
        Scenario(
            "unreliable_baseline_1200",
            _candle(12, 0, open_=100, high=103, low=99, close=102.5, volume=20_000),
        ),
        Scenario(
            "partial_candle_1230",
            _candle(
                12,
                30,
                is_partial=True,
                has_full_minute_coverage=False,
                volume=20_000,
            ),
        ),
        Scenario(
            "exactly_1400_eligible_normal",
            _candle(14, 0),
        ),
        Scenario(
            "after_1400_rejected_window",
            _candle(14, 1, open_=100, high=103, low=99, close=102.5, volume=20_000),
        ),
    ]


def _seed_baselines(db_path: Path) -> None:
    conn = init_baselines_db(db_path)

    def insert(
        minute: int,
        *,
        as_of: str = BASELINE_AS_OF,
        is_reliable: int = 1,
        valid_sessions: int = 21,
        median_volume: float = 5_000.0,
        trimmed: float = 4_800.0,
        median_abs_return: float = 0.0005,
    ) -> None:
        conn.execute(
            """
            INSERT INTO baselines (
                instrument_token, tradingsymbol, minute_of_day,
                median_volume, trimmed_mean_volume, median_abs_return,
                valid_session_count, is_reliable, baseline_as_of_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                TOKEN,
                SYMBOL,
                minute,
                median_volume,
                trimmed,
                median_abs_return,
                valid_sessions,
                is_reliable,
                as_of,
            ),
        )

    # Strictly prior as_of used for live session.
    for minute in (555, 570, 600, 630, 660, 840, 841):
        insert(minute, is_reliable=1)
    insert(720, is_reliable=0, valid_sessions=10)  # 12:00 unreliable
    # Same-day as_of must NOT be selected for session 2026-07-23.
    insert(630, as_of=SESSION_DATE, median_volume=999_999.0)

    conn.commit()
    conn.close()


def _build_stack(
    live_db: Path,
    baselines_db: Path,
    *,
    extra_strategy: Optional[Callable[[CompletedOneMinuteCandle], None]] = None,
) -> tuple[
    LiveCandlePipeline,
    LiveOneMinuteCandleWriter,
    IntradaySpikeWriter,
    IntradaySpikeDetector,
    MarketDataCoordinator,
    BaselineStore,
]:
    store = BaselineStore.load(SESSION_DATE, db_path=baselines_db)
    token_map = {TOKEN: SYMBOL}
    candle_writer = LiveOneMinuteCandleWriter(
        db_path=live_db,
        token_to_symbol=token_map,
    )
    spike_writer = IntradaySpikeWriter(
        db_path=live_db,
        token_to_symbol=token_map,
    )
    detector = IntradaySpikeDetector(
        baseline_store=store,
        writer=spike_writer,
        token_to_symbol=token_map,
    )
    consumers: list = [detector.on_candle]
    if extra_strategy is not None:
        consumers.append(extra_strategy)
    coordinator = MarketDataCoordinator(
        candle_writer=candle_writer,
        strategy_consumers=consumers,
        closeables=[spike_writer],
    )
    pipeline = LiveCandlePipeline(coordinator=coordinator)
    return pipeline, candle_writer, spike_writer, detector, coordinator, store


def _dispatch(pipeline: LiveCandlePipeline, scenarios: List[Scenario]) -> None:
    for scenario in scenarios:
        pipeline.builder._on_candle(scenario.candle)


def _check(condition: bool, message: str, failures: List[str]) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {message}")
    if not condition:
        failures.append(message)


def main() -> int:
    print("=" * 72)
    print("Offline intraday spike E2E replay smoke test")
    print(f"Simulated session_date={SESSION_DATE}  expected baseline_as_of<{SESSION_DATE}")
    print("=" * 72)

    failures: List[str] = []
    scenarios = _scenarios()

    with tempfile.TemporaryDirectory(prefix="spike_replay_") as tmp:
        root = Path(tmp)
        live_db = root / "nifty50_live_1m.db"
        baselines_db = root / "nifty50_baselines.db"
        _seed_baselines(baselines_db)

        # --- Pass 1 ---
        print("\n[1] First replay pass through MarketDataCoordinator")
        (
            pipeline,
            candle_writer,
            spike_writer,
            detector,
            coordinator,
            store,
        ) = _build_stack(live_db, baselines_db)
        _check(
            store.baseline_as_of_date == BASELINE_AS_OF,
            f"BaselineStore as_of == {BASELINE_AS_OF} (got {store.baseline_as_of_date})",
            failures,
        )
        _check(
            store.baseline_as_of_date is not None
            and store.baseline_as_of_date < SESSION_DATE,
            "Baseline as_of strictly before simulated trading date",
            failures,
        )
        # Prove same-day as_of was not loaded (would have median_volume 999999 at 630).
        hit = store.get(TOKEN, 630)
        _check(
            hit is not None and hit.median_volume == 5_000.0,
            "Loaded prior as_of baselines (not same-day poisoned row)",
            failures,
        )

        _dispatch(pipeline, scenarios)
        metrics = detector.metrics.snapshot()
        print("\n  Detector metrics after pass 1:")
        print(f"    candles_seen={metrics.candles_seen}")
        print(f"    eligible_candles={metrics.eligible_candles}")
        print(f"    partial_skipped={metrics.partial_skipped}")
        print(f"    baseline_miss={metrics.baseline_miss}")
        print(f"    baseline_unreliable={metrics.baseline_unreliable}")
        print(f"    accepted_spikes={metrics.accepted_spikes}")
        print(f"    rejected_spikes={metrics.rejected_spikes}")
        print(f"    writer_failures={metrics.writer_failures}")

        _check(metrics.candles_seen == 10, "candles_seen == 10", failures)
        _check(metrics.eligible_candles == 9, "eligible_candles == 9", failures)
        _check(metrics.partial_skipped == 1, "partial_skipped == 1", failures)
        _check(metrics.baseline_miss == 1, "baseline_miss == 1", failures)
        _check(metrics.baseline_unreliable == 1, "baseline_unreliable == 1", failures)
        _check(metrics.accepted_spikes == 2, "accepted_spikes == 2", failures)
        _check(metrics.rejected_spikes == 5, "rejected_spikes == 5", failures)
        _check(metrics.writer_failures == 0, "writer_failures == 0", failures)
        _check(
            candle_writer.metrics.candles_inserted == 10,
            "candle rows inserted == 10",
            failures,
        )
        _check(
            spike_writer.metrics.spikes_inserted == 2,
            "spike rows inserted == 2",
            failures,
        )

        coordinator.close()

        # --- Inspect DB after pass 1 ---
        print("\n[2] Manual DB verification after pass 1")
        conn = sqlite3.connect(live_db)
        conn.row_factory = sqlite3.Row
        candle_rows = conn.execute(
            """
            SELECT candle_time, is_partial, volume, open, high, low, close
            FROM live_1m_candles
            ORDER BY candle_time
            """
        ).fetchall()
        spike_rows = conn.execute(
            """
            SELECT candle_time, direction, relative_volume_median,
                   relative_volume_trimmed, absolute_return, body_ratio,
                   close_location, baseline_as_of_date, rule_version
            FROM live_intraday_spikes
            ORDER BY candle_time
            """
        ).fetchall()

        print(f"  live_1m_candles rows: {len(candle_rows)}")
        for row in candle_rows:
            print(
                f"    {row['candle_time']} partial={row['is_partial']} "
                f"O={row['open']} H={row['high']} L={row['low']} C={row['close']} "
                f"V={row['volume']}"
            )
        print(f"  live_intraday_spikes rows: {len(spike_rows)}")
        for row in spike_rows:
            print(
                f"    {row['candle_time']} {row['direction']} "
                f"rvol_med={row['relative_volume_median']:.2f} "
                f"rvol_trm={row['relative_volume_trimmed']:.2f} "
                f"abs_ret={row['absolute_return']:.4f} "
                f"body={row['body_ratio']:.3f} "
                f"cloc={row['close_location']:.3f} "
                f"as_of={row['baseline_as_of_date']} "
                f"ver={row['rule_version']}"
            )

        _check(len(candle_rows) == 10, "DB candle count == 10", failures)
        _check(len(spike_rows) == 2, "DB spike count == 2", failures)

        times = [r["candle_time"] for r in candle_rows]
        _check(
            any("09:15:00" in t for t in times),
            "Candle present for 09:15",
            failures,
        )
        _check(
            any("09:30:00" in t for t in times),
            "Candle present for 09:30",
            failures,
        )
        _check(
            any("14:00:00" in t for t in times),
            "Candle present for 14:00",
            failures,
        )
        _check(
            any("14:01:00" in t for t in times),
            "Candle present for 14:01",
            failures,
        )
        partials = [r for r in candle_rows if r["is_partial"] == 1]
        _check(len(partials) == 1, "Exactly one partial candle row", failures)

        spike_times = [r["candle_time"] for r in spike_rows]
        _check(
            any("10:30:00" in t for t in spike_times),
            "Bullish spike at 10:30 persisted",
            failures,
        )
        _check(
            any("11:00:00" in t for t in spike_times),
            "Bearish spike at 11:00 persisted",
            failures,
        )
        for row in spike_rows:
            _check(
                row["baseline_as_of_date"] == BASELINE_AS_OF,
                f"Spike baseline_as_of_date == {BASELINE_AS_OF} ({row['candle_time']})",
                failures,
            )
            _check(
                row["rule_version"] == "intraday_spike_v1",
                f"Spike rule_version == intraday_spike_v1 ({row['candle_time']})",
                failures,
            )
            _check(
                row["relative_volume_median"] >= 2.0
                and row["relative_volume_trimmed"] >= 2.0,
                f"Spike relative volumes >= 2.0 ({row['candle_time']})",
                failures,
            )
            _check(
                row["body_ratio"] >= 0.60,
                f"Spike body_ratio >= 0.60 ({row['candle_time']})",
                failures,
            )

        bull = next(r for r in spike_rows if "10:30:00" in r["candle_time"])
        bear = next(r for r in spike_rows if "11:00:00" in r["candle_time"])
        _check(bull["direction"] == "UP", "10:30 spike direction UP", failures)
        _check(bull["close_location"] >= 0.70, "10:30 close_location >= 0.70", failures)
        _check(bear["direction"] == "DOWN", "11:00 spike direction DOWN", failures)
        _check(bear["close_location"] <= 0.30, "11:00 close_location <= 0.30", failures)
        conn.close()

        # --- Pass 2: restart + replay ---
        print("\n[3] Restart + second replay (idempotent)")
        (
            pipeline2,
            candle_writer2,
            spike_writer2,
            detector2,
            coordinator2,
            _,
        ) = _build_stack(live_db, baselines_db)
        _dispatch(pipeline2, scenarios)
        _check(
            candle_writer2.metrics.duplicates_ignored == 10,
            "Second pass: 10 candle duplicates ignored",
            failures,
        )
        _check(
            candle_writer2.metrics.candles_inserted == 0,
            "Second pass: 0 new candle inserts",
            failures,
        )
        _check(
            spike_writer2.metrics.duplicates_ignored == 2,
            "Second pass: 2 spike duplicates ignored",
            failures,
        )
        _check(
            spike_writer2.metrics.spikes_inserted == 0,
            "Second pass: 0 new spike inserts",
            failures,
        )
        coordinator2.close()

        conn = sqlite3.connect(live_db)
        candle_count = conn.execute("SELECT COUNT(*) FROM live_1m_candles").fetchone()[0]
        spike_count = conn.execute(
            "SELECT COUNT(*) FROM live_intraday_spikes"
        ).fetchone()[0]
        dup_candle = conn.execute(
            """
            SELECT COUNT(*) FROM (
              SELECT instrument_token, candle_time, COUNT(*) c
              FROM live_1m_candles
              GROUP BY 1, 2 HAVING c > 1
            )
            """
        ).fetchone()[0]
        dup_spike = conn.execute(
            """
            SELECT COUNT(*) FROM (
              SELECT instrument_token, candle_time, rule_version, COUNT(*) c
              FROM live_intraday_spikes
              GROUP BY 1, 2, 3 HAVING c > 1
            )
            """
        ).fetchone()[0]
        conn.close()
        _check(candle_count == 10, "After replay: still 10 candle rows", failures)
        _check(spike_count == 2, "After replay: still 2 spike rows", failures)
        _check(dup_candle == 0, "No duplicate candle PKs", failures)
        _check(dup_spike == 0, "No duplicate spike PKs", failures)

        # --- Strategy failure isolation ---
        print("\n[4] Strategy failure does not stop candle storage")
        live_db_iso = root / "isolation.db"
        boom_count = {"n": 0}

        def boom(_candle: CompletedOneMinuteCandle) -> None:
            boom_count["n"] += 1
            raise RuntimeError("simulated strategy crash")

        pipeline_iso, candle_iso, _, _, coord_iso, _ = _build_stack(
            live_db_iso,
            baselines_db,
            extra_strategy=boom,
        )
        pipeline_iso.builder._on_candle(_candle(10, 45))
        _check(
            candle_iso.metrics.candles_inserted == 1,
            "Candle stored despite strategy crash",
            failures,
        )
        _check(
            coord_iso.metrics.strategy_consumer_failures >= 1,
            "Coordinator counted strategy consumer failure",
            failures,
        )
        raised = False
        try:
            pipeline_iso.builder._on_candle(_candle(10, 46))
        except Exception:
            raised = True
            traceback.print_exc()
        _check(not raised, "Strategy crash did not propagate from coordinator", failures)
        coord_iso.close()

        # --- Candle persistence remains fatal ---
        print("\n[5] Candle persistence conflict remains fatal")
        live_db_fatal = root / "fatal.db"
        pipeline_f, candle_f, _, _, coord_f, _ = _build_stack(
            live_db_fatal,
            baselines_db,
        )
        first = _candle(10, 50, close=100.2)
        pipeline_f.builder._on_candle(first)
        fatal_ok = False
        try:
            pipeline_f.builder._on_candle(_candle(10, 50, close=100.9))
        except CandleEmissionError:
            fatal_ok = True
        except Exception:
            traceback.print_exc()
        _check(fatal_ok, "Divergent candle PK raises CandleEmissionError", failures)
        coord_f.close()

    print("\n" + "=" * 72)
    if failures:
        print(f"RESULT: FAIL ({len(failures)} check(s) failed)")
        for item in failures:
            print(f"  - {item}")
        print("=" * 72)
        return 1

    print("RESULT: PASS — all offline E2E replay checks succeeded")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
