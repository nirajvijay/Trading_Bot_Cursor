"""
Post-market live-vs-Kite 1m candle reconciliation (sample).

Usage (after a live session and with valid Kite token):
  python smoke_test_live_vs_kite_reconcile.py --session-date 2026-08-04 --sample 5

Does not start observation or download full history. Compares stored live_1m_candles
against Kite historical_data for a sample of symbols.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

from config.nifty100_symbols import NIFTY_100_SYMBOLS
from historical_collector import load_nifty50_tokens
from login import _get_kite, check_access_token

ROOT = Path(__file__).resolve().parent
DEFAULT_LIVE_DB = ROOT / "data" / "nifty50_live_1m.db"
DEFAULT_INSTRUMENTS_DB = ROOT / "data" / "local" / "nifty50_instruments.db"


def _load_live_candles(
    conn: sqlite3.Connection, token: int, session_date: str
) -> dict[str, tuple[float, float, float, float, int]]:
    rows = conn.execute(
        """
        SELECT candle_time, open, high, low, close, volume
        FROM live_1m_candles
        WHERE instrument_token = ?
          AND substr(candle_time, 1, 10) = ?
        ORDER BY candle_time
        """,
        (token, session_date),
    ).fetchall()
    out: dict[str, tuple[float, float, float, float, int]] = {}
    for row in rows:
        # Normalize key to minute precision
        key = str(row[0])[:16]
        out[key] = (float(row[1]), float(row[2]), float(row[3]), float(row[4]), int(row[5]))
    return out


def reconcile_symbol(
    kite,
    live_conn: sqlite3.Connection,
    token: int,
    symbol: str,
    session_date: str,
) -> dict:
    live = _load_live_candles(live_conn, token, session_date)
    from_dt = datetime.fromisoformat(f"{session_date}T09:15:00+05:30")
    to_dt = datetime.fromisoformat(f"{session_date}T15:30:00+05:30")
    hist = kite.historical_data(token, from_dt, to_dt, "minute")
    kite_map: dict[str, tuple[float, float, float, float, int]] = {}
    for c in hist:
        ts = c["date"]
        if hasattr(ts, "isoformat"):
            key = ts.isoformat()[:16]
        else:
            key = str(ts)[:16]
        kite_map[key] = (
            float(c["open"]),
            float(c["high"]),
            float(c["low"]),
            float(c["close"]),
            int(c["volume"]),
        )

    only_live = sorted(set(live) - set(kite_map))
    only_kite = sorted(set(kite_map) - set(live))
    mismatches = []
    for key in sorted(set(live) & set(kite_map)):
        if live[key] != kite_map[key]:
            mismatches.append(key)

    return {
        "symbol": symbol,
        "live_count": len(live),
        "kite_count": len(kite_map),
        "only_live": len(only_live),
        "only_kite": len(only_kite),
        "ohlcv_mismatches": len(mismatches),
        "sample_mismatch_keys": mismatches[:5],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile live 1m vs Kite historical sample")
    parser.add_argument("--session-date", required=True, help="YYYY-MM-DD IST")
    parser.add_argument("--sample", type=int, default=5, help="Number of symbols to sample")
    parser.add_argument("--live-db", default=str(DEFAULT_LIVE_DB))
    parser.add_argument("--instruments-db", default=str(DEFAULT_INSTRUMENTS_DB))
    args = parser.parse_args()

    valid, message = check_access_token()
    if not valid:
        raise SystemExit(message)

    kite = _get_kite()
    stocks = load_nifty50_tokens(Path(args.instruments_db), kite=kite, symbols=NIFTY_100_SYMBOLS)
    sample = stocks[: max(1, min(args.sample, len(stocks)))]

    live_path = Path(args.live_db)
    if not live_path.exists():
        raise SystemExit(f"Live DB not found: {live_path}")

    conn = sqlite3.connect(f"file:{live_path}?mode=ro", uri=True)
    try:
        print(f"Reconciling {len(sample)} symbols for {args.session_date}")
        failures = 0
        for stock in sample:
            report = reconcile_symbol(
                kite, conn, stock.instrument_token, stock.tradingsymbol, args.session_date
            )
            ok = (
                report["only_live"] == 0
                and report["only_kite"] == 0
                and report["ohlcv_mismatches"] == 0
            )
            status = "OK" if ok else "DIFF"
            if not ok:
                failures += 1
            print(
                f"  [{status}] {report['symbol']}: live={report['live_count']} "
                f"kite={report['kite_count']} only_live={report['only_live']} "
                f"only_kite={report['only_kite']} mismatches={report['ohlcv_mismatches']}"
            )
        if failures:
            raise SystemExit(f"Reconciliation found differences in {failures}/{len(sample)} symbols")
        print("Reconciliation complete: sample matched")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
