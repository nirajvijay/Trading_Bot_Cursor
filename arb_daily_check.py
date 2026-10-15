"""Post-market check: did the live engine trade the strategy that was backtested?

There is no paper phase for ARB: it is tested with real Kite orders at micro
size. This is what makes that safe to learn from. After the close it takes
the day's own signals and 1-minute bars, runs them through the same rules
with ideal fills (arb_replay), and compares that, signal by signal, with
what the engine really did:

* **Decision:** entered or skipped. A signal the engine never saw (paused,
  stopped or halted at that moment) counts as not entered.
* **Size:** when both entered, the rupee risk within one share's worth.
* **Day halt:** the hard day stop or day lock, and when.
* **Result in R before charges:** live against the replay.

A mismatch is either a bug in the live engine or a real live-versus-backtest
difference (latency, fills, a tick-level P&L where the replay has 1-minute
closes). Either way it is found in days, at micro money.

Read-only against the observation DB and the engine's positions. The one
thing it writes is its own result, to ``arb_daily_checks`` in the engine DB,
for the Performance tab. It makes no Kite call and places no order.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import arb_metrics
import engine_clock
from arb_replay import FREE_BY_STOP, DayReplay, ReplayBar, ReplayCandidate, ReplayTrade, replay_day
from engine_arb import ARB_EXTRA_KEY, ArbSettings
from engine_arb_context import LiveArbContext, trigger_moment_ist
from engine_config import ARB_DEFAULT_TOTAL_CAPITAL_RUPEES
from engine_entry import compute_stop_price
from engine_types import TriggerCandidate
from trading_engine_broker import parse_timestamp_text
from trading_engine_handoff import fetch_arb_candidates_since
from trading_engine_types import STRATEGY_FUNNEL, STRATEGY_ORB

CHECK_VERSION = "arb_check_v1"

CREATE_CHECKS_SQL = """
CREATE TABLE IF NOT EXISTS arb_daily_checks (
    session_date  TEXT PRIMARY KEY,
    generated_at  TEXT NOT NULL,
    check_version TEXT NOT NULL,
    summary_json  TEXT NOT NULL,
    rows_json     TEXT NOT NULL
);
"""

LIVE_ENTER = "enter"
LIVE_SKIP = "skip"
LIVE_NOT_SEEN = "not_seen"

# Engine rows in these states never held shares.
_NEVER_HELD = ("rejected", "cancelled")
_ENGINE_START_EVENT = "broker_session_checked"


class DailyCheckError(Exception):
    """The check cannot be made (a database is missing or unreadable)."""


def _read_only(path: Path) -> sqlite3.Connection:
    if not Path(path).exists():
        raise DailyCheckError(f"database not found: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _json(raw: Any) -> dict:
    try:
        data = json.loads(str(raw or "{}"))
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _minute(moment: datetime) -> str:
    return engine_clock.to_ist(moment).strftime("%Y-%m-%dT%H:%M")


# -----------------------------------------------------------------------------
# What the engine did
# -----------------------------------------------------------------------------


def load_engine_day(engine_db: Path, session_date: str) -> Dict[str, Any]:
    """The engine's rows, starts and halt for one session."""
    conn = _read_only(engine_db)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "positions" not in tables:
            raise DailyCheckError("engine db has no positions table")
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM positions WHERE session_date = ? ORDER BY created_at ASC",
                (session_date,),
            )
        ]
        starts: List[str] = []
        if "position_events" in tables:
            starts = [
                str(r[0])
                for r in conn.execute(
                    "SELECT at FROM position_events WHERE trade_id = '__engine__' "
                    "AND event_type = ? AND at >= ? AND at < ? ORDER BY event_id ASC",
                    (_ENGINE_START_EVENT, session_date, session_date + "T99"),
                )
            ]
        halt = None
        if "arb_day_state" in tables:
            found = conn.execute(
                "SELECT * FROM arb_day_state WHERE session_date = ?", (session_date,)
            ).fetchone()
            halt = dict(found) if found else None
    except sqlite3.Error as exc:
        raise DailyCheckError(f"engine db read failed: {exc}") from exc
    finally:
        conn.close()
    return {"rows": rows, "starts": starts, "day_state": halt}


def _live_fact(row: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """One engine row, reduced to what is compared."""
    if row is None:
        return {"decision": LIVE_NOT_SEEN}
    extra = _json(row.get("extra_json"))
    arb = extra.get(ARB_EXTRA_KEY) if isinstance(extra.get(ARB_EXTRA_KEY), dict) else {}
    state = str(row.get("state"))
    if state in _NEVER_HELD:
        return {
            "decision": LIVE_SKIP,
            "reason": extra.get("skip_reason") or extra.get("reject_reason") or extra.get("cancel_reason"),
            "score": arb.get("score"),
        }
    candidate = _json(row.get("candidate_json"))
    initial = extra.get("initial_stop_price", row.get("stop_price"))
    closed = state == "closed"
    return {
        "decision": LIVE_ENTER,
        "state": state,
        "score": arb.get("score"),
        "risk_used": arb.get("risk_used", extra.get("risk_cap_rupees")),
        "half_size": arb.get("half_size"),
        "qty": int(row.get("qty") or 0),
        "entry_price": row.get("entry_price"),
        "initial_stop": initial,
        "entry_slippage_bps": arb_metrics.entry_slippage_bps(candidate, row.get("entry_price")),
        "close_reason": extra.get("close_reason"),
        "realised_pnl": row.get("realised_pnl") if closed else None,
        "r_gross": (
            arb_metrics.r_gross(row.get("entry_price"), initial, row.get("qty"), row.get("realised_pnl"))
            if closed
            else None
        ),
    }


# -----------------------------------------------------------------------------
# What the rules say
# -----------------------------------------------------------------------------


def load_bars(live_db: Path, session_date: str, symbols: Sequence[str]) -> Dict[str, List[ReplayBar]]:
    """Complete 1-minute bars per symbol (the same bars the engine's lock rule reads)."""
    bars: Dict[str, List[ReplayBar]] = {symbol: [] for symbol in symbols}
    if not symbols:
        return bars
    conn = _read_only(live_db)
    try:
        marks = ",".join("?" for _ in symbols)
        for symbol, minute, o, h, l, c in conn.execute(
            f"SELECT tradingsymbol, candle_time, open, high, low, close FROM live_1m_candles "
            f"WHERE session_date = ? AND is_partial = 0 AND tradingsymbol IN ({marks}) "
            f"ORDER BY candle_time ASC",
            (session_date, *symbols),
        ):
            bars[str(symbol)].append(ReplayBar(str(minute)[:16], float(o), float(h), float(l), float(c)))
    except sqlite3.Error as exc:
        raise DailyCheckError(f"observation db read failed: {exc}") from exc
    finally:
        conn.close()
    return bars


def build_candidates(
    candidates: Sequence[TriggerCandidate], context: LiveArbContext, settings: ArbSettings
) -> List[ReplayCandidate]:
    out: List[ReplayCandidate] = []
    for candidate in candidates:
        moment = trigger_moment_ist(candidate)
        if moment is None:
            continue
        stop = compute_stop_price(candidate)
        features = None
        if candidate.strategy == STRATEGY_FUNNEL:
            try:
                features = context.features_for(candidate, stop)
            except Exception:  # noqa: BLE001 - the replay records it as context unavailable
                features = None
        out.append(
            ReplayCandidate(
                setup_id=candidate.setup_id,
                strategy=candidate.strategy,
                symbol=candidate.tradingsymbol,
                direction=candidate.direction,
                minute=_minute(moment),
                moment=moment.isoformat(),
                trigger_price=float(candidate.trigger_price),
                stop=stop,
                tick_size=float(candidate.tick_size),
                features=features,
                orb_in_window=moment.time().replace(tzinfo=None) < settings.orb_cutoff,
            )
        )
    return out


def _replay_fact(trade: ReplayTrade) -> Dict[str, Any]:
    if not trade.entered:
        return {"decision": LIVE_SKIP, "reason": trade.reason, "score": trade.score}
    return {
        "decision": LIVE_ENTER,
        "score": trade.score,
        "risk_used": round(trade.risk_used, 2),
        "half_size": trade.half_size,
        "qty": trade.qty,
        "entry_price": trade.candidate.trigger_price,
        "initial_stop": trade.candidate.stop,
        "exit_reason": trade.exit_reason,
        "exit_minute": trade.exit_minute,
        "exit_price": trade.exit_price,
        "r_gross": None if trade.r_gross is None else round(trade.r_gross, 4),
    }


# -----------------------------------------------------------------------------
# The comparison
# -----------------------------------------------------------------------------


def _compare(live: Mapping[str, Any], replay: Mapping[str, Any], risk_per_share: Optional[float]) -> Dict[str, Any]:
    live_in = live["decision"] == LIVE_ENTER
    replay_in = replay["decision"] == LIVE_ENTER
    if live_in != replay_in:
        why = "live_entered_replay_skipped" if live_in else (
            "engine_did_not_see_it" if live["decision"] == LIVE_NOT_SEEN else "live_skipped_replay_entered"
        )
        return {"match": False, "why": why}
    if not live_in:
        return {"match": True, "why": None}
    try:
        gap = abs(float(live.get("risk_used")) - float(replay.get("risk_used")))
    except (TypeError, ValueError):
        return {"match": False, "why": "live_risk_unknown"}
    # Quantity is whole shares, so risk can differ by up to one share's worth.
    allowed = max(float(risk_per_share or 0.0), 1.0)
    if gap > allowed + 1e-6:
        return {"match": False, "why": "risk_differs", "risk_gap": round(gap, 2)}
    return {"match": True, "why": None}


def compare(
    *,
    session_date: str,
    candidates: Sequence[ReplayCandidate],
    replay: DayReplay,
    engine_day: Mapping[str, Any],
    risk_scale: float,
    first_start: Optional[str],
) -> Dict[str, Any]:
    by_setup = {str(row.get("setup_id")): row for row in engine_day["rows"]}
    trades = {t.candidate.setup_id: t for t in replay.trades}
    rows: List[Dict[str, Any]] = []
    for cand in candidates:
        trade = trades[cand.setup_id]
        live = _live_fact(by_setup.get(cand.setup_id))
        ideal = _replay_fact(trade)
        per_share = None if cand.stop is None else abs(cand.trigger_price - float(cand.stop))
        rows.append(
            {
                "setup_id": cand.setup_id,
                "symbol": cand.symbol,
                "strategy": cand.strategy,
                "direction": cand.direction,
                "time": cand.moment,
                "live": live,
                "replay": ideal,
                **_compare(live, ideal, per_share),
            }
        )
    matched = sum(1 for row in rows if row["match"])
    live_entered = [row for row in rows if row["live"]["decision"] == LIVE_ENTER]
    live_r = [row["live"]["r_gross"] for row in live_entered if row["live"].get("r_gross") is not None]
    replay_r_same = [
        row["replay"]["r_gross"]
        for row in live_entered
        if row["live"].get("r_gross") is not None and row["replay"].get("r_gross") is not None
    ]
    slips = [
        row["live"]["entry_slippage_bps"]
        for row in live_entered
        if row["live"].get("entry_slippage_bps") is not None
    ]
    state = engine_day.get("day_state") or {}
    halted_at = parse_timestamp_text(state.get("halted_at"))
    live_halt = None
    if state.get("halted_reason"):
        live_halt = {
            "reason": state.get("halted_reason"),
            "minute": None if halted_at is None or halted_at.tzinfo is None else _minute(halted_at),
            "mtm": state.get("halted_mtm"),
        }
    replay_halt = None
    if replay.halt_minute is not None:
        replay_halt = {"reason": replay.halt_reason, "minute": replay.halt_minute}
    summary = {
        "session_date": session_date,
        "risk_scale": risk_scale,
        "engine_first_start": first_start,
        "candidates": len(rows),
        "matched": matched,
        "match_rate": round(matched / len(rows), 4) if rows else None,
        "live_entered": len(live_entered),
        "replay_entered": sum(1 for row in rows if row["replay"]["decision"] == LIVE_ENTER),
        "live_r": round(sum(live_r), 4) if live_r else 0.0,
        "replay_r": replay.r_gross,
        # The replay's R on the trades the engine also took and closed.
        "replay_r_same_trades": round(sum(replay_r_same), 4) if replay_r_same else 0.0,
        "live_halt": live_halt,
        "replay_halt": replay_halt,
        "halt_match": (live_halt or {}).get("reason") == (replay_halt or {}).get("reason"),
        "entry_slippage_bps_median": round(statistics.median(slips), 2) if slips else None,
        "entry_slippage_bps_mean": round(statistics.fmean(slips), 2) if slips else None,
        "entry_slippage_trades": len(slips),
    }
    return {"summary": summary, "rows": rows}


def run_check(
    *,
    live_db: Path,
    engine_db: Path,
    session_date: str,
    capital_rupees: Optional[float] = None,
    leverage: float = 5.0,
) -> Dict[str, Any]:
    """Build the day's comparison. Raises DailyCheckError if it cannot be made.

    ``capital_rupees`` defaults to what the engine stamped on the day's rows.
    """
    datetime.strptime(session_date, "%Y-%m-%d")  # ValueError on a malformed date
    engine_day = load_engine_day(Path(engine_db), session_date)
    if capital_rupees is None:
        stamped = [
            float(arb["capital"])
            for row in engine_day["rows"]
            for arb in [_json(row.get("extra_json")).get(ARB_EXTRA_KEY)]
            if isinstance(arb, dict) and arb.get("capital")
        ]
        capital_rupees = stamped[0] if stamped else ARB_DEFAULT_TOTAL_CAPITAL_RUPEES
    scales = sorted(
        {
            float(arb["risk_scale"])
            for row in engine_day["rows"]
            for arb in [_json(row.get("extra_json")).get(ARB_EXTRA_KEY)]
            if isinstance(arb, dict) and arb.get("risk_scale") is not None
        }
    )
    if not scales:
        raise DailyCheckError(f"no ARB activity recorded for {session_date}")
    if len(scales) > 1:
        raise DailyCheckError(f"more than one ARB stage ran on {session_date}: {scales}")
    settings = ArbSettings(risk_scale=scales[0])
    starts = engine_day["starts"]
    first_start = starts[0] if starts else None
    if not Path(live_db).exists():
        raise DailyCheckError(f"database not found: {live_db}")
    try:
        # From the first engine start: earlier signals were never the engine's to take.
        signals = [
            c
            for c in fetch_arb_candidates_since(Path(live_db), created_at_gte=first_start or session_date)
            if c.session_date == session_date and c.strategy in (STRATEGY_FUNNEL, STRATEGY_ORB)
        ]
    except sqlite3.Error as exc:
        raise DailyCheckError(f"observation db read failed: {exc}") from exc
    candidates = build_candidates(signals, LiveArbContext(Path(live_db)), settings)
    bars = load_bars(Path(live_db), session_date, sorted({c.symbol for c in candidates}))
    replay = replay_day(
        candidates,
        bars,
        settings,
        capital_rupees=capital_rupees,
        leverage=leverage,
        slip_rate=0.0,
        free_rule=FREE_BY_STOP,
    )
    result = compare(
        session_date=session_date,
        candidates=candidates,
        replay=replay,
        engine_day=engine_day,
        risk_scale=scales[0],
        first_start=first_start,
    )
    result["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result["check_version"] = CHECK_VERSION
    return result


# -----------------------------------------------------------------------------
# Storage and report
# -----------------------------------------------------------------------------


def save_check(engine_db: Path, result: Mapping[str, Any]) -> None:
    conn = sqlite3.connect(str(engine_db), timeout=10.0)
    try:
        with conn:
            conn.execute(CREATE_CHECKS_SQL)
            conn.execute(
                "INSERT INTO arb_daily_checks (session_date, generated_at, check_version, "
                "summary_json, rows_json) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(session_date) DO UPDATE SET generated_at = excluded.generated_at, "
                "check_version = excluded.check_version, summary_json = excluded.summary_json, "
                "rows_json = excluded.rows_json",
                (
                    result["summary"]["session_date"],
                    result["generated_at"],
                    result["check_version"],
                    json.dumps(result["summary"], separators=(",", ":"), default=str),
                    json.dumps(result["rows"], separators=(",", ":"), default=str),
                ),
            )
    finally:
        conn.close()


def load_checks(engine_db: Path, *, with_rows_for: Optional[str] = None) -> List[Dict[str, Any]]:
    """Stored summaries, newest first; rows only for ``with_rows_for``."""
    if not Path(engine_db).exists():
        return []
    try:
        conn = _read_only(Path(engine_db))
    except DailyCheckError:
        return []
    try:
        found = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='arb_daily_checks'"
        ).fetchone()
        if not found:
            return []
        out = []
        for row in conn.execute("SELECT * FROM arb_daily_checks ORDER BY session_date DESC"):
            item = {
                "session_date": row["session_date"],
                "generated_at": row["generated_at"],
                "check_version": row["check_version"],
                "summary": _json(row["summary_json"]),
            }
            if with_rows_for == row["session_date"]:
                try:
                    item["rows"] = json.loads(row["rows_json"])
                except (ValueError, TypeError):
                    item["rows"] = []
            out.append(item)
        return out
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def format_report(result: Mapping[str, Any]) -> str:
    s = result["summary"]
    rate = "n/a" if s["match_rate"] is None else f"{s['match_rate'] * 100:.0f}%"
    lines = [
        f"ARB daily check, {s['session_date']} (stage {s['risk_scale']:g})",
        f"  Signals since the engine started: {s['candidates']}",
        f"  Same decision and size: {s['matched']} of {s['candidates']} ({rate})",
        f"  Entered: live {s['live_entered']}, replay {s['replay_entered']}",
        f"  Result in R before charges: live {s['live_r']:+.2f}, replay {s['replay_r']:+.2f} "
        f"(replay on the same trades {s['replay_r_same_trades']:+.2f})",
        f"  Day halt: live {_halt_text(s['live_halt'])}, replay {_halt_text(s['replay_halt'])}",
    ]
    if s["entry_slippage_bps_median"] is not None:
        lines.append(
            f"  Entry slippage: median {s['entry_slippage_bps_median']:+.1f} bps, "
            f"mean {s['entry_slippage_bps_mean']:+.1f} bps over {s['entry_slippage_trades']} trades"
        )
    wrong = [row for row in result["rows"] if not row["match"]]
    if wrong:
        lines.append("  Mismatches:")
        for row in wrong:
            live, ideal = row["live"], row["replay"]
            lines.append(
                f"    {row['time'][11:19]} {row['symbol']:<12} {row['strategy']:<6} {row['why']}: "
                f"live {live['decision']} ({live.get('reason') or live.get('risk_used')}), "
                f"replay {ideal['decision']} ({ideal.get('reason') or ideal.get('risk_used')})"
            )
    else:
        lines.append("  No mismatches.")
    return "\n".join(lines)


def _halt_text(halt: Optional[Mapping[str, Any]]) -> str:
    if not halt:
        return "none"
    minute = str(halt.get("minute") or "")[11:16] or "time unknown"
    return f"{halt.get('reason')} at {minute}"
