"""Pre-market checklist API (read-only checks + local data generation).

Checklist read requires website session.
Generate requires website session AND localhost (existing safeguard retained).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query

from api import config
from api.auth.deps import require_web_session, require_web_session_mutating
from api.queries.checklist import fetch_premarket_checklist
from api.routers.auth import require_localhost
from api.schemas.checklist import GenerateResponse, PreMarketChecklistResponse
from api.services.checklist_cache import invalidate_checklist_cache, write_checklist_cache
from api.services.local_data_generation import TASK_NAMES, run_local_generation

router = APIRouter(tags=["checklist"])


def _today_ist() -> str:
    return datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")


@router.get(
    "/premarket-checklist",
    response_model=PreMarketChecklistResponse,
    dependencies=[Depends(require_web_session)],
)
def premarket_checklist(
    session_date: Optional[str] = Query(default=None, description="IST session date YYYY-MM-DD"),
) -> PreMarketChecklistResponse:
    date = session_date or _today_ist()
    data = fetch_premarket_checklist(
        live_db=config.LIVE_DB_PATH,
        instruments_db=config.INSTRUMENTS_DB_PATH,
        historical_db=config.HISTORICAL_DB_PATH,
        baselines_db=config.BASELINES_DB_PATH,
        session_date=date,
    )
    # Cache every completed result (ok / warning / failed), not only green.
    write_checklist_cache(data)
    return PreMarketChecklistResponse(**data)


@router.post(
    "/premarket-checklist/generate/{task}",
    response_model=GenerateResponse,
    dependencies=[Depends(require_web_session_mutating), Depends(require_localhost)],
)
def generate_local_data(
    task: str,
    session_date: Optional[str] = Query(default=None, description="IST session date YYYY-MM-DD"),
) -> GenerateResponse:
    if task not in TASK_NAMES:
        raise HTTPException(status_code=400, detail=f"Unknown task: {task}")
    date = session_date or _today_ist()
    success, message = run_local_generation(task, session_date=date)
    if not success:
        status = 409 if "another generation task is running" in message else 400
        raise HTTPException(status_code=status, detail=message)
    invalidate_checklist_cache()
    return GenerateResponse(success=True, message=message, task=task)
