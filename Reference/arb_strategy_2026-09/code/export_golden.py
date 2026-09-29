"""Export golden fixtures for the live ARB port (repo tests/fixtures/arb_golden/).

Run from this research folder:  python3 export_golden.py <repo>/tests/fixtures/arb_golden

funnel.json   every 2026 funnel trigger: inputs + expected filter pass and score
budget.json   every ARB candidate decision (final setting): inputs + taken risk or skip
days.json     every ARB day: per-minute net P&L path + expected halt minute and reason
context.json  sample triggers: compact 1m candles + expected market/sector alignment

The budget log replays hybrid_v2.run exactly (same loop, same arithmetic) and is
checked against hybrid_v2.run's own taken trades before anything is written.
"""
import json, os, sys, sqlite3
from collections import defaultdict
import port4
from port4 import R, base, score, vx
import hybrid_v2
from hybrid_v2 import cands, SZ, intraday_charges, net_at, _path
from redctl import day_manage

OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
SLIP = 3e-4

# ---------------------------------------------------------------- funnel.json
fun = []
for t in sorted(R, key=lambda t: t["trigger_ts"]):
    ok = bool(base(t)) and t["nth_sym"] == 0
    # [direction, trigger_ts, trigger_price, stop, breakout_volume, avg_prior_3,
    #  first_today, align_mkt, align_sec, expected_ok, expected_score]
    fun.append([t["direction"], t["trigger_ts"], t["trigger_price"], t["stop"],
                t["breakout_volume"], t["avg_prior_3"], t["nth_sym"] == 0,
                t.get("align_mkt"), t.get("align_sec"), ok, min(score(t), 3) if ok else None])
json.dump(fun, open(f"{OUT}/funnel.json", "w"), separators=(",", ":"))

# ---------------------------------------------------------------- budget.json
# Final setting: run(H=1e9, house=1.0, open_cap=6000, cut_after=1), defaults otherwise.
H, HOUSE, CAP, CUT_AFTER, CUT_MULT, MIN_RISK, CAPITAL, MAX_LOW, FREE = 10**9, 1.0, 6000, 1, 0.5, 500, 500000, 2, 1.0
ORB_RISK, FUN_RISKS = 2500, (1000, 2500, 4500, 6000)
by_day = defaultdict(list)
for c in cands(): by_day[c["day"]].append(c)
log, taken_all = [], []
for day, cs in by_day.items():
    cs = sorted(cs, key=lambda c: c["ts"])
    open_until, taken, nlow = {}, [], 0
    for c in cs:
        now = c["ts"]; nm = now[:16]
        if open_until.get(c["sym"], "") > now: continue
        mtm = 0.0; live = 0.0; losers = 0; used = 0.0
        for x in taken:
            if x["xt"][:16] <= nm:
                mtm += x["net"]; losers += x["net"] < 0; continue
            used += x["q"] * x["e"] / 5
            px = None; hit1r = False
            for pm, cl in x["path"]:
                if pm >= nm: break
                px = cl
                if (cl - x["e"]) * (1 if x["up"] else -1) >= FREE * x["r1"]: hit1r = True
            if px is not None: mtm += net_at(x, px, SLIP)
            if not hit1r: live += x["q"] * x["r1"]
        if c["src"] == "FUN":
            b = FUN_RISKS[c["sc"]]
            if c["sc"] == 0 and nlow >= MAX_LOW: continue
        else:
            b = ORB_RISK
        base_pre_cut = b
        if losers >= CUT_AFTER: b *= CUT_MULT
        budget = H + (mtm if mtm < 0 else HOUSE * mtm) - live
        risk = min(b, budget)
        risk = min(risk, CAP + max(0, mtm) * HOUSE - live)
        entry = {"day": day, "ts": now, "src": c["src"], "sc": c["sc"], "base": base_pre_cut,
                 "losers": losers, "mtm": mtm, "live": live}
        if risk < MIN_RISK:
            log.append(dict(entry, expected_risk=0.0)); continue
        s = SZ.decide(entry_price=c["e"], stop_price=c["stop"], risk_cap_rupees=risk,
                      available_capital_rupees=CAPITAL - used, leverage_factor=5.0)
        log.append(dict(entry, expected_risk=risk, qty=s.qty))
        if s.qty <= 0: continue
        q, e, xp = s.qty, c["e"], c["xp"]
        per = (xp - e) if c["up"] else (e - xp)
        bv, sv = (e*q, xp*q) if c["up"] else (xp*q, e*q)
        gross = per*q; chg = intraday_charges(bv, sv); net = gross - chg - SLIP*e*q
        x = dict(c, q=q, gross=gross, chg=chg, slip=SLIP*e*q, net=net, risk_used=risk, r1=abs(e - c["stop"]))
        x["path"] = _path(x)
        open_until[c["sym"]] = c["xt"]; taken.append(x); taken_all.append(x)
        nlow += c["src"] == "FUN" and c["sc"] == 0

ref = hybrid_v2.run(H=H, house=HOUSE, open_cap=CAP, cut_after=CUT_AFTER)
key = lambda x: (x["day"], x["ts"], x["sym"], x["q"], round(x["risk_used"], 6))
assert sorted(map(key, taken_all)) == sorted(map(key, ref)), "replay does not match hybrid_v2.run"
json.dump(log, open(f"{OUT}/budget.json", "w"), separators=(",", ":"))

# ---------------------------------------------------------------- days.json
final = day_manage(ref, arm=3000, keep=0.4, hard=5000, slip=SLIP)
halts = {}
for y in final:
    if y.get("flat"): halts[y["day"]] = (y["xt"][:16], y["flatwhy"])
days = []
by = defaultdict(list)
for x in ref: by[x["day"]].append(x)
for day, xs in sorted(by.items()):
    mins = sorted({m for x in xs for m, _ in x["path"]} | {x["xt"][:16] for x in xs})
    path = []
    for m in mins:
        eq = 0.0
        for x in xs:
            if x["ts"][:16] > m: continue
            if x["xt"][:16] <= m: eq += x["net"]; continue
            px = None
            for pm, c in x["path"]:
                if pm <= m: px = c
                else: break
            if px is not None: eq += net_at(x, px, SLIP)
        path.append([int(m[11:13]) * 60 + int(m[14:16]), round(eq, 4)])  # minute of day, net P&L
    # Same rule and order as redctl.day_manage, recorded whether or not a trade was open.
    peak, h = -1e18, None
    for m, (_, eq) in zip(mins, path):
        peak = max(peak, eq)
        if peak >= 3000 and eq <= peak * 0.4: h = (m, "DAY LOCK flatten"); break
        if eq <= -5000: h = (m, "HARD DAY STOP flatten"); break
    f = halts.get(day)
    assert f is None or (h is not None and f[0] == h[0][:16] and f[1] == h[1]), (day, f, h)
    days.append({"day": day, "path": path,
                 "halt_minute": int(h[0][11:13]) * 60 + int(h[0][14:16]) if h else None,
                 "halt_reason": ("day_lock" if "LOCK" in h[1] else "hard_day_stop") if h else None})
json.dump(days, open(f"{OUT}/days.json", "w"), separators=(",", ":"))

# ---------------------------------------------------------------- context.json
db = sqlite3.connect("file:data/kite_12m.db?mode=ro", uri=True)
ins = sqlite3.connect("data/nifty50_instruments.db")
tok2sym = dict((t, s) for s, t in ins.execute("select tradingsymbol, instrument_token from nifty50_instruments"))
picks, seen_day = [], set()
for t in sorted(R, key=lambda t: t["trigger_ts"]):
    if t.get("align_mkt") is None or t["session_date"] in seen_day: continue
    hm = t["trigger_ts"][11:16]
    if len(picks) < 4 and hm < "10:00" or 4 <= len(picks) < 8 and hm >= "12:00":
        picks.append(t); seen_day.add(t["session_date"])
    if len(picks) >= 8: break
ctx = []
for t in picks:
    day = t["session_date"]; cutoff = t["trigger_ts"][:16] + ":00+05:30"
    rows = db.execute("select instrument_token, candle_time, open, close from candles where candle_time >= ? and candle_time < ? "
                      "order by candle_time", (day, day + "T99")).fetchall()
    first, last, at_cut = {}, {}, {}
    for tok, ct, o, c in rows:
        if ct < cutoff:
            first.setdefault(tok, (ct, o, c)); last[tok] = (ct, o, c)
        elif ct == cutoff:
            at_cut[tok] = (ct, o, c)
    bars = []
    for tok in set(first) | set(at_cut):
        seen = set()
        for src in (first, last, at_cut):
            if tok in src and src[tok][0] not in seen:
                seen.add(src[tok][0]); ct, o, c = src[tok]; bars.append([tok2sym[tok], ct, o, c])
    ctx.append({"setup_id": t["setup_id"], "session_date": day, "symbol": t["tradingsymbol"], "direction": t["direction"],
                "trigger_ts": t["trigger_ts"], "bars": bars,
                "expected_align_mkt": t["align_mkt"], "expected_align_sec": t.get("align_sec")})
json.dump(ctx, open(f"{OUT}/context.json", "w"), separators=(",", ":"))

print(f"halts recorded {sum(d['halt_minute'] is not None for d in days)} (flattening {len(halts)}) | funnel {len(fun)} ({sum(f[9] for f in fun)} pass) | budget {len(log)} decisions "
      f"({sum(1 for l in log if l['expected_risk'] == 0)} budget skips), {len(taken_all)} trades | "
      f"days {len(days)} ({len(halts)} halts) | context {len(ctx)}")
