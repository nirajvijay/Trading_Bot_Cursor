"""
Collect Nifty 100 NSE equity instruments from Kite Connect and store in SQLite.

Uses kite.instruments("NSE") for master instrument data and kite.quote()
for live market snapshot. Kite does not provide a Nifty 100 constituents API,
so symbols are matched against config/nifty100_symbols.py.

All-or-nothing: validates all configured symbols against Kite before writing.
If any symbol is missing, exits without opening/writing the instruments DB.

Usage:
  python instrument_collector.py
  python instrument_collector.py --db data/local/nifty50_instruments.db
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

from config.nifty100_symbols import NIFTY_100_SYMBOLS
from login import _get_kite, check_access_token
from universe_manifest import default_manifest_path, write_universe_manifest_atomic

ROOT = Path(__file__).resolve().parent
LOCAL_DATA_DIR = ROOT / "data" / "local"
DEFAULT_DB_PATH = LOCAL_DATA_DIR / "nifty50_instruments.db"

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


def filter_universe_instruments(
    nse_equities: list[dict],
    symbols: tuple[str, ...] = NIFTY_100_SYMBOLS,
) -> tuple[list[dict], list[str]]:
    """Return matched instrument rows and any symbols not found."""
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


# Backward-compatible alias
filter_nifty50_instruments = filter_universe_instruments


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
    Collect Nifty 100 instrument master data and live quotes into SQLite.

    Validates all configured symbols against Kite before opening/writing the DB.
    On any missing symbol, raises RuntimeError and writes nothing.
    After a successful instruments commit, writes universe_manifest.json atomically.
    """
    valid, message = check_access_token()
    if not valid:
        raise RuntimeError(message)

    expected = len(NIFTY_100_SYMBOLS)
    if expected != 100:
        raise RuntimeError(f"NIFTY_100_SYMBOLS must be length 100, got {expected}")

    kite = _get_kite()
    collected_at = datetime.now().isoformat(timespec="seconds")

    nse_equities = fetch_nse_equity_instruments(kite)
    universe_rows, missing = filter_universe_instruments(nse_equities, NIFTY_100_SYMBOLS)

    if missing or len(universe_rows) != expected:
        missing_list = missing or [
            s for s in NIFTY_100_SYMBOLS if s not in {r["tradingsymbol"] for r in universe_rows}
        ]
        raise RuntimeError(
            "Instrument collection aborted — refusing to write partial universe. "
            f"Matched {len(universe_rows)}/{expected}. Missing: {', '.join(missing_list)}"
        )

    quotes: dict[str, dict] = {}
    quote_permission_denied = False

    if fetch_live_quotes:
        quotes = fetch_quotes(kite, universe_rows)
        if not quotes and universe_rows:
            quote_permission_denied = True

    # Only open/write DB after 100/100 validation succeeded.
    conn = init_db(db_path)
    try:
        saved = save_instruments(conn, universe_rows, quotes, collected_at)
        log_collection_run(
            conn,
            collected_at=collected_at,
            requested=expected,
            found=len(universe_rows),
            quoted=len(quotes),
            missing=[],
        )
        conn.commit()
    finally:
        conn.close()

    # Manifest after successful instruments commit (temp + rename).
    manifest_path = default_manifest_path(LOCAL_DATA_DIR)
    # If db is under local/, place manifest beside local data dir from config path parent chain
    if db_path.parent.name == "local":
        manifest_path = default_manifest_path(db_path.parent)
    try:
        write_universe_manifest_atomic(manifest_path, created_at=collected_at)
    except OSError as exc:
        raise RuntimeError(
            "Instruments DB committed but universe manifest write failed — "
            f"checklist remains not initialized: {exc}"
        ) from exc

    return {
        "db_path": str(db_path),
        "manifest_path": str(manifest_path),
        "collected_at": collected_at,
        "symbols_requested": expected,
        "symbols_found": len(universe_rows),
        "symbols_quoted": len(quotes),
        "symbols_saved": saved,
        "missing_symbols": [],
        "quote_permission_denied": quote_permission_denied,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect Nifty 100 NSE instruments from Kite into SQLite"
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

    print("Nifty 100 instrument collection complete")
    print(f"  database:          {summary['db_path']}")
    print(f"  manifest:          {summary['manifest_path']}")
    print(f"  collected_at:      {summary['collected_at']}")
    print(f"  symbols requested: {summary['symbols_requested']}")
    print(f"  symbols found:     {summary['symbols_found']}")
    print(f"  symbols quoted:    {summary['symbols_quoted']}")
    print(f"  symbols saved:     {summary['symbols_saved']}")
    if summary["quote_permission_denied"]:
        print(
            "  note:              live quotes unavailable (Kite market data permission). "
            "Instrument master data was saved."
        )


if __name__ == "__main__":
    main()
