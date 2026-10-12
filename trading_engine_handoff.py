"""Read TRIGGERED rows: continuation (funnel) and ORB. Observation tables are
never written here."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List, Optional

from trading_engine_types import STRATEGY_ORB, TriggerCandidate

VWAP_RULE_VERSION = "vwap_qualifier_v2"

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
    a.session_date,
    d.breakout_candle_volume,
    d.avg_prior_3_1m_volume
FROM live_continuation_decisions d
JOIN live_continuation_arms a
  ON a.setup_id = d.setup_id
 AND a.continuation_rule_version = d.continuation_rule_version
WHERE d.decision_type = 'TRIGGERED'
  AND d.created_at >= ?
ORDER BY d.created_at ASC
"""

VWAP_CLASS_SQL = """
SELECT classification
FROM live_vwap_qualifications
WHERE session_date = ?
  AND setup_id = ?
  AND continuation_rule_version = ?
  AND vwap_rule_version = ?
"""

TRIGGER_WITH_VWAP_SQL = """
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
    a.session_date,
    d.breakout_candle_volume,
    d.avg_prior_3_1m_volume,
    v.classification
FROM live_continuation_decisions d
JOIN live_continuation_arms a
  ON a.setup_id = d.setup_id
 AND a.continuation_rule_version = d.continuation_rule_version
LEFT JOIN live_vwap_qualifications v
  ON v.session_date = a.session_date
 AND v.setup_id = d.setup_id
 AND v.continuation_rule_version = d.continuation_rule_version
 AND v.vwap_rule_version = ?
WHERE d.decision_type = 'TRIGGERED'
  AND d.created_at >= ?
ORDER BY d.created_at ASC
"""


# ORB breakouts (orb_detector). One row per stock per day at most.
ORB_TRIGGER_SQL = """
SELECT
    d.setup_id,
    d.orb_rule_version,
    d.trigger_exchange_ts,
    d.trigger_price,
    d.planned_stop,
    d.created_at,
    a.instrument_token,
    a.tradingsymbol,
    a.direction,
    a.level,
    a.tick_size,
    a.session_date,
    a.rvol,
    a.atr14,
    a.rank
FROM live_orb_decisions d
JOIN live_orb_arms a
  ON a.setup_id = d.setup_id
 AND a.orb_rule_version = d.orb_rule_version
WHERE d.decision_type = 'TRIGGERED'
  AND d.created_at >= ?
ORDER BY d.created_at ASC
"""


class VwapLookupError(Exception):
    """Hard SQLite failure reading live_vwap_qualifications."""


def _candidate_from_row(row, *, classification: Optional[str] = None) -> TriggerCandidate:
    """Build a candidate from a joined trigger row.

    Shared by both fetchers so a column added to one query cannot silently go
    unread by the other.
    """
    return TriggerCandidate(
        setup_id=str(row["setup_id"]),
        continuation_rule_version=str(row["continuation_rule_version"]),
        session_date=str(row["session_date"]),
        tradingsymbol=str(row["tradingsymbol"]),
        instrument_token=int(row["instrument_token"]),
        direction=str(row["direction"]),
        trigger_price=float(row["trigger_price"]),
        pullback_swing_high=(
            None if row["pullback_swing_high"] is None else float(row["pullback_swing_high"])
        ),
        pullback_swing_low=(
            None if row["pullback_swing_low"] is None else float(row["pullback_swing_low"])
        ),
        tick_size=float(row["tick_size"]),
        buffer_ticks=int(row["buffer_ticks"]),
        trigger_exchange_ts=(
            None if row["trigger_exchange_ts"] is None else str(row["trigger_exchange_ts"])
        ),
        created_at=str(row["created_at"]),
        last_price=(None if row["last_price"] is None else float(row["last_price"])),
        vwap_classification=(None if classification is None else str(classification)),
        breakout_candle_volume=(
            None if row["breakout_candle_volume"] is None else int(row["breakout_candle_volume"])
        ),
        avg_prior_3_1m_volume=(
            None if row["avg_prior_3_1m_volume"] is None else float(row["avg_prior_3_1m_volume"])
        ),
    )


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

    return [_candidate_from_row(row) for row in rows]


def fetch_vwap_classification(
    live_db: Path,
    *,
    session_date: str,
    setup_id: str,
    continuation_rule_version: str,
    vwap_rule_version: str = VWAP_RULE_VERSION,
) -> Optional[str]:
    """Return classification for the four-field identity, or None if absent.

    Raises VwapLookupError on missing table or SQLite read errors.
    """
    if not live_db.exists():
        raise VwapLookupError("live db missing")
    conn = sqlite3.connect(f"file:{live_db}?mode=ro", uri=True)
    try:
        row = conn.execute(
            VWAP_CLASS_SQL,
            (session_date, setup_id, continuation_rule_version, vwap_rule_version),
        ).fetchone()
    except sqlite3.Error as exc:
        raise VwapLookupError("vwap qualification lookup failed: %s" % exc) from exc
    finally:
        conn.close()
    if row is None:
        return None
    value = row[0]
    if value is None:
        return None
    return str(value)


def fetch_triggered_with_vwap_since(
    live_db: Path,
    *,
    created_at_gte: str,
    vwap_rule_version: str = VWAP_RULE_VERSION,
) -> List[TriggerCandidate]:
    """Like fetch_triggered_since, but each candidate already carries its
    VWAP verdict (or None, if the qualifier hasn't written one yet).

    No polling: a candidate with vwap_classification=None simply reappears,
    unchanged, on the next call with the same created_at_gte floor — the
    caller's own poll cadence does the waiting, so there's no separate
    retry/timeout state to maintain here.
    """
    if not live_db.exists():
        return []
    conn = sqlite3.connect(f"file:{live_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        try:
            rows = conn.execute(
                TRIGGER_WITH_VWAP_SQL, (vwap_rule_version, created_at_gte)
            ).fetchall()
        except sqlite3.OperationalError:
            # live_vwap_qualifications may not exist yet (its writer creates
            # it lazily in a separate process) — fall back to trigger-only
            # rows rather than dropping everything.
            try:
                rows = conn.execute(TRIGGER_SQL, (created_at_gte,)).fetchall()
            except sqlite3.OperationalError:
                return []
            rows = [dict(row, classification=None) for row in rows]
    finally:
        conn.close()

    return [
        _candidate_from_row(row, classification=row["classification"]) for row in rows
    ]


def _orb_candidate_from_row(row) -> TriggerCandidate:
    """An ORB breakout as a candidate.

    ``trigger_price`` is the price of the tick that broke the level (what the
    engine sizes from), and ``planned_stop`` the stop the detector fixed at
    that moment. There are no pullback swings: an ORB stop is never
    structural. A row missing its price or stop still becomes a candidate, so
    the engine records a visible skip instead of the row vanishing.
    """
    level = float(row["level"])
    trigger_price = level if row["trigger_price"] is None else float(row["trigger_price"])
    return TriggerCandidate(
        setup_id=str(row["setup_id"]),
        continuation_rule_version=str(row["orb_rule_version"]),
        session_date=str(row["session_date"]),
        tradingsymbol=str(row["tradingsymbol"]),
        instrument_token=int(row["instrument_token"]),
        direction=str(row["direction"]),
        trigger_price=trigger_price,
        pullback_swing_high=None,
        pullback_swing_low=None,
        tick_size=float(row["tick_size"]),
        buffer_ticks=0,
        trigger_exchange_ts=(
            None if row["trigger_exchange_ts"] is None else str(row["trigger_exchange_ts"])
        ),
        created_at=str(row["created_at"]),
        last_price=(None if row["trigger_price"] is None else float(row["trigger_price"])),
        strategy=STRATEGY_ORB,
        planned_stop=(None if row["planned_stop"] is None else float(row["planned_stop"])),
        orb_level=level,
        orb_rvol=float(row["rvol"]),
        orb_atr14=float(row["atr14"]),
        orb_rank=int(row["rank"]),
    )


def fetch_orb_triggered_since(
    live_db: Path,
    *,
    created_at_gte: str,
) -> List[TriggerCandidate]:
    """ORB breakouts recorded at or after ``created_at_gte``, oldest first.

    Empty when the detector has never run against this database (its tables
    do not exist yet).
    """
    if not live_db.exists():
        return []
    conn = sqlite3.connect(f"file:{live_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(ORB_TRIGGER_SQL, (created_at_gte,)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    return [_orb_candidate_from_row(row) for row in rows]


def fetch_arb_candidates_since(
    live_db: Path,
    *,
    created_at_gte: str,
    vwap_rule_version: str = VWAP_RULE_VERSION,
) -> List[TriggerCandidate]:
    """Everything an ARB engine may act on: funnel triggers and ORB breakouts.

    Only for an engine in ARB mode. A legacy engine keeps using
    fetch_triggered_with_vwap_since and never sees an ORB row. The funnel
    rows still carry their VWAP verdict, for the record only.
    """
    funnel = fetch_triggered_with_vwap_since(
        live_db, created_at_gte=created_at_gte, vwap_rule_version=vwap_rule_version
    )
    orb = fetch_orb_triggered_since(live_db, created_at_gte=created_at_gte)
    return sorted(funnel + orb, key=lambda candidate: candidate.created_at)
