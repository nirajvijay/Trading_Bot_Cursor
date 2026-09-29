import csv, sys
from collections import defaultdict
from hybrid_v2 import run, jj
from redctl import day_manage, stats, run as rrun
OUT = sys.argv[1]; rows = []
def rec(group, label, f):
    r = dict(test=group, setting=label)
    for sl in (3e-4, 5e-4):
        tr = f(sl); s = stats(tr); j = jj(tr); b = int(round(sl*1e4))
        r.update({f"jan_sep_avg_{b}bps": round(s["mo"]), f"worst_month_{b}bps": round(min(s["months"])), f"jun_sep_avg_{b}bps": round(j["jsavg"]),
                  f"worst_day_{b}bps": round(s["worst_day"]), f"max_dd_{b}bps": round(s["dd"])})
    rows.append(r)
for house in (0.5, 1.0, 1.5, 2.0):
    for cap in (5000, 6000, 7000):
        for fa in (0.75, 1.0):
            rec("neighbourhood", f"house={house} cap={cap} free_at={fa}R", lambda sl: day_manage(run(slip=sl, H=10**9, house=house, open_cap=cap, cut_after=1, free_at=fa), hard=5000, slip=sl))
A = dict(open_cap=6000, cut_after=1, house=1.0)
for arm in (2500, 3000, 3500, 4000):
    for keep in (0.3, 0.35, 0.4, 0.45, 0.5):
        rec("day_lock", f"arm={arm} keep={keep}", lambda sl: day_manage(run(slip=sl, H=10**9, **A), arm=arm, keep=keep, hard=5000, slip=sl))
for H in (4000, 4500, 5000, 6000):
    rec("hard_day_stop", f"hard={H}", lambda sl: day_manage(run(slip=sl, H=10**9, **A), hard=H, slip=sl))
FUN = {"FUN0","FUN1","FUN2","FUN3"}
alts = {"ORB exempt from half-size": dict(cut_srcs=FUN), "skip score-0 funnel after a loss": dict(low_after_loss=False),
        "no entries after 12:00": dict(cutoff="12:00"), "same-direction-as-last-loser x0.5": dict(same_dir_mult=0.5),
        "capital 4L": dict(capital=400000), "ORB risk 3000": dict(orb_risk=3000), "funnel risk x1.25": dict(fun_risks=(1250, 3100, 5600, 7500)),
        "half-size -> x0.66": dict(cut_mult=0.66)}
for k, kw in alts.items():
    rec("rejected_or_alt", k, lambda sl: day_manage(run(slip=sl, H=10**9, **{**A, **kw}), hard=5000, slip=sl))
rec("rejected_or_alt", "remaining-budget sizing (H5k)", lambda sl: day_manage(run(slip=sl, H=5000, house=1.0), hard=5000, slip=sl))
with open(f"{OUT}/data/robustness_tests.csv", "w") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
print(len(rows))
