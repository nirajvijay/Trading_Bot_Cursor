"""Observation runner control.

Readiness requires website session.
Start requires website session AND localhost (existing safeguard retained).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth.deps import require_web_session, require_web_session_mutating
from api.routers.auth import require_localhost
from api.schemas.observation import ObservationReadinessResponse, ObservationStartResponse
from api.services.observation_runner import compute_readiness, start_observation_runner

router = APIRouter(prefix="/observation", tags=["observation"])


@router.get(
    "/readiness",
    response_model=ObservationReadinessResponse,
    dependencies=[Depends(require_web_session)],
)
def observation_readiness(
    session_date: Optional[str] = Query(default=None, description="IST session date YYYY-MM-DD"),
) -> ObservationReadinessResponse:
    data = compute_readiness(session_date)
    return ObservationReadinessResponse(**data)


@router.post(
    "/start",
    response_model=ObservationStartResponse,
    dependencies=[Depends(require_web_session_mutating), Depends(require_localhost)],
)
def observation_start(
    session_date: Optional[str] = Query(default=None, description="IST session date YYYY-MM-DD"),
) -> ObservationStartResponse:
    # Single locked start path performs cheap readiness gates + start lease.
    success, message, pid = start_observation_runner(session_date)
    if not success:
        lowered = message.lower()
        if (
            "already running" in lowered
            or "already starting" in lowered
            or "start lease" in lowered
        ):
            raise HTTPException(status_code=409, detail=message)
        raise HTTPException(status_code=400, detail=message)
    return ObservationStartResponse(success=True, message=message, pid=pid)
