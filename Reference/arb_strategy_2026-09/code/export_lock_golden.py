"""Export golden fixtures for the live lock rule and day replay (repo tests/fixtures/lock_golden/).

Run from this research folder:  PYTHONPATH=. python3 export_lock_golden.py <repo>/tests/fixtures/lock_golden

For every 2026 ARB candidate (funnel triggers that pass the filters, and the ORB
breakouts of the stocks armed at 09:20) it writes the 1-minute bars of the trade
and two exits:

  research  the backtest's own exit (trail_research.walk / orb.walk).  The walks
            below are re-typed from those files and asserted against the stored
            exits (trail_research.json, orb.json) before anything is written.
  live      the same walk with the three things a live engine must do differently:
            every stop on the instrument's tick grid, away from the market (the
            backtest left ORB stops off the grid, and rounded a short's lock stop
            toward the market); the lock thresholds compared exactly, not in
            floating point; and the cap 'one tick inside the close' using the real
            tick (orb.py used a flat 0.05).

It then runs the whole backtest (hybrid_v2.run + redctl.day_manage, unchanged) on
the live exits and writes each day's trades and halt.  This file does not import
anything from the repo's lock rule: the fixture is an independent second opinion.
"""
import gzip, json, os, sqlite3, sys
from collections import defaultdict
from datetime import datetime, time
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

import redctl, hybrid_v2
from port4 import R, base, score, vx
from port3 import sp
from combo2 import O
from redctl import day_manage, stats
from daylock2 import SYM2TOK

OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
REPO = os.path.abspath(os.path.join(OUT, "..", "..", ".."))
KEY, RULE, SLIP = "atr0.35_lock", "BEG_be1_l0.25_a2_f0.25", 3e-4
SQUARE_OFF = time(14, 50)

db = sqlite3.connect("file:data/kite_12m.db?mode=ro", uri=True)
rep = sqlite3.connect("r12.db")
ins = sqlite3.connect("data/nifty50_instruments.db")
ARM_TICK = {r[0]: r[1] for r in rep.execute("select setup_id, tick_size from live_continuation_arms")}
SYM_TICK = {s: json.loads(d)["tick_size"] for s, d in ins.execute("select tradingsymbol, instrument_data from nifty50_instruments")}

_bars = {}
def day_bars(sym, day):
    tok = SYM2TOK.get(sym) or int(sym)
    if tok not in _bars:
        days = defaultdict(list)
        for row in db.execute("select candle_time, open, high, low, close from candles where instrument_token=? "
                              "and candle_time >= '2026' order by candle_time", (tok,)):
            days[row[0][:10]].append(row)
        _bars[tok] = days
    return _bars[tok][day]


def tick_path(o, h, l, c):
    return (o, l, h, c) if c >= o else (o, h, l, c)


def grid(x, tick, down):
    q = (Decimal(str(x)) / Decimal(str(tick))).to_integral_value(rounding=ROUND_FLOOR if down else ROUND_CEILING)
    return float(q * Decimal(str(tick)))


# ---------------------------------------------------------------- research walks (re-typed, asserted below)
def research_fun(t, stop0, tick, bars):
    long = t["direction"] == "UP"; sgn = 1 if long else -1
    e = t["trigger_price"]; r = abs(e - stop0)
    trig_min = datetime.fromisoformat(t["trigger_ts"]).replace(second=0)
    stop, peak = stop0, e
    def rnd(x):
        q = x / tick
        return round((int(q + 1e-9) if long else -int(-q + 1e-9)) * tick, 4)
    for ct, o, h, l, c in bars:
        dt = datetime.fromisoformat(ct)
        if dt < trig_min: continue
        if dt.time() >= SQUARE_OFF: return o, ct
        path = tick_path(o, h, l, c)
        start = 0
        if dt == trig_min:
            start = next(i for i, p in enumerate(path) if (p >= e if long else p <= e)) + 1
        for i in range(start, 4):
            p = path[i]
            if (p <= stop) if long else (p >= stop):
                return ((min(p, stop) if long else max(p, stop)) if i == 0 else stop), ct
            peak = max(peak, p) if long else min(peak, p)
        gain_r = sgn * (peak - e) / r
        if gain_r >= 1:
            b = rnd(e + sgn * 0.25 * r)
            b = min(b, rnd(c - tick)) if long else max(b, rnd(c + tick))
            if (b > stop) if long else (b < stop): stop = b
        if gain_r >= 2:
            cand = rnd(e + sgn * 0.25 * abs(peak - e))
            cand = min(cand, rnd(c - tick)) if long else max(cand, rnd(c + tick))
            if (cand > stop) if long else (cand < stop): stop = cand
    return bars[-1][4], bars[-1][0]


def research_orb(bars, i0, sgn, entry, stop0):
    r = abs(entry - stop0)
    stop, peak = stop0, entry
    for j in range(i0, len(bars)):
        ct, o, h, l, c = bars[j]
        if ct[11:16] >= "14:50": return o, ct
        if j == i0:
            if (l <= stop) if sgn > 0 else (h >= stop): return stop, ct
            path = (c,)
        else:
            path = tick_path(o, h, l, c)
        for p in path:
            if (p <= stop) if sgn > 0 else (p >= stop):
                return (p if (p - stop) * sgn < 0 and j > i0 and p == o else stop), ct
            peak = max(peak, p) if sgn > 0 else min(peak, p)
        g = sgn * (peak - entry) / r
        cand = None
        if g >= 2: cand = entry + sgn * max(0.25 * abs(peak - entry), 0.25 * r)
        elif g >= 1: cand = entry + sgn * 0.25 * r
        if cand is not None:
            cand = min(cand, c - 0.05) if sgn > 0 else max(cand, c + 0.05)
            if (cand > stop) if sgn > 0 else (cand < stop): stop = cand
    return bars[-1][4], bars[-1][0]


# ---------------------------------------------------------------- the live walk (one rule for both)
def live_walk(src, long, e, stop0, tick, bars):
    """bars[0] is the entry bar.  Returns (exit price, exit bar time, reason)."""
    D = Decimal
    sgn = 1 if long else -1
    ed, rd, td = D(str(e)), abs(D(str(e)) - D(str(stop0))), D(str(tick))
    stop, peak = stop0, ed
    for j, (ct, o, h, l, c) in enumerate(bars):
        if ct[11:16] >= "14:50":
            return o, ct, "eod"
        why = "stop" if stop == stop0 else "lock"
        path = tick_path(o, h, l, c)
        if j == 0 and src == "ORB":
            if (l <= stop) if long else (h >= stop):
                return stop, ct, why
            pts = []
        elif j == 0:
            start = next(i for i, p in enumerate(path) if (p >= e if long else p <= e)) + 1
            pts = [(i, path[i]) for i in range(start, 4)]
        else:
            pts = list(enumerate(path))
        for i, p in pts:
            if (p <= stop) if long else (p >= stop):
                return ((min(p, stop) if long else max(p, stop)) if i == 0 else stop), ct, why
        best = D(str(c if j == 0 else (h if long else l)))
        if (best > peak) if long else (best < peak):
            peak = best
        gain = (peak - ed) * sgn
        if gain >= rd:
            locked = max(D("0.25") * gain, D("0.25") * rd) if gain >= 2 * rd else D("0.25") * rd
            lvl = ed + sgn * locked
            cap = D(str(c)) - sgn * td
            cand = grid(min(lvl, cap) if long else max(lvl, cap), tick, down=long)
            if (cand > stop) if long else (cand < stop):
                stop = cand
    return bars[-1][4], bars[-1][0], "end_of_data"


# ---------------------------------------------------------------- candidates
rows, FUN_C, ORB_C = [], [], []
n_same = 0

FUN = [t for t in sorted(R, key=lambda t: t["trigger_ts"]) if base(t) and t["nth_sym"] == 0]
for t in FUN:
    tick = ARM_TICK[t["setup_id"]]
    bars = day_bars(t["tradingsymbol"], t["session_date"])
    rx = research_fun(t, t["stop"], tick, bars)
    assert [rx[0], rx[1]] == list(t["x"][RULE]), ("funnel walk differs from trail_research.json", t["setup_id"])
    trig_min = datetime.fromisoformat(t["trigger_ts"]).replace(second=0)
    tail = [b for b in bars if datetime.fromisoformat(b[0]) >= trig_min]
    long = t["direction"] == "UP"
    lx = live_walk("FUN", long, t["trigger_price"], t["stop"], tick, tail)
    same = lx[0] == rx[0] and lx[1] == rx[1]
    n_same += same
    last = max(lx[1], rx[1])
    rows.append([t["setup_id"], "FUN", t["session_date"], t["tradingsymbol"], t["direction"], t["trigger_ts"],
                 t["trigger_price"], t["stop"], tick, min(score(t), 3), vx(t), sp(t), t.get("align_mkt"), t.get("align_sec"),
                 rx[0], rx[1][:16], lx[0], lx[1][:16], lx[2],
                 [[b[0][11:16], b[1], b[2], b[3], b[4]] for b in tail if b[0] <= last]])
    FUN_C.append(dict(src="FUN", day=t["session_date"], ts=t["trigger_ts"], sym=t["tradingsymbol"], up=long,
                      e=t["trigger_price"], stop=t["stop"], xp=lx[0], xt=lx[1], sc=min(score(t), 3), id=t["setup_id"]))

brk = json.load(open(f"{REPO}/tests/fixtures/orb_golden/breakouts.json"))
rec = {(t["sym"], t["day"]): t for t in O if t["OR"] == 5 and KEY in t["x"]}
for row in brk:
    sym, day = row[0], row[1]
    if row[-1] is None:
        continue
    t = rec[(sym, day)]
    sgn, tick = t["dir"], SYM_TICK[sym]
    rest = [x for x in day_bars(sym, day) if x[0][11:16] >= "09:20"]
    idx = next(i for i, x in enumerate(rest) if x[0] == t["ts"])
    stop_raw = t["entry"] - sgn * t["r"]["atr0.35"]
    rx = research_orb(rest, idx, sgn, t["entry"], stop_raw)
    assert [rx[0], rx[1]] == list(t["x"][KEY]), ("ORB walk differs from orb.json", sym, day)
    stop_grid = grid(stop_raw, tick, down=sgn > 0)      # what the live detector sends (PR B D8)
    tail = rest[idx:]
    lx = live_walk("ORB", sgn > 0, t["entry"], stop_grid, tick, tail)
    same = lx[0] == rx[0] and lx[1] == rx[1]
    n_same += same
    last = max(lx[1], rx[1])
    cid = f"{sym}|{day}|ORB"
    rows.append([cid, "ORB", day, sym, "UP" if sgn > 0 else "DOWN", t["ts"], t["entry"], stop_grid, tick, None,
                 None, None, None, None, rx[0], rx[1][:16], lx[0], lx[1][:16], lx[2],
                 [[b[0][11:16], b[1], b[2], b[3], b[4]] for b in tail if b[0] <= last]])
    ORB_C.append(dict(src="ORB", day=day, ts=t["ts"], sym=sym, up=sgn > 0, e=t["entry"], stop=stop_grid,
                      rvol=t["rvol"], xp=lx[0], xt=lx[1], sc=None, id=cid))

# ---------------------------------------------------------------- the whole system on the live exits
redctl._C = ORB_C + FUN_C
taken = hybrid_v2.run(H=10**9, house=1.0, open_cap=6000, cut_after=1, slip=SLIP)
final = day_manage(taken, arm=3000, keep=0.4, hard=5000, slip=SLIP)
days = defaultdict(lambda: {"taken": [], "halt": None})
for y in sorted(final, key=lambda y: (y["ts"], y["id"])):
    d = days[y["day"]]
    d["taken"].append([y["id"], y["q"], y["risk_used"], y["xp"], y["xt"][:16], y["net"], bool(y.get("flat"))])
    if y.get("flat"):
        d["halt"] = [y["xt"][:16], "day_lock" if y["flatwhy"].startswith("DAY LOCK") else "hard_day_stop"]
def book(slip):
    tr = final if slip == SLIP else day_manage(
        hybrid_v2.run(H=10**9, house=1.0, open_cap=6000, cut_after=1, slip=slip), arm=3000, keep=0.4, hard=5000, slip=slip)
    s = stats(tr)
    js = s["months"][5:]
    return {"net_per_month": round(s["mo"], 2), "jun_sep_per_month": round(sum(js) / 4, 2),
            "months": [round(m, 2) for m in s["months"]], "green_months": s["gm"],
            "worst_month": round(s["worst_mo"], 2), "green_days": round(s["gd"], 4), "worst_day": round(s["worst_day"], 2),
            "days_below_minus_6000": sum(1 for v in _day_net(tr).values() if v < -6000),
            "max_drawdown": round(s["dd"], 2), "trades": len(tr), "orb_trades": sum(1 for x in tr if x["src"] == "ORB")}

def _day_net(tr):
    d = defaultdict(float)
    for x in tr: d[x["day"]] += x["net"]
    return d

summary = {
    "candidates": len(rows), "funnel": len(FUN), "orb": len(ORB_C),
    "live_exit_equals_research": n_same,
    "trades_after_day_controls": len(final), "days": len(days),
    "halts": sum(1 for d in days.values() if d["halt"]),
    "bars": sum(len(r[-1]) for r in rows),
    "backtest_3bps": book(3e-4), "backtest_5bps": book(5e-4),
    "rule": "live lock rule (grid stops, exact thresholds), ORB armed top 3 at 09:20, hybrid_v2.run(H=1e9, house=1, open_cap=6000, cut_after=1) + day_manage(3000, 0.4, 5000)",
}
with gzip.open(f"{OUT}/lock_golden.json.gz", "wt", compresslevel=9) as fh:
    json.dump({"summary": summary, "candidates": rows, "days": days}, fh, separators=(",", ":"))
print(json.dumps(summary, indent=1))
print("fixture bytes:", os.path.getsize(f"{OUT}/lock_golden.json.gz"))
