"""
Generate per-minute historical baselines for NIFTY RADAR spike detection.

Reads completed 1-minute candles from the historical SQLite DB and writes
volume / absolute-return baselines into data/nifty50_baselines.db.

Look-ahead contract (no bias):
  A baseline with baseline_as_of_date = D uses only candles from completed
  sessions on or before D. That baseline is intended for trading on the
  *next* session after D — never on D itself.

  Example: historical data ends 22-Jul-2026 → generate as_of 22-Jul-2026
  → use those baselines on 23-Jul-2026.

Session selection is per stock (each instrument uses its own latest N
completed sessions). Unreliable rows (valid_session_count < 18) are stored
with is_reliable = 0 for the live strategy to ignore later.

Usage:
  python baseline_generator.py
  python baseline_generator.py --symbol RELIANCE
  python baseline_generator.py --as-of 2026-07-22 --sessions 21
  python baseline_generator.py --historical-db data/nifty50_historical.db \\
                               --baselines-db data/nifty50_baselines.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_HISTORICAL_DB_PATH = ROOT / "data" / "nifty50_historical.db"
DEFAULT_BASELINES_DB_PATH = ROOT / "data" / "nifty50_baselines.db"

GENERATOR_VERSION = "1.0.0"
LOOKBACK_SESSIONS = 21
TRIM_FRACTION = 0.10
RELIABLE_MIN_SESSIONS = 18

# NSE cash session minutes (inclusive), minutes since midnight IST.
SESSION_MINUTE_START = 9 * 60 + 15  # 09:15 → 555
SESSION_MINUTE_END = 15 * 60 + 29  # 15:29 → 929

CREATE_BASELINES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS baselines (
    instrument_token      INTEGER NOT NULL,
    tradingsymbol         TEXT    NOT NULL,
    minute_of_day         INTEGER NOT NULL,
    median_volume         REAL    NOT NULL,
    trimmed_mean_volume   REAL    NOT NULL,
    median_abs_return     REAL    NOT NULL,
    valid_session_count   INTEGER NOT NULL,
    is_reliable           INTEGER NOT NULL,
    baseline_as_of_date   TEXT    NOT NULL,
    PRIMARY KEY (instrument_token, minute_of_day, baseline_as_of_date)
);
"""

CREATE_BASELINES_AS_OF_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_baselines_as_of
    ON baselines(baseline_as_of_date, instrument_token);
"""

CREATE_BASELINES_SYMBOL_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_baselines_symbol
    ON baselines(tradingsymbol, baseline_as_of_date);
"""

CREATE_RUNS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS baseline_generation_runs (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at         TEXT NOT NULL,
    baseline_as_of_date  TEXT NOT NULL,
    lookback_sessions    INTEGER NOT NULL,
    trim_fraction        REAL NOT NULL,
    generator_version    TEXT NOT NULL,
    stocks_requested     INTEGER NOT NULL,
    stocks_completed     INTEGER NOT NULL,
    baseline_rows        INTEGER NOT NULL,
    reliable_rows        INTEGER NOT NULL,
    unreliable_rows      INTEGER NOT NULL,
    errors               TEXT
);
"""


@dataclass(frozen=True)
class StockToken:
    tradingsymbol: str
    instrument_token: int


@dataclass(frozen=True)
class BaselineRow:
    instrument_token: int
    tradingsymbol: str
    minute_of_day: int
    median_volume: float
    trimmed_mean_volume: float
    median_abs_return: float
    valid_session_count: int
    is_reliable: int
    baseline_as_of_date: str


def init_baselines_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(CREATE_BASELINES_TABLE_SQL)
    conn.execute(CREATE_BASELINES_AS_OF_INDEX_SQL)
    conn.execute(CREATE_BASELINES_SYMBOL_INDEX_SQL)
    conn.execute(CREATE_RUNS_TABLE_SQL)
    return conn


def minute_of_day_from_candle_time(candle_time: str) -> int:
    """Return IST wall-clock minutes since midnight (09:15 → 555)."""
    dt = datetime.fromisoformat(candle_time)
    return dt.hour * 60 + dt.minute


def absolute_return(open_price: float, close_price: float) -> float | None:
    if open_price <= 0:
        return None
    return abs(close_price - open_price) / open_price


def trimmed_mean(values: list[float], trim_fraction: float = TRIM_FRACTION) -> float:
    """Symmetric trimmed mean: drop floor(trim_fraction * n) from each end."""
    if not values:
        raise ValueError("trimmed_mean requires at least one value")
    n = len(values)
    k = int(trim_fraction * n)
    if 2 * k >= n:
        # Degenerate small samples: fall back to arithmetic mean.
        return statistics.fmean(values)
    trimmed = sorted(values)[k : n - k]
    return statistics.fmean(trimmed)


def is_reliable_count(valid_session_count: int) -> int:
    """Return 1 if count meets the reliability threshold, else 0."""
    return 1 if valid_session_count >= RELIABLE_MIN_SESSIONS else 0


def list_stocks(
    historical_conn: sqlite3.Connection,
    symbol: str | None = None,
) -> list[StockToken]:
    if symbol:
        rows = historical_conn.execute(
            """
            SELECT DISTINCT instrument_token, tradingsymbol
            FROM candles
            WHERE tradingsymbol = ?
            ORDER BY tradingsymbol
            """,
            (symbol.upper(),),
        ).fetchall()
    else:
        rows = historical_conn.execute(
            """
            SELECT DISTINCT instrument_token, tradingsymbol
            FROM candles
            ORDER BY tradingsymbol
            """
        ).fetchall()

    return [
        StockToken(tradingsymbol=row[1], instrument_token=row[0]) for row in rows
    ]


def discover_sessions_for_stock(
    historical_conn: sqlite3.Connection,
    instrument_token: int,
    lookback_sessions: int = LOOKBACK_SESSIONS,
    as_of: str | None = None,
) -> list[str]:
    """
    Return this stock's latest completed session dates (YYYY-MM-DD), newest last.

    Only sessions on or before as_of are considered. When as_of is None, uses
    all available completed sessions for the stock.
    """
    if as_of is not None:
        rows = historical_conn.execute(
            """
            SELECT DISTINCT substr(candle_time, 1, 10) AS session_date
            FROM candles
            WHERE instrument_token = ?
              AND substr(candle_time, 1, 10) <= ?
            ORDER BY session_date DESC
            LIMIT ?
            """,
            (instrument_token, as_of, lookback_sessions),
        ).fetchall()
    else:
        rows = historical_conn.execute(
            """
            SELECT DISTINCT substr(candle_time, 1, 10) AS session_date
            FROM candles
            WHERE instrument_token = ?
            ORDER BY session_date DESC
            LIMIT ?
            """,
            (instrument_token, lookback_sessions),
        ).fetchall()

    # Query returns newest-first; reverse to chronological order.
    return [row[0] for row in reversed(rows)]


def load_session_candles(
    historical_conn: sqlite3.Connection,
    instrument_token: int,
    session_dates: list[str],
) -> list[tuple[str, float, float, int]]:
    """
    Load candles for one stock across the given session dates.

    Returns list of (candle_time, open, close, volume).
    """
    if not session_dates:
        return []

    placeholders = ",".join("?" for _ in session_dates)
    rows = historical_conn.execute(
        f"""
        SELECT candle_time, open, close, volume
        FROM candles
        WHERE instrument_token = ?
          AND substr(candle_time, 1, 10) IN ({placeholders})
        ORDER BY candle_time
        """,
        (instrument_token, *session_dates),
    ).fetchall()
    return [(row[0], float(row[1]), float(row[2]), int(row[3])) for row in rows]


def compute_baselines_for_stock(
    stock: StockToken,
    candles: list[tuple[str, float, float, int]],
    session_dates: list[str],
    trim_fraction: float = TRIM_FRACTION,
) -> list[BaselineRow]:
    """
    Compute one baseline row per minute_of_day for this stock.

    baseline_as_of_date is the latest session included for this stock.
    That baseline must only be used on the next trading session after that date.
    """
    if not session_dates:
        return []

    baseline_as_of_date = max(session_dates)
    by_minute: dict[int, list[tuple[float, float | None]]] = defaultdict(list)

    for candle_time, open_price, close_price, volume in candles:
        minute = minute_of_day_from_candle_time(candle_time)
        if minute < SESSION_MINUTE_START or minute > SESSION_MINUTE_END:
            continue
        abs_ret = absolute_return(open_price, close_price)
        by_minute[minute].append((float(volume), abs_ret))

    rows: list[BaselineRow] = []
    for minute_of_day, samples in sorted(by_minute.items()):
        volumes = [volume for volume, _ in samples]
        returns = [ret for _, ret in samples if ret is not None]
        valid_session_count = len(volumes)
        if valid_session_count < 1:
            continue
        if not returns:
            continue

        rows.append(
            BaselineRow(
                instrument_token=stock.instrument_token,
                tradingsymbol=stock.tradingsymbol,
                minute_of_day=minute_of_day,
                median_volume=float(statistics.median(volumes)),
                trimmed_mean_volume=float(trimmed_mean(volumes, trim_fraction)),
                median_abs_return=float(statistics.median(returns)),
                valid_session_count=valid_session_count,
                is_reliable=is_reliable_count(valid_session_count),
                baseline_as_of_date=baseline_as_of_date,
            )
        )

    return rows


def save_baselines(conn: sqlite3.Connection, rows: list[BaselineRow]) -> int:
    """Insert or replace baselines for the given as-of keys; preserve other dates."""
    if not rows:
        return 0

    payload = [
        (
            row.instrument_token,
            row.tradingsymbol,
            row.minute_of_day,
            row.median_volume,
            row.trimmed_mean_volume,
            row.median_abs_return,
            row.valid_session_count,
            row.is_reliable,
            row.baseline_as_of_date,
        )
        for row in rows
    ]
    conn.executemany(
        """
        INSERT OR REPLACE INTO baselines (
            instrument_token, tradingsymbol, minute_of_day,
            median_volume, trimmed_mean_volume, median_abs_return,
            valid_session_count, is_reliable, baseline_as_of_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def log_generation_run(
    conn: sqlite3.Connection,
    generated_at: str,
    baseline_as_of_date: str,
    lookback_sessions: int,
    trim_fraction: float,
    generator_version: str,
    stocks_requested: int,
    stocks_completed: int,
    baseline_rows: int,
    reliable_rows: int,
    unreliable_rows: int,
    errors: list[str],
) -> None:
    conn.execute(
        """
        INSERT INTO baseline_generation_runs (
            generated_at, baseline_as_of_date,
            lookback_sessions, trim_fraction, generator_version,
            stocks_requested, stocks_completed,
            baseline_rows, reliable_rows, unreliable_rows, errors
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            generated_at,
            baseline_as_of_date,
            lookback_sessions,
            trim_fraction,
            generator_version,
            stocks_requested,
            stocks_completed,
            baseline_rows,
            reliable_rows,
            unreliable_rows,
            json.dumps(errors) if errors else None,
        ),
    )


def generate_baselines(
    historical_db: Path = DEFAULT_HISTORICAL_DB_PATH,
    baselines_db: Path = DEFAULT_BASELINES_DB_PATH,
    lookback_sessions: int = LOOKBACK_SESSIONS,
    as_of: str | None = None,
    symbol: str | None = None,
    trim_fraction: float = TRIM_FRACTION,
) -> dict:
    """
    Generate baselines from completed historical sessions.

    Baselines for baseline_as_of_date=D must only be used on the next session
    after D (never on D) to avoid look-ahead bias.
    """
    if not historical_db.exists():
        raise FileNotFoundError(f"Historical database not found: {historical_db}")

    if lookback_sessions < 1:
        raise ValueError("lookback_sessions must be >= 1")

    generated_at = datetime.now().isoformat(timespec="seconds")
    historical_conn = sqlite3.connect(historical_db)
    baselines_conn = init_baselines_db(baselines_db)

    errors: list[str] = []
    stocks_requested = 0
    stocks_completed = 0
    baseline_rows = 0
    reliable_rows = 0
    unreliable_rows = 0
    as_of_dates_seen: set[str] = set()

    try:
        stocks = list_stocks(historical_conn, symbol=symbol)
        if symbol and not stocks:
            raise ValueError(f"Symbol not found in historical DB: {symbol}")

        stocks_requested = len(stocks)
        for stock_idx, stock in enumerate(stocks, start=1):
            try:
                session_dates = discover_sessions_for_stock(
                    historical_conn,
                    instrument_token=stock.instrument_token,
                    lookback_sessions=lookback_sessions,
                    as_of=as_of,
                )
                if not session_dates:
                    error_msg = (
                        f"{stock.tradingsymbol}: no completed sessions "
                        f"found at or before as_of={as_of or 'latest'}"
                    )
                    errors.append(error_msg)
                    print(f"  error: {error_msg}")
                    continue

                candles = load_session_candles(
                    historical_conn,
                    instrument_token=stock.instrument_token,
                    session_dates=session_dates,
                )
                rows = compute_baselines_for_stock(
                    stock=stock,
                    candles=candles,
                    session_dates=session_dates,
                    trim_fraction=trim_fraction,
                )
                saved = save_baselines(baselines_conn, rows)
                stock_reliable = sum(1 for row in rows if row.is_reliable == 1)
                stock_unreliable = saved - stock_reliable

                baseline_rows += saved
                reliable_rows += stock_reliable
                unreliable_rows += stock_unreliable
                stocks_completed += 1
                as_of_dates_seen.add(max(session_dates))

                print(
                    f"[{stock_idx}/{stocks_requested}] {stock.tradingsymbol} "
                    f"sessions={len(session_dates)} as_of={max(session_dates)} "
                    f"rows={saved} reliable={stock_reliable} "
                    f"unreliable={stock_unreliable}"
                )
            except Exception as exc:
                error_msg = f"{stock.tradingsymbol}: {exc}"
                errors.append(error_msg)
                print(f"  error: {error_msg}")

            baselines_conn.commit()

        run_as_of = as_of or (max(as_of_dates_seen) if as_of_dates_seen else "")
        log_generation_run(
            baselines_conn,
            generated_at=generated_at,
            baseline_as_of_date=run_as_of,
            lookback_sessions=lookback_sessions,
            trim_fraction=trim_fraction,
            generator_version=GENERATOR_VERSION,
            stocks_requested=stocks_requested,
            stocks_completed=stocks_completed,
            baseline_rows=baseline_rows,
            reliable_rows=reliable_rows,
            unreliable_rows=unreliable_rows,
            errors=errors,
        )
        baselines_conn.commit()
    finally:
        historical_conn.close()
        baselines_conn.close()

    return {
        "historical_db": str(historical_db),
        "baselines_db": str(baselines_db),
        "generated_at": generated_at,
        "baseline_as_of_date": as_of or (max(as_of_dates_seen) if as_of_dates_seen else ""),
        "lookback_sessions": lookback_sessions,
        "trim_fraction": trim_fraction,
        "generator_version": GENERATOR_VERSION,
        "stocks_requested": stocks_requested,
        "stocks_completed": stocks_completed,
        "baseline_rows": baseline_rows,
        "reliable_rows": reliable_rows,
        "unreliable_rows": unreliable_rows,
        "errors": errors,
        "as_of_dates_seen": sorted(as_of_dates_seen),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate Nifty 50 per-minute baselines from completed historical "
            "sessions. Baselines with as_of=D are for the next session after D only "
            "(no look-ahead)."
        )
    )
    parser.add_argument(
        "--historical-db",
        default=str(DEFAULT_HISTORICAL_DB_PATH),
        help=f"Input historical candles DB (default: {DEFAULT_HISTORICAL_DB_PATH})",
    )
    parser.add_argument(
        "--baselines-db",
        default=str(DEFAULT_BASELINES_DB_PATH),
        help=f"Output baselines DB (default: {DEFAULT_BASELINES_DB_PATH})",
    )
    parser.add_argument(
        "--sessions",
        type=int,
        default=LOOKBACK_SESSIONS,
        help=f"Per-stock lookback completed sessions (default: {LOOKBACK_SESSIONS})",
    )
    parser.add_argument(
        "--as-of",
        help=(
            "Only use completed sessions on or before this date (YYYY-MM-DD). "
            "Generated baselines are intended for the next trading session after "
            "this as-of date, never for the as-of session itself. "
            "Default: each stock's latest available completed session."
        ),
    )
    parser.add_argument(
        "--symbol",
        help="Generate baselines for a single symbol only (e.g. RELIANCE)",
    )
    args = parser.parse_args()

    historical_path = Path(args.historical_db)
    if not historical_path.exists():
        raise SystemExit(f"Historical database not found: {historical_path}")

    try:
        summary = generate_baselines(
            historical_db=historical_path,
            baselines_db=Path(args.baselines_db),
            lookback_sessions=args.sessions,
            as_of=args.as_of,
            symbol=args.symbol,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    print("\nBaseline generation complete")
    print(f"  historical_db:        {summary['historical_db']}")
    print(f"  baselines_db:         {summary['baselines_db']}")
    print(f"  generated_at:         {summary['generated_at']}")
    print(f"  baseline_as_of_date:  {summary['baseline_as_of_date']}")
    print(f"  lookback_sessions:    {summary['lookback_sessions']}")
    print(f"  trim_fraction:        {summary['trim_fraction']}")
    print(f"  generator_version:    {summary['generator_version']}")
    print(f"  stocks requested:     {summary['stocks_requested']}")
    print(f"  stocks completed:     {summary['stocks_completed']}")
    print(f"  baseline rows:        {summary['baseline_rows']}")
    print(f"  reliable rows:        {summary['reliable_rows']}")
    print(f"  unreliable rows:      {summary['unreliable_rows']}")
    if summary["as_of_dates_seen"]:
        print(f"  as_of dates used:     {', '.join(summary['as_of_dates_seen'])}")
    if summary["errors"]:
        print(f"  errors:               {len(summary['errors'])}")


if __name__ == "__main__":
    main()
