"""Loss-control layer over ORB + funnel: entry gating (K losers, size cut, open-risk cap) + MTM hard day stop + day lock."""
from collections import defaultdict
from combo2 import orb_cands, fun_cands, SZ, intraday_charges
from daylock2 import prepare, net_at
KW = dict(OR=5, key="atr0.35_lock", rv=3.0, N=3)
_C = None
def cands():
    global _C
    if _C is None: _C = orb_cands(**KW) + fun_cands()
    return _C
def run(orb_risk=2500, fun_risks=(1000, 2500, 4500, 6000), capital=500000, dl=6000, slip=3e-4, max_low=2,
        max_losers=None, cut_after=None, cut_mult=0.5, open_risk_cap=None):
    by_day = defaultdict(list)
    for c in cands(): by_day[c["day"]].append(c)
    trades = []
    for day, cs in by_day.items():
        cs = sorted(cs, key=lambda c: c["ts"])
        open_until, closed, margin, nlow, openrisk = {}, [], [], 0, []
        for c in cs:
            now = c["ts"]
            if open_until.get(c["sym"], "") > now: continue
            done = [(x, p) for x, p in closed if x <= now]
            if -sum(p for x, p in done if p < 0) >= dl: continue
            losers = sum(p < 0 for x, p in done)
            if max_losers is not None and losers >= max_losers: continue
            if c["src"] == "FUN":
                risk = fun_risks[c["sc"]]
                if not risk or (c["sc"] == 0 and nlow >= max_low): continue
            else:
                risk = orb_risk
            base_risk = risk; cut = False
            if cut_after is not None and losers >= cut_after: risk *= cut_mult; cut = True
            if open_risk_cap is not None:
                live = sum(r for x, r in openrisk if x > now)
                risk = min(risk, open_risk_cap - live)
                if risk < 500: continue
            used = sum(m for x, m in margin if x > now)
            s = SZ.decide(entry_price=c["e"], stop_price=c["stop"], risk_cap_rupees=risk,
                          available_capital_rupees=capital - used, leverage_factor=5.0)
            if s.qty <= 0: continue
            q, e, xp = s.qty, c["e"], c["xp"]
            per = (xp - e) if c["up"] else (e - xp)
            bv, sv = (e*q, xp*q) if c["up"] else (xp*q, e*q)
            gross = per*q; chg = intraday_charges(bv, sv); net = gross - chg - slip*e*q
            open_until[c["sym"]] = c["xt"]; closed.append((c["xt"], gross)); margin.append((c["xt"], q*e/5))
            openrisk.append((c["xt"], q * abs(e - c["stop"])))
            nlow += c["src"] == "FUN" and c["sc"] == 0
            trades.append(dict(c, q=q, gross=gross, chg=chg, slip=slip*e*q, net=net, base_risk=base_risk, risk_used=risk, cut=cut))
    return prepare(trades)
def day_manage(trades, arm=3000, keep=0.4, hard=None, slip=3e-4):
    by_day = defaultdict(list)
    for x in trades: by_day[x["day"]].append(x)
    out = []
    for day, xs in by_day.items():
        mins = sorted({m for x in xs for m, _ in x["path"]} | {x["xt"][:16] for x in xs})
        peak = -1e18; stop_at = None
        for m in mins:
            eq = 0.0
            for x in xs:
                if x["ts"][:16] > m: continue
                if x["xt"][:16] <= m: eq += x["net"]; continue
                px = None
                for pm, c in x["path"]:
                    if pm <= m: px = c
                    else: break
                if px is not None: eq += net_at(x, px, slip)
            peak = max(peak, eq)
            if arm is not None and peak >= arm and eq <= peak * keep: stop_at = m; why = "DAY LOCK flatten"; break
            if hard is not None and eq <= -hard: stop_at = m; why = "HARD DAY STOP flatten"; break
        for x in xs:
            if stop_at is None or x["xt"][:16] <= stop_at: out.append(x); continue
            if x["ts"][:16] > stop_at: continue
            px = x["e"]
            for pm, c in x["path"]:
                if pm <= stop_at: px = c
            y = dict(x); y["net"] = net_at(x, px, slip); y["xt"] = stop_at + ":00"; y["xp"] = px; y["flat"] = True; y["flatwhy"] = why; out.append(y)
    return out
def stats(tr):
    d = defaultdict(float); m = defaultdict(float)
    for x in tr: d[x["day"]] += x["net"]; m[x["day"][:7]] += x["net"]
    ms = [m.get(f"2026-{i:02d}", 0) for i in range(1, 10)]
    red = sorted(v for v in d.values() if v <= 0); grn = [v for v in d.values() if v > 0]
    eq = pk = dd = 0
    for k in sorted(d): eq += d[k]; pk = max(pk, eq); dd = min(dd, eq - pk)
    return dict(mo=sum(ms)/9, gm=sum(v > 0 for v in ms), worst_mo=min(ms), months=ms, nred=len(red), gd=len(grn)/len(d),
                avg_red=sum(red)/len(red), worst_day=red[0], red_total=sum(red), p90red=red[int(len(red)*0.1)],
                avg_green=sum(grn)/len(grn), dd=dd, big_red=sum(v < -8000 for v in red))
def line(name, s):
    return (f"{name:42s} ₹{s['mo']:>6,.0f}/mo {s['gm']}/9 | RED: {s['nred']:2d} days avg ₹{s['avg_red']:>6,.0f} worst ₹{s['worst_day']:>7,.0f} "
            f"<-8k:{s['big_red']:2d} total ₹{s['red_total']/1000:>5.0f}k | green {s['gd']:.0%} avgG ₹{s['avg_green']:,.0f} | DD ₹{s['dd']:,.0f}")
