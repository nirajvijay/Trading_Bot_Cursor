"""2026 triggers x many trailing-stop rules -> exit price/time per rule.

Exits are only: stop (initial structural stop, ratcheted by the trail) or the
14:50 square-off. Trails update at each 1m bar close (one order modify per
minute at most); stop hits are checked on the O->near->far->C tick path.
The engine's own 0.5R schedule is included per tick as the baseline.
Output: trail_research.json
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, time
from pathlib import Path

REPO = Path("/Users/nj/nifty-radar-project")
sys.path.insert(0, str(REPO))
from engine_stop import structural_stop_price  # noqa: E402
from engine_trailing import resolve_stop, schedule_stop  # noqa: E402

BT = Path(__file__).resolve().parent
SQUARE_OFF = time(14, 50)


def rules():
    out = {"engine": {"kind": "engine"}}
    for a in (0, 0.5, 1, 1.5, 2):
        for b in (0.5, 0.75, 1, 1.5, 2, 3):
            out[f"R_a{a}_b{b}"] = {"kind": "R", "a": a, "b": b}
    for a in (0, 0.5, 1):
        for k in (1.5, 2, 3, 4, 6, 8):
            out[f"ATR_a{a}_k{k}"] = {"kind": "atr", "a": a, "k": k}
        for n in (3, 5, 10, 15, 20, 30):
            out[f"BAR_a{a}_n{n}"] = {"kind": "bar", "a": a, "n": n}
        for p in (0.3, 0.5, 0.75, 1.0, 1.5):
            out[f"PCT_a{a}_p{p}"] = {"kind": "pct", "a": a, "p": p}
    # Step ladder: at +k R lock (k - s) R.
    for s in (0.5, 1, 1.5):
        out[f"STEP_s{s}"] = {"kind": "step", "s": s}
    for a in (0, 2, 3):
        for b in (4, 5, 6):
            out[f"R_a{a}_b{b}"] = {"kind": "R", "a": a, "b": b}
    for a in (3, 4, 5):
        for b in (1, 1.5, 2, 2.5):
            out[f"R_a{a}_b{b}"] = {"kind": "R", "a": a, "b": b}
    for a in (1, 2, 3, 4):
        for f in (0.25, 0.4, 0.5, 0.6, 0.75):
            out[f"GIVE_a{a}_f{f}"] = {"kind": "give", "a": a, "f": f}
    for late in ("13:30", "14:00", "14:20"):
        for b in (3, 4):
            for n in (3, 5, 10):
                out[f"LATE_b{b}_{late}_n{n}"] = {"kind": "late", "b": b, "late": late, "n": n}
    for k in (0.5, 0.75, 1, 1.5, 2):
        out[f"TGT_{k}"] = {"kind": "none", "tgt": k}
    for be in (0.5, 0.75, 1, 1.5):
        for lock in (0, 0.1, 0.25):
            for a, f in ((2, 0.25), (3, 0.4), (1.5, 0.5), (1, 0.5)):
                out[f"BEG_be{be}_l{lock}_a{a}_f{f}"] = {"kind": "give", "be": be, "lock": lock, "a": a, "f": f}
    for m in (20, 30, 45, 60, 90):
        for x in (0.3, 0.5, 1):
            out[f"TS_m{m}_x{x}"] = {"kind": "give", "a": 2, "f": 0.25, "ts": (m, x)}
            out[f"TSBE_m{m}_x{x}"] = {"kind": "give", "a": 2, "f": 0.25, "ts": (m, x), "be": 1, "lock": 0.1}
    return out


RULES = rules()


def tick_path(o, h, l, c):
    return (o, l, h, c) if c >= o else (o, h, l, c)


def walk(t, stop0, tick, bars):
    long = t["direction"] == "UP"
    sgn = 1 if long else -1
    e = t["trigger_price"]
    r = abs(e - stop0)
    trig_min = datetime.fromisoformat(t["trigger_ts"]).replace(second=0)
    st = {n: {"stop": stop0, "peak": e} for n in RULES}
    out = {}
    trs = []  # true ranges of all bars so far (for ATR)
    prev_c = None
    hist = []  # completed bars since session start (for N-bar trail)

    def rnd(x):  # round a stop away from price onto the tick grid
        q = x / tick
        return round((int(q + 1e-9) if long else -int(-q + 1e-9)) * tick, 4)

    for ct, o, h, l, c in bars:
        dt = datetime.fromisoformat(ct)
        tr = h - l if prev_c is None else max(h - l, abs(h - prev_c), abs(l - prev_c))
        if dt < trig_min:
            trs.append(tr)
            hist.append((h, l))
            prev_c = c
            continue
        if dt.time() >= SQUARE_OFF:
            for n in RULES:
                out.setdefault(n, (o, ct, "EOD"))
            break
        path = tick_path(o, h, l, c)
        start = 0
        if dt == trig_min:
            start = next(i for i, p in enumerate(path) if (p >= e if long else p <= e)) + 1
        for i in range(start, 4):
            p = path[i]
            for n, s in st.items():
                if n in out:
                    continue
                if (p <= s["stop"]) if long else (p >= s["stop"]):
                    fill = (min(p, s["stop"]) if long else max(p, s["stop"])) if i == 0 else s["stop"]
                    out[n] = (fill, ct, "STOP" if s["stop"] == stop0 else "TRAIL")
                    continue
                tg = RULES[n].get("tgt")
                if tg is not None:
                    tp = e + sgn * tg * r
                    if (p >= tp) if long else (p <= tp):
                        out[n] = ((max(p, tp) if long else min(p, tp)) if i == 0 else tp, ct, "TGT")
                        continue
                s["peak"] = max(s["peak"], p) if long else min(s["peak"], p)
                if RULES[n]["kind"] == "engine":
                    cand = schedule_stop(direction=t["direction"], entry=e, r_pts=r, ltp=p, tick_size=tick)
                    if cand is not None:
                        d = resolve_stop(direction=t["direction"], current=s["stop"], candidate=cand,
                                         floor=stop0, tick_size=tick, auto_on=True, ltp=p)
                        if d.ok:
                            s["stop"] = d.target
        # Bar close: update bar-close trails.
        trs.append(tr)
        hist.append((h, l))
        prev_c = c
        atr = sum(trs[-14:]) / len(trs[-14:])
        for n, s in st.items():
            if n in out:
                continue
            cfg = RULES[n]
            k = cfg["kind"]
            if k == "engine":
                continue
            gain_r = sgn * (s["peak"] - e) / r
            if "ts" in cfg:
                mins = (dt - trig_min).total_seconds() / 60 + 1
                if mins >= cfg["ts"][0] and gain_r < cfg["ts"][1]:
                    out[n] = (c, ct, "TIME")
                    continue
            if cfg.get("be") is not None and gain_r >= cfg["be"]:
                b = rnd(e + sgn * cfg.get("lock", 0) * r)
                b = min(b, rnd(c - tick)) if long else max(b, rnd(c + tick))
                if (b > s["stop"]) if long else (b < s["stop"]):
                    s["stop"] = b
            if k == "none":
                continue
            if k == "step":
                if gain_r >= 1:
                    lock = int(gain_r / cfg["s"]) * cfg["s"] - cfg["s"]
                    cand = e + sgn * max(lock, 0) * r
                else:
                    continue
            elif gain_r < cfg.get("a", 0):
                continue
            elif k == "R":
                cand = s["peak"] - sgn * cfg["b"] * r
            elif k == "atr":
                cand = s["peak"] - sgn * cfg["k"] * atr
            elif k == "bar":
                w = hist[-cfg["n"]:]
                cand = min(x[1] for x in w) if long else max(x[0] for x in w)
            elif k == "give":
                cand = e + sgn * cfg["f"] * abs(s["peak"] - e)
            elif k == "late":
                if ct[11:16] >= cfg["late"]:
                    w = hist[-cfg["n"]:]
                    cand = min(x[1] for x in w) if long else max(x[0] for x in w)
                else:
                    cand = s["peak"] - sgn * cfg["b"] * r
            elif k == "pct":
                cand = s["peak"] * (1 - sgn * cfg["p"] / 100)
            cand = rnd(cand)
            # Never place a stop at/through the last price (it would fill at once).
            cand = min(cand, rnd(c - tick)) if long else max(cand, rnd(c + tick))
            if (cand > s["stop"]) if long else (cand < s["stop"]):
                s["stop"] = cand
        if len(out) == len(RULES):
            break
    for n in RULES:
        out.setdefault(n, (bars[-1][4], bars[-1][0], "EOD_DATA"))
    return r, out


def main():
    triggers = [t for t in json.loads((BT / "t12.json").read_text()) if t["session_date"] >= "2026-01-01"]
    rep = sqlite3.connect(BT / "r12.db")
    arms = {row[0]: row[1:] for row in rep.execute(
        "select setup_id, pullback_swing_high, pullback_swing_low, tick_size, buffer_ticks "
        "from live_continuation_arms")}
    hist = sqlite3.connect(f"file:{BT / 'data' / 'kite_12m.db'}?mode=ro", uri=True)
    by_tok = defaultdict(list)
    for t in triggers:
        by_tok[t["instrument_token"]].append(t)
    rows = []
    for tok, ts in by_tok.items():
        days = defaultdict(list)
        for ct, o, h, l, c in hist.execute(
                "select candle_time, open, high, low, close from candles where instrument_token=? "
                "and candle_time >= '2026' order by candle_time", (tok,)):
            days[ct[:10]].append((ct, o, h, l, c))
        for t in ts:
            sh, sl, tick, buf = arms[t["setup_id"]]
            stop0 = structural_stop_price(direction=t["direction"], swing_high=sh, swing_low=sl,
                                          tick_size=tick, buffer_ticks=buf)
            if stop0 is None:
                continue
            r, out = walk(t, stop0, tick, days[t["session_date"]])
            rows.append({k: t[k] for k in ("setup_id", "session_date", "tradingsymbol", "direction",
                                           "trigger_ts", "trigger_price", "gap", "vwap_class",
                                           "breakout_volume", "avg_prior_3")}
                        | {"stop": stop0, "r": r, "x": {n: v[:2] for n, v in out.items()}})
    (BT / "trail_research.json").write_text(json.dumps(rows))
    print(len(rows), "triggers x", len(RULES), "rules")


if __name__ == "__main__":
    main()
