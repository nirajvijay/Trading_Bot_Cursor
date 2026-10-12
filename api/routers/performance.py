"""The Performance tab: ARB's forward test against its backtest.

Display only, with one action: running the post-market check for a day. That
reads the observation DB and the engine's positions, and writes only its own
result table (arb_daily_check); it makes no Kite call and places no order.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

import engine_clock
from api import config
from api.auth.deps import require_web_session, require_web_session_mutating
from api.queries.performance import fetch_performance
from arb_daily_check import DailyCheckError, format_report, run_check, save_check

router = APIRouter(prefix="/performance", tags=["performance"])

_DATE = r"^\d{4}-\d{2}-\d{2}$"


@router.get("/summary", dependencies=[Depends(require_web_session)])
def performance_summary(
    risk_scale: Optional[float] = Query(default=None, gt=0, le=1),
    parity_date: Optional[str] = Query(default=None, pattern=_DATE),
) -> Dict[str, Any]:
    """One stage's forward test: P&L, sources, slippage, halts, parity, gates.

    ``parity_date`` adds that day's mismatched signals from its stored check.
    """
    return fetch_performance(
        config.execution_engine_db_path(), risk_scale=risk_scale, parity_date=parity_date
    )


@router.post("/check", dependencies=[Depends(require_web_session_mutating)])
def performance_check(
    session_date: Optional[str] = Query(default=None, pattern=_DATE),
) -> Dict[str, Any]:
    """Run and store the daily check for one session (today by default).

    The same thing as scripts/arb_daily_check.py. Run it after the close: a
    check made while trades are still open is simply replaced by a later one.
    """
    day = session_date or engine_clock.to_ist().strftime("%Y-%m-%d")
    engine_db = config.execution_engine_db_path()
    try:
        result = run_check(live_db=config.live_db_path(), engine_db=engine_db, session_date=day)
    except DailyCheckError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    save_check(engine_db, result)
    return {"summary": result["summary"], "report": format_report(result)}
