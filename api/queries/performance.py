"""The Performance tab: ARB's forward test, read from the engine DB.

Everything here is derived from closed ARB trades (rows the engine stamped
with its ARB decision), the day-state table and the stored post-market
checks. Read-only; nothing here is read by the engine.

**One stage at a time.** Rupee figures from different stages cannot be added,
so the summary covers one stage: the latest one traded unless another is
asked for.

**Charges are the engine's estimate** (engine_charges), the same figure the
hard day stop and day lock run on. The Charges tab shows Kite's own.

**Stages are judged in R before charges** (IMPLEMENTATION_IDEA section 6.3).
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from collections import OrderedDict, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import arb_metrics
from arb_daily_check import load_checks
from engine_charges import round_trip_charges
from engine_config import ARB_STAGES, ARB_UNLOCKED_SCALE

# The backtest of the rule that is live (Jan-Sep 2026, full size, after
# charges): ORB armed at 09:20, stops on the tick grid, lock thresholds exact.
# From tests/fixtures/lock_golden (export_lock_golden.py); see PR_C_PLAN.md.
BACKTEST_REFERENCE = {
    "period": "Jan-Sep 2026",
    "at_3bps": {"net_per_month": 39532.0, "worst_day": -5970.0, "green_days": 0.59,
                "max_drawdown": -26741.0, "trades_per_month": 82},
    "at_5bps": {"net_per_month": 33230.0, "worst_day": -6330.0, "green_days": 0.59,
                "max_drawdown": -31302.0, "trades_per_month": 82},
}

# Promotion gates (IMPLEMENTATION_IDEA section 6.4), by the stage being left.
GATE_MIN_DAYS = {0.05: 15, 0.25: 20, 0.5: 20}
GATE_MIN_TRADES = {0.05: 40, 0.25: 50, 0.5: 50}
GATE_PARITY_MIN = 0.95
GATE_SLIPPAGE_MEDIAN_BPS = 3.0
GATE_SLIPPAGE_MEAN_BPS = 5.0
GATE_R_SHARE_OF_REPLAY = 0.70
# Entry slippage watch: the mean of the last 20 trades.
ROLLING_TRADES = 20
ROLLING_ALERT_BPS = 4.0
ROLLING_DEMOTE_BPS = 6.0

# Closes the engine made to protect itself. Each is a safety incident to
# review; the gate needs none. Other incidents (an orphan order, a duplicate
# entry, a restart that lost day state) cannot be detected from these rows.
INCIDENT_CLOSE_REASONS = (
    "unprotected_timeout",
    "stop_replacement_cap",
    "direction_mismatch_flatten",
    "stop_triggered_unfilled",
    "abnormal_slippage_flatten",
)
HALT_REASONS = ("hard_day_stop", "day_lock")
STOP_CLOSE_REASON = "stop_hit"


def _json(raw: Any) -> dict:
    try:
        data = json.loads(str(raw or "{}"))
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _same_scale(a: Any, b: float) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return False


def _stage_key(scale: float) -> str:
    for key, value in ARB_STAGES:
        if _same_scale(value, scale):
            return key
    return f"{scale:g}"


def _next_stage(scale: float) -> Optional[float]:
    larger = [value for _, value in ARB_STAGES if value > scale + 1e-9]
    return min(larger) if larger else None


def _load(engine_db: Path) -> Dict[str, Any]:
    out: Dict[str, Any] = {"rows": [], "day_state": {}}
    if not Path(engine_db).exists():
        return out
    conn = sqlite3.connect(f"file:{engine_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "positions" in tables:
            out["rows"] = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM positions WHERE extra_json LIKE '%\"arb\"%' "
                    "ORDER BY session_date ASC, created_at ASC"
                )
            ]
        if "arb_day_state" in tables:
            out["day_state"] = {
                str(row["session_date"]): dict(row)
                for row in conn.execute("SELECT * FROM arb_day_state")
            }
    finally:
        conn.close()
    return out


def _trade(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One closed ARB trade with its measurements, or None if it is not one."""
    extra = _json(row.get("extra_json"))
    arb = extra.get("arb")
    if not isinstance(arb, dict) or str(row.get("state")) != "closed":
        return None
    qty = int(row.get("qty") or 0)
    entry = row.get("entry_price")
    if qty <= 0 or entry is None:
        return None
    candidate = _json(row.get("candidate_json"))
    direction = str(row.get("direction"))
    gross = row.get("realised_pnl")
    initial = extra.get("initial_stop_price", row.get("stop_price"))
    exit_at = arb_metrics.exit_price(direction, entry, qty, gross)
    charges = (
        round_trip_charges(direction=direction, qty=qty, entry=float(entry), exit_price=exit_at)
        if exit_at is not None
        else None
    )
    reason = extra.get("close_reason")
    return {
        "trade_id": str(row.get("trade_id")),
        "session_date": str(row.get("session_date")),
        "symbol": str(row.get("tradingsymbol")),
        "direction": direction,
        "risk_scale": arb.get("risk_scale"),
        "source": arb_metrics.source_label(arb),
        "gross": None if gross is None else float(gross),
        "charges": charges,
        "net": None if gross is None or charges is None else float(gross) - charges,
        "r_gross": arb_metrics.r_gross(entry, initial, qty, gross),
        "close_reason": reason,
        "entry_slippage_bps": arb_metrics.entry_slippage_bps(candidate, entry),
        "stop_exit_slippage_bps": (
            arb_metrics.stop_exit_slippage_bps(direction, row.get("stop_price"), entry, qty, gross)
            if reason == STOP_CLOSE_REASON
            else None
        ),
    }


def _slippage(values: List[float]) -> Dict[str, Any]:
    if not values:
        return {"trades": 0, "median": None, "mean": None}
    return {
        "trades": len(values),
        "median": round(statistics.median(values), 2),
        "mean": round(statistics.fmean(values), 2),
    }


def _gate(key: str, label: str, ok: Optional[bool], detail: str) -> Dict[str, Any]:
    return {"key": key, "label": label, "ok": ok, "detail": detail}


def fetch_performance(
    engine_db: Path, *, risk_scale: Optional[float] = None, parity_date: Optional[str] = None
) -> Dict[str, Any]:
    data = _load(Path(engine_db))
    everything = [t for t in (_trade(row) for row in data["rows"]) if t is not None]
    # The stage is read from every row ARB stamped, open and skipped ones
    # included: a session that has not closed a trade yet still has a stage.
    stamped = []
    for row in data["rows"]:
        arb = _json(row.get("extra_json")).get("arb")
        try:
            stamped.append(float(arb["risk_scale"]))
        except (TypeError, ValueError, KeyError):
            continue
    scales = sorted(set(stamped))
    scale = float(risk_scale) if risk_scale is not None else (
        stamped[-1] if stamped else ARB_UNLOCKED_SCALE
    )
    trades = [t for t in everything if _same_scale(t["risk_scale"], scale)]

    # --- days, months ---------------------------------------------------------
    by_day: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for t in trades:
        by_day.setdefault(t["session_date"], []).append(t)
    days: List[Dict[str, Any]] = []
    cumulative = peak = drawdown = 0.0
    for day, items in by_day.items():
        net = sum(t["net"] or 0.0 for t in items)
        cumulative += net
        peak = max(peak, cumulative)
        drawdown = min(drawdown, cumulative - peak)
        state = data["day_state"].get(day) or {}
        days.append(
            {
                "session_date": day,
                "trades": len(items),
                "gross": round(sum(t["gross"] or 0.0 for t in items), 2),
                "charges": round(sum(t["charges"] or 0.0 for t in items), 2),
                "net": round(net, 2),
                "r_gross": round(sum(t["r_gross"] or 0.0 for t in items), 4),
                "cumulative_net": round(cumulative, 2),
                "halted_reason": state.get("halted_reason"),
            }
        )
    months: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    for day in days:
        month = months.setdefault(
            day["session_date"][:7],
            {"month": day["session_date"][:7], "net": 0.0, "r_gross": 0.0, "trades": 0,
             "days": 0, "green_days": 0},
        )
        month["net"] = round(month["net"] + day["net"], 2)
        month["r_gross"] = round(month["r_gross"] + day["r_gross"], 4)
        month["trades"] += day["trades"]
        month["days"] += 1
        month["green_days"] += day["net"] > 0
    day_nets = [d["net"] for d in days]
    wins = sum(1 for t in trades if (t["net"] or 0.0) > 0)
    totals = {
        "trades": len(trades),
        "wins": wins,
        "win_rate": round(wins / len(trades), 4) if trades else None,
        "gross": round(sum(t["gross"] or 0.0 for t in trades), 2),
        "charges": round(sum(t["charges"] or 0.0 for t in trades), 2),
        "net": round(sum(t["net"] or 0.0 for t in trades), 2),
        "r_gross": round(sum(t["r_gross"] or 0.0 for t in trades), 4),
        "days": len(days),
        "green_days": sum(1 for v in day_nets if v > 0),
        "green_day_rate": round(sum(1 for v in day_nets if v > 0) / len(days), 4) if days else None,
        "worst_day": min(day_nets) if day_nets else None,
        "best_day": max(day_nets) if day_nets else None,
        "max_drawdown": round(drawdown, 2),
    }

    # --- by source --------------------------------------------------------------
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in trades:
        grouped[t["source"] or "?"].append(t)
    sources = []
    for name in list(arb_metrics.SOURCES) + sorted(set(grouped) - set(arb_metrics.SOURCES)):
        items = grouped.get(name, [])
        r_values = [t["r_gross"] for t in items if t["r_gross"] is not None]
        sources.append(
            {
                "source": name,
                "trades": len(items),
                "wins": sum(1 for t in items if (t["net"] or 0.0) > 0),
                "net": round(sum(t["net"] or 0.0 for t in items), 2),
                "r_gross": round(sum(r_values), 4),
                "avg_r": round(statistics.fmean(r_values), 4) if r_values else None,
            }
        )

    # --- slippage ---------------------------------------------------------------
    entry_slips = [t["entry_slippage_bps"] for t in trades if t["entry_slippage_bps"] is not None]
    stop_slips = [t["stop_exit_slippage_bps"] for t in trades if t["stop_exit_slippage_bps"] is not None]
    recent = entry_slips[-ROLLING_TRADES:]
    rolling = round(statistics.fmean(recent), 2) if len(recent) == ROLLING_TRADES else None
    slippage = {
        "entry": _slippage(entry_slips),
        "stop_exit": _slippage(stop_slips),
        "rolling_trades": ROLLING_TRADES,
        "rolling_entry_mean": rolling,
        "rolling_alert": rolling is not None and rolling > ROLLING_ALERT_BPS,
        "rolling_demote": rolling is not None and rolling > ROLLING_DEMOTE_BPS,
        "alert_bps": ROLLING_ALERT_BPS,
        "demote_bps": ROLLING_DEMOTE_BPS,
    }

    # --- day controls -----------------------------------------------------------
    stage_days = set(by_day)
    day_controls = [
        {
            "session_date": day,
            "reason": state.get("halted_reason"),
            "at": state.get("halted_at"),
            "mtm": state.get("halted_mtm"),
            "peak": state.get("peak_mtm"),
            "day_net": next((d["net"] for d in days if d["session_date"] == day), None),
        }
        for day, state in sorted(data["day_state"].items())
        if state.get("halted_reason") in HALT_REASONS and day in stage_days
    ]

    # --- parity -----------------------------------------------------------------
    checks = [
        c for c in load_checks(Path(engine_db), with_rows_for=parity_date)
        if _same_scale((c.get("summary") or {}).get("risk_scale"), scale)
    ]
    parity_days = []
    candidates = matched = 0
    live_r = replay_r = 0.0
    for check in checks:
        s = check["summary"]
        candidates += int(s.get("candidates") or 0)
        matched += int(s.get("matched") or 0)
        live_r += float(s.get("live_r") or 0.0)
        replay_r += float(s.get("replay_r") or 0.0)
        parity_days.append(
            {
                "session_date": check["session_date"],
                "generated_at": check["generated_at"],
                "candidates": s.get("candidates"),
                "matched": s.get("matched"),
                "match_rate": s.get("match_rate"),
                "live_entered": s.get("live_entered"),
                "replay_entered": s.get("replay_entered"),
                "live_r": s.get("live_r"),
                "replay_r": s.get("replay_r"),
                "halt_match": s.get("halt_match"),
                "live_halt": s.get("live_halt"),
                "replay_halt": s.get("replay_halt"),
            }
        )
    detail = next((c for c in checks if "rows" in c), None)
    unchecked = sorted(stage_days - {c["session_date"] for c in checks})
    parity = {
        "days": parity_days,
        "candidates": candidates,
        "matched": matched,
        "match_rate": round(matched / candidates, 4) if candidates else None,
        "live_r": round(live_r, 4),
        "replay_r": round(replay_r, 4),
        "unchecked_days": unchecked,
        "detail_date": None if detail is None else detail["session_date"],
        "detail_rows": [] if detail is None else [r for r in detail["rows"] if not r.get("match")],
    }

    # --- stage and gates ----------------------------------------------------------
    incidents = [t for t in trades if t["close_reason"] in INCIDENT_CLOSE_REASONS]
    wrong_halts = [d for d in parity_days if d["halt_match"] is False]
    next_scale = _next_stage(scale)
    gates: List[Dict[str, Any]] = []
    if next_scale is not None:
        need_days = GATE_MIN_DAYS.get(scale, 20)
        need_trades = GATE_MIN_TRADES.get(scale, 50)
        gates.append(
            _gate(
                "time_in_stage",
                f"At least {need_days} trading days and {need_trades} filled trades",
                len(days) >= need_days and len(trades) >= need_trades,
                f"{len(days)} days, {len(trades)} trades",
            )
        )
        n_incidents = len(incidents) + len(wrong_halts)
        gates.append(
            _gate(
                "safety_incidents",
                "No safety incidents",
                n_incidents == 0,
                (
                    f"Safety exits: {len(incidents)}. Days whose halt differs from the replay: "
                    f"{len(wrong_halts)}. Orphan orders, duplicate entries and restarts are not "
                    "detected here; check those by hand."
                ),
            )
        )
        rate = parity["match_rate"]
        gates.append(
            _gate(
                "parity",
                "Live decisions match the replay for at least 95% of signals",
                None if rate is None else rate >= GATE_PARITY_MIN and not unchecked,
                (
                    "no daily check stored yet"
                    if rate is None
                    else f"{matched} of {candidates} ({rate * 100:.0f}%)"
                    + (
                        f"; {len(unchecked)} traded day{'' if len(unchecked) == 1 else 's'} not checked"
                        if unchecked
                        else ""
                    )
                ),
            )
        )
        entry = slippage["entry"]
        gates.append(
            _gate(
                "entry_slippage",
                "Entry slippage: median at most 3 bps, mean at most 5 bps",
                None
                if not entry["trades"]
                else entry["median"] <= GATE_SLIPPAGE_MEDIAN_BPS and entry["mean"] <= GATE_SLIPPAGE_MEAN_BPS,
                "no trades yet"
                if not entry["trades"]
                else f"median {entry['median']:+.1f} bps, mean {entry['mean']:+.1f} bps",
            )
        )
        share = (live_r / replay_r) if replay_r > 0 else None
        gates.append(
            _gate(
                "r_vs_replay",
                "Result in R before charges at least 70% of the replay's",
                None if share is None else share >= GATE_R_SHARE_OF_REPLAY,
                (
                    f"live {live_r:+.2f}R, replay {replay_r:+.2f}R"
                    + ("" if share is None else f" ({share * 100:.0f}%)")
                    if checks
                    else "no daily check stored yet"
                ),
            )
        )
    stage = {
        "risk_scale": scale,
        "key": _stage_key(scale),
        "unlocked_scale": ARB_UNLOCKED_SCALE,
        "next_scale": next_scale,
        "next_key": None if next_scale is None else _stage_key(next_scale),
        "days": len(days),
        "trades": len(trades),
        "first_day": days[0]["session_date"] if days else None,
        "last_day": days[-1]["session_date"] if days else None,
        "gates": gates,
        "all_gates_met": bool(gates) and all(g["ok"] is True for g in gates),
        "incidents": [
            {"session_date": t["session_date"], "symbol": t["symbol"], "close_reason": t["close_reason"]}
            for t in incidents
        ],
    }
    return {
        "risk_scale": scale,
        "available_scales": scales,
        "stage": stage,
        "totals": totals,
        "days": days,
        "months": list(months.values()),
        "sources": sources,
        "slippage": slippage,
        "day_controls": day_controls,
        "parity": parity,
        "reference": {**BACKTEST_REFERENCE, "scaled_to": scale},
    }
