"""Cross-process checklist ownership and durable, non-secret stage activity.

The lock inode is never removed. A supervisor passes its descriptor to children,
so a killed parent cannot let another writer race an orphaned collector.
"""
from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime

from fastapi import HTTPException

from api import config
from nse_trading_calendar import IST

STAGES = {"kite": "kite_auth", "instruments": "instruments", "historical": "historical_candles",
          "baselines": "baselines", "five-minute": "five_minute_candles"}


class ChecklistBusy(RuntimeError):
    pass


def ensure_market_data_idle() -> None:
    from api.services.observation_runner import is_runner_running, _current_runner_pid
    from api.services.execution_engine_runner import engine_is_running, heartbeat
    from engine_status import process_alive
    pids = [_current_runner_pid(), (heartbeat() or {}).get("pid")]
    # A stale heartbeat does not make it safe to rewrite a live process's data.
    if is_runner_running() or engine_is_running() or any(
        isinstance(pid, int) and pid > 0 and process_alive(pid) for pid in pids
    ):
        raise ValueError("Observation or execution is active; preparation cannot change its data.")


def today() -> str:
    return datetime.now(IST).date().isoformat()


@contextmanager
def workflow_lock(*, shared=False):
    root = config.runtime_cache_dir()
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root / "checklist-workflow.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ChecklistBusy("Checklist preparation is already running") from None
        yield fd
    finally:
        # close, not LOCK_UN: inherited child descriptors must retain ownership.
        os.close(fd)


def workflow_busy() -> bool:
    path = config.runtime_cache_dir() / "checklist-workflow.lock"
    if not path.exists():
        return False
    fd = os.open(path, os.O_RDONLY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True
    finally:
        os.close(fd)


def save_activity(payload: dict) -> dict:
    root = config.runtime_cache_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "checklist-runs.db"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    payload = {**payload, "revision": uuid.uuid4().hex, "updated_at": datetime.now(IST).isoformat()}
    with closing(sqlite3.connect(path, timeout=5)) as conn, conn:
        conn.execute("CREATE TABLE IF NOT EXISTS runs (session_date TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        conn.execute("INSERT OR REPLACE INTO runs VALUES (?, ?)", (payload["session_date"], json.dumps(payload)))
    return payload


def read_activity(session_date: str | None = None) -> dict | None:
    day = session_date or today()
    path = config.runtime_cache_dir() / "checklist-runs.db"
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=2)
        try:
            row = conn.execute("SELECT payload FROM runs WHERE session_date = ?", (day,)).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        payload = json.loads(row[0])
        if payload["status"] == "running" and (day != today() or not workflow_busy()):
            return {**payload, "status": "blocked", "message": "Preparation interrupted; retry the incomplete stage.",
                    "revision": payload["revision"] + "-interrupted"}
        return payload
    except (sqlite3.Error, ValueError, KeyError, TypeError):
        return {"session_date": day, "status": "blocked", "source": "automatic", "stage": "kite",
                "revision": "unreadable", "message": "Checklist activity unavailable; inspect server state."}


def activity_blocks_readiness(activity: dict | None) -> bool:
    return bool(activity and (activity.get("status") in ("running", "blocked") or activity.get("dirty")))


def overlay_activity(data: dict, activity: dict | None) -> dict:
    data["activity"] = activity
    for stage in (activity or {}).get("dirty", []):
        if stage in STAGES:
            data["areas"][STAGES[stage]].update(status="needs_update", message="Source data changed; regenerate this stage.")
    if not activity_blocks_readiness(activity):
        return data
    message = activity.get("message") or ("Source data changed; regenerate dependent stages." if activity.get("dirty") else "Checklist preparation is running")
    key = STAGES.get(activity.get("stage"), "dashboard_readiness")
    data["areas"][key].update(status="warning" if activity["status"] == "running" else "failed", message=message)
    data.update(overall_status="warning" if activity["status"] == "running" else "failed", next_step=message)
    data["blockers"] = [message, *data.get("blockers", [])]
    return data


@contextmanager
def manual_operation(stage: str):
    try:
        with workflow_lock() as fd:
            from api.services.checklist_cache import invalidate_checklist_cache
            previous = read_activity() or {}
            dirty = set(previous.get("dirty", []))
            if stage != "kite":
                try:
                    ensure_market_data_idle()
                except ValueError as exc:
                    raise HTTPException(status_code=409, detail=str(exc)) from None
            if stage in ("instruments", "historical"):
                dirty.update(["baselines", "five-minute"])
            state = save_activity({"session_date": today(), "source": "manual", "status": "running",
                                   "stage": stage, "dirty": sorted(dirty), "started_at": datetime.now(IST).isoformat(), "message": ""})
            invalidate_checklist_cache()
            try:
                yield fd
            except BaseException:
                save_activity({**state, "status": "blocked", "message": "Stage failed; retry this stage or check its configuration."})
                raise
            else:
                dirty.discard(stage)
                save_activity({**state, "status": "completed", "dirty": sorted(dirty), "message": ""})
    except ChecklistBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


def kite_activity():
    with manual_operation("kite"):
        yield
