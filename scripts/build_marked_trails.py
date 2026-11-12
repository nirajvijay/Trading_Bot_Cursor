"""Build config/marked_trails.json from the 2026-11-12 plan backtest.

One-off generator (research machine only): imports the research script
plan_bt.py for the marked rules (BUY, SELL, FLOOR_L, BELOW_ACTUAL) and the
per-stock average day high/low from the open (LV, computed by levels() over
the 3-year 1-minute cache; LTM and DMART fixed as saved in their rule files),
and writes them as the engine's rules table.

    python3 scripts/build_marked_trails.py [--research DIR] [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RESEARCH = Path("/Users/nj/nifty-radar-research/plan_marked_trails_2026-11")
OUT = Path(__file__).resolve().parent.parent / "config" / "marked_trails.json"
# LTM's sell stop starts 0.5% above the level, then min(that, low * (1 + p)).
LTM_SELL_CAP_PCT = 0.5


def build(research: Path) -> dict:
    sys.path.insert(0, str(research))
    import plan_bt as P  # noqa: E402 - research module, path set above

    stocks = {}
    for sym in sorted(P.MARKED):
        hi, lo = P.LV[sym]
        buy = sell = None
        if sym in P.BUY:
            buy = {"trail_pct": float(P.BUY[sym]),
                   "mode": "level_floor" if sym in P.FLOOR_L else "plain"}
        if sym in P.SELL:
            sell = {"trail_pct": float(P.SELL[sym]), "mode": "plain"}
            if sym == "LTM":
                sell = {"trail_pct": float(P.SELL[sym]), "mode": "level_cap",
                        "cap_pct": LTM_SELL_CAP_PCT}
        stocks[sym] = {
            "avg_high_pct": round(float(hi), 2),
            "avg_low_pct": round(float(lo), 2),
            "levels_fixed": sym in P.FIXED_LV,
            "below_actual": sym in P.BELOW_ACTUAL,
            "buy": buy,
            "sell": sell,
        }
    dates = P.dates
    return {
        "version": 1,
        "generated": "2026-11-12",
        "source": str(research / "plan_bt.py"),
        "levels": {
            "what": "mean of (day high / 09:15 open - 1) and |day low / open - 1| in percent, "
                    "09:15-14:49 bars, over every full day of the cache",
            "from": dates[0],
            "to": dates[-1],
            "fixed": {s: list(v) for s, v in P.FIXED_LV.items()},
        },
        "notes": "below_actual: the stock's own study found the mark loses to the normal exits "
                 "(HINDUNILVR, HINDZINC); arb_marked_trails_drop_below_actual drops them.",
        "stocks": stocks,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--research", type=Path, default=RESEARCH)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    table = build(args.research)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(table, indent=1, sort_keys=False) + "\n")
    n_buy = sum(1 for v in table["stocks"].values() if v["buy"])
    n_sell = sum(1 for v in table["stocks"].values() if v["sell"])
    print(f"wrote {args.out}: {len(table['stocks'])} stocks, {n_buy} buy, {n_sell} sell")


if __name__ == "__main__":
    main()
