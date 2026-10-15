"""Tiered sizing blend: each trade gets a tier -> (risk, exit rule). Engine gates, shared capital, charges, slippage."""
import json, sys, itertools
from collections import defaultdict
sys.path.insert(0, "/Users/nj/nifty-radar-project")
from engine_sizing import RiskCappedSizing
from charges import intraday_charges
R = json.load(open("trail_research.json"))
for f in ("features.json", "features2.json"):
    FE = json.load(open(f))
    for t in R: t.update(FE.get(t["setup_id"], {}))
vx = lambda t: t["breakout_volume"]/t["avg_prior_3"] if t["avg_prior_3"] else 0
sp = lambda t: t["r"]/t["trigger_price"]*100
base = lambda t: vx(t) >= 2 and t["trigger_ts"][11:16] < "13:00" and sp(t) >= .25
SZ = RiskCappedSizing()
def tier(t, mth, sec_th):
    if not base(t): return None
    m = t.get("align_mkt", 9)
    if m < mth:
        s = t.get("align_sec")
        return 2 if (s is not None and s > sec_th) else 1
    return 0
def sim(risks, rules, mth=-0.1, sec_th=0.0, capital=500000, dl=6000, slip=3e-4, max_t0=None, max_total=None, months=None):
    by_day = defaultdict(list)
    for t in sorted(R, key=lambda t: t["trigger_ts"]):
        k = tier(t, mth, sec_th)
        if k is None or not risks[k]: continue
        by_day[t["session_date"]].append((t, k))
    trades = []
    for day, ts in by_day.items():
        open_until, closed, margin, n0 = {}, [], [], 0
        for t, k in ts:
            now = t["trigger_ts"]
            if open_until.get(t["tradingsymbol"], "") > now: continue
            if -sum(p for x, p in closed if x <= now and p < 0) >= dl: continue
            if max_total and len(closed) >= max_total: continue
            if k == 0 and max_t0 and n0 >= max_t0: continue
            used = sum(m for x, m in margin if x > now)
            s = SZ.decide(entry_price=t["trigger_price"], stop_price=t["stop"], risk_cap_rupees=risks[k],
                          available_capital_rupees=capital-used, leverage_factor=5.0)
            if s.qty <= 0: continue
            q, e = s.qty, t["trigger_price"]; xp, xt = t["x"][rules[k]]
            up = t["direction"] == "UP"
            per = (xp-e) if up else (e-xp)
            bv, sv = (e*q, xp*q) if up else (xp*q, e*q)
            gross = per*q; chg = intraday_charges(bv, sv); net = gross - chg - slip*e*q
            open_until[t["tradingsymbol"]] = xt
            closed.append((xt, gross)); margin.append((xt, q*e/5)); n0 += k == 0
            trades.append(dict(t=t, tier=k, day=day, q=q, xp=xp, xt=xt, gross=gross, chg=chg, slip=slip*e*q, net=net))
    return trades
def summary(trades):
    m = defaultdict(float); d = defaultdict(float)
    for x in trades: m[x["day"][:7]] += x["net"]; d[x["day"]] += x["net"]
    eq = pk = dd = 0
    for k in sorted(d): eq += d[k]; pk = max(pk, eq); dd = min(dd, eq-pk)
    ms = [m.get(f"2026-{i:02d}", 0) for i in range(1, 10)]
    return dict(n=len(trades), mo=sum(ms)/9, maysep=sum(ms[4:])/5, worst=min(ms), green_mo=sum(v > 0 for v in ms), months=ms,
                green_day=sum(v > 0 for v in d.values())/max(len(d), 1), days=len(d), dd=dd, wday=min(d.values()),
                win=sum(x["net"] > 0 for x in trades)/max(len(trades), 1))
