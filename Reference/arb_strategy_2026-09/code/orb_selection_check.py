"""Does the ORB 'top 3' have to know the future?  Run from this research folder.

combo2.orb_cands (the reported backtest) ranks by RVOL among the stocks in orb.json,
and orb.json only holds stock-days that DID break out by 11:00.  At 09:20 nobody
knows which stocks will break out, so that ranking cannot be followed live.

This script rebuilds every 2026 stock-day's 09:20 state (broke out or not), then
runs the full ARB backtest with different ways of choosing the ORB stocks:

  BT   as reported: top 3 by RVOL among stocks that broke out (uses the future)
  A    rank at 09:20 among RVOL >= 3, arm the top 3, trade the ones that break out
  A4.. arm the top N at 09:20, trade the first 3 of them to break out
  B    arm every RVOL >= 3 stock, trade the first 3 (or 4) to break out
  ALL  arm every RVOL >= 3 stock, trade every breakout

A is what the live detector does (repo orb_rules.rank_ranges).
"""
import json, sqlite3
from collections import defaultdict
import redctl, hybrid_v2
from combo2 import O, fun_cands
from redctl import day_manage, stats

KEY = "atr0.35_lock"
db = sqlite3.connect("file:data/kite_12m.db?mode=ro", uri=True)
ins = sqlite3.connect("data/nifty50_instruments.db")
tok2sym = dict((t, s) for s, t in ins.execute("select tradingsymbol, instrument_token from nifty50_instruments"))

# Every stock-day's opening range at 09:20, with orb.py's arithmetic.
ARMS = []
for tok in [r[0] for r in db.execute("select distinct instrument_token from candles where candle_time >= '2026-09-01'")]:
    days = defaultdict(list)
    for row in db.execute("select candle_time, open, high, low, close, volume from candles where instrument_token=? "
                          "and candle_time >= '2025-11-15' order by candle_time", (tok,)):
        days[row[0][:10]].append(row)
    ds = sorted(days)
    for n, d in enumerate(ds):
        if d < "2026-01-01" or n < 15:
            continue
        prev = ds[n - 14:n]
        bars = days[d]
        orb = [x for x in bars if x[0][11:16] < "09:20"]
        if len(orb) < 5:
            continue
        past = [sum(x[5] for x in days[p] if x[0][11:16] < "09:20") for p in prev]
        avgv = sum(past) / len(past)
        if avgv <= 0 or orb[-1][4] == orb[0][1]:
            continue
        sgn = 1 if orb[-1][4] > orb[0][1] else -1
        lvl = max(x[2] for x in orb) if sgn > 0 else min(x[3] for x in orb)
        trig = None
        for x in bars[len(orb):]:
            if x[0][11:16] > "11:00":
                break
            if (x[2] >= lvl) if sgn > 0 else (x[3] <= lvl):
                trig = x[0]; break
        ARMS.append(dict(sym=tok2sym.get(tok, str(tok)), day=d, rvol=sum(x[5] for x in orb) / avgv, trig_ts=trig))

rec = {(t["sym"], t["day"]): t for t in O if t["OR"] == 5 and KEY in t["x"]}
FUN = fun_cands()
by_day = defaultdict(list)
for a in ARMS:
    if a["rvol"] >= 3.0:
        by_day[a["day"]].append(a)
in_play = sum(len(v) for v in by_day.values())
print(f"stock-days with RVOL >= 3 at 09:20: {in_play}; of those that broke out by 11:00: "
      f"{sum(1 for v in by_day.values() for a in v if a['trig_ts'])}")


def cand(t):
    return dict(src="ORB", day=t["day"], ts=t["ts"], sym=t["sym"], up=t["dir"] > 0, e=t["entry"],
                stop=t["entry"] - t["dir"] * t["r"]["atr0.35"], rvol=t["rvol"], xp=t["x"][KEY][0], xt=t["x"][KEY][1], sc=None)


def select(mode, arm_n=None, take=3):
    out = []
    for d, arms in by_day.items():
        ranked = sorted(arms, key=lambda a: -a["rvol"])
        if mode == "BT":
            trig = [a for a in ranked if (a["sym"], d) in rec][:3]
        else:
            pool = ranked if arm_n is None else ranked[:arm_n]
            trig = sorted((a for a in pool if (a["sym"], d) in rec), key=lambda a: (a["trig_ts"], -a["rvol"]))
            if take is not None:
                trig = trig[:take]
        out += [cand(rec[(a["sym"], d)]) for a in trig]
    return out


def run(orb):
    redctl._C = orb + FUN
    tr = day_manage(hybrid_v2.run(H=10**9, house=1.0, open_cap=6000, cut_after=1), arm=3000, keep=0.4, hard=5000, slip=3e-4)
    o = [x for x in tr if x["src"] == "ORB"]
    return stats(tr), len(o), sum(x["net"] for x in o), len(tr)


for name, kw in [("BT   as reported (uses the future)", dict(mode="BT")),
                 ("A    arm top 3 at 09:20  <- live", dict(mode="C", arm_n=3, take=None)),
                 ("A4   arm top 4, first 3", dict(mode="C", arm_n=4, take=3)),
                 ("A5   arm top 5, first 3", dict(mode="C", arm_n=5, take=3)),
                 ("A6   arm top 6, first 3", dict(mode="C", arm_n=6, take=3)),
                 ("B    arm all, first 3", dict(mode="C", arm_n=None, take=3)),
                 ("B4   arm all, first 4", dict(mode="C", arm_n=None, take=4)),
                 ("ALL  arm all, take all", dict(mode="C", arm_n=None, take=None))]:
    orb = select(**kw)
    s, n_orb, orb_net, n = run(orb)
    print(f"{name:36s} ORB taken {n_orb:4d}, net {orb_net/9:>7,.0f}/mo | ALL {s['mo']:>7,.0f}/mo, {s['gm']}/9 green, "
          f"worst month {s['worst_mo']:>7,.0f} | red days {s['nred']:3d}, avg {s['avg_red']:>6,.0f}, worst {s['worst_day']:>7,.0f} | "
          f"green days {s['gd']:.0%} | drawdown {s['dd']:>8,.0f} | trades {n}")
    print("      months Jan-Sep (thousands):", " ".join(f"{v/1000:+.1f}" for v in s["months"]))
