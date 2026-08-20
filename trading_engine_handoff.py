"""Read continuation TRIGGERED rows. Observation tables are never written here."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List, Optional

from trading_engine_types import TriggerCandidate

TRIGGER_SQL = """
SELECT
    d.setup_id,
    d.continuation_rule_version,
    d.trigger_exchange_ts,
    d.last_price,
    d.created_at,
    a.instrument_token,
    a.tradingsymbol,
    a.direction,
    a.pullback_swing_high,
    a.pullback_swing_low,
    a.trigger_price,
    a.tick_size,
    a.buffer_ticks,
    a.session_date
FROM live_continuation_decisions d
JOIN live_continuation_arms a
  ON a.setup_id = d.setup_id
 AND a.continuation_rule_version = d.continuation_rule_version
WHERE d.decision_type = 'TRIGGERED'
  AND d.created_at >= ?
ORDER BY d.created_at ASC
"""


def fetch_triggered_since(
    live_db: Path,
    *,
    created_at_gte: str,
) -> List[TriggerCandidate]:
    if not live_db.exists():
        return []
    conn = sqlite3.connect(f"file:{live_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(TRIGGER_SQL, (created_at_gte,)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()

    out: List[TriggerCandidate] = []
    for row in rows:
        out.append(
            TriggerCandidate(
                setup_id=str(row["setup_id"]),
                continuation_rule_version=str(row["continuation_rule_version"]),
                session_date=str(row["session_date"]),
                tradingsymbol=str(row["tradingsymbol"]),
                instrument_token=int(row["instrument_token"]),
                direction=str(row["direction"]),
                trigger_price=float(row["trigger_price"]),
                pullback_swing_high=(
                    None
                    if row["pullback_swing_high"] is None
                    else float(row["pullback_swing_high"])
                ),
                pullback_swing_low=(
                    None
                    if row["pullback_swing_low"] is None
                    else float(row["pullback_swing_low"])
                ),
                tick_size=float(row["tick_size"]),
                buffer_ticks=int(row["buffer_ticks"]),
                trigger_exchange_ts=(
                    None
                    if row["trigger_exchange_ts"] is None
                    else str(row["trigger_exchange_ts"])
                ),
                created_at=str(row["created_at"]),
                last_price=(
                    None if row["last_price"] is None else float(row["last_price"])
                ),
            )
        )
    return out
