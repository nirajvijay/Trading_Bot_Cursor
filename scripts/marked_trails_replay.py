"""Live-faithful replay of the marked-trail plan (research machine only).

    /Users/nj/nifty-radar-research/.venv/bin/python scripts/marked_trails_replay.py [--out FILE]

ORB: the 3-year top-3 replay of plan_marked_trails_2026-11/orb_marked_replay.py
(same candidates: every stock ranked, top 3 selected, scale 0.5, slip 3 bps),
run three ways through THIS checkout's arb_replay.replay_day:

  ref   unmarked top-3 stocks dropped before the replay, normal exits
        (= the research's orb_marked_no.json; sanity check)
  live  ArbSettings(marked_trails_enabled=True) with the rules table: rule 1
        and the level check at entry (before the budget, so an out-of-level
        trade no longer uses budget or moves the day controls), the trail
        from a bar that completed before the normal exit, tick-rounded stops,
        14:50 open square-off, day controls on the trailed paths, a halt
        flattens trailed trades.

GUC (not in arb_replay): the research table with the live conventions
(touch before the normal exit bar, tick-rounded trail, 14:50 open). GUC has
no halt in the research table.
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # before orb_topn adds the main checkout
import arb_replay  # noqa: E402,F401  (this checkout's, cached for orb_topn)
import engine_marked_trails as M  # noqa: E402
from arb_replay import FREE_BY_STOP, ReplayBar, ReplayCandidate, replay_day  # noqa: E402
from engine_arb import ArbSettings  # noqa: E402

sys.path.insert(1, "/Users/nj/nifty-radar-research/orb_topN_3y")
import orb_topn as T  # noqa: E402
from orb_rules import first_breakout_bar, make_arm, planned_stop, range_from_candles, rank_ranges, MinuteBar  # noqa: E402

assert Path(arb_replay.__file__).resolve().parent == ROOT, arb_replay.__file__

LIVE = ArbSettings(risk_scale=0.5, funnel_enabled=False)
MARKED = dataclasses.replace(LIVE, marked_trails_enabled=True)


def cands_for(day):
    exp = T.expected(day, T.CFG.sessions_needed)
    pr = T.prior(day)
    bars, ranges, by_tok = {}, [], {}
    for sym, tok in T.STOCKS:
        rows = [(ts[11:16], o, h, l, c, v) for ts, o, h, l, c, v in T.con.execute(
            "select ts,o,h,l,c,v from minute where symbol=? and ts>=? and ts<? order by ts", (sym, day, day + " 99"))]
        if not rows:
            continue
        bars[sym] = rows
        prev_days = [d for d in T.SUMM[sym] if d.session_date < day and d.session_date not in T.SPECIAL]
        bl = T.baseline_from_days(instrument_token=tok, tradingsymbol=sym, days=prev_days[-40:], prior_session=pr,
                                  config=T.CFG, expected_sessions=exp)
        mb = [MinuteBar(*r) for r in rows]
        ranges.append(range_from_candles(instrument_token=tok, tradingsymbol=sym, baseline=bl, bars=mb))
        by_tok[tok] = mb
    rb = {s: [ReplayBar(f"{day}T{tm}", o, h, l, c) for tm, o, h, l, c, v in rows] for s, rows in bars.items()}
    cands = []
    for ranked in rank_ranges(ranges, dataclasses.replace(T.CFG, top_n=3)):
        if not ranked.selected:
            continue
        sym = ranked.range.tradingsymbol
        arm = make_arm(ranked, session_date=day, tick_size=T.day_tick(bars[sym], T.TICK.get(sym, 0.05)))
        hit = first_breakout_bar(by_tok[arm.instrument_token], direction=arm.direction, level=arm.level)
        if hit is None:
            continue
        bar, entry = hit
        stop = planned_stop(direction=arm.direction, reference_price=entry, distance=arm.stop_distance,
                            tick_size=arm.tick_size)
        cands.append(ReplayCandidate(setup_id=arm.setup_id, strategy="ORB", symbol=sym, direction=arm.direction,
                                     minute=f"{day}T{bar.time}", moment=f"{day}T{bar.time}:00+05:30",
                                     trigger_price=float(entry), stop=stop, tick_size=arm.tick_size))
    return cands, rb


def entered(r):
    return [t for t in r.trades if t.entered]


def guc_live(book):
    """GUC research table with the live conventions (plan_bt-style overlay)."""
    sys.path.insert(0, "/Users/nj/nifty-radar-research/strategy2_2026-11")
    import lib  # noqa: E402
    sys.path.insert(0, str(ROOT / "scripts"))
    import marked_trails_parity as B  # noqa: E402  (npy-cache bars, as the research)
    out = []
    for line in open("/Users/nj/nifty-radar-research/strategy2_2026-11/trades/gapup5_live.jsonl"):
        t = json.loads(line)
        if t["sym"] not in book:
            continue
        d = "UP" if t["dir"] > 0 else "DOWN"
        bars = B.bars_of(t["sym"], t["day"])
        check = M.check_entry(book, symbol=t["sym"], direction=d, entry_price=t["entry"], day_open=bars[0].open)
        if not check.ok:
            continue
        rule = M.rule_from_plan(check.plan)
        net = t["net"]
        ex = None
        if rule is not None:
            ex = M.walk_trail(bars, direction=d, level=check.plan["level"], rule=rule,
                              entry_minute=f"{t['day']}T{B.hhmm(t['m'])}", touch_until=f"{t['day']}T{B.hhmm(t['xm'])}",
                              touch_inclusive=False, research_eod=False, tick_size=T.TICK.get(t["sym"], 0.05))
            if ex is not None:
                net = lib.trade_net(t["entry"], ex.price, t["dir"], t["q"])
        out.append((t["day"], net, ex is not None))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    book = M.MarkedTrailBook.load()
    days = [json.loads(line)["day"] for line in open("/Users/nj/nifty-radar-research/orb_topN_3y/all.jsonl")]
    if args.limit:
        days = days[: args.limit]
    ref_no = json.load(open("/Users/nj/nifty-radar-research/plan_marked_trails_2026-11/orb_marked_no.json"))
    tot = collections.defaultdict(float)
    year = collections.defaultdict(lambda: collections.defaultdict(float))
    counts = collections.Counter()
    ref_mismatch = []
    for k, day in enumerate(days):
        cands, rb = cands_for(day)
        ref_c = [c for c in cands if c.symbol in book]
        ref = replay_day(ref_c, rb, LIVE, capital_rupees=T.CAP, slip_rate=3e-4, free_rule=FREE_BY_STOP) if ref_c else None
        live = replay_day(cands, rb, MARKED, capital_rupees=T.CAP, slip_rate=3e-4, free_rule=FREE_BY_STOP,
                          marked_book=book) if cands else None
        ref_net = 0.0 if ref is None else ref.net
        want = ref_no.get(day, {}).get("live", {}).get("net", 0.0)
        if abs(ref_net - want) > 0.05:
            ref_mismatch.append((day, ref_net, want))
        tot["ref"] += ref_net
        year["ref"][day[:4]] += ref_net
        if live is not None:
            tot["live"] += live.net
            year["live"][day[:4]] += live.net
            for t in entered(live):
                counts["live_trades"] += 1
                counts[f"exit_{t.exit_reason}"] += 1
            for t in live.trades:
                if not t.entered and t.reason:
                    counts[f"skip_{t.reason}"] += 1
            if live.halt_reason:
                counts[f"halt_{live.halt_reason}"] += 1
        if ref is not None:
            counts["ref_trades"] += len(entered(ref))
        if k % 100 == 0:
            print(k, day, {kk: round(v) for kk, v in tot.items()}, flush=True)
    guc = guc_live(book)
    guc_tot = sum(n for _, n, _ in guc)
    lines = [
        f"days {len(days)}",
        f"ORB ref (unmarked dropped, normal exits): {counts['ref_trades']} trades {tot['ref']:+,.0f} "
        f"(research orb_marked_no: 718 trades +72,513); day mismatches > Rs 0.05: {len(ref_mismatch)}",
        f"ORB live-faithful (rules at entry, trails, halts on trailed paths): {counts['live_trades']} trades "
        f"{tot['live']:+,.0f}",
        "  by year: " + ", ".join(f"{y} {v:+,.0f}" for y, v in sorted(year['live'].items())),
        "  exits/skips/halts: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()) if not k.endswith('trades')),
        f"GUC live conventions x1: {len(guc)} trades ({sum(1 for g in guc if g[2])} touched) {guc_tot:+,.0f}",
        f"PLAN live-faithful total (ORB + GUC x1): {tot['live'] + guc_tot:+,.0f}",
    ]
    if ref_mismatch:
        lines.append("  first ref mismatches: " + "; ".join(f"{d} {a:+.2f} vs {b:+.2f}" for d, a, b in ref_mismatch[:5]))
    text = "\n".join(lines)
    print(text)
    if args.out:
        args.out.write_text(text + "\n")


if __name__ == "__main__":
    main()
