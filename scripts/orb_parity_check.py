#!/usr/bin/env python3
"""Compare a session's live ORB signals with the backtest's rule, from candles.

Read-only: no Kite calls, no orders. Run it after the session's 1-minute
candles are in the historical DB (the next morning's history refresh, or a
manual collection after the close).

    python scripts/orb_parity_check.py --session-date 2026-10-13
    python scripts/orb_parity_check.py --session-date 2026-10-13 --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import config  # noqa: E402
from orb_parity import ParityError, compare_day, format_report  # noqa: E402


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--session-date", required=True, help="YYYY-MM-DD (IST)")
    parser.add_argument("--live-db", type=Path, default=None, help="observation DB")
    parser.add_argument("--historical-db", type=Path, default=None, help="1-minute candle DB")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = parser.parse_args(argv)
    try:
        result = compare_day(
            live_db=args.live_db or config.live_db_path(),
            historical_db=args.historical_db or config.HISTORICAL_DB_PATH,
            session_date=args.session_date,
        )
    except ParityError as exc:
        print(f"Cannot compare: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"Bad input: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, default=str) if args.json else format_report(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
