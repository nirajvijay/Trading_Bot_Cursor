"""Read-only queries for the Strategy tab.

Two views of the observation database, neither of which the engine reads:

* the ORB day: baselines, the 09:20 ranking, the armed stocks and what became
  of each (tables written by orb_writer);
* the funnel feed: every continuation trigger of the day run through ARB's
  filters and score, so they can be watched before ARB trades anything.

The funnel feed is computed here for display. When ARB is live, the numbers
the engine acted on are the ones stamped on its own position rows.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from engine_arb import ArbSettings, classify_funnel
from engine_arb_context import ArbContextError, LiveArbContext, trigger_moment_ist
from engine_entry import compute_stop_price
from orb_config import DEFAULT_ORB_CONFIG, OrbConfig
from orb_rules import planned_stop
from orb_types import ARM_ARMED
from trading_engine_handoff import fetch_triggered_with_vwap_since

# How many not-yet-scored triggers one request may work through. Scoring reads
# the day's candles, so a first open late in the session is spread over a few
# polls instead of holding one request.
FUNNEL_MAX_NEW_PER_REQUEST = 15
_FUNNEL_CACHE_LIMIT = 2000
_funnel_cache: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}

NEAR_MISS_MIN_RVOL = 2.0
NEAR_MISS_LIMIT = 10


def _connect(db_path: Path) -> Optional[sqlite3.Connection]:
    if not db_path.exists():
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn: sqlite3.Connection, sql: str, params: tuple) -> List[sqlite3.Row]:
    """A table that does not exist yet reads as empty, not as an error."""
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


def _rules(config: OrbConfig) -> Dict[str, Any]:
    return {
        "rule_version": config.rule_version,
        "range": f"{config.range_start.strftime('%H:%M')}-{config.range_end.strftime('%H:%M')}",
        "min_rvol": config.min_rvol,
        "top_n": config.top_n,
        "stop_atr_mult": config.stop_atr_mult,
        "trigger_end": config.trigger_end.strftime("%H:%M"),
        "max_overshoot_atr": config.max_overshoot_atr,
    }


def fetch_orb_day(
    live_db: Path, session_date: str, config: OrbConfig = DEFAULT_ORB_CONFIG
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "session_date": session_date,
        "rules": _rules(config),
        "status": None,
        "baselines": {"ready": 0, "total": 0, "as_of": None, "not_ready": []},
        "ranked": False,
        "ranked_at": None,
        "arms": [],
        "in_play_not_armed": [],
        "near_misses": [],
        "valid_count": 0,
        "unusable": {},
    }
    conn = _connect(live_db)
    if conn is None:
        return out
    key = (session_date, config.rule_version)
    try:
        status = _rows(
            conn,
            "SELECT state, reason, detail_json, updated_at FROM live_orb_status "
            "WHERE session_date = ? AND orb_rule_version = ?",
            key,
        )
        if status:
            row = status[0]
            try:
                detail = json.loads(row["detail_json"] or "{}")
            except ValueError:
                detail = {}
            out["status"] = {
                "state": str(row["state"]),
                "reason": row["reason"],
                "updated_at": str(row["updated_at"]),
                "restored": bool(detail.get("restored")),
            }

        baselines = _rows(
            conn,
            "SELECT tradingsymbol, ready, reason, detail, history_as_of FROM live_orb_baselines "
            "WHERE session_date = ? AND orb_rule_version = ? ORDER BY tradingsymbol",
            key,
        )
        ready = [b for b in baselines if b["ready"]]
        as_of = sorted(str(b["history_as_of"]) for b in ready if b["history_as_of"])
        out["baselines"] = {
            "ready": len(ready),
            "total": len(baselines),
            "as_of": as_of[-1] if as_of else None,
            "not_ready": [
                {"symbol": str(b["tradingsymbol"]), "reason": b["reason"], "detail": b["detail"]}
                for b in baselines
                if not b["ready"]
            ],
        }

        ranges = _rows(
            conn,
            "SELECT * FROM live_orb_ranges WHERE session_date = ? AND orb_rule_version = ?",
            key,
        )
        out["ranked"] = bool(ranges)
        if ranges:
            out["ranked_at"] = str(ranges[0]["ranked_at"])
        out["valid_count"] = sum(1 for r in ranges if r["valid"])
        unusable: Dict[str, int] = {}
        for r in ranges:
            if not r["valid"]:
                reason = str(r["reason"] or "unknown")
                unusable[reason] = unusable.get(reason, 0) + 1
        out["unusable"] = unusable
        out["in_play_not_armed"] = [
            {
                "symbol": str(r["tradingsymbol"]),
                "direction": r["direction"],
                "rank": r["rank"],
                "rvol": r["rvol"],
                "level": r["level"],
                "reason": r["reason"],
            }
            for r in sorted(
                (r for r in ranges if r["rank"] is not None and not r["selected"]),
                key=lambda r: r["rank"],
            )
        ]
        near = sorted(
            (
                r
                for r in ranges
                if r["valid"] and r["rank"] is None and (r["rvol"] or 0.0) >= NEAR_MISS_MIN_RVOL
            ),
            key=lambda r: -float(r["rvol"]),
        )[:NEAR_MISS_LIMIT]
        out["near_misses"] = [
            {"symbol": str(r["tradingsymbol"]), "direction": r["direction"], "rvol": r["rvol"]}
            for r in near
        ]

        arms = _rows(
            conn,
            """
            SELECT a.*, d.decision_type, d.reason AS decision_reason, d.trigger_exchange_ts,
                   d.trigger_price, d.planned_stop, d.overshoot, d.overshoot_atr
            FROM live_orb_arms a
            LEFT JOIN live_orb_decisions d
              ON d.setup_id = a.setup_id AND d.orb_rule_version = a.orb_rule_version
            WHERE a.session_date = ? AND a.orb_rule_version = ?
            ORDER BY a.rank ASC
            """,
            key,
        )
        out["arms"] = [_arm_view(a) for a in arms]
    finally:
        conn.close()
    return out


def _arm_view(row: sqlite3.Row) -> Dict[str, Any]:
    level = float(row["level"])
    distance = float(row["stop_distance"])
    overshoot = row["overshoot"]
    return {
        "setup_id": str(row["setup_id"]),
        "symbol": str(row["tradingsymbol"]),
        "direction": str(row["direction"]),
        "rank": int(row["rank"]),
        "rvol": float(row["rvol"]),
        "level": level,
        "atr14": float(row["atr14"]),
        "stop_distance": distance,
        # Where the stop would sit for a breakout exactly at the level. The
        # real stop is fixed from the trigger price when the breakout fires.
        "stop_at_level": planned_stop(
            direction=str(row["direction"]),
            reference_price=level,
            distance=distance,
            tick_size=float(row["tick_size"]),
        ),
        "or_open": float(row["or_open"]),
        "or_high": float(row["or_high"]),
        "or_low": float(row["or_low"]),
        "or_close": float(row["or_close"]),
        "or_volume": int(row["or_volume"]),
        "avg_or_volume": float(row["avg_or_volume"]),
        "state": str(row["decision_type"] or ARM_ARMED),
        "reason": row["decision_reason"],
        "decided_at": row["trigger_exchange_ts"],
        "trigger_price": row["trigger_price"],
        "planned_stop": row["planned_stop"],
        "overshoot": overshoot,
        "overshoot_bps": None if overshoot is None or level <= 0 else float(overshoot) / level * 1e4,
    }


def fetch_funnel_feed(
    live_db: Path, session_date: str, settings: Optional[ArbSettings] = None
) -> Dict[str, Any]:
    """Each continuation trigger of the day against ARB's filters and score."""
    settings = settings or ArbSettings()
    out: Dict[str, Any] = {"session_date": session_date, "triggers": [], "pending": 0}
    try:
        floor = (date.fromisoformat(session_date) - timedelta(days=1)).isoformat()
    except ValueError:
        return out
    try:
        candidates = [
            c
            for c in fetch_triggered_with_vwap_since(live_db, created_at_gte=floor)
            if c.session_date == session_date
        ]
    except sqlite3.Error:
        return out
    if len(_funnel_cache) > _FUNNEL_CACHE_LIMIT:
        _funnel_cache.clear()

    context = LiveArbContext(live_db)
    rows: List[Dict[str, Any]] = []
    fresh = 0
    for candidate in candidates:
        cache_key = (
            str(live_db),
            session_date,
            candidate.setup_id,
            candidate.continuation_rule_version,
        )
        row = _funnel_cache.get(cache_key)
        if row is None:
            if fresh >= FUNNEL_MAX_NEW_PER_REQUEST:
                out["pending"] += 1
                continue
            fresh += 1
            row = _funnel_row(candidate, context, settings)
            # A row that could not be scored is tried again on the next poll.
            if row["reason"] != "context_unavailable":
                _funnel_cache[cache_key] = row
        rows.append(row)
    rows.sort(key=lambda r: (r["trigger_time"] or "", r["symbol"]))
    out["triggers"] = rows
    return out


def _funnel_row(candidate, context: LiveArbContext, settings: ArbSettings) -> Dict[str, Any]:
    moment = trigger_moment_ist(candidate)
    stop = compute_stop_price(candidate)
    row: Dict[str, Any] = {
        "setup_id": candidate.setup_id,
        "symbol": candidate.tradingsymbol,
        "direction": candidate.direction,
        "trigger_time": None if moment is None else moment.isoformat(timespec="seconds"),
        "trigger_price": float(candidate.trigger_price),
        "stop": stop,
        "vwap_classification": candidate.vwap_classification,
        "volume_ratio": None,
        "stop_pct": None,
        "is_first_trigger_today": None,
        "align_market_pct": None,
        "align_sector_pct": None,
        "passes": False,
        "reason": None,
        "score": None,
        "score_parts": {},
        "base_risk": None,
    }
    try:
        features = context.features_for(candidate, stop)
    except (ArbContextError, sqlite3.Error):
        row["reason"] = "context_unavailable"
        return row
    # The two-a-day limit on score-0 trades depends on what the engine has
    # already taken, so it is not applied here.
    verdict = classify_funnel(features, settings, score0_taken_today=0)
    row.update(
        volume_ratio=features.volume_ratio,
        stop_pct=features.stop_pct,
        is_first_trigger_today=features.is_first_trigger_today,
        align_market_pct=features.align_market_pct,
        align_sector_pct=features.align_sector_pct,
        passes=bool(verdict.ok),
        reason=verdict.reason,
        score=verdict.score,
        score_parts=dict(verdict.score_parts),
        base_risk=verdict.base_risk_rupees if verdict.ok else None,
    )
    return row


_NEVER_HELD_STATES = ("rejected", "cancelled")


def fetch_engine_decisions(engine_db: Path, session_date: str) -> Dict[str, Dict[str, Any]]:
    """What the engine did with each signal of the day, keyed by setup id.

    Read-only against the engine DB. Empty when the engine has never run, or
    the DB cannot be read: the Strategy tab then simply shows no decision.
    """
    import json

    import arb_metrics

    if not Path(engine_db).exists():
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    try:
        conn = sqlite3.connect(f"file:{engine_db}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT setup_id, state, qty, entry_price, extra_json FROM positions "
                "WHERE session_date = ?",
                (session_date,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return {}
    for setup_id, state, qty, entry_price, extra_json in rows:
        try:
            extra = json.loads(str(extra_json or "{}"))
        except (ValueError, TypeError):
            extra = {}
        if not isinstance(extra, dict):
            extra = {}
        arb = extra.get("arb") if isinstance(extra.get("arb"), dict) else {}
        held = str(state) not in _NEVER_HELD_STATES
        out[str(setup_id)] = {
            "decision": "entered" if held else "skipped",
            "state": str(state),
            "reason": None if held else (
                extra.get("skip_reason") or extra.get("reject_reason") or extra.get("cancel_reason")
            ),
            "source": arb_metrics.source_label(arb),
            "base_risk": arb.get("base_risk"),
            "risk_used": arb.get("risk_used") if held else None,
            "half_size": arb.get("half_size"),
            "qty": int(qty or 0) if held else None,
            "entry_price": entry_price if held else None,
            "close_reason": extra.get("close_reason"),
        }
    return out


_BREADTH_SQL = """
SELECT tradingsymbol, candle_time, open, close
FROM live_1m_candles
WHERE session_date = ?
ORDER BY candle_time ASC
"""


def fetch_breadth(live_db: Path, session_date: str) -> Dict[str, Any]:
    """How far the watchlist has moved from its open, overall and by sector.

    The same arithmetic the funnel score uses (engine_arb_context.alignment):
    each stock's latest 1-minute close against its first open of the day,
    averaged. A funnel trade scores a point when the market has moved more
    than 0.2% against it, so the response says which side that is right now.
    """
    from engine_arb import ArbSettings
    from engine_arb_context import sector_lookup

    threshold = abs(ArbSettings().score_market_against_pct)
    empty: Dict[str, Any] = {
        "session_date": session_date, "as_of": None, "stocks": 0, "market_pct": None,
        "threshold_pct": threshold, "scores_for": None, "sectors": [],
    }
    conn = _connect(live_db)
    if conn is None:
        return empty
    first_open: Dict[str, float] = {}
    last_close: Dict[str, float] = {}
    as_of = None
    try:
        for symbol, minute, open_, close in _rows(conn, _BREADTH_SQL, (session_date,)):
            first_open.setdefault(str(symbol), float(open_))
            last_close[str(symbol)] = float(close)
            as_of = str(minute)
    finally:
        conn.close()
    moves = {
        symbol: (close / first_open[symbol] - 1.0) * 100.0
        for symbol, close in last_close.items()
        if first_open.get(symbol, 0) > 0
    }
    if not moves:
        return empty
    market = sum(moves.values()) / len(moves)
    sector_of = sector_lookup()
    by_sector: Dict[str, List[float]] = {}
    for symbol, move in moves.items():
        by_sector.setdefault(sector_of.get(symbol, "Other"), []).append(move)
    return {
        "session_date": session_date,
        "as_of": as_of,
        "stocks": len(moves),
        "market_pct": round(market, 4),
        "threshold_pct": threshold,
        # Longs score when the market is down more than the threshold; shorts when up.
        "scores_for": "UP" if market < -threshold else ("DOWN" if market > threshold else None),
        "sectors": sorted(
            (
                {"sector": name, "pct": round(sum(values) / len(values), 4), "stocks": len(values)}
                for name, values in by_sector.items()
            ),
            key=lambda item: item["pct"],
            reverse=True,
        ),
    }
