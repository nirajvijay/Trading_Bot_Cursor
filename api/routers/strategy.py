"""The Strategy tab: today's ORB board and the funnel feed.

Display only. Reads the observation database read-only; nothing here reaches
the engine, places an order, or changes what the detector does. Each signal
also carries what the engine did with it, read from the engine database.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

import engine_clock
from api import config
from api.auth.deps import require_web_session
from typing import Any, Dict

from api.queries.strategy import (
    fetch_breadth,
    fetch_engine_decisions,
    fetch_funnel_feed,
    fetch_orb_day,
)
from api.schemas.strategy import FunnelFeedResponse, OrbDayResponse

router = APIRouter(prefix="/strategy", tags=["strategy"])

_DATE = r"^\d{4}-\d{2}-\d{2}$"


def _today() -> str:
    return engine_clock.to_ist().strftime("%Y-%m-%d")


@router.get("/orb", response_model=OrbDayResponse, dependencies=[Depends(require_web_session)])
def orb_day(session_date: Optional[str] = Query(default=None, pattern=_DATE)) -> OrbDayResponse:
    day = session_date or _today()
    data = fetch_orb_day(config.live_db_path(), day)
    _attach_engine(data.get("arms") or [], day)
    return OrbDayResponse(**data)


def _attach_engine(signals: list, day: str) -> None:
    decisions = fetch_engine_decisions(config.execution_engine_db_path(), day)
    for signal in signals:
        signal["engine"] = decisions.get(str(signal.get("setup_id")))


@router.get(
    "/funnel", response_model=FunnelFeedResponse, dependencies=[Depends(require_web_session)]
)
def funnel_feed(
    session_date: Optional[str] = Query(default=None, pattern=_DATE),
) -> FunnelFeedResponse:
    day = session_date or _today()
    data = fetch_funnel_feed(config.live_db_path(), day)
    _attach_engine(data.get("triggers") or [], day)
    return FunnelFeedResponse(**data)


@router.get("/breadth", dependencies=[Depends(require_web_session)])
def breadth(session_date: Optional[str] = Query(default=None, pattern=_DATE)) -> Dict[str, Any]:
    """The market's move from the open, overall and by sector (the funnel score's input)."""
    return fetch_breadth(config.live_db_path(), session_date or _today())
