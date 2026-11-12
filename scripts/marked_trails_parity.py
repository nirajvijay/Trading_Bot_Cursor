"""Parity check: the engine's marked-trail module against the 2026-11-12 plan backtest.

Research machine only (needs numpy and the research folders). Run with the
research venv:

    /Users/nj/nifty-radar-research/.venv/bin/python scripts/marked_trails_parity.py

Step 0 (overlay parity): the research applied its rules AFTER the replay, on
a fixed trade table: ORB trades from orb_marked_no.json (top-3 ranked on
all stocks, unmarked skipped, slot left empty; normal exits and halts from
arb_replay), GUC trades from gapup5_live.jsonl. This script runs the same
table through engine_marked_trails (check_entry for the level, walk_trail for
the trail) with the research's conventions (touch scan through the exit bar,
14:50 close, no tick rounding, lib.trade_net charges) and compares every
trade with plan_bt.sim. Target: the NO plan total +594,936 at GUC x1.

Step 1 (the one live difference): an ORB trade that was really flattened by
a day halt (exit reason "day_halt": hard day stop or day lock) is closed at
the halt minute even after its touch, as live does. Ordinary lock-stop exits
("lock") are NOT halts. GUC trades have no halt in the research table.
"""
from __future__ import annotations

import collections
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RESEARCH = Path("/Users/nj/nifty-radar-research/plan_marked_trails_2026-11")
KIT = Path("/Users/nj/nifty-radar-research/strategy2_2026-11")
sys.path.insert(0, str(RESEARCH))
sys.path.insert(0, str(KIT))

import engine_marked_trails as M  # noqa: E402
import lib  # noqa: E402  (research kit: trade_net)
import plan_bt as P  # noqa: E402  (research: sim, LV, rules)

EOD_HHMM = "14:50"


@dataclass(frozen=True)
class Bar:
    minute: str
    open: float
    high: float
    low: float
    close: float


_BARS = {}


def bars_of(sym: str, day: str):
    key = (sym, day)
    if key not in _BARS:
        a = P.arr(sym)[P.ix[day]]
        out = []
        for j in range(0, 336):  # 09:15 .. 14:50
            mm = 555 + j
            out.append(Bar(f"{day}T{mm // 60:02d}:{mm % 60:02d}", float(a[j, 0]), float(a[j, 1]),
                           float(a[j, 2]), float(a[j, 3])))
        _BARS[key] = out
    return _BARS[key]


def hhmm(m: int) -> str:
    return f"{(555 + m) // 60:02d}:{(555 + m) % 60:02d}"


def load_trades():
    T = []
    no = json.load(open(RESEARCH / "orb_marked_no.json"))
    halts = {}
    for day, v in no.items():
        halts[day] = v["live"]["minute"]
        for t in v["live"]["trades"]:
            T.append(dict(k="ORB", sym=t["s"], day=day, d=t["d"], m=t["m"], xm=t["xm"], e=t["entry"],
                          q=t["q"], net=t["net"], why=t["why"]))
    for line in open(KIT / "trades" / "gapup5_live.jsonl"):
        t = json.loads(line)
        T.append(dict(k="GUC", sym=t["sym"], day=t["day"], d="UP" if t["dir"] > 0 else "DOWN",
                      m=hhmm(t["m"]), xm=hhmm(t["xm"]), e=t["entry"], q=t["q"], net=t["net"], why=t["why"]))
    return T, halts


def run(book: M.MarkedTrailBook, T, halts, *, cut_at_halt: bool, guc_scale: float = 1.0):
    rows = []
    for t in T:
        if t["sym"] not in book:
            continue  # ORB: already skipped in the replay; GUC: rule 1
        a = P.arr(t["sym"])[P.ix[t["day"]]]
        check = M.check_entry(book, symbol=t["sym"], direction=t["d"], entry_price=t["e"],
                              day_open=float(a[0][0]))
        if not check.ok:
            continue
        rule = M.rule_from_plan(check.plan)
        sc = guc_scale if t["k"] == "GUC" else 1.0
        if rule is None:
            rows.append((t, t["net"] * sc, None))
            continue
        bars = bars_of(t["sym"], t["day"])
        ex = M.walk_trail(bars, direction=t["d"], level=check.plan["level"], rule=rule,
                          entry_minute=f"{t['day']}T{t['m']}", touch_until=f"{t['day']}T{t['xm']}",
                          touch_inclusive=True, research_eod=True)
        if ex is None:
            rows.append((t, t["net"] * sc, None))
            continue
        price, minute = ex.price, ex.minute
        if cut_at_halt and t["k"] == "ORB" and halts.get(t["day"]):
            # Any touched ORB trade still trailing when the day halts is
            # flattened then, whatever its normal exit would have been.
            hm = halts[t["day"]]
            if minute > hm:
                # Flattened with the sleeve at the halt minute's close.
                price = next(b.close for b in bars if b.minute == hm)
                minute = hm
        sign = 1 if t["d"] == "UP" else -1
        rows.append((t, lib.trade_net(t["e"], price, sign, t["q"]) * sc, ex))
    return rows


def research_rows():
    """plan_bt.sim on the same table (eval_slots.planrows for mode 'no')."""
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):  # eval_slots prints its tables on import
        import eval_slots as E
    return E.planrows(E.load("no"), 1.0)


def by_year(rows):
    y = collections.defaultdict(float)
    for t, v, *_ in rows:
        y[t["day"][:4]] += v
    return dict(sorted(y.items()))


def main() -> None:
    book = M.MarkedTrailBook.load()
    T, halts = load_trades()
    mine = run(book, T, halts, cut_at_halt=False)
    ref = research_rows()
    print(f"rules table: {book.rule_counts[0]} buy, {book.rule_counts[1]} sell, generated {book.generated}")
    print(f"step 0 overlay: engine module {len(mine)} trades {sum(v for _, v, _ in mine):+,.2f}  |  "
          f"research {len(ref)} trades {sum(n for _, n, _ in ref):+,.2f}  (REPORT: 583 trades +594,936)")
    # per trade
    key = lambda t: (t["k"], t["day"], t["sym"], t["d"] if "d" in t else t["s"], t["e"])
    refmap = {}
    for t, n, _ in ref:
        refmap[(t["k"], t["day"], t["sym"], "UP" if t["s"] > 0 else "DOWN", t["e"])] = n
    diffs = [(t, v, refmap.get(key(t))) for t, v, _ in mine]
    missing = [d for d in diffs if d[2] is None]
    bad = [d for d in diffs if d[2] is not None and abs(d[1] - d[2]) > 0.5]
    print(f"  per trade: {len(missing)} not in research, {len(bad)} differ by more than Rs 0.5")
    for t, v, r in bad[:10]:
        print(f"    {t['k']} {t['day']} {t['sym']} {t['d']} engine {v:+,.2f} research {r:+,.2f}")
    print("  by year:", {k: round(v) for k, v in by_year(mine).items()})
    for k in ("ORB", "GUC"):
        rows = [x for x in mine if x[0]["k"] == k]
        print(f"  {k}: {len(rows)} trades ({sum(1 for x in rows if x[2] is not None)} touched) "
              f"{sum(v for _, v, _ in rows):+,.0f}")

    touched = [(t, v, ex) for t, v, ex in mine if ex is not None]
    halted = [x for x in touched if x[0]["k"] == "ORB" and halts.get(x[0]["day"])
              and x[2].minute > halts[x[0]["day"]]]
    split = collections.Counter(x[0]["why"] for x in halted)
    lock_or_halt = [x for x in touched if x[0]["k"] == "ORB" and x[0]["why"] in ("day_halt", "lock")]
    cut = run(book, T, halts, cut_at_halt=True)
    tot0 = sum(v for _, v, _ in mine)
    tot1 = sum(v for _, v, _ in cut)
    print(f"\nstep 1 trailed trades closed at the halt minute (live rule):")
    print(f"  touched trades {len(touched)}; ORB trades still trailing at their day's halt minute: {len(halted)} "
          f"(by normal exit: {dict(split)}); research note's filter (why in day_halt/lock) counted {len(lock_or_halt)}")
    print(f"  total {tot0:+,.0f} -> {tot1:+,.0f}  (change {tot1 - tot0:+,.0f})")
    print("  by year:", {k: round(v) for k, v in by_year(cut).items()})
    for sc in (0.5,):
        a = sum(v for _, v, _ in run(book, T, halts, cut_at_halt=False, guc_scale=sc))
        b = sum(v for _, v, _ in run(book, T, halts, cut_at_halt=True, guc_scale=sc))
        print(f"  GUC x{sc}: {a:+,.0f} -> {b:+,.0f} (change {b - a:+,.0f})")


if __name__ == "__main__":
    main()
