"""Admin Console V1 API."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api import config
from api.admin_config.store import AdminConfigStore, VersionConflictError
from api.auth.audit import write_audit
from api.auth.deps import WebAuthContext, require_web_session, require_web_session_mutating
from api.auth.rate_limit import ACTION_ADMIN_CONFIG, check_rate_limit
from api.schemas.admin import (
    AdminActionResponse,
    AdminAuditEntry,
    AdminAuditResponse,
    AdminConfigPatchRequest,
    AdminConfigResponse,
    AdminConfigValues,
    AdminRollbackRequest,
)
from api.services.execution_engine_runner import engine_is_running, heartbeat
from engine_commands import CommandKind, CommandQueue

router = APIRouter(prefix="/admin", tags=["admin"])


def _store() -> AdminConfigStore:
    return AdminConfigStore(config.admin_config_db_path())


def _username(ctx: WebAuthContext) -> str:
    if ctx.auth_disabled:
        return "owner"
    return str(ctx.session.username or "owner")


def _enqueue_engine_command(kind: CommandKind) -> None:
    """Queue a command for the execution engine, if there is a store to queue in.

    Diagnostics' pause/resume are the same STOP/START entries toggle the
    Execution Desk exposes, so they go through the one command queue rather
    than a second control path.
    """
    db = config.execution_engine_db_path()
    if not db.exists():
        return
    queue = CommandQueue(db)
    try:
        queue.enqueue(kind)
    finally:
        queue.close()


def _config_response() -> AdminConfigResponse:
    store = _store()
    try:
        raw = store.get_config_response()
        running = engine_is_running()
        beat = heartbeat() or {}
        values = AdminConfigValues(**raw["values"])
        effective = store.load_effective_payload()
        return AdminConfigResponse(
            version_id=str(raw["version_id"]),
            entries_paused=bool(raw["entries_paused"]),
            values=values,
            vwap_accept_gap_percent=float(raw["vwap_accept_gap_percent"]),
            vwap_limited_gap_percent=float(raw["vwap_limited_gap_percent"]),
            warnings=list(raw.get("warnings") or []),
            accepting_triggers=bool(beat.get("entries_allowed")) and running,
            engine_running=running,
            effective_values=effective,
            effective_version_id=store.effective_version_id(),
            pending_next_arm=[k for k,v in raw["values"].items() if effective.get(k) != v],
        )
    finally:
        store.close()


def _today_ist() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")


def _resume_preconditions() -> Optional[str]:
    """Refuse a resume the engine could not honour anyway.

    Read from the heartbeat: the API never talks to the running process.
    """
    if not engine_is_running():
        return "engine_not_running"
    beat = heartbeat() or {}
    if str(beat.get("state") or "") == "error":
        return "engine_error"
    if beat.get("escalations"):
        return "step_escalated"
    if int(beat.get("unprotected") or 0) > 0:
        # A filled position with no confirmed stop: do not add new exposure on
        # top of exposure that is not yet protected.
        return "unprotected_lockout"
    return None


@router.get("/config", response_model=AdminConfigResponse)
def get_admin_config(
    _ctx: WebAuthContext = Depends(require_web_session),
) -> AdminConfigResponse:
    return _config_response()


@router.patch("/config", response_model=AdminConfigResponse)
def patch_admin_config(
    request: Request,
    body: AdminConfigPatchRequest,
    ctx: WebAuthContext = Depends(require_web_session_mutating),
) -> AdminConfigResponse:
    check_rate_limit(ACTION_ADMIN_CONFIG, request)
    store = _store()
    try:
        # Merge against saved/effective inside update_config — do not pre-fill
        # omitted keys from code defaults (would reset ₹2,995 etc.).
        try:
            version_id, warnings = store.update_config(
                body.values.model_dump(exclude_unset=True),
                actor=_username(ctx),
                expected_version_id=body.expected_version_id,
                comment=body.comment,
            )
        except VersionConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"version_conflict:{exc.current_version_id}",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        write_audit(
            "admin_config_updated",
            version_id=version_id,
            warnings=warnings,
        )
    finally:
        store.close()
    resp = _config_response()
    resp.warnings = warnings
    return resp


@router.post("/trading/pause", response_model=AdminActionResponse)
def pause_trading(
    request: Request,
    ctx: WebAuthContext = Depends(require_web_session_mutating),
) -> AdminActionResponse:
    check_rate_limit(ACTION_ADMIN_CONFIG, request)
    store = _store()
    try:
        already = store.read_entries_paused()
        store.set_entries_paused(True)
        store.append_control_log(
            actor_username=_username(ctx),
            action="pause_entries",
            result="already_paused" if already else "ok",
            detail=None if not already else "already_paused",
        )
        write_audit("admin_entries_paused", already=already)
    finally:
        store.close()
    _enqueue_engine_command(CommandKind.STOP)
    return AdminActionResponse(
        success=True,
        message="already_paused" if already else "Entries paused",
        entries_paused=True,
        detail="already_paused" if already else None,
    )


@router.post("/trading/resume", response_model=AdminActionResponse, status_code=202)
def resume_trading(
    request: Request,
    ctx: WebAuthContext = Depends(require_web_session_mutating),
) -> AdminActionResponse:
    check_rate_limit(ACTION_ADMIN_CONFIG, request)
    blocked = _resume_preconditions()
    if blocked:
        store = _store()
        try:
            store.append_control_log(
                actor_username=_username(ctx),
                action="resume_entries",
                result="rejected_precondition",
                detail=blocked,
            )
        finally:
            store.close()
        raise HTTPException(status_code=409, detail=blocked)
    # There is no separate "arm" concept in the new engine: resuming entries is
    # simply the START command, the same one the Execution Desk sends.
    db = config.execution_engine_db_path()
    if not db.exists():
        raise HTTPException(status_code=409, detail="engine_not_running")
    store = _store()
    queue = CommandQueue(db)
    try:
        command_id = queue.enqueue(CommandKind.START, actor=_username(ctx))
        store.set_entries_paused(False)
        paused = store.read_entries_paused()
    finally:
        queue.close()
        store.close()
    return AdminActionResponse(
        success=True,
        message="Entries resume queued; the engine applies it on its next tick",
        entries_paused=paused,
        command_id=command_id,
        state="queued",
    )


@router.get("/audit", response_model=AdminAuditResponse)
def get_audit(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _ctx: WebAuthContext = Depends(require_web_session),
) -> AdminAuditResponse:
    store = _store()
    try:
        rows = store.list_audit(limit=limit, offset=offset)
    finally:
        store.close()
    entries = [
        AdminAuditEntry(
            id=int(r["id"]),
            at=str(r["at"]),
            actor_username=str(r["actor_username"]),
            action=str(r["action"]),
            version_id=r.get("version_id"),
            from_version_id=r.get("from_version_id"),
            diff_json=str(r.get("diff_json") or "{}"),
            result=str(r["result"]),
            detail=r.get("detail"),
            step_up_verified=bool(int(r.get("step_up_verified") or 0)),
        )
        for r in rows
    ]
    return AdminAuditResponse(entries=entries, limit=limit, offset=offset)


@router.post("/config/rollback", response_model=AdminConfigResponse)
def rollback_config(
    request: Request,
    body: AdminRollbackRequest,
    ctx: WebAuthContext = Depends(require_web_session_mutating),
) -> AdminConfigResponse:
    check_rate_limit(ACTION_ADMIN_CONFIG, request)
    store = _store()
    try:
        try:
            version_id = store.rollback_config(
                body.target_version_id,
                actor=_username(ctx),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="version_not_found") from exc
        write_audit("admin_config_rollback", version_id=version_id)
    finally:
        store.close()
    return _config_response()
