"""Execution Desk API for the rebuilt engine.

Registered alongside the old /trading-engine router, never replacing it, so the
rebuild stays additive until the old engine is actually removed.

Everything here is a read of what the engine wrote, or a row appended to the
command queue. The API never talks to the running engine process directly.
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

import engine_clock
from api import config
from api.auth.deps import require_web_session, require_web_session_mutating, WebAuthContext
from api.schemas.execution import (
    CapitalView,
    EventsResponse,
    EventView,
    ExecutionCommandRequest,
    ExecutionCommandResponse,
    ExecutionStartRequest,
    ExecutionStartResponse,
    ExecutionStatusResponse,
    PositionsResponse,
    PositionView,
    PreconditionView,
    PreflightResponse,
    SessionCaps,
)
from api.services.execution_engine_runner import (
    engine_is_running,
    heartbeat,
    live_marks,
    liveness,
    preflight,
    start_engine,
)
from engine_commands import CommandKind, CommandQueue
from engine_config import SessionRiskConfig, margin_used_rupees
from engine_status import heartbeat_age_seconds
from engine_store import SqlitePositionStore

router = APIRouter(prefix="/execution", tags=["execution"])

# States the desk shows as "open work".
_OPEN_STATES = {
    "pending_entry",
    "entry_submitted",
    "entered",
    "protected",
    "trailing",
    "exit_submitted",
}


def _today() -> str:
    return engine_clock.to_ist().strftime("%Y-%m-%d")


def _store() -> Optional[SqlitePositionStore]:
    """The store, or None before the engine has ever run."""
    path = config.execution_engine_db_path()
    if not path.exists():
        return None
    return SqlitePositionStore(path)


def _queue(*, read_only: bool = False) -> CommandQueue:
    path = config.execution_engine_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return CommandQueue(path, read_only=read_only and path.exists())


def _caps_from(config_values: SessionCaps) -> SessionRiskConfig:
    return SessionRiskConfig(
        per_trade_cap_rupees=config_values.per_trade_cap_rupees,
        per_trade_cap_vwap_limited_rupees=config_values.per_trade_cap_vwap_limited_rupees,
        daily_loss_cap_rupees=config_values.daily_loss_cap_rupees,
        total_capital_rupees=config_values.total_capital_rupees,
        leverage_factor=config_values.leverage_factor,
    )


def _extra(row) -> dict:
    try:
        return json.loads(str(row["extra_json"] or "{}"))
    except (ValueError, TypeError):
        return {}


def _to_view(row, live_pnl: Dict[str, Optional[float]]) -> PositionView:
    extra = _extra(row)
    return PositionView(
        trade_id=str(row["trade_id"]),
        setup_id=str(row["setup_id"]),
        session_date=str(row["session_date"]),
        tradingsymbol=str(row["tradingsymbol"]),
        direction=str(row["direction"]),
        vwap_classification=row["vwap_classification"],
        state=str(row["state"]),
        qty=int(row["qty"] or 0),
        entry_price=row["entry_price"],
        stop_price=row["stop_price"],
        risk_taken_rupees=row["risk_taken_rupees"],
        realised_pnl=row["realised_pnl"],
        live_pnl=live_pnl.get(str(row["trade_id"])),
        entry_order_id=row["entry_order_id"],
        stop_order_id=row["stop_order_id"],
        exit_order_id=row["exit_order_id"],
        is_live=bool(row["is_live"]),
        run_id=row["run_id"],
        close_reason=extra.get("close_reason"),
        skip_reason=extra.get("skip_reason") or extra.get("reject_reason"),
        stop_adopted_from_broker="stop_adopted_from_broker" in extra,
        manual_review=extra.get("manual_review"),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


@router.get(
    "/preflight",
    response_model=PreflightResponse,
    dependencies=[Depends(require_web_session)],
)
def execution_preflight() -> PreflightResponse:
    """Every start precondition, evaluated now, each with its own reason.

    Rendered on the desk before the button is pressed, so a refusal is
    understood in advance rather than discovered by clicking.
    """
    checks = preflight()
    return PreflightResponse(
        can_start=checks.can_start,
        checks=[
            PreconditionView(key=c.key, ok=c.ok, detail=c.detail) for c in checks.checks
        ],
        engine_state=checks.engine.state,
        engine_reason=checks.engine.reason,
        refusals=checks.refusals,
    )


@router.get(
    "/status",
    response_model=ExecutionStatusResponse,
    dependencies=[Depends(require_web_session)],
)
def execution_status() -> ExecutionStatusResponse:
    data = heartbeat() or {}
    verdict = liveness()
    marks = live_marks()

    caps = SessionCaps(
        daily_loss_cap_rupees=float(data.get("daily_loss_cap") or 0.0)
        or SessionCaps().daily_loss_cap_rupees
    )

    capital: Optional[CapitalView] = None
    store = _store()
    try:
        if store is not None:
            session_config = SessionRiskConfig(
                daily_loss_cap_rupees=caps.daily_loss_cap_rupees
            )
            used = margin_used_rupees(
                store.open_positions(), session_config.leverage_factor
            )
            capital = CapitalView(
                total_capital_rupees=session_config.total_capital_rupees,
                leverage_factor=session_config.leverage_factor,
                buying_power_rupees=session_config.buying_power_rupees,
                margin_used_rupees=used,
                remaining_capital_rupees=session_config.remaining_capital_rupees(used),
                remaining_buying_power_rupees=session_config.remaining_buying_power_rupees(
                    used
                ),
            )
    finally:
        if store is not None:
            store.close()

    return ExecutionStatusResponse(
        engine_state=verdict.state,
        engine_reason=verdict.reason,
        heartbeat_age_seconds=heartbeat_age_seconds(data) if data else None,
        stopped_on_purpose=bool(data.get("stopped_on_purpose")),
        stop_reason=data.get("stop_reason"),
        run_id=data.get("run_id"),
        session_date=data.get("session_date"),
        is_live=bool(data.get("is_live")),
        tick_count=int(data.get("tick_count") or 0),
        entries_allowed=bool(data.get("entries_allowed")) and verdict.running,
        entries_stopped=bool(data.get("entries_stopped")),
        entries_paused=bool(data.get("entries_paused")),
        pause_reason=data.get("pause_reason"),
        open_positions=int(data.get("open_positions") or 0),
        unprotected=int(data.get("unprotected") or 0),
        realised_loss_today=float(data.get("realised_loss_today") or 0.0),
        daily_loss_cap=float(data.get("daily_loss_cap") or 0.0),
        remaining_daily=float(data.get("remaining_daily") or 0.0),
        caps=caps,
        capital=capital,
        total_live_pnl=marks.get("total_pnl"),
        live_pnl_as_of=marks.get("as_of"),
        live_pnl_complete=bool(marks.get("complete")),
        last_error=data.get("last_error"),
        escalations=dict(data.get("escalations") or {}),
    )


@router.get(
    "/positions",
    response_model=PositionsResponse,
    dependencies=[Depends(require_web_session)],
)
def execution_positions(
    session_date: Optional[str] = Query(default=None),
) -> PositionsResponse:
    day = session_date or _today()
    marks = live_marks()
    pnl: Dict[str, Optional[float]] = dict(marks.get("pnl") or {})

    store = _store()
    if store is None:
        return PositionsResponse(session_date=day)
    try:
        rows = store.list_positions(day)
    finally:
        store.close()

    open_rows: List[PositionView] = []
    closed_rows: List[PositionView] = []
    rejected_rows: List[PositionView] = []
    for row in rows:
        view = _to_view(row, pnl)
        state = str(row["state"])
        if state in _OPEN_STATES:
            open_rows.append(view)
        elif state == "closed":
            closed_rows.append(view)
        else:
            rejected_rows.append(view)

    return PositionsResponse(
        session_date=day,
        open=open_rows,
        closed=closed_rows,
        rejected=rejected_rows,
        total_live_pnl=marks.get("total_pnl"),
        live_pnl_as_of=marks.get("as_of"),
        live_pnl_complete=bool(marks.get("complete")),
    )


@router.get(
    "/positions/{trade_id}/events",
    response_model=EventsResponse,
    dependencies=[Depends(require_web_session)],
)
def execution_position_events(trade_id: str) -> EventsResponse:
    """The append-only diary for one trade.

    This is the review surface: pull it up after a small live trade and check
    it against what Kite's own order history shows.
    """
    store = _store()
    if store is None:
        return EventsResponse(trade_id=trade_id)
    try:
        rows = store.list_events(trade_id)
    finally:
        store.close()
    events = []
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except (ValueError, TypeError):
            payload = {}
        events.append(
            EventView(
                event_id=int(row["event_id"]),
                trade_id=str(row["trade_id"]),
                at=str(row["at"]),
                event_type=str(row["event_type"]),
                payload=payload,
            )
        )
    return EventsResponse(trade_id=trade_id, events=events)


@router.post(
    "/start",
    response_model=ExecutionStartResponse,
    dependencies=[Depends(require_web_session_mutating)],
)
def execution_start(
    body: ExecutionStartRequest = Body(default_factory=ExecutionStartRequest),
) -> ExecutionStartResponse:
    """Validate, re-check every precondition, then spawn the process.

    Refusals are outright and specific; nothing queues waiting for a window.
    """
    success, message, pid = start_engine(
        session_config=_caps_from(body.caps),
        session_date=body.session_date,
        live_orders=body.live_orders,
    )
    if not success:
        status = 409 if "already running" in message else 400
        raise HTTPException(status_code=status, detail=message)
    return ExecutionStartResponse(success=True, message=message, pid=pid)


@router.post(
    "/commands",
    response_model=ExecutionCommandResponse,
    status_code=202,
    dependencies=[Depends(require_web_session_mutating)],
)
def execution_command(
    body: ExecutionCommandRequest,
    ctx: WebAuthContext = Depends(require_web_session_mutating),
) -> ExecutionCommandResponse:
    try:
        kind = CommandKind(str(body.kind).lower())
    except ValueError:
        raise HTTPException(status_code=400, detail="unknown_command_kind")

    if kind is CommandKind.CLOSE_POSITION and not body.trade_id:
        raise HTTPException(status_code=400, detail="trade_id_required")

    if not engine_is_running():
        # Every command here assumes a live loop to read it; queueing one for a
        # process that does not exist would silently do nothing.
        raise HTTPException(status_code=409, detail="engine_not_running")

    queue = _queue()
    try:
        command_id = queue.enqueue(
            kind,
            trade_id=body.trade_id,
            actor=(ctx.session.username if ctx and ctx.session else None) or "owner",
        )
        record = queue.record(command_id) or {}
    finally:
        queue.close()

    return ExecutionCommandResponse(
        command_id=command_id,
        kind=kind.value,
        status=str(record.get("status") or "pending"),
        trade_id=body.trade_id,
        result=record.get("result"),
        at=record.get("at"),
        applied_at=record.get("applied_at"),
    )


@router.get(
    "/commands/{command_id}",
    response_model=ExecutionCommandResponse,
    dependencies=[Depends(require_web_session)],
)
def execution_command_status(command_id: int) -> ExecutionCommandResponse:
    """So an optimistic click can be reconciled with the real outcome."""
    path = config.execution_engine_db_path()
    if not path.exists():
        raise HTTPException(status_code=404, detail="command_not_found")
    queue = _queue(read_only=True)
    try:
        record = queue.record(command_id)
    finally:
        queue.close()
    if record is None:
        raise HTTPException(status_code=404, detail="command_not_found")
    return ExecutionCommandResponse(
        command_id=int(record["command_id"]),
        kind=str(record["kind"]),
        status=str(record["status"]),
        trade_id=record.get("trade_id"),
        result=record.get("result"),
        at=record.get("at"),
        applied_at=record.get("applied_at"),
    )
