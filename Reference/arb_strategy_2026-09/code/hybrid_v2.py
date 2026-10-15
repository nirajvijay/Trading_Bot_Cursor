"""Hybrid loss control: size every new trade from the day's REMAINING loss budget (prop-desk style).

budget = H + day_MTM_now - live_open_risk  (live risk of a trade = 0 once it has closed a bar >= +1R, since the lock is then at +0.25R)
risk   = min(base_risk * m, budget - reserve)   ; skip if < min_risk
Optional: extra cut after losers, low-score gating when the day is red, no-new-entries-after time when red.
Then MTM hard day stop at -H and day lock (arm/keep) via redctl.day_manage.
"""
from collections import defaultdict
from redctl import cands, SZ, intraday_charges, day_manage, stats
from daylock2 import prepare, net_at, closes, SYM2TOK

def _path(c):
    tok = SYM2TOK.get(c["sym"]) or int(c["sym"])
    return [(m, px) for m, px in closes(tok, c["day"]) if c["ts"][:16] <= m < c["xt"][:16]]

def run(orb_risk=2500, fun_risks=(1000, 2500, 4500, 6000), capital=500000, slip=3e-4, max_low=2,
        H=5000, reserve=0, min_risk=500, house=1.0, open_cap=None, cut_after=None, cut_mult=0.5,
        low_needs_green=False, red_cutoff=None, scale=1.0, max_losers=None,
        cut_srcs=None, low_after_loss=True, same_dir_mult=1.0, cutoff=None, orb_after_loss=1.0, free_at=1.0):
    by_day = defaultdict(list)
    for c in cands(): by_day[c["day"]].append(c)
    trades = []
    for day, cs in by_day.items():
        cs = sorted(cs, key=lambda c: c["ts"])
        open_until, taken, nlow = {}, [], 0
        for c in cs:
            now = c["ts"]; nm = now[:16]
            if open_until.get(c["sym"], "") > now: continue
            # day state at 'now' (1m close of the previous minute for open trades)
            if cutoff and now[11:16] >= cutoff: continue
            mtm = 0.0; live = 0.0; losers = 0; used = 0.0; last_loser = None
            for x in taken:
                if x["xt"][:16] <= nm:
                    mtm += x["net"]; losers += x["net"] < 0
                    if x["net"] < 0 and (last_loser is None or x["xt"] > last_loser["xt"]): last_loser = x
                    continue
                used += x["q"] * x["e"] / 5
                px = None; hit1r = False
                for pm, cl in x["path"]:
                    if pm >= nm or free_at is None: break
                    px = cl
                    if (cl - x["e"]) * (1 if x["up"] else -1) >= free_at * x["r1"]: hit1r = True
                if px is not None: mtm += net_at(x, px, slip)
                if not hit1r: live += x["q"] * x["r1"]
            if max_losers is not None and losers >= max_losers: continue
            if c["src"] == "FUN":
                base = fun_risks[c["sc"]]
                if not base or (c["sc"] == 0 and nlow >= max_low): continue
                if low_needs_green and c["sc"] == 0 and mtm < 0: continue
                if not low_after_loss and c["sc"] == 0 and losers: continue
            else:
                base = orb_risk
            base *= scale
            if red_cutoff and mtm < 0 and now[11:16] >= red_cutoff: continue
            key = c["src"] + (str(c["sc"]) if c["sc"] is not None else "")
            if cut_after is not None and losers >= cut_after and (cut_srcs is None or key in cut_srcs): base *= cut_mult
            if last_loser is not None and last_loser["up"] == c["up"]: base *= same_dir_mult
            if losers and c["src"] == "ORB": base *= orb_after_loss
            budget = H + (mtm if mtm < 0 else house * mtm) - live - reserve
            risk = min(base, budget)
            if open_cap is not None: risk = min(risk, open_cap + max(0, mtm) * house - live)
            if risk < min_risk: continue
            s = SZ.decide(entry_price=c["e"], stop_price=c["stop"], risk_cap_rupees=risk,
                          available_capital_rupees=capital - used, leverage_factor=5.0)
            if s.qty <= 0: continue
            q, e, xp = s.qty, c["e"], c["xp"]
            per = (xp - e) if c["up"] else (e - xp)
            bv, sv = (e*q, xp*q) if c["up"] else (xp*q, e*q)
            gross = per*q; chg = intraday_charges(bv, sv); net = gross - chg - slip*e*q
            x = dict(c, q=q, gross=gross, chg=chg, slip=slip*e*q, net=net, base_risk=base, risk_used=risk,
                     r1=abs(e - c["stop"]), mtm_at_entry=mtm, live_at_entry=live, cut=bool(cut_after is not None and losers >= cut_after))
            x["path"] = _path(x)
            open_until[c["sym"]] = c["xt"]; taken.append(x); trades.append(x)
            nlow += c["src"] == "FUN" and c["sc"] == 0
    return trades

def full(slip=3e-4, arm=3000, keep=0.4, hard=5000, **kw):
    return day_manage(run(slip=slip, H=kw.pop("H", hard), **kw), arm=arm, keep=keep, hard=hard, slip=slip)

def jj(tr):
    """Jun-Sep and Jan-Apr / May-Sep split summary."""
    d = defaultdict(float); m = defaultdict(float)
    for x in tr: d[x["day"]] += x["net"]; m[x["day"][:7]] += x["net"]
    js = [m.get(f"2026-{i:02d}", 0) for i in (6, 7, 8, 9)]
    jd = {k: v for k, v in d.items() if k >= "2026-06"}
    red = sorted(v for v in jd.values() if v <= 0)
    return dict(js=js, jsavg=sum(js)/4, jgreen=sum(v > 0 for v in jd.values()), jdays=len(jd),
                javg_red=sum(red)/max(1, len(red)), jworst=red[0] if red else 0, jbig=sum(v < -6000 for v in red),
                h1=sum(m.get(f"2026-{i:02d}", 0) for i in range(1, 5))/4, h2=sum(m.get(f"2026-{i:02d}", 0) for i in range(5, 10))/5)
