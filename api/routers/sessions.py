"""FastAPI routers."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException

from api import config
from api.db import open_readonly
from api.queries.radar import fetch_coverage, fetch_radar_rows, list_sessions
from api.queries.status import read_runner_status
from api.queries.symbol_timeline import fetch_symbol_timeline
from api.schemas.radar import (
    HealthResponse,
    InstrumentRow,
    RadarResponse,
    RunnerStatus,
    SessionCoverage,
)
from api.schemas.symbol_timeline import SymbolTimelineResponse
from config.nifty50_symbols import NIFTY_50_SYMBOLS

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        live_db=config.LIVE_DB_PATH.exists(),
        instruments_db=config.INSTRUMENTS_DB_PATH.exists(),
        baselines_db=config.BASELINES_DB_PATH.exists(),
    )


@router.get("/instruments", response_model=List[InstrumentRow])
def instruments() -> List[InstrumentRow]:
    try:
        conn = open_readonly(config.INSTRUMENTS_DB_PATH)
    except FileNotFoundError:
        return []
    try:
        rows = conn.execute(
            """
            SELECT tradingsymbol, instrument_token, exchange
            FROM nifty50_instruments
            ORDER BY tradingsymbol
            """
        ).fetchall()
        return [
            InstrumentRow(
                tradingsymbol=str(r["tradingsymbol"]),
                instrument_token=int(r["instrument_token"]),
                exchange=str(r["exchange"]) if r["exchange"] else None,
            )
            for r in rows
        ]
    except Exception:
        return []
    finally:
        conn.close()


@router.get("/sessions", response_model=List[str])
def sessions() -> List[str]:
    return list_sessions(config.LIVE_DB_PATH)


@router.get("/sessions/{session_date}/coverage", response_model=SessionCoverage)
def session_coverage(session_date: str) -> SessionCoverage:
    data = fetch_coverage(
        config.LIVE_DB_PATH,
        config.BASELINES_DB_PATH,
        session_date,
    )
    return SessionCoverage(**data)


@router.get("/sessions/{session_date}/radar", response_model=RadarResponse)
def session_radar(session_date: str) -> RadarResponse:
    rows = fetch_radar_rows(
        config.LIVE_DB_PATH,
        config.INSTRUMENTS_DB_PATH,
        session_date,
    )
    return RadarResponse(session_date=session_date, rows=rows)


@router.get(
    "/sessions/{session_date}/symbols/{symbol}/timeline",
    response_model=SymbolTimelineResponse,
)
def session_symbol_timeline(session_date: str, symbol: str) -> SymbolTimelineResponse:
    if symbol not in NIFTY_50_SYMBOLS:
        raise HTTPException(status_code=404, detail="Symbol not in NIFTY 50")
    return fetch_symbol_timeline(config.LIVE_DB_PATH, session_date, symbol)


@router.get("/sessions/{session_date}/status", response_model=RunnerStatus)
def session_status(session_date: str) -> RunnerStatus:
    status = read_runner_status(str(config.RUNNER_STATUS_FILE))
    if status.session_date and status.session_date != session_date:
        return RunnerStatus()
    return status
