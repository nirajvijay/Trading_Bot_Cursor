"""Trading engine control API. Observation runner is not modified."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from api import config
from api.auth.deps import (
    WebAuthContext,
    require_step_up,
    require_web_session,
    require_web_session_mutating,
)
from api.queries.trading import load_snapshot
from api.schemas.trading import (
    TradingCapitalRequest,
    TradingSnapshotResponse,
    TradingStartRequest,
    TradingStartResponse,
    TradingStatusResponse,
    TradingStopResponse,
    TradingTrailRequest,
)
from api.services.trading_engine_runner import (
    is_engine_running,
    start_trading_engine,
    stop_trading_engine,
)
from trading_engine_store import TradingEngineStore
from trading_engine_types import DEFAULT_TOTAL_CAPITAL, DEMO_LEVERAGE_FACTOR

router = APIRouter(prefix="/trading-engine", tags=["trading-engine"])


def _session_date(session_date: Optional[str]) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    if session_date:
        return session_date
    return datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")


@router.get(
    "/status",
    response_model=TradingStatusResponse,
    dependencies=[Depends(require_web_session)],
)
def trading_status(
    session_date: Optional[str] = Query(default=None),
) -> TradingStatusResponse:
    date = _session_date(session_date)
    running = is_engine_running(session_date=date)
    snap = load_snapshot(config.trading_engine_db_path(), date, running=running)
    env_live = config.trading_engine_live_orders_enabled()
    return TradingStatusResponse(
        state=str(snap["state"]),
        session_date=str(snap["session_date"]),
        live_orders_enabled=bool(snap["live_orders_enabled"]),
        live_orders_env_enabled=env_live,
        unprotected_count=int(snap["unprotected_count"]),
        limits_protected=bool(snap["limits_protected"]),
        closed_loss_today=float(snap["closed_loss_today"]),
        committed_risk=float(snap["committed_risk"]),
        remaining_daily=float(snap["remaining_daily"]),
        live_pnl=float(snap["live_pnl"]),
        total_capital=float(snap["total_capital"]),
        leverage_factor=float(snap["leverage_factor"]),
        margin_used=float(snap["margin_used"]),
        remaining_capital=float(snap["remaining_capital"]),
        buying_power=float(snap["buying_power"]),
        last_error=snap.get("last_error"),
        engine_running=running,
        can_confirm_live=env_live,
    )


@router.get(
    "/snapshot",
    response_model=TradingSnapshotResponse,
    dependencies=[Depends(require_web_session)],
)
def trading_snapshot(
    session_date: Optional[str] = Query(default=None),
) -> TradingSnapshotResponse:
    date = _session_date(session_date)
    running = is_engine_running(session_date=date)
    snap = load_snapshot(config.trading_engine_db_path(), date, running=running)
    return TradingSnapshotResponse(**snap)


@router.post("/start", response_model=TradingStartResponse)
def trading_start(
    request: Request,
    body: TradingStartRequest = Body(default_factory=TradingStartRequest),
    ctx: WebAuthContext = Depends(require_web_session_mutating),
) -> TradingStartResponse:
    if body.confirm_live_orders:
        require_step_up(request, ctx)
    success, message, pid = start_trading_engine(
        body.session_date,
        confirm_live_orders=body.confirm_live_orders,
        total_capital=body.total_capital or DEFAULT_TOTAL_CAPITAL,
    )
    if not success:
        lowered = message.lower()
        if "already running" in lowered or "already starting" in lowered:
            raise HTTPException(status_code=409, detail=message)
        raise HTTPException(status_code=400, detail=message)
    return TradingStartResponse(success=True, message=message, pid=pid)


@router.post(
    "/stop",
    response_model=TradingStopResponse,
    dependencies=[Depends(require_web_session_mutating)],
)
def trading_stop() -> TradingStopResponse:
    success, message = stop_trading_engine()
    return TradingStopResponse(success=success, message=message)


@router.post(
    "/capital",
    dependencies=[Depends(require_web_session_mutating)],
)
def trading_capital(body: TradingCapitalRequest) -> dict:
    db = config.trading_engine_db_path()
    store = TradingEngineStore(db)
    try:
        run = store.latest_run()
        if run is None:
            store.start_run(
                session_date=_session_date(None),
                live_orders_enabled=False,
                pid=None,
                total_capital=body.total_capital,
                leverage=DEMO_LEVERAGE_FACTOR,
                status="stopped",
            )
        else:
            store.set_total_capital(str(run["run_id"]), body.total_capital)
    finally:
        store.close()
    return {"success": True, "total_capital": body.total_capital}


@router.post(
    "/trades/{trade_id}/trail-stop",
    dependencies=[Depends(require_web_session_mutating)],
)
def trading_trail(
    trade_id: str,
    request: Request,
    body: TradingTrailRequest,
    ctx: WebAuthContext = Depends(require_web_session_mutating),
) -> dict:
    db = config.trading_engine_db_path()
    store = TradingEngineStore(db)
    try:
        trade = store.get_trade(trade_id)
        if trade is None:
            raise HTTPException(status_code=404, detail="trade_not_found")
        run = store.latest_run()
        if run is not None and int(run["live_orders_enabled"] or 0):
            require_step_up(request, ctx)
        store.enqueue_command(
            "trail_stop",
            trade_id=trade_id,
            payload={"new_stop": body.new_stop, "last_price": body.last_price},
        )
    finally:
        store.close()
    return {"success": True, "message": "Trail requested"}
