#!/usr/bin/env python3
"""Compare a session's live ARB trading with the same rules replayed on its bars.

Run it after the close. It reads the observation DB and the engine's
positions, makes no Kite call and places no order. Unless --no-save is given
it stores its result in the engine DB, where the Performance tab reads it.

    python scripts/arb_daily_check.py --session-date 2026-10-14
    python scripts/arb_daily_check.py --session-date 2026-10-14 --no-save --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import config  # noqa: E402
from arb_daily_check import DailyCheckError, format_report, run_check, save_check  # noqa: E402


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--session-date", required=True, help="YYYY-MM-DD (IST)")
    parser.add_argument("--live-db", type=Path, default=None, help="observation DB")
    parser.add_argument("--engine-db", type=Path, default=None, help="execution engine DB")
    parser.add_argument(
        "--capital",
        type=float,
        default=None,
        help="the capital the session was started with (default: what the engine recorded)",
    )
    parser.add_argument("--no-save", action="store_true", help="print only; write nothing")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = parser.parse_args(argv)
    engine_db = args.engine_db or config.execution_engine_db_path()
    try:
        result = run_check(
            live_db=args.live_db or config.live_db_path(),
            engine_db=engine_db,
            session_date=args.session_date,
            capital_rupees=args.capital,
        )
    except DailyCheckError as exc:
        print(f"Cannot check: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"Bad input: {exc}", file=sys.stderr)
        return 2
    if not args.no_save:
        save_check(engine_db, result)
    print(json.dumps(result, indent=2, default=str) if args.json else format_report(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
