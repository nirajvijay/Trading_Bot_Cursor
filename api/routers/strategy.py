"""The Strategy tab: today's ORB board and the funnel feed.

Display only. Reads the observation database read-only; nothing here reaches
the engine, places an order, or changes what the detector does.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

import engine_clock
from api import config
from api.auth.deps import require_web_session
from api.queries.strategy import fetch_funnel_feed, fetch_orb_day
from api.schemas.strategy import FunnelFeedResponse, OrbDayResponse

router = APIRouter(prefix="/strategy", tags=["strategy"])

_DATE = r"^\d{4}-\d{2}-\d{2}$"


def _today() -> str:
    return engine_clock.to_ist().strftime("%Y-%m-%d")


@router.get("/orb", response_model=OrbDayResponse, dependencies=[Depends(require_web_session)])
def orb_day(session_date: Optional[str] = Query(default=None, pattern=_DATE)) -> OrbDayResponse:
    return OrbDayResponse(**fetch_orb_day(config.live_db_path(), session_date or _today()))


@router.get(
    "/funnel", response_model=FunnelFeedResponse, dependencies=[Depends(require_web_session)]
)
def funnel_feed(
    session_date: Optional[str] = Query(default=None, pattern=_DATE),
) -> FunnelFeedResponse:
    return FunnelFeedResponse(**fetch_funnel_feed(config.live_db_path(), session_date or _today()))
