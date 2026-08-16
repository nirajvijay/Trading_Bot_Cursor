"""
Generate historical 5-minute OHLCV candles from stored 1-minute candles.

Reads 1-minute candles from the historical SQLite DB, aggregates them using
the shared candle_aggregation module, and writes results to candles_5m.

Usage:
  python five_minute_candle_generator.py
  python five_minute_candle_generator.py --symbol RELIANCE
  python five_minute_candle_generator.py --db data/nifty50_historical.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from api.config import HISTORICAL_DB_PATH
from candle_aggregation import FiveMinuteCandle, OneMinuteCandle, aggregate_session

ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = HISTORICAL_DB_PATH

CREATE_CANDLES_5M_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS candles_5m (
    instrument_token INTEGER NOT NULL,
    tradingsymbol    TEXT    NOT NULL,
    candle_time      TEXT    NOT NULL,
    open             REAL    NOT NULL,
    high             REAL    NOT NULL,
    low              REAL    NOT NULL,
    close            REAL    NOT NULL,
    volume           INTEGER NOT NULL,
    PRIMARY KEY (instrument_token, candle_time)
);
"""

CREATE_CANDLES_5M_TOKEN_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_candles_5m_token
    ON candles_5m(instrument_token);
"""

CREATE_CANDLES_5M_SYMBOL_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_candles_5m_symbol
    ON candles_5m(tradingsymbol);
"""

CREATE_RUNS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS five_minute_generation_runs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at       TEXT NOT NULL,
    stocks_requested   INTEGER NOT NULL,
    stocks_completed   INTEGER NOT NULL,
    sessions_processed INTEGER NOT NULL,
    candles_generated  INTEGER NOT NULL,
    buckets_skipped    INTEGER NOT NULL,
    runtime_seconds    REAL NOT NULL,
    errors             TEXT
);
"""

UPSERT_CANDLES_5M_SQL = """
INSERT INTO candles_5m (
    instrument_token, tradingsymbol, candle_time,
    open, high, low, close, volume
) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(instrument_token, candle_time) DO UPDATE SET
    tradingsymbol = excluded.tradingsymbol,
    open          = excluded.open,
    high          = excluded.high,
    low           = excluded.low,
    close         = excluded.close,
    volume        = excluded.volume
"""


@dataclass(frozen=True)
class StockToken:
    tradingsymbol: str
    instrument_token: int


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(CREATE_CANDLES_5M_TABLE_SQL)
    conn.execute(CREATE_CANDLES_5M_TOKEN_INDEX_SQL)
    conn.execute(CREATE_CANDLES_5M_SYMBOL_INDEX_SQL)
    conn.execute(CREATE_RUNS_TABLE_SQL)
    return conn


def list_stocks(conn: sqlite3.Connection, symbol: str | None = None) -> list[StockToken]:
    if symbol:
        rows = conn.execute(
            """
            SELECT DISTINCT instrument_token, tradingsymbol
            FROM candles
            WHERE tradingsymbol = ?
            ORDER BY tradingsymbol
            """,
            (symbol.upper(),),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT DISTINCT instrument_token, tradingsymbol
            FROM candles
            ORDER BY tradingsymbol
            """
        ).fetchall()

    return [StockToken(tradingsymbol=row[1], instrument_token=row[0]) for row in rows]


def discover_all_sessions_for_stock(
    conn: sqlite3.Connection,
    instrument_token: int,
) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT substr(candle_time, 1, 10) AS session_date
        FROM candles
        WHERE instrument_token = ?
        ORDER BY session_date
        """,
        (instrument_token,),
    ).fetchall()
    return [row[0] for row in rows]


def load_session_candles(
    conn: sqlite3.Connection,
    instrument_token: int,
    session_date: str,
) -> list[OneMinuteCandle]:
    rows = conn.execute(
        """
        SELECT candle_time, open, high, low, close, volume
        FROM candles
        WHERE instrument_token = ?
          AND substr(candle_time, 1, 10) = ?
        ORDER BY candle_time
        """,
        (instrument_token, session_date),
    ).fetchall()
    return [
        OneMinuteCandle(
            candle_time=row[0],
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=int(row[5]),
        )
        for row in rows
    ]


def save_candles_5m(
    conn: sqlite3.Connection,
    stock: StockToken,
    candles: list[FiveMinuteCandle],
) -> int:
    if not candles:
        return 0

    rows = [
        (
            stock.instrument_token,
            stock.tradingsymbol,
            candle.candle_time,
            candle.open,
            candle.high,
            candle.low,
            candle.close,
            candle.volume,
        )
        for candle in candles
    ]
    conn.executemany(UPSERT_CANDLES_5M_SQL, rows)
    return len(rows)


def log_generation_run(
    conn: sqlite3.Connection,
    generated_at: str,
    stocks_requested: int,
    stocks_completed: int,
    sessions_processed: int,
    candles_generated: int,
    buckets_skipped: int,
    runtime_seconds: float,
    errors: list[str],
) -> None:
    conn.execute(
        """
        INSERT INTO five_minute_generation_runs (
            generated_at, stocks_requested, stocks_completed,
            sessions_processed, candles_generated, buckets_skipped,
            runtime_seconds, errors
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            generated_at,
            stocks_requested,
            stocks_completed,
            sessions_processed,
            candles_generated,
            buckets_skipped,
            runtime_seconds,
            json.dumps(errors) if errors else None,
        ),
    )


def generate_five_minute_candles(
    db_path: Path = DEFAULT_DB_PATH,
    symbol: str | None = None,
) -> dict:
    if not db_path.exists():
        raise FileNotFoundError(f"Historical database not found: {db_path}")

    started_at = time.perf_counter()
    generated_at = datetime.now().isoformat(timespec="seconds")

    conn = init_db(db_path)
    errors: list[str] = []
    stocks_requested = 0
    stocks_completed = 0
    sessions_processed = 0
    candles_generated = 0
    buckets_skipped = 0

    try:
        stocks = list_stocks(conn, symbol=symbol)
        if symbol and not stocks:
            raise ValueError(f"Symbol not found in historical DB: {symbol}")

        stocks_requested = len(stocks)
        for stock_idx, stock in enumerate(stocks, start=1):
            stock_generated = 0
            stock_skipped = 0
            stock_sessions = 0

            try:
                session_dates = discover_all_sessions_for_stock(
                    conn,
                    instrument_token=stock.instrument_token,
                )
                for session_date in session_dates:
                    one_minute_candles = load_session_candles(
                        conn,
                        instrument_token=stock.instrument_token,
                        session_date=session_date,
                    )
                    generated, skipped = aggregate_session(one_minute_candles)
                    saved = save_candles_5m(conn, stock, generated)

                    stock_generated += saved
                    stock_skipped += skipped
                    stock_sessions += 1
                    sessions_processed += 1
                    candles_generated += saved
                    buckets_skipped += skipped

                stocks_completed += 1
                print(
                    f"[{stock_idx}/{stocks_requested}] {stock.tradingsymbol} "
                    f"sessions={stock_sessions} generated={stock_generated} "
                    f"skipped={stock_skipped}"
                )
            except Exception as exc:
                error_msg = f"{stock.tradingsymbol}: {exc}"
                errors.append(error_msg)
                print(f"  error: {error_msg}")

            conn.commit()

        runtime_seconds = time.perf_counter() - started_at
        log_generation_run(
            conn,
            generated_at=generated_at,
            stocks_requested=stocks_requested,
            stocks_completed=stocks_completed,
            sessions_processed=sessions_processed,
            candles_generated=candles_generated,
            buckets_skipped=buckets_skipped,
            runtime_seconds=runtime_seconds,
            errors=errors,
        )
        conn.commit()
    finally:
        conn.close()

    runtime_seconds = time.perf_counter() - started_at
    return {
        "db_path": str(db_path),
        "generated_at": generated_at,
        "stocks_requested": stocks_requested,
        "stocks_completed": stocks_completed,
        "sessions_processed": sessions_processed,
        "candles_generated": candles_generated,
        "buckets_skipped": buckets_skipped,
        "runtime_seconds": runtime_seconds,
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate historical 5-minute candles from stored 1-minute candles "
            "using shared aggregation logic"
        )
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help=f"Historical SQLite database path (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--symbol",
        help="Generate 5-minute candles for a single symbol only (e.g. RELIANCE)",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Historical database not found: {db_path}")

    try:
        summary = generate_five_minute_candles(db_path=db_path, symbol=args.symbol)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    print("\n5-minute candle generation complete")
    print(f"  database:             {summary['db_path']}")
    print(f"  generated_at:         {summary['generated_at']}")
    print(f"  stocks requested:     {summary['stocks_requested']}")
    print(f"  stocks completed:     {summary['stocks_completed']}")
    print(f"  sessions processed:   {summary['sessions_processed']}")
    print(f"  candles generated:    {summary['candles_generated']}")
    print(f"  buckets skipped:      {summary['buckets_skipped']}")
    print(f"  runtime (seconds):    {summary['runtime_seconds']:.2f}")
    if summary["errors"]:
        print(f"  errors:               {len(summary['errors'])}")


if __name__ == "__main__":
    main()
