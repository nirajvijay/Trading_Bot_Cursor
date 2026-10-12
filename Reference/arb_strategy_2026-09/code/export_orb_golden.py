"""Export golden fixtures for the live ORB port (repo tests/fixtures/orb_golden/).

Run from this research folder:  python3 export_orb_golden.py <repo>/tests/fixtures/orb_golden

Everything is recomputed here from data/kite_12m.db with orb.py's own arithmetic
(same operations, same order), and checked against orb.json before writing.

baselines.json  every armed stock-day: its 15 prior sessions (high, low, close,
                opening volume, ...) + the expected ATR14 and average opening volume
days.json       every 2026 day: the first-five-minute candles of every stock near or
                above the RVOL line (>= 2x, valid or not) + the expected arms
                (top 3 by RVOL among RVOL >= 3, ranked at 09:20)
breakouts.json  every armed stock-day: the 1-minute candles after the range +
                the expected breakout minute, entry price and stop (or none)

"Armed" is the causal rule the live detector follows: rank at 09:20 on what is
known at 09:20. The first backtest (combo2.orb_cands) took the top 3 among
stocks that went on to break out; summary.json records both.
"""
import json, os, sqlite3, sys
from collections import defaultdict

OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
RV, N, K = 3.0, 3, 0.35
NEAR = 2.0

db = sqlite3.connect("file:data/kite_12m.db?mode=ro", uri=True)
ins = sqlite3.connect("data/nifty50_instruments.db")
tok2sym = dict((t, s) for s, t in ins.execute("select tradingsymbol, instrument_token from nifty50_instruments"))
O = json.load(open("orb.json"))
REC = {(t["sym"], t["day"]): t for t in O if t["OR"] == 5}

rows_by_day = defaultdict(list)      # day -> per-stock record (valid or not)
base_inputs = {}                     # (sym, day) -> 15 prior sessions
post_bars = {}                       # (sym, day) -> bars from 09:20
for tok in [r[0] for r in db.execute("select distinct instrument_token from candles where candle_time >= '2026-09-01'")]:
    sym = tok2sym.get(tok, str(tok))
    days = defaultdict(list)
    for row in db.execute("select candle_time, open, high, low, close, volume from candles where instrument_token=? "
                          "and candle_time >= '2025-11-15' order by candle_time", (tok,)):
        days[row[0][:10]].append(row)
    ds = sorted(days)
    daily = {d: (max(x[2] for x in days[d]), min(x[3] for x in days[d]), days[d][-1][4]) for d in ds}
    for n, d in enumerate(ds):
        if d < "2026-01-01" or n < 15:
            continue
        prev = ds[n - 14:n]
        trs, pc = [], daily[ds[n - 15]][2]
        for p in prev:
            h, l, c = daily[p]
            trs.append(max(h - l, abs(h - pc), abs(l - pc))); pc = c
        atr = sum(trs) / len(trs)
        bars = days[d]
        orb = [x for x in bars if x[0][11:16] < "09:20"]
        past = [sum(x[5] for x in days[p] if x[0][11:16] < "09:20") for p in prev]
        avgv = sum(past) / len(past)
        if avgv <= 0 or not orb:
            continue
        orvol = sum(x[5] for x in orb)
        rvol = orvol / avgv
        valid = len(orb) >= 5 and orb[-1][4] != orb[0][1]
        rec = dict(sym=sym, day=d, atr=atr, avgv=avgv, rvol=rvol, valid=valid,
                   or_bars=[[x[0][11:16], x[1], x[2], x[3], x[4], x[5]] for x in orb])
        if valid:
            oo, oc = orb[0][1], orb[-1][4]
            sgn = 1 if oc > oo else -1
            rec.update(dir=sgn, lvl=max(x[2] for x in orb) if sgn > 0 else min(x[3] for x in orb))
        rows_by_day[d].append(rec)
        if rvol >= NEAR:
            fifteen = ds[n - 15:n]
            base_inputs[(sym, d)] = [[p, daily[p][0], daily[p][1], daily[p][2],
                                      sum(x[5] for x in days[p] if x[0][11:16] < "09:20"),
                                      sum(1 for x in days[p] if x[0][11:16] < "09:20"),
                                      days[p][0][0][11:16], days[p][-1][0][11:16]] for p in fifteen]
            post_bars[(sym, d)] = [[x[0][11:16], x[1], x[2], x[3]] for x in bars[len(orb):] if x[0][11:16] <= "11:03"]

baselines, days_out, breakouts = [], [], []
n_armed = n_trig = 0
causal_keys = set()
for d in sorted(rows_by_day):
    recs = rows_by_day[d]
    near = sorted((r for r in recs if r["rvol"] >= NEAR), key=lambda r: r["sym"])
    in_play = sorted((r for r in recs if r["valid"] and r["rvol"] >= RV), key=lambda r: -r["rvol"])
    armed = in_play[:N]
    expected = []
    for rank, r in enumerate(armed, 1):
        n_armed += 1
        key = (r["sym"], d)
        sgn, lvl = r["dir"], r["lvl"]
        hit = None
        for hm, o, h, l in post_bars[key]:
            if hm > "11:00":
                break
            if (h >= lvl) if sgn > 0 else (l <= lvl):
                entry = max(lvl, o) if sgn > 0 else min(lvl, o)
                hit = [hm, entry, entry - sgn * K * r["atr"]]
                break
        t = REC.get(key)
        if hit is None:
            assert t is None, ("orb.json has a breakout the replay did not find", key)
        else:
            n_trig += 1; causal_keys.add(key)
            assert t is not None and t["ts"][11:16] == hit[0] and t["entry"] == hit[1], (key, hit, t and (t["ts"], t["entry"]))
            assert abs((t["entry"] - t["dir"] * t["r"]["atr0.35"]) - hit[2]) < 1e-9, key
            assert t["rvol"] == r["rvol"] and t["atr"] == r["atr"] and t["dir"] == sgn, key
        expected.append([r["sym"], "UP" if sgn > 0 else "DOWN", lvl, r["rvol"], rank])
        baselines.append([r["sym"], d, base_inputs[key], r["atr"], r["avgv"]])
        cut = post_bars[key]
        if hit is not None:
            cut = [b for b in cut if b[0] <= hit[0]]
        breakouts.append([r["sym"], d, "UP" if sgn > 0 else "DOWN", lvl, r["atr"], cut, hit])
    days_out.append({"day": d, "stocks": [[r["sym"], r["atr"], r["avgv"], r["or_bars"]] for r in near],
                     "expected": expected, "in_play": len(in_play)})

# The first backtest's selection, for the record (top 3 among stocks that broke out).
bt = 0
for d in rows_by_day:
    trig = sorted((r for r in rows_by_day[d] if r["valid"] and r["rvol"] >= RV and (r["sym"], d) in REC), key=lambda r: -r["rvol"])[:N]
    bt += len(trig)
summary = {"days": len(days_out), "armed": n_armed, "broke_out": n_trig, "first_backtest_candidates": bt,
           "rule": {"min_rvol": RV, "top_n": N, "stop_atr_mult": K}}

json.dump(baselines, open(f"{OUT}/baselines.json", "w"), separators=(",", ":"))
json.dump(days_out, open(f"{OUT}/days.json", "w"), separators=(",", ":"))
json.dump(breakouts, open(f"{OUT}/breakouts.json", "w"), separators=(",", ":"))
json.dump(summary, open(f"{OUT}/summary.json", "w"), indent=1)
print(summary)
for f in ("baselines.json", "days.json", "breakouts.json"):
    print(f, os.path.getsize(f"{OUT}/{f}") // 1024, "KB")
