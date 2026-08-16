"""
Collect Nifty 50 NSE equity instruments from Kite Connect and store in SQLite.

Uses kite.instruments("NSE") for master instrument data and kite.quote()
for live market snapshot. Kite does not provide a Nifty 50 constituents API,
so symbols are matched against config/nifty50_symbols.py.

Usage:
  python instrument_collector.py
  python instrument_collector.py --db data/nifty50.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from kiteconnect import KiteConnect
from kiteconnect.exceptions import PermissionException

from config.nifty50_symbols import NIFTY_50_SYMBOLS
from login import _get_kite, check_access_token

ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = ROOT / "data" / "nifty50_instruments.db"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS nifty50_instruments (
    tradingsymbol TEXT PRIMARY KEY,
    instrument_token INTEGER NOT NULL,
    exchange TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    instrument_data TEXT NOT NULL,
    quote_data TEXT
);
"""

CREATE_RUNS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS collection_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collected_at TEXT NOT NULL,
    symbols_requested INTEGER NOT NULL,
    symbols_found INTEGER NOT NULL,
    symbols_quoted INTEGER NOT NULL,
    missing_symbols TEXT
);
"""


def _json_default(obj: object) -> str:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _to_json(data: object) -> str:
    return json.dumps(data, default=_json_default)


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_RUNS_TABLE_SQL)
    return conn


def fetch_nse_equity_instruments(kite: KiteConnect) -> list[dict]:
    """Fetch all NSE instruments and keep equity cash segment rows."""
    instruments = kite.instruments(kite.EXCHANGE_NSE)
    return [
        row
        for row in instruments
        if row.get("instrument_type") == "EQ" and row.get("segment") == "NSE"
    ]


def filter_nifty50_instruments(
    nse_equities: list[dict],
    symbols: tuple[str, ...] = NIFTY_50_SYMBOLS,
) -> tuple[list[dict], list[str]]:
    """Return Nifty 50 instrument rows and any symbols not found."""
    by_symbol = {row["tradingsymbol"]: row for row in nse_equities}
    matched = []
    missing = []

    for symbol in symbols:
        row = by_symbol.get(symbol)
        if row:
            matched.append(row)
        else:
            missing.append(symbol)

    return matched, missing


def fetch_quotes(kite: KiteConnect, instruments: list[dict]) -> dict[str, dict]:
    """Fetch full market quotes for instruments (NSE:SYMBOL format)."""
    if not instruments:
        return {}

    keys = [f"{row['exchange']}:{row['tradingsymbol']}" for row in instruments]
    try:
        return kite.quote(keys)
    except PermissionException:
        return {}


def save_instruments(
    conn: sqlite3.Connection,
    instruments: list[dict],
    quotes: dict[str, dict],
    collected_at: str,
) -> int:
    """Upsert instrument and quote JSON into SQLite. Returns rows saved."""
    saved = 0
    for row in instruments:
        key = f"{row['exchange']}:{row['tradingsymbol']}"
        conn.execute(
            """
            INSERT INTO nifty50_instruments (
                tradingsymbol, instrument_token, exchange, collected_at,
                instrument_data, quote_data
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(tradingsymbol) DO UPDATE SET
                instrument_token = excluded.instrument_token,
                exchange = excluded.exchange,
                collected_at = excluded.collected_at,
                instrument_data = excluded.instrument_data,
                quote_data = excluded.quote_data
            """,
            (
                row["tradingsymbol"],
                row["instrument_token"],
                row["exchange"],
                collected_at,
                _to_json(row),
                _to_json(quotes.get(key)) if key in quotes else None,
            ),
        )
        saved += 1
    return saved


def log_collection_run(
    conn: sqlite3.Connection,
    collected_at: str,
    requested: int,
    found: int,
    quoted: int,
    missing: list[str],
) -> None:
    conn.execute(
        """
        INSERT INTO collection_runs (
            collected_at, symbols_requested, symbols_found,
            symbols_quoted, missing_symbols
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            collected_at,
            requested,
            found,
            quoted,
            ",".join(missing) if missing else None,
        ),
    )


def collect_nifty50(db_path: Path = DEFAULT_DB_PATH, *, fetch_live_quotes: bool = True) -> dict:
    """
    Collect Nifty 50 instrument master data and live quotes into SQLite.
    Returns a summary dict.
    """
    valid, message = check_access_token()
    if not valid:
        raise RuntimeError(message)

    kite = _get_kite()
    collected_at = datetime.now().isoformat(timespec="seconds")

    nse_equities = fetch_nse_equity_instruments(kite)
    nifty50_rows, missing = filter_nifty50_instruments(nse_equities)
    quotes: dict[str, dict] = {}
    quote_permission_denied = False

    if fetch_live_quotes:
        quotes = fetch_quotes(kite, nifty50_rows)
        if not quotes and nifty50_rows:
            quote_permission_denied = True

    conn = init_db(db_path)
    try:
        saved = save_instruments(conn, nifty50_rows, quotes, collected_at)
        log_collection_run(
            conn,
            collected_at=collected_at,
            requested=len(NIFTY_50_SYMBOLS),
            found=len(nifty50_rows),
            quoted=len(quotes),
            missing=missing,
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "db_path": str(db_path),
        "collected_at": collected_at,
        "symbols_requested": len(NIFTY_50_SYMBOLS),
        "symbols_found": len(nifty50_rows),
        "symbols_quoted": len(quotes),
        "symbols_saved": saved,
        "missing_symbols": missing,
        "quote_permission_denied": quote_permission_denied,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect Nifty 50 NSE instruments from Kite into SQLite"
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help=f"SQLite database path (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--skip-quotes",
        action="store_true",
        help="Skip live quote fetch (instrument master data only)",
    )
    args = parser.parse_args()

    summary = collect_nifty50(Path(args.db), fetch_live_quotes=not args.skip_quotes)

    print("Nifty 50 instrument collection complete")
    print(f"  database:          {summary['db_path']}")
    print(f"  collected_at:      {summary['collected_at']}")
    print(f"  symbols requested: {summary['symbols_requested']}")
    print(f"  symbols found:     {summary['symbols_found']}")
    print(f"  symbols quoted:    {summary['symbols_quoted']}")
    print(f"  symbols saved:     {summary['symbols_saved']}")
    if summary["missing_symbols"]:
        print(f"  missing symbols:   {', '.join(summary['missing_symbols'])}")
    if summary["quote_permission_denied"]:
        print(
            "  note:              live quotes unavailable (Kite market data permission). "
            "Instrument master data was saved."
        )


if __name__ == "__main__":
    main()
