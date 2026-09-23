"""Compose per-symbol event timeline from read-only SQLite queries."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from api.db import open_readonly
from api.lib.phase_mapper import format_timeline_event, map_setup_status
from api.schemas.symbol_timeline import (
    SymbolTimelineResponse,
    TimelineContinuation,
    TimelineEvent,
    TimelineSetup,
    TimelineSpike,
)


def fetch_symbol_timeline(
    live_db: Path,
    session_date: str,
    symbol: str,
) -> SymbolTimelineResponse:
    spikes: List[TimelineSpike] = []
    setups: List[TimelineSetup] = []

    try:
        conn = open_readonly(live_db)
    except FileNotFoundError:
        return SymbolTimelineResponse(
            session_date=session_date,
            symbol=symbol,
            spikes=spikes,
            setups=setups,
        )

    try:
        spikes = _fetch_spikes(conn, session_date, symbol)
        setup_rows = _fetch_setups(conn, session_date, symbol)
        if not setup_rows:
            return SymbolTimelineResponse(
                session_date=session_date,
                symbol=symbol,
                spikes=spikes,
                setups=setups,
            )

        setup_ids = [str(row["setup_id"]) for row in setup_rows]
        events_by_setup = _fetch_events_by_setup(conn, setup_ids)
        arms_by_setup = _fetch_arms_by_setup(conn, setup_ids)
        decisions_by_setup = _fetch_decisions_by_setup(conn, setup_ids)

        for setup_row in setup_rows:
            setup_id = str(setup_row["setup_id"])
            event_rows = events_by_setup.get(setup_id, [])
            final_state = (
                str(event_rows[-1]["resulting_state"])
                if event_rows
                else "UNKNOWN"
            )
            arm = arms_by_setup.get(setup_id)
            decision = decisions_by_setup.get(setup_id)
            continuation_decision = (
                str(decision["decision_type"]) if decision is not None else None
            )
            continuation_reason = (
                str(decision["reason"]) if decision and decision["reason"] else None
            )

            events = [
                TimelineEvent(
                    sequence_number=int(row["sequence_number"]),
                    event_type=str(row["event_type"]),
                    resulting_state=str(row["resulting_state"]),
                    label=format_timeline_event(
                        event_type=str(row["event_type"]),
                        resulting_state=str(row["resulting_state"]),
                        continuation_reason=continuation_reason
                        if str(row["event_type"])
                        in {"CONTINUATION_TRIGGERED", "CONTINUATION_REJECTED"}
                        else None,
                    ),
                    evaluation_candle_time=(
                        str(row["evaluation_candle_time"])
                        if row["evaluation_candle_time"]
                        else None
                    ),
                    created_at=str(row["created_at"]),
                )
                for row in event_rows
            ]

            continuation: Optional[TimelineContinuation] = None
            if arm is not None:
                continuation = TimelineContinuation(
                    trigger_price=float(arm["trigger_price"]),
                    armed_at=str(arm["armed_at"]),
                    decision=continuation_decision,
                    reason=continuation_reason,
                )

            setups.append(
                TimelineSetup(
                    setup_id=setup_id,
                    direction=str(setup_row["direction"]),
                    spike_candle_time=str(setup_row["spike_candle_time"]),
                    created_at=str(setup_row["created_at"]),
                    final_state=final_state,
                    status=map_setup_status(
                        final_state=final_state,
                        continuation_decision=continuation_decision,
                    ),
                    events=events,
                    continuation=continuation,
                )
            )
    finally:
        conn.close()

    return SymbolTimelineResponse(
        session_date=session_date,
        symbol=symbol,
        spikes=spikes,
        setups=setups,
    )


def _fetch_spikes(
    conn: sqlite3.Connection,
    session_date: str,
    symbol: str,
) -> List[TimelineSpike]:
    try:
        rows = conn.execute(
            """
            SELECT candle_time, direction, detected_at, close
            FROM live_intraday_spikes
            WHERE session_date = ? AND tradingsymbol = ?
            ORDER BY detected_at ASC
            """,
            (session_date, symbol),
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    return [
        TimelineSpike(
            candle_time=str(row["candle_time"]),
            direction=str(row["direction"]),
            detected_at=str(row["detected_at"]),
            close=float(row["close"]),
        )
        for row in rows
    ]


def _fetch_setups(
    conn: sqlite3.Connection,
    session_date: str,
    symbol: str,
) -> List[sqlite3.Row]:
    try:
        return conn.execute(
            """
            SELECT setup_id, direction, spike_candle_time, created_at
            FROM live_pullback_setups
            WHERE session_date = ? AND tradingsymbol = ?
            ORDER BY created_at ASC
            """,
            (session_date, symbol),
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def _fetch_events_by_setup(
    conn: sqlite3.Connection,
    setup_ids: List[str],
) -> Dict[str, List[sqlite3.Row]]:
    if not setup_ids:
        return {}

    placeholders = ",".join("?" for _ in setup_ids)
    try:
        rows = conn.execute(
            f"""
            SELECT setup_id, sequence_number, event_type, resulting_state,
                   evaluation_candle_time, created_at
            FROM live_pullback_setup_events
            WHERE setup_id IN ({placeholders})
            ORDER BY setup_id, sequence_number ASC
            """,
            tuple(setup_ids),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}

    events_by_setup: Dict[str, List[sqlite3.Row]] = {}
    for row in rows:
        setup_id = str(row["setup_id"])
        events_by_setup.setdefault(setup_id, []).append(row)
    return events_by_setup


def _fetch_arms_by_setup(
    conn: sqlite3.Connection,
    setup_ids: List[str],
) -> Dict[str, sqlite3.Row]:
    if not setup_ids:
        return {}

    placeholders = ",".join("?" for _ in setup_ids)
    try:
        rows = conn.execute(
            f"""
            SELECT setup_id, trigger_price, armed_at
            FROM live_continuation_arms
            WHERE setup_id IN ({placeholders})
            """,
            tuple(setup_ids),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}

    return {str(row["setup_id"]): row for row in rows}


def _fetch_decisions_by_setup(
    conn: sqlite3.Connection,
    setup_ids: List[str],
) -> Dict[str, sqlite3.Row]:
    if not setup_ids:
        return {}

    placeholders = ",".join("?" for _ in setup_ids)
    try:
        rows = conn.execute(
            f"""
            SELECT setup_id, decision_type, reason
            FROM live_continuation_decisions
            WHERE setup_id IN ({placeholders})
            """,
            tuple(setup_ids),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}

    return {str(row["setup_id"]): row for row in rows}
