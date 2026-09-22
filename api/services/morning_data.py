"""Bounded daily repair using the existing candle and quality implementations."""
from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from datetime import datetime

from api import config
from config.nifty100_symbols import NIFTY_100_SYMBOLS
from historical_collector import StockToken, DateChunk, fetch_candles_for_chunk, save_candles
from nse_trading_calendar import IST, prior_nse_trading_session
from session_quality import (LOOKBACK_COMPLETED_SESSIONS, discover_completed_sessions,
                             evaluate_symbol_session, load_session_minutes)


def required_sessions(session_date: str) -> list[str]:
    sessions = []
    for _ in range(LOOKBACK_COMPLETED_SESSIONS):
        session_date = prior_nse_trading_session(session_date)
        if session_date is None:
            raise ValueError("Calendar cannot resolve historical sessions; update the NSE calendar.")
        sessions.append(session_date)
    return sessions


def repair_plan(session_date: str) -> list[tuple[StockToken, str]]:
    """Read-only plan. Permit at most three deficient sessions per symbol."""
    if not config.HISTORICAL_DB_PATH.exists() or not config.INSTRUMENTS_DB_PATH.exists():
        raise ValueError("Initial historical backfill required before enabling morning preparation.")
    sessions = required_sessions(session_date)
    with closing(sqlite3.connect(f"{config.INSTRUMENTS_DB_PATH.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        tokens = dict(conn.execute("SELECT tradingsymbol, instrument_token FROM nifty50_instruments"))
    if any(symbol not in tokens for symbol in NIFTY_100_SYMBOLS):
        raise ValueError("Instrument universe incomplete; generate instruments first.")
    pending = []
    with closing(sqlite3.connect(f"{config.HISTORICAL_DB_PATH.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        for symbol in NIFTY_100_SYMBOLS:
            stock = StockToken(symbol, tokens[symbol])
            deficient = []
            completed = discover_completed_sessions(conn, stock.instrument_token, as_of=sessions[0])
            missing_completed = max(0, LOOKBACK_COMPLETED_SESSIONS - len(completed))
            for day in sessions:
                quality = evaluate_symbol_session(conn, stock.instrument_token, day)
                # Stage 5 needs source data through 15:04, beyond baseline eligibility.
                minutes = set(load_session_minutes(conn, stock.instrument_token, day)) if day == sessions[0] else set()
                prior_deficient = day == sessions[0] and (not quality.is_completed or not set(range(555, 905)).issubset(minutes))
                if prior_deficient or (not quality.is_completed and missing_completed > 0):
                    deficient.append(day)
                    if not quality.is_completed:
                        missing_completed = max(0, missing_completed - 1)
            if len(deficient) > 3 or missing_completed:
                raise ValueError("Initial historical backfill required: more than three deficient sessions for a symbol.")
            pending.extend((stock, day) for day in deficient)
    return pending


def repair_history(session_date: str) -> None:
    from login import _get_kite
    pending = repair_plan(session_date)
    kite = _get_kite()
    for stock, day in pending:
        chunk = DateChunk(datetime.fromisoformat(day + "T09:15:00").replace(tzinfo=IST),
                          datetime.fromisoformat(day + "T15:29:00").replace(tzinfo=IST))
        candles = fetch_candles_for_chunk(kite, stock.instrument_token, chunk)
        # Transactional replacement accepts corrected candles and removes obsolete rows.
        with closing(sqlite3.connect(config.HISTORICAL_DB_PATH)) as conn, conn:
            conn.execute("DELETE FROM candles WHERE instrument_token=? AND substr(candle_time,1,10)=?",
                         (stock.instrument_token, day))
            save_candles(conn, stock, candles)
            quality = evaluate_symbol_session(conn, stock.instrument_token, day)
            minutes = set(load_session_minutes(conn, stock.instrument_token, day))
            if not quality.is_completed or (day == required_sessions(session_date)[0] and not set(range(555, 905)).issubset(minutes)):
                raise ValueError("Historical provider returned an incomplete session; retry after checking data availability.")
            # Remove derived rows in the same transaction; never retain stale 5m OHLCV.
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='candles_5m'").fetchone():
                conn.execute("DELETE FROM candles_5m WHERE instrument_token=? AND substr(candle_time,1,10)=?",
                             (stock.instrument_token, day))
        time.sleep(0.5)
    if repair_plan(session_date):
        raise ValueError("Historical coverage remains incomplete.")


def repair_five_minute(session_date: str) -> None:
    from five_minute_candle_generator import (init_db, list_stocks, load_session_candles,
                                              aggregate_session, save_candles_5m)
    sessions = required_sessions(session_date)
    conn = init_db(config.HISTORICAL_DB_PATH)
    try:
        for stock in list_stocks(conn):
            if stock.tradingsymbol not in NIFTY_100_SYMBOLS:
                continue
            for day in sessions:
                expected, _ = aggregate_session(load_session_candles(conn, stock.instrument_token, day))
                # Compare content, not merely row counts: a repaired source can change OHLCV.
                actual = list(conn.execute(
                    "SELECT candle_time,open,high,low,close,volume FROM candles_5m "
                    "WHERE instrument_token=? AND substr(candle_time,1,10)=? ORDER BY candle_time",
                    (stock.instrument_token, day)))
                desired = [(x.candle_time, x.open, x.high, x.low, x.close, x.volume) for x in expected]
                if actual != desired:
                    with conn:
                        conn.execute("DELETE FROM candles_5m WHERE instrument_token=? AND substr(candle_time,1,10)=?",
                                     (stock.instrument_token, day))
                        save_candles_5m(conn, stock, expected)
    finally:
        conn.close()
