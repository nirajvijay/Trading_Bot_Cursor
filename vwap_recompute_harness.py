#!/usr/bin/env python3
"""
Phase 0: offline 1m session VWAP recompute harness.

Recomputes session VWAP from live_1m_candles for TRIGGERED continuation rows
and exports gap distribution for threshold validation.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

from vwap_qualifier_v2_features import classify_gap, directional_gap, hlc3_pv
from vwap_session_cache import session_open_dt

IST = ZoneInfo("Asia/Kolkata")

TRIGGERED_SQL = """
SELECT
    d.setup_id,
    d.continuation_rule_version,
    d.trigger_exchange_ts,
    d.last_price,
    a.instrument_token,
    a.tradingsymbol,
    a.direction,
    a.trigger_price,
    a.session_date
FROM live_continuation_decisions d
JOIN live_continuation_arms a
  ON a.setup_id = d.setup_id
 AND a.continuation_rule_version = d.continuation_rule_version
WHERE d.decision_type = 'TRIGGERED'
  AND a.session_date = ?
ORDER BY d.created_at ASC
"""

CANDLES_SQL = """
SELECT candle_time, open, high, low, close, volume
FROM live_1m_candles
WHERE instrument_token = ?
  AND session_date = ?
ORDER BY candle_time ASC
"""


@dataclass
class TriggerRecompute:
    setup_id: str
    session_date: str
    instrument_token: int
    tradingsymbol: str
    direction: str
    trigger_price: float
    trigger_exchange_ts: str
    vwap: Optional[float]
    gap: Optional[float]
    classification: str
    minute_count: int


def _parse_ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw).astimezone(IST)


def _last_completed_minute(trigger_ts: datetime) -> datetime:
    floored = trigger_ts.replace(second=0, microsecond=0)
    return floored - timedelta(minutes=1)


def recompute_vwap(
    conn: sqlite3.Connection,
    *,
    session_date: str,
    token: int,
    through: datetime,
) -> tuple[Optional[float], int]:
    open_dt = session_open_dt(session_date)
    end = through.replace(second=0, microsecond=0)
    rows = conn.execute(
        CANDLES_SQL,
        (token, session_date),
    ).fetchall()
    pv = 0.0
    vol = 0
    count = 0
    for row in rows:
        minute = _parse_ts(str(row[0]))
        if minute < open_dt or minute > end:
            continue
        o, h, l, c, v = float(row[1]), float(row[2]), float(row[3]), float(row[4]), int(row[5])
        if v < 0 or not all(math.isfinite(x) for x in (o, h, l, c)):
            continue
        pv += hlc3_pv(h, l, c, v)
        vol += v
        count += 1
    if vol <= 0:
        return None, count
    return pv / float(vol), count


def export_triggered(
    live_db: Path,
    *,
    session_date: str,
    output: Optional[Path] = None,
) -> List[TriggerRecompute]:
    if not live_db.exists():
        raise FileNotFoundError(live_db)
    conn = sqlite3.connect(f"file:{live_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        triggers = conn.execute(TRIGGERED_SQL, (session_date,)).fetchall()
        results: List[TriggerRecompute] = []
        for row in triggers:
            trigger_ts = _parse_ts(str(row["trigger_exchange_ts"]))
            through = _last_completed_minute(trigger_ts)
            vwap, minute_count = recompute_vwap(
                conn,
                session_date=session_date,
                token=int(row["instrument_token"]),
                through=through,
            )
            gap = None
            classification = "UNAVAILABLE"
            if vwap is not None:
                gap = directional_gap(str(row["direction"]), float(row["trigger_price"]), vwap)
                classification, _ = classify_gap(gap, quality_ok=True)
            results.append(
                TriggerRecompute(
                    setup_id=str(row["setup_id"]),
                    session_date=session_date,
                    instrument_token=int(row["instrument_token"]),
                    tradingsymbol=str(row["tradingsymbol"]),
                    direction=str(row["direction"]),
                    trigger_price=float(row["trigger_price"]),
                    trigger_exchange_ts=str(row["trigger_exchange_ts"]),
                    vwap=vwap,
                    gap=gap,
                    classification=classification,
                    minute_count=minute_count,
                )
            )
    finally:
        conn.close()
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = [asdict(r) for r in results]
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return results


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="VWAP v2 1m recompute harness")
    parser.add_argument("--live-db", type=Path, required=True)
    parser.add_argument("--session-date", required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    results = export_triggered(
        args.live_db,
        session_date=args.session_date,
        output=args.output,
    )
    counts = {}
    for r in results:
        counts[r.classification] = counts.get(r.classification, 0) + 1
    print(
        "Exported %d TRIGGERED rows: %s"
        % (len(results), json.dumps(counts, sort_keys=True)),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
