"""Trading engine control API. Observation runner is not modified."""

from __future__ import annotations

from typing import Optional
import os
import json
from datetime import datetime, timezone
from dataclasses import asdict

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from api import config
from api.admin_config.store import AdminConfigStore
from api.auth.deps import (
    require_web_session,
    require_web_session_mutating,
    require_step_up, WebAuthContext,
)
from api.queries.trading import load_snapshot
from api.schemas.trading import (
    TradingAutoTrailRequest,
    TradingCapitalRequest,
    TradingSnapshotResponse,
    TradingStartRequest,
    TradingStartResponse,
    TradingStatusResponse,
    TradingStopResponse,
    TradingTrailRequest,
    TradingCommandRequest, TradingPreviewRequest,
)
from api.services.trading_engine_runner import (
    heartbeat_indicates_running,
    is_engine_running,
    start_trading_engine,
    stop_trading_engine,
)
from api.services.trading_engine_start_lock import is_start_lease_active
from trading_engine_store import TradingEngineStore
from trading_engine_types import DEFAULT_TOTAL_CAPITAL, DEMO_LEVERAGE_FACTOR

router = APIRouter(prefix="/trading-engine", tags=["trading-engine"])


@router.get("/report", dependencies=[Depends(require_web_session)])
def trading_report(session_date: str, download: bool = False):
    from datetime import date
    from fastapi.responses import JSONResponse
    from trading_engine_report import session_report
    try:
        day = date.fromisoformat(session_date).isoformat()
    except ValueError:
        raise HTTPException(400, "invalid_session_date")
    store = TradingEngineStore(config.trading_engine_db_path())
    try:
        report = session_report(store, day)
    finally:
        store.close()
    headers = {"Cache-Control": "no-store"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="nifty-radar-{day}.json"'
    return JSONResponse(report, headers=headers)


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
    state = str(snap["state"])
    if (
        is_start_lease_active(date)
        and not heartbeat_indicates_running(expected_session_date=date)
        and state not in {"error", "critical"}
    ):
        state = "starting"
    return TradingStatusResponse(
        state=state,
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
        can_confirm_live=os.environ.get("NIFTY_RADAR_LIVE_WRITES_AUTHORIZED") == "1",
        accepting_triggers=bool(snap.get("accepting_triggers")),
        require_vwap_accept=bool(snap.get("require_vwap_accept", True)),
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


@router.post(
    "/start",
    response_model=TradingStartResponse,
    dependencies=[Depends(require_step_up)],
)
def trading_start(
    body: TradingStartRequest = Body(default_factory=TradingStartRequest),
) -> TradingStartResponse:
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
    status_code=202,
    dependencies=[Depends(require_web_session_mutating)],
)
def trading_trail(
    trade_id: str,
    body: TradingTrailRequest,
) -> dict:
    db = config.trading_engine_db_path()
    store = TradingEngineStore(db)
    try:
        trade = store.get_trade(trade_id)
        if trade is None:
            raise HTTPException(status_code=404, detail="trade_not_found")
        cid = store.enqueue_command(
            "trail_stop",
            trade_id=trade_id,
            payload={"new_stop": body.new_stop, "last_price": body.last_price},
        )
    finally:
        store.close()
    return {"state":"queued", "command_id":cid, "message":"Trail accepted; awaiting broker confirmation"}


@router.post(
    "/trades/{trade_id}/auto-trail",
    status_code=202,
    dependencies=[Depends(require_web_session_mutating)],
)
def trading_auto_trail(
    trade_id: str,
    body: TradingAutoTrailRequest,
) -> dict:
    db = config.trading_engine_db_path()
    store = TradingEngineStore(db)
    try:
        trade = store.get_trade(trade_id)
        if trade is None:
            raise HTTPException(status_code=404, detail="trade_not_found")
        cid = store.enqueue_command(
            "set_auto_trail",
            trade_id=trade_id,
            payload={"enabled": body.enabled},
        )
    finally:
        store.close()
    return {"state":"queued", "command_id":cid, "message":"Auto-trail change queued"}


@router.post("/commands", status_code=202)
def trading_command(body: TradingCommandRequest, ctx: WebAuthContext = Depends(require_step_up)) -> dict:
    if body.kind in {"close_position","trail_stop","set_auto_trail"} and not body.trade_id:
        raise HTTPException(400,"trade_id_required")
    if body.kind == "trail_stop" and body.new_stop is None:
        raise HTTPException(400,"new_stop_required")
    if body.kind == "set_auto_trail" and body.enabled is None:
        raise HTTPException(400,"enabled_required")
    if body.kind == "approve_entry" and (not body.setup_id or not body.continuation_rule_version):
        raise HTTPException(400,"setup_identity_required")
    if body.kind == "arm_session" and not body.config_version_id:
        raise HTTPException(400,"config_version_required")
    if body.kind == "arm_session" and body.execution_mode == "LIVE" and (
        not body.live_confirmation or os.environ.get("NIFTY_RADAR_LIVE_WRITES_AUTHORIZED") != "1"):
        raise HTTPException(409,"live_execution_not_authorized")
    store = TradingEngineStore(config.trading_engine_db_path())
    try:
        run = store.latest_run()
        if body.kind not in {"pause_entries","disarm","stop_engine"} and not is_engine_running():
            raise HTTPException(409,"start_engine_first")
        payload = body.model_dump(exclude={"kind","trade_id","client_command_id"}, exclude_none=True)
        if run:
            payload["run_id"] = str(run["run_id"])
        try:
            cid = store.enqueue_command(body.kind, trade_id=body.trade_id, payload=payload,
                client_command_id=body.client_command_id, actor=ctx.session.username or "owner")
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from exc
        if body.kind in {"pause_entries","disarm","stop_engine","close_all","switch_manual"}:
            admin = AdminConfigStore(config.admin_config_db_path())
            try:
                admin.set_entries_paused(True)
            finally:
                admin.close()
            if body.kind in {"disarm","stop_engine"}:
                store.disarm_session(str(run["session_date"]) if run else _session_date(None))
        return store.command_record(cid)
    finally:
        store.close()


@router.get("/commands/{command_id}", dependencies=[Depends(require_web_session)])
def trading_command_status(command_id: int) -> dict:
    store = TradingEngineStore(config.trading_engine_db_path())
    try:
        row = store.command_record(command_id)
        if row is None:
            raise HTTPException(404,"command_not_found")
        return row
    finally:
        store.close()


@router.get("/setups", dependencies=[Depends(require_web_session)])
def trading_setups() -> dict:
    from trading_engine_handoff import fetch_triggered_since
    from trading_engine_quotes import age_seconds
    now = datetime.now(timezone.utc)
    day = _session_date(None)
    candidates = fetch_triggered_since(config.live_db_path(), created_at_gte=day)
    return {"setups":[{**asdict(c), "signal_age_seconds":age_seconds(now,c.trigger_exchange_ts)}
                       for c in candidates if c.session_date == day][-100:]}


@router.get("/trades/{trade_id}/audit", dependencies=[Depends(require_web_session)])
def trading_trade_audit(trade_id: str) -> dict:
    """Persisted accounting and immutable plan; never refresh through broker writes."""
    store = TradingEngineStore(config.trading_engine_db_path())
    try:
        trade = store.get_trade(trade_id)
        if trade is None:
            raise HTTPException(404, "trade_not_found")
        raw = asdict(trade)
        original = raw.pop("original_setup_json", None)
        try:
            original = json.loads(original) if original else None
        except (ValueError, TypeError):
            original = None
        events = []
        for row in store.list_events(trade_id):
            event = dict(row)
            event["payload"] = json.loads(event.pop("payload_json") or "{}")
            events.append(event)
        from api.queries.trading import live_trade_mark
        from api.services.trading_engine_runner import read_heartbeat
        from trading_engine_cycle import feed_age_seconds_from_runner_status
        now = datetime.now(timezone.utc)
        feed_age = feed_age_seconds_from_runner_status(config.RUNNER_STATUS_FILE, now=now,
                                                       expected_session_date=trade.session_date)
        mark = live_trade_mark(trade, read_heartbeat() or {}, now, feed_age)
        return {"trade": raw, "original_setup": original, "live_mark": mark,
                "original_setup_available": original is not None,
                "events": events, "orders": [dict(r) for r in store.list_order_links(trade_id)],
                "as_of": datetime.now(timezone.utc).isoformat(),
                "source": "durable_reconciliation_snapshot"}
    finally:
        store.close()


@router.post("/preview", dependencies=[Depends(require_web_session_mutating)])
def trading_preview(body: TradingPreviewRequest) -> dict:
    from login import _get_kite
    from trading_engine_broker import KiteBroker
    from trading_engine_cycle import TradingEngineCycle
    from trading_engine_handoff import fetch_triggered_since
    from trading_engine_preview import preview_trade
    from trading_engine_cycle import feed_age_seconds_from_runner_status
    store = TradingEngineStore(config.trading_engine_db_path())
    cycle = None
    try:
        run = store.latest_run()
        if run is None:
            raise HTTPException(409,"start_engine_first")
        day = _session_date(None)
        candidates = [c for c in fetch_triggered_since(config.live_db_path(), created_at_gte=day)
            if c.setup_id == body.setup_id and c.continuation_rule_version == body.continuation_rule_version and c.session_date == day]
        if len(candidates) != 1:
            raise HTTPException(404,"setup_missing_or_ambiguous")
        try:
            broker = KiteBroker(_get_kite(), live_orders_enabled=False)
        except Exception as exc:
            raise HTTPException(409,"market_data_unavailable") from exc
        cycle = TradingEngineCycle(store,broker,live_db=config.live_db_path(),session_date=day,
            started_at=str(run["started_at"]),run_id=str(run["run_id"]),
            live_orders_enabled=bool(run["live_orders_enabled"]),admin_config_db=config.admin_config_db_path(),
            feed_age_seconds_fn=lambda:feed_age_seconds_from_runner_status(config.RUNNER_STATUS_FILE,
                now=datetime.now(timezone.utc), expected_session_date=day))
        return preview_trade(cycle,candidates[0],qty_override=body.qty_override,stop_tighten=body.stop_tighten)
    finally:
        if cycle:
            cycle.close()
        store.close()


@router.get("/control", dependencies=[Depends(require_web_session)])
def trading_control() -> dict:
    from api.admin_config.store import AdminConfigStore
    from api.services.trading_engine_runner import read_heartbeat
    from trading_engine_quotes import age_seconds
    from trading_engine_cycle import feed_age_seconds_from_runner_status
    now = datetime.now(timezone.utc)
    day = _session_date(None)
    store = TradingEngineStore(config.trading_engine_db_path())
    admin = AdminConfigStore(config.admin_config_db_path())
    try:
        run = store.latest_run()
        arm = store.session_arm(day)
        running = is_engine_running()
        heartbeat = read_heartbeat() or {}
        if heartbeat.get("session_date") != day:
            heartbeat = {}
        feed_age = feed_age_seconds_from_runner_status(config.RUNNER_STATUS_FILE,now=now,expected_session_date=day)
        sync_age = age_seconds(now,heartbeat.get("broker_sync_at"))
        loss = heartbeat.get("loss_halt") or {}
        mark_age = age_seconds(now, loss.get("as_of"))
        if mark_age is not None and loss.get("mark_age_seconds") is not None:
            mark_age += float(loss["mark_age_seconds"])
        else:
            mark_age = None
        trades = store.list_trades(None)
        incidents = [asdict(t) for t in trades if t.status == "reconciliation_required"]
        recovery_events = [dict(r) for r in store.list_events("__recovery__")][-100:]
        effective = admin.load_effective_payload()
        saved = admin.load_active_payload()
        permission = "disarmed"
        if running and arm and arm["armed"] and run and arm["run_id"] == run["run_id"]:
            if admin.read_entries_paused() or heartbeat.get("draining"):
                permission = "paused"
            elif incidents or heartbeat.get("recovery_unresolved") or loss.get("halted"):
                permission = "recovery_required"
            elif sync_age is None or sync_age >= 5 or feed_age is None or feed_age >= 5:
                permission = "data_not_ready"
            else:
                permission = "armed"
        return {"run":dict(run) if run else None,"arm":arm,
            "strip":{"execution_mode":"LIVE" if run and run["live_orders_enabled"] else "PAPER",
                "entry_mode":arm["entry_mode"] if arm else "MANUAL","entry_permission":permission,
                "engine_state":"stopping" if running and heartbeat.get("draining") else "running" if running else "stopped",
                "feed_age_seconds":feed_age,"feed_status":"unknown" if feed_age is None else "stale" if feed_age>=5 else "live",
                "sync_age_seconds":sync_age,"mark_age_seconds":mark_age,
                "open_pnl":loss.get("unrealised") if loss.get("complete") and mark_age is not None and mark_age<=2 else None,
                "unresolved_incident":bool(incidents or heartbeat.get("recovery_unresolved")),"as_of":now.isoformat()},
            "effective":effective,"saved":saved,"saved_version_id":admin.active_version_id(),
            "effective_version_id":admin.effective_version_id(),"commands":store.command_records(),
            "incidents":incidents,"recovery_events":recovery_events,"loss_halt":loss,
            "live_execution_authorized":os.environ.get("NIFTY_RADAR_LIVE_WRITES_AUTHORIZED")=="1"}
    finally:
        store.close()
        admin.close()
