"""Quality-score sizing: score from robust features -> risk. Engine gates, shared capital, charges, slippage."""
import itertools
from collections import defaultdict
import port3
from port3 import R, base, vx, SZ, intraday_charges
seen = defaultdict(int)
for t in sorted(R, key=lambda t: t["trigger_ts"]):
    k = (t["session_date"], t["tradingsymbol"]); t["nth_sym"] = seen[k]; seen[k] += 1
def score(t, mth=-0.2):
    m = t.get("align_mkt", 9); s = t.get("align_sec")
    sc = 0
    sc += m < mth
    sc += (m < mth) and (s is not None and s > 0)
    sc += vx(t) >= 6
    sc += t["trigger_ts"][11:16] < "10:30"
    return sc
def sim(risk_by_score, rule, skip_repeat=True, max_low=2, capital=500000, dl=6000, slip=3e-4, mth=-0.2):
    by_day = defaultdict(list)
    for t in sorted(R, key=lambda t: t["trigger_ts"]):
        if not base(t) or (skip_repeat and t["nth_sym"] > 0): continue
        sc = min(score(t, mth), len(risk_by_score) - 1)
        if not risk_by_score[sc]: continue
        by_day[t["session_date"]].append((t, sc))
    trades = []
    for day, ts in by_day.items():
        open_until, closed, margin, nlow = {}, [], [], 0
        for t, sc in ts:
            now = t["trigger_ts"]
            if open_until.get(t["tradingsymbol"], "") > now: continue
            if -sum(p for x, p in closed if x <= now and p < 0) >= dl: continue
            if sc == 0 and max_low and nlow >= max_low: continue
            used = sum(m for x, m in margin if x > now)
            s = SZ.decide(entry_price=t["trigger_price"], stop_price=t["stop"], risk_cap_rupees=risk_by_score[sc],
                          available_capital_rupees=capital-used, leverage_factor=5.0)
            if s.qty <= 0: continue
            q, e = s.qty, t["trigger_price"]; xp, xt = t["x"][rule]
            up = t["direction"] == "UP"; per = (xp-e) if up else (e-xp)
            bv, sv = (e*q, xp*q) if up else (xp*q, e*q)
            gross = per*q; chg = intraday_charges(bv, sv); net = gross - chg - slip*e*q
            open_until[t["tradingsymbol"]] = xt; closed.append((xt, gross)); margin.append((xt, q*e/5)); nlow += sc == 0
            trades.append(dict(t=t, tier=sc, day=day, q=q, xp=xp, xt=xt, gross=gross, chg=chg, slip=slip*e*q, net=net))
    return trades
