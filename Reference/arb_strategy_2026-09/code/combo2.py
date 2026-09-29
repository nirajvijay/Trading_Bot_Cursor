"""Two streams in one account: ORB stocks-in-play + quality-score funnel. Shared capital, per-symbol lock, daily loss cap."""
import json, sys, itertools
from collections import defaultdict
sys.path.insert(0, "/Users/nj/nifty-radar-project")
from engine_sizing import RiskCappedSizing
from charges import intraday_charges
import port4
from port4 import R, base, score
O = json.load(open("orb.json"))
SZ = RiskCappedSizing()
def orb_cands(OR=5, key="atr0.35_lock", rv=3.0, N=3):
    byd = defaultdict(list)
    for t in O:
        if t["OR"] == OR and t["rvol"] >= rv and key in t["x"]: byd[t["day"]].append(t)
    out = []
    for d, ts in byd.items():
        for t in sorted(ts, key=lambda t: -t["rvol"])[:N]:
            s = key.rsplit("_", 1)[0]
            out.append(dict(src="ORB", day=d, ts=t["ts"], sym=t["sym"], up=t["dir"] > 0, e=t["entry"],
                            stop=t["entry"] - t["dir"] * t["r"][s], rvol=t["rvol"], xp=t["x"][key][0], xt=t["x"][key][1], sc=None))
    return out
def fun_cands(rule="BEG_be1_l0.25_a2_f0.25"):
    out = []
    for t in R:
        if not base(t) or t["nth_sym"] > 0: continue
        out.append(dict(src="FUN", day=t["session_date"], ts=t["trigger_ts"], sym=t["tradingsymbol"], up=t["direction"] == "UP",
                        e=t["trigger_price"], stop=t["stop"], xp=t["x"][rule][0], xt=t["x"][rule][1], sc=min(score(t), 3)))
    return out
def run(orb_risk, fun_risks, orb_kw=None, capital=500000, dl=6000, slip=3e-4, max_low=2, use_fun=True, use_orb=True):
    C = (orb_cands(**(orb_kw or {})) if use_orb else []) + (fun_cands() if use_fun else [])
    by_day = defaultdict(list)
    for c in C: by_day[c["day"]].append(c)
    trades = []
    for day, cs in by_day.items():
        cs.sort(key=lambda c: c["ts"])
        open_until, closed, margin, nlow = {}, [], [], 0
        for c in cs:
            now = c["ts"]
            if open_until.get(c["sym"], "") > now: continue
            if -sum(p for x, p in closed if x <= now and p < 0) >= dl: continue
            if c["src"] == "FUN":
                risk = fun_risks[c["sc"]]
                if not risk or (c["sc"] == 0 and nlow >= max_low): continue
            else:
                risk = orb_risk
            used = sum(m for x, m in margin if x > now)
            s = SZ.decide(entry_price=c["e"], stop_price=c["stop"], risk_cap_rupees=risk,
                          available_capital_rupees=capital-used, leverage_factor=5.0)
            if s.qty <= 0: continue
            q, e, xp = s.qty, c["e"], c["xp"]
            per = (xp-e) if c["up"] else (e-xp)
            bv, sv = (e*q, xp*q) if c["up"] else (xp*q, e*q)
            gross = per*q; chg = intraday_charges(bv, sv); net = gross - chg - slip*e*q
            open_until[c["sym"]] = c["xt"]; closed.append((c["xt"], gross)); margin.append((c["xt"], q*e/5))
            nlow += c["src"] == "FUN" and c["sc"] == 0
            trades.append(dict(c, q=q, gross=gross, chg=chg, slip=slip*e*q, net=net))
    return trades
def summ(tr):
    m = defaultdict(float); d = defaultdict(float)
    for x in tr: m[x["day"][:7]] += x["net"]; d[x["day"]] += x["net"]
    ms = [m.get(f"2026-{i:02d}", 0) for i in range(1, 10)]
    eq = pk = dd = 0
    for k in sorted(d): eq += d[k]; pk = max(pk, eq); dd = min(dd, eq-pk)
    g = [v for v in d.values() if v > 0]; r = [v for v in d.values() if v <= 0]
    per = {}
    for k, v in d.items(): per.setdefault(k[:7], []).append(v > 0)
    return dict(mo=sum(ms)/9, maysep=sum(ms[4:])/5, months=ms, gm=sum(v > 0 for v in ms), worst=min(ms),
                gd=len(g)/len(d), days=len(d), avg_g=sum(g)/max(1, len(g)), avg_r=sum(r)/max(1, len(r)), dd=dd, wday=min(d.values()),
                per_month_green=[f"{sum(v)}/{len(v)}" for k, v in sorted(per.items())], n=len(tr),
                win=sum(x["net"] > 0 for x in tr)/len(tr))
def line(name, s):
    return (f"{name:46s} {s['mo']:+6.0f}/mo MaySep {s['maysep']:+6.0f} | " + " ".join(f"{v/1000:+.0f}" for v in s['months']) +
            f" | {s['gm']}/9 | GREEN DAYS {s['gd']:.0%} ({s['days']}d) avgG {s['avg_g']:+.0f} avgR {s['avg_r']:+.0f} | win {s['win']:.0%} DD {s['dd']:+.0f} n/mo {s['n']/9:.0f}")
