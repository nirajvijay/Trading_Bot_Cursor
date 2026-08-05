"""Compose the 100-row radar table from read-only SQLite queries."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from config.nifty100_symbols import NIFTY_100_SYMBOLS

from api.db import open_readonly
from api.lib.phase_mapper import format_last_event, map_to_ui_phase
from api.schemas.radar import RadarRow


@dataclass
class _SymbolContext:
    symbol: str
    instrument_token: Optional[int] = None
    session_open: Optional[float] = None
    last_1m_close: Optional[float] = None
    last_1m_volume: Optional[int] = None
    last_1m_time: Optional[str] = None
    has_spike: bool = False
    spike_direction: Optional[str] = None
    setup_id: Optional[str] = None
    setup_state: Optional[str] = None
    last_event_type: Optional[str] = None
    last_event_at: Optional[str] = None
    continuation_decision: Optional[str] = None
    continuation_reason: Optional[str] = None
    trigger_price: Optional[float] = None
    has_arm: bool = False
    setup_count: int = 0


def _load_tokens(instruments_db: Path) -> Dict[str, int]:
    token_by_symbol: Dict[str, int] = {}
    try:
        conn = open_readonly(instruments_db)
    except FileNotFoundError:
        return token_by_symbol
    try:
        rows = conn.execute(
            """
            SELECT tradingsymbol, instrument_token
            FROM nifty50_instruments
            """
        ).fetchall()
        for row in rows:
            token_by_symbol[str(row["tradingsymbol"])] = int(row["instrument_token"])
    finally:
        conn.close()
    return token_by_symbol


def _count(conn: sqlite3.Connection, sql: str, params: tuple) -> int:
    try:
        row = conn.execute(sql, params).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except sqlite3.OperationalError:
        return 0


def fetch_coverage(
    live_db: Path,
    baselines_db: Path,
    session_date: str,
    *,
    subscribed: int = 100,
) -> dict:
    baseline_as_of: Optional[str] = None
    try:
        bconn = open_readonly(baselines_db)
        try:
            row = bconn.execute(
                """
                SELECT MAX(baseline_as_of_date)
                FROM baselines
                WHERE baseline_as_of_date < ?
                """,
                (session_date,),
            ).fetchone()
            if row and row[0]:
                baseline_as_of = str(row[0])
        finally:
            bconn.close()
    except FileNotFoundError:
        pass

    tokens_1m = 0
    tokens_5m = 0
    spikes = 0
    setups = 0
    arms = 0
    decisions = 0
    continuation_successful = 0
    continuation_failed = 0
    try:
        conn = open_readonly(live_db)
        try:
            tokens_1m = _count(
                conn,
                """
                SELECT COUNT(DISTINCT instrument_token)
                FROM live_1m_candles
                WHERE session_date = ?
                """,
                (session_date,),
            )
            tokens_5m = _count(
                conn,
                """
                SELECT COUNT(DISTINCT instrument_token)
                FROM live_5m_candles
                WHERE session_date = ?
                """,
                (session_date,),
            )
            spikes = _count(
                conn,
                "SELECT COUNT(*) FROM live_intraday_spikes WHERE session_date = ?",
                (session_date,),
            )
            setups = _count(
                conn,
                "SELECT COUNT(*) FROM live_pullback_setups WHERE session_date = ?",
                (session_date,),
            )
            arms = _count(
                conn,
                "SELECT COUNT(*) FROM live_continuation_arms WHERE session_date = ?",
                (session_date,),
            )
            decisions = _count(
                conn,
                """
                SELECT COUNT(*)
                FROM live_continuation_decisions d
                JOIN live_continuation_arms a
                  ON a.setup_id = d.setup_id
                 AND a.continuation_rule_version = d.continuation_rule_version
                WHERE a.session_date = ?
                """,
                (session_date,),
            )
            continuation_successful = _count(
                conn,
                """
                SELECT COUNT(*)
                FROM live_continuation_decisions d
                JOIN live_continuation_arms a
                  ON a.setup_id = d.setup_id
                 AND a.continuation_rule_version = d.continuation_rule_version
                WHERE a.session_date = ? AND d.decision_type = 'TRIGGERED'
                """,
                (session_date,),
            )
            continuation_failed = _count(
                conn,
                """
                SELECT COUNT(*)
                FROM live_continuation_decisions d
                JOIN live_continuation_arms a
                  ON a.setup_id = d.setup_id
                 AND a.continuation_rule_version = d.continuation_rule_version
                WHERE a.session_date = ? AND d.decision_type = 'REJECTED'
                """,
                (session_date,),
            )
        finally:
            conn.close()
    except FileNotFoundError:
        pass

    return {
        "session_date": session_date,
        "subscribed": subscribed,
        "tokens_with_1m": tokens_1m,
        "tokens_with_5m": tokens_5m,
        "baseline_as_of": baseline_as_of,
        "spikes": spikes,
        "setups": setups,
        "continuation_arms": arms,
        "continuation_decisions": decisions,
        "continuation_successful": continuation_successful,
        "continuation_failed": continuation_failed,
    }


def list_sessions(live_db: Path) -> List[str]:
    try:
        conn = open_readonly(live_db)
    except FileNotFoundError:
        return []
    try:
        rows = conn.execute(
            """
            SELECT session_date FROM (
                SELECT DISTINCT session_date FROM live_1m_candles
                UNION
                SELECT DISTINCT session_date FROM live_intraday_spikes
                UNION
                SELECT DISTINCT session_date FROM live_pullback_setups
            )
            ORDER BY session_date DESC
            """
        ).fetchall()
        return [str(r[0]) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def fetch_radar_rows(
    live_db: Path,
    instruments_db: Path,
    session_date: str,
) -> List[RadarRow]:
    token_by_symbol = _load_tokens(instruments_db)
    contexts: Dict[str, _SymbolContext] = {
        symbol: _SymbolContext(
            symbol=symbol,
            instrument_token=token_by_symbol.get(symbol),
        )
        for symbol in NIFTY_100_SYMBOLS
    }

    try:
        conn = open_readonly(live_db)
    except FileNotFoundError:
        return [_to_row(ctx) for ctx in contexts.values()]

    try:
        _apply_candles(conn, contexts, session_date)
        _apply_spikes(conn, contexts, session_date)
        _apply_setups(conn, contexts, session_date)
        _apply_continuation(conn, contexts, session_date)
    finally:
        conn.close()

    return [_to_row(ctx) for ctx in contexts.values()]


def _apply_candles(
    conn: sqlite3.Connection,
    contexts: Dict[str, _SymbolContext],
    session_date: str,
) -> None:
    try:
        rows = conn.execute(
            """
            SELECT instrument_token, tradingsymbol, open, close, volume, candle_time, inserted_at
            FROM live_1m_candles
            WHERE session_date = ?
            ORDER BY instrument_token, candle_time
            """,
            (session_date,),
        ).fetchall()
    except sqlite3.OperationalError:
        return

    first_by_token: Dict[int, sqlite3.Row] = {}
    last_by_token: Dict[int, sqlite3.Row] = {}
    for row in rows:
        token = int(row["instrument_token"])
        first_by_token.setdefault(token, row)
        last_by_token[token] = row

    for ctx in contexts.values():
        if ctx.instrument_token is None:
            continue
        first = first_by_token.get(ctx.instrument_token)
        last = last_by_token.get(ctx.instrument_token)
        if first is not None:
            ctx.session_open = float(first["open"])
        if last is not None:
            ctx.last_1m_close = float(last["close"])
            ctx.last_1m_volume = int(last["volume"])
            ctx.last_1m_time = str(last["candle_time"])
            ctx.last_event_at = str(last["inserted_at"])


def _apply_spikes(
    conn: sqlite3.Connection,
    contexts: Dict[str, _SymbolContext],
    session_date: str,
) -> None:
    try:
        rows = conn.execute(
            """
            SELECT tradingsymbol, direction, detected_at
            FROM live_intraday_spikes
            WHERE session_date = ?
            ORDER BY tradingsymbol, detected_at
            """,
            (session_date,),
        ).fetchall()
    except sqlite3.OperationalError:
        return

    for row in rows:
        symbol = str(row["tradingsymbol"])
        ctx = contexts.get(symbol)
        if ctx is None:
            continue
        ctx.has_spike = True
        ctx.spike_direction = str(row["direction"])
        detected = str(row["detected_at"])
        if ctx.last_event_at is None or detected > ctx.last_event_at:
            ctx.last_event_at = detected


def _apply_setups(
    conn: sqlite3.Connection,
    contexts: Dict[str, _SymbolContext],
    session_date: str,
) -> None:
    try:
        setups = conn.execute(
            """
            SELECT setup_id, tradingsymbol, direction, created_at
            FROM live_pullback_setups
            WHERE session_date = ?
            ORDER BY tradingsymbol, created_at
            """,
            (session_date,),
        ).fetchall()
    except sqlite3.OperationalError:
        return

    setup_count_by_symbol: Dict[str, int] = {}
    latest_setup_by_symbol: Dict[str, sqlite3.Row] = {}
    for row in setups:
        symbol = str(row["tradingsymbol"])
        setup_count_by_symbol[symbol] = setup_count_by_symbol.get(symbol, 0) + 1
        latest_setup_by_symbol[symbol] = row

    setup_ids = [str(r["setup_id"]) for r in latest_setup_by_symbol.values()]
    events_by_setup: Dict[str, sqlite3.Row] = {}
    if setup_ids:
        placeholders = ",".join("?" for _ in setup_ids)
        try:
            event_rows = conn.execute(
                f"""
                SELECT setup_id, event_type, resulting_state, created_at, sequence_number
                FROM live_pullback_setup_events
                WHERE setup_id IN ({placeholders})
                ORDER BY setup_id, sequence_number
                """,
                tuple(setup_ids),
            ).fetchall()
        except sqlite3.OperationalError:
            event_rows = []
        for row in event_rows:
            events_by_setup[str(row["setup_id"])] = row

    for symbol, setup in latest_setup_by_symbol.items():
        ctx = contexts.get(symbol)
        if ctx is None:
            continue
        ctx.setup_count = setup_count_by_symbol.get(symbol, 0)
        setup_id = str(setup["setup_id"])
        ctx.setup_id = setup_id
        if ctx.spike_direction is None and setup["direction"]:
            ctx.spike_direction = str(setup["direction"])
        event = events_by_setup.get(setup_id)
        if event is not None:
            ctx.setup_state = str(event["resulting_state"])
            ctx.last_event_type = str(event["event_type"])
            created = str(event["created_at"])
            if ctx.last_event_at is None or created > ctx.last_event_at:
                ctx.last_event_at = created


def _apply_continuation(
    conn: sqlite3.Connection,
    contexts: Dict[str, _SymbolContext],
    session_date: str,
) -> None:
    try:
        arms = conn.execute(
            """
            SELECT setup_id, tradingsymbol, trigger_price, armed_at
            FROM live_continuation_arms
            WHERE session_date = ?
            """,
            (session_date,),
        ).fetchall()
    except sqlite3.OperationalError:
        return

    arm_by_setup = {str(r["setup_id"]): r for r in arms}
    decisions_by_setup: Dict[str, sqlite3.Row] = {}
    if arm_by_setup:
        placeholders = ",".join("?" for _ in arm_by_setup)
        try:
            decision_rows = conn.execute(
                f"""
                SELECT setup_id, decision_type, reason, created_at
                FROM live_continuation_decisions
                WHERE setup_id IN ({placeholders})
                """,
                tuple(arm_by_setup.keys()),
            ).fetchall()
        except sqlite3.OperationalError:
            decision_rows = []
        for row in decision_rows:
            decisions_by_setup[str(row["setup_id"])] = row

    for ctx in contexts.values():
        if ctx.setup_id is None:
            continue
        arm = arm_by_setup.get(ctx.setup_id)
        if arm is None:
            continue
        ctx.has_arm = True
        if arm["trigger_price"] is not None:
            ctx.trigger_price = float(arm["trigger_price"])
        armed_at = str(arm["armed_at"])
        if ctx.last_event_at is None or armed_at > ctx.last_event_at:
            ctx.last_event_at = armed_at

        decision = decisions_by_setup.get(ctx.setup_id)
        if decision is not None:
            ctx.continuation_decision = str(decision["decision_type"])
            if decision["reason"]:
                ctx.continuation_reason = str(decision["reason"])
            created = str(decision["created_at"])
            if ctx.last_event_at is None or created > ctx.last_event_at:
                ctx.last_event_at = created


def _spike_label(has_spike: bool) -> str:
    return "Confirmed" if has_spike else "-"


def _pullback_label(setup_state: Optional[str]) -> str:
    if setup_state == "PULLBACK_READY":
        return "Ready"
    if setup_state == "PULLBACK_MONITORING":
        return "Watching"
    if setup_state == "IMPULSE_MONITORING":
        return "Impulse"
    if setup_state == "SPIKE_ACCEPTED":
        return "Setup"
    if setup_state in {
        "CONTINUATION_MONITORING",
        "CONTINUATION_TRIGGERED",
        "CONTINUATION_REJECTED",
    }:
        return "Confirmed"
    return "-"


def _continuation_label(
    *,
    continuation_decision: Optional[str],
    setup_state: Optional[str],
    has_arm: bool,
) -> str:
    if continuation_decision == "TRIGGERED":
        return "Triggered"
    if continuation_decision == "REJECTED":
        return "Rejected"
    if continuation_decision == "DISARMED":
        return "Disarmed"
    if setup_state == "CONTINUATION_REJECTED":
        return "Rejected"
    if setup_state == "CONTINUATION_TRIGGERED":
        return "Triggered"
    if setup_state == "CONTINUATION_MONITORING" or has_arm:
        return "Armed"
    return "-"


def _direction_label(direction: Optional[str]) -> Optional[str]:
    if direction in {"UP", "DOWN"}:
        return direction
    return None


def _to_row(ctx: _SymbolContext) -> RadarRow:
    phase = map_to_ui_phase(
        has_spike=ctx.has_spike,
        setup_state=ctx.setup_state,
        continuation_decision=ctx.continuation_decision,
    )
    pct_change: Optional[float] = None
    if (
        ctx.session_open is not None
        and ctx.last_1m_close is not None
        and ctx.session_open != 0
    ):
        pct_change = (ctx.last_1m_close - ctx.session_open) / ctx.session_open * 100.0

    distance_pct: Optional[float] = None
    if (
        ctx.trigger_price is not None
        and ctx.last_1m_close is not None
        and ctx.last_1m_close != 0
        and phase == "CONTINUATION_ARMED"
    ):
        distance_pct = (ctx.trigger_price - ctx.last_1m_close) / ctx.last_1m_close * 100.0

    last_event = format_last_event(
        phase=phase,
        setup_state=ctx.setup_state,
        event_type=ctx.last_event_type,
        event_payload=None,
        continuation_decision=ctx.continuation_decision,
        continuation_reason=ctx.continuation_reason,
    )

    return RadarRow(
        symbol=ctx.symbol,
        instrument_token=ctx.instrument_token,
        last_1m_close=ctx.last_1m_close,
        pct_change=pct_change,
        phase=phase,
        direction=_direction_label(ctx.spike_direction),
        spike=_spike_label(ctx.has_spike),
        pullback=_pullback_label(ctx.setup_state),
        continuation=_continuation_label(
            continuation_decision=ctx.continuation_decision,
            setup_state=ctx.setup_state,
            has_arm=ctx.has_arm,
        ),
        volume=ctx.last_1m_volume,
        trigger_price=ctx.trigger_price,
        distance_pct=distance_pct,
        last_event=last_event,
        updated_at=ctx.last_event_at,
        setup_count=ctx.setup_count,
    )
