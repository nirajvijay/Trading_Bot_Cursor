#!/usr/bin/env python3
"""Phase 4: offline 1m VWAP gap distribution report for threshold validation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from vwap_recompute_harness import export_triggered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VWAP v2 threshold study")
    parser.add_argument("--live-db", type=Path, required=True)
    parser.add_argument("--session-date", required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    results = export_triggered(
        args.live_db,
        session_date=args.session_date,
        output=args.output,
    )
    gaps = [r.gap for r in results if r.gap is not None]
    report = {
        "session_date": args.session_date,
        "trigger_count": len(results),
        "classified": {},
        "gap_percentiles": {},
    }
    for r in results:
        report["classified"][r.classification] = report["classified"].get(r.classification, 0) + 1
    if gaps:
        sorted_gaps = sorted(gaps)
        for p in (50, 75, 90, 95, 99):
            idx = min(len(sorted_gaps) - 1, int(len(sorted_gaps) * p / 100))
            report["gap_percentiles"]["p%d" % p] = sorted_gaps[idx]
    text = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
