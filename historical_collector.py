"""
Download historical 1-minute candles for Nifty 50 stocks via Kite Connect.

Fetches data in 29-day chunks (Kite limits minute data to ~30 days per request)
and stores all candles in a single SQLite database keyed by instrument_token.

Usage:
  python historical_collector.py
  python historical_collector.py --symbol RELIANCE
  python historical_collector.py --months 3 --db data/nifty50_historical.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from kiteconnect import KiteConnect
from kiteconnect.exceptions import InputException, PermissionException, TokenException

from config.nifty50_symbols import NIFTY_50_SYMBOLS
from login import _get_kite, check_access_token

ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = ROOT / "data" / "nifty50_historical.db"
DEFAULT_INSTRUMENTS_DB_PATH = ROOT / "data" / "nifty50_instruments.db"

INTERVAL = "minute"
CHUNK_DAYS = 29
REQUEST_DELAY_SECONDS = 0.5

CREATE_CANDLES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS candles (
    instrument_token INTEGER NOT NULL,
    tradingsymbol    TEXT NOT NULL,
    candle_time      TEXT NOT NULL,
    open             REAL NOT NULL,
    high             REAL NOT NULL,
    low              REAL NOT NULL,
    close            REAL NOT NULL,
    volume           INTEGER NOT NULL,
    PRIMARY KEY (instrument_token, candle_time)
);
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_candles_token ON candles(instrument_token);
"""

CREATE_RUNS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS historical_collection_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collected_at TEXT NOT NULL,
    months INTEGER NOT NULL,
    interval TEXT NOT NULL,
    stocks_requested INTEGER NOT NULL,
    stocks_completed INTEGER NOT NULL,
    chunks_fetched INTEGER NOT NULL,
    candles_saved INTEGER NOT NULL,
    errors TEXT
);
"""


@dataclass(frozen=True)
class StockToken:
    tradingsymbol: str
    instrument_token: int


@dataclass(frozen=True)
class DateChunk:
    from_date: datetime
    to_date: datetime


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(CREATE_CANDLES_TABLE_SQL)
    conn.execute(CREATE_INDEX_SQL)
    conn.execute(CREATE_RUNS_TABLE_SQL)
    return conn


def load_nifty50_tokens(
    instruments_db: Path,
    kite: KiteConnect | None = None,
    symbols: tuple[str, ...] = NIFTY_50_SYMBOLS,
) -> list[StockToken]:
    """Load instrument tokens from instruments DB, with Kite API fallback."""
    tokens: list[StockToken] = []
    missing: list[str] = list(symbols)

    if instruments_db.exists():
        conn = sqlite3.connect(instruments_db)
        try:
            for symbol in symbols:
                row = conn.execute(
                    """
                    SELECT instrument_token, tradingsymbol
                    FROM nifty50_instruments
                    WHERE tradingsymbol = ?
                    """,
                    (symbol,),
                ).fetchone()
                if row:
                    tokens.append(StockToken(tradingsymbol=row[1], instrument_token=row[0]))
                    missing.remove(symbol)
        finally:
            conn.close()

    if missing and kite is not None:
        nse_equities = {
            row["tradingsymbol"]: row
            for row in kite.instruments(kite.EXCHANGE_NSE)
            if row.get("instrument_type") == "EQ" and row.get("segment") == "NSE"
        }
        for symbol in missing:
            row = nse_equities.get(symbol)
            if row:
                tokens.append(
                    StockToken(
                        tradingsymbol=row["tradingsymbol"],
                        instrument_token=row["instrument_token"],
                    )
                )

    tokens.sort(key=lambda item: item.tradingsymbol)
    return tokens


def build_date_chunks(
    months: int = 3,
    chunk_days: int = CHUNK_DAYS,
    end_date: datetime | None = None,
) -> list[DateChunk]:
    """Split a date range into chunks no larger than chunk_days."""
    end = end_date or datetime.now()
    start = end - timedelta(days=months * 30)
    chunks: list[DateChunk] = []
    cursor = start

    while cursor < end:
        chunk_end = min(cursor + timedelta(days=chunk_days), end)
        chunks.append(DateChunk(from_date=cursor, to_date=chunk_end))
        cursor = chunk_end

    return chunks


def fetch_candles_for_chunk(
    kite: KiteConnect,
    instrument_token: int,
    chunk: DateChunk,
) -> list[dict]:
    return kite.historical_data(
        instrument_token=instrument_token,
        from_date=chunk.from_date,
        to_date=chunk.to_date,
        interval=INTERVAL,
        continuous=False,
        oi=False,
    )


def save_candles(
    conn: sqlite3.Connection,
    stock: StockToken,
    candles: list[dict],
) -> int:
    if not candles:
        return 0

    rows = [
        (
            stock.instrument_token,
            stock.tradingsymbol,
            candle["date"].isoformat(timespec="seconds")
            if hasattr(candle["date"], "isoformat")
            else str(candle["date"]),
            candle["open"],
            candle["high"],
            candle["low"],
            candle["close"],
            int(candle["volume"]),
        )
        for candle in candles
    ]
    conn.executemany(
        """
        INSERT OR IGNORE INTO candles (
            instrument_token, tradingsymbol, candle_time,
            open, high, low, close, volume
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def log_collection_run(
    conn: sqlite3.Connection,
    collected_at: str,
    months: int,
    stocks_requested: int,
    stocks_completed: int,
    chunks_fetched: int,
    candles_saved: int,
    errors: list[str],
) -> None:
    conn.execute(
        """
        INSERT INTO historical_collection_runs (
            collected_at, months, interval,
            stocks_requested, stocks_completed,
            chunks_fetched, candles_saved, errors
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            collected_at,
            months,
            INTERVAL,
            stocks_requested,
            stocks_completed,
            chunks_fetched,
            candles_saved,
            json.dumps(errors) if errors else None,
        ),
    )


def collect_historical(
    db_path: Path = DEFAULT_DB_PATH,
    instruments_db: Path = DEFAULT_INSTRUMENTS_DB_PATH,
    months: int = 3,
    symbol: str | None = None,
) -> dict:
    valid, message = check_access_token()
    if not valid:
        raise RuntimeError(message)

    kite = _get_kite()
    symbols = (symbol.upper(),) if symbol else NIFTY_50_SYMBOLS
    stocks = load_nifty50_tokens(instruments_db, kite=kite, symbols=symbols)

    if symbol and not stocks:
        raise ValueError(f"Symbol not found in Nifty 50 list or instruments DB: {symbol}")

    chunks = build_date_chunks(months=months)
    collected_at = datetime.now().isoformat(timespec="seconds")

    conn = init_db(db_path)
    errors: list[str] = []
    stocks_completed = 0
    chunks_fetched = 0
    candles_saved = 0

    try:
        total_stocks = len(stocks)
        for stock_idx, stock in enumerate(stocks, start=1):
            stock_saved = 0
            stock_errors = 0

            for chunk_idx, chunk in enumerate(chunks, start=1):
                try:
                    candles = fetch_candles_for_chunk(kite, stock.instrument_token, chunk)
                    saved = save_candles(conn, stock, candles)
                    stock_saved += saved
                    candles_saved += saved
                    chunks_fetched += 1
                    print(
                        f"[{stock_idx}/{total_stocks}] {stock.tradingsymbol} "
                        f"chunk {chunk_idx}/{len(chunks)} - {saved} candles saved"
                    )
                except (PermissionException, TokenException, InputException) as exc:
                    stock_errors += 1
                    error_msg = (
                        f"{stock.tradingsymbol} chunk {chunk_idx} "
                        f"({chunk.from_date.date()} to {chunk.to_date.date()}): {exc}"
                    )
                    errors.append(error_msg)
                    print(f"  error: {error_msg}")
                except Exception as exc:
                    stock_errors += 1
                    error_msg = (
                        f"{stock.tradingsymbol} chunk {chunk_idx} "
                        f"({chunk.from_date.date()} to {chunk.to_date.date()}): {exc}"
                    )
                    errors.append(error_msg)
                    print(f"  error: {error_msg}")

                time.sleep(REQUEST_DELAY_SECONDS)

            if stock_errors < len(chunks):
                stocks_completed += 1

            conn.commit()

        log_collection_run(
            conn,
            collected_at=collected_at,
            months=months,
            stocks_requested=len(stocks),
            stocks_completed=stocks_completed,
            chunks_fetched=chunks_fetched,
            candles_saved=candles_saved,
            errors=errors,
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "db_path": str(db_path),
        "collected_at": collected_at,
        "months": months,
        "interval": INTERVAL,
        "stocks_requested": len(stocks),
        "stocks_completed": stocks_completed,
        "chunks_fetched": chunks_fetched,
        "candles_saved": candles_saved,
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download Nifty 50 historical 1-minute candles into SQLite"
    )
    parser.add_argument(
        "--months",
        type=int,
        default=3,
        help="Number of months of history to download (default: 3)",
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help=f"Historical SQLite database path (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--instruments-db",
        default=str(DEFAULT_INSTRUMENTS_DB_PATH),
        help=f"Instruments SQLite database path (default: {DEFAULT_INSTRUMENTS_DB_PATH})",
    )
    parser.add_argument(
        "--symbol",
        help="Download a single Nifty 50 symbol only (e.g. RELIANCE)",
    )
    args = parser.parse_args()

    summary = collect_historical(
        db_path=Path(args.db),
        instruments_db=Path(args.instruments_db),
        months=args.months,
        symbol=args.symbol,
    )

    print("\nHistorical collection complete")
    print(f"  database:           {summary['db_path']}")
    print(f"  collected_at:       {summary['collected_at']}")
    print(f"  months:             {summary['months']}")
    print(f"  interval:           {summary['interval']}")
    print(f"  stocks requested:   {summary['stocks_requested']}")
    print(f"  stocks completed:   {summary['stocks_completed']}")
    print(f"  chunks fetched:     {summary['chunks_fetched']}")
    print(f"  candles saved:      {summary['candles_saved']}")
    if summary["errors"]:
        print(f"  errors:             {len(summary['errors'])}")


if __name__ == "__main__":
    main()
