import csv, sys, datetime
from collections import defaultdict
from hybrid_v2 import run
from redctl import day_manage, stats, run as rrun, cands
OUT = sys.argv[1]
V = {"ARB": lambda sl: day_manage(run(slip=sl, H=10**9, house=1.0, open_cap=6000, cut_after=1), hard=5000, slip=sl),
     "FULL_LOSS_CONTROL": lambda sl: day_manage(rrun(slip=sl, cut_after=1, open_risk_cap=6000), hard=5000, slip=sl),
     "HARD_STOP_ONLY": lambda sl: day_manage(rrun(slip=sl), hard=5000, slip=sl),
     "NO_LOSS_CONTROL": lambda sl: day_manage(rrun(slip=sl), hard=None, slip=sl)}
mrows, drows, srows = [], [], []
for sl in (3e-4, 5e-4):
    for n, f in V.items():
        tr = f(sl); s = stats(tr); bps = int(round(sl*1e4))
        d = defaultdict(lambda: [0, 0.0, 0.0]); m = defaultdict(lambda: [0, 0.0, 0.0, 0.0])
        for x in tr:
            d[x["day"]][0] += 1; d[x["day"]][1] += x["gross"] if not x.get("flat") else x["net"] + x["chg"] + x["slip"]; d[x["day"]][2] += x["net"]
        for k, (nt, g, ne) in sorted(d.items()):
            drows.append(dict(version=n, slippage_bps=bps, date=k, trades=nt, pnl_after_charges=round(ne)))
            mm = m[k[:7]]; mm[0] += nt; mm[2] += ne; mm[1] += 1; mm[3] += ne > 0
        for k, (nt, nd, ne, ng) in sorted(m.items()):
            red = [v[2] for kk, v in d.items() if kk.startswith(k) and v[2] <= 0]
            mrows.append(dict(version=n, slippage_bps=bps, month=k, trades=nt, days=nd, green_days=int(ng), pnl_after_charges=round(ne),
                              worst_day=round(min(red)) if red else 0, avg_red_day=round(sum(red)/len(red)) if red else 0))
        ks = sorted(d); st = mx = 0
        for k in ks: st = st + 1 if d[k][2] <= 0 else 0; mx = max(mx, st)
        wk = defaultdict(float)
        for k in ks: wk[datetime.date.fromisoformat(k).isocalendar()[:2]] += d[k][2]
        jd = {k: v[2] for k, v in d.items() if k >= "2026-06"}; jr = [v for v in jd.values() if v <= 0]
        srows.append(dict(version=n, slippage_bps=bps, jan_sep_avg_month=round(s["mo"]), green_months=f"{s['gm']}/9", worst_month=round(s["worst_mo"]),
                          jun_sep_avg_month=round(sum(v for v in jd.values())/4), jun_sep_green_days=f"{sum(v>0 for v in jd.values())}/{len(jd)}",
                          green_day_pct=round(s["gd"]*100), avg_red_day=round(s["avg_red"]), worst_day=round(s["worst_day"]),
                          days_below_minus6k=sum(v < -6000 for v in (x[2] for x in d.values())), max_drawdown=round(s["dd"]),
                          longest_red_streak=mx, worst_week=round(min(wk.values())), trades_per_month=round(len(tr)/9)))
        if n == "ARB" and bps == 3:
            with open(f"{OUT}/data/arb_all_trades_2026_3bps.csv", "w") as fh:
                w = csv.writer(fh); w.writerow(["date","strategy","symbol","side","qty","entry_time","entry","initial_stop","exit_time","exit","risk_used","half_size","day_pnl_at_entry","live_open_risk_at_entry","flatten_reason","pnl_before_charges","charges","slippage","pnl_after_charges"])
                for x in sorted(tr, key=lambda x: x["ts"]):
                    e, q = x["e"], x["q"]; g = ((x["xp"]-e) if x["up"] else (e-x["xp"]))*q
                    from charges import intraday_charges
                    bv, sv = (e*q, x["xp"]*q) if x["up"] else (x["xp"]*q, e*q)
                    w.writerow([x["day"], "ORB" if x["src"]=="ORB" else f"FUNNEL_score{x['sc']}", x["sym"], "BUY" if x["up"] else "SELL", q, x["ts"][11:19], round(e,2), round(x["stop"],2),
                                x["xt"][11:16], round(x["xp"],2), round(x["risk_used"]), x["cut"], round(x["mtm_at_entry"]), round(x["live_at_entry"]), x.get("flatwhy",""),
                                round(g,2), round(intraday_charges(bv, sv),2), round(x["slip"],2), round(x["net"],2)])
for fn, rows in (("monthly_results.csv", mrows), ("daily_results.csv", drows), ("summary_by_version.csv", srows)):
    with open(f"{OUT}/data/{fn}", "w") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
with open(f"{OUT}/data/candidates_2026.csv", "w") as fh:
    w = csv.writer(fh); w.writerow(["date","source","score","symbol","side","signal_time","entry","initial_stop","exit_price_if_unmanaged","exit_time_if_unmanaged","orb_rvol"])
    for c in sorted(cands(), key=lambda c: c["ts"]):
        w.writerow([c["day"], c["src"], c["sc"] if c["sc"] is not None else "", c["sym"], "BUY" if c["up"] else "SELL", c["ts"][11:19], round(c["e"],2), round(c["stop"],2), round(c["xp"],2), c["xt"][11:16], round(c.get("rvol") or 0, 2) or ""])
for s in srows: print(s)
