"""Opening Range Breakout on 'stocks in play' (Zarattini/Barbon/Aziz style), 2026, 1m data.

Per stock-day: opening range = first OR minutes. Direction = sign(OR close - OR open).
Entry: stop order at OR high (long) / OR low (short), live until 11:00.
Stop variants: k x ATR14(daily) from entry, or opposite side of the range.
Exits: EOD 14:50 ('eod'), or profit lock at +1R -> +0.25R then 25% give-back after +2R ('lock').
RVOL = OR volume / mean OR volume over prior 14 sessions.
Output orb.json rows with exits per variant.
"""
import json, sqlite3
from collections import defaultdict

DB = "data/kite_12m.db"
ORS = (5, 15)
STOPS = ("atr0.1", "atr0.2", "atr0.35", "range")


def tick_path(o, h, l, c):
    return (o, l, h, c) if c >= o else (o, h, l, c)


def walk(bars, i0, sgn, entry, stop0, mode):
    r = abs(entry - stop0)
    stop, peak = stop0, entry
    for j in range(i0, len(bars)):
        ct, o, h, l, c, v = bars[j]
        if ct[11:16] >= "14:50":
            return o, ct
        if j == i0:
            # Entry minute, conservative: if the bar also traded through the stop, assume stopped.
            if (l <= stop) if sgn > 0 else (h >= stop):
                return stop, ct
            path = (c,)
        else:
            path = tick_path(o, h, l, c)
        for p in path:
            if (p <= stop) if sgn > 0 else (p >= stop):
                return (p if (p - stop) * sgn < 0 and j > i0 and p == o else stop), ct
            peak = max(peak, p) if sgn > 0 else min(peak, p)
        if mode == "lock":
            g = sgn * (peak - entry) / r
            cand = None
            if g >= 2:
                cand = entry + sgn * max(0.25 * abs(peak - entry), 0.25 * r)
            elif g >= 1:
                cand = entry + sgn * 0.25 * r
            if cand is not None:
                cand = min(cand, c - 0.05) if sgn > 0 else max(cand, c + 0.05)
                if (cand > stop) if sgn > 0 else (cand < stop):
                    stop = cand
    return bars[-1][4], bars[-1][0]


def main():
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    ins = sqlite3.connect("data/nifty50_instruments.db")
    tok2sym = dict((t, s) for s, t in ins.execute("select tradingsymbol, instrument_token from nifty50_instruments"))
    out = []
    for tok in [r[0] for r in db.execute("select distinct instrument_token from candles where candle_time >= '2026-09-01'")]:
        days = defaultdict(list)
        for row in db.execute("select candle_time, open, high, low, close, volume from candles where instrument_token=? "
                              "and candle_time >= '2025-11-15' order by candle_time", (tok,)):
            days[row[0][:10]].append(row)
        ds = sorted(days)
        daily = {}
        for d in ds:
            b = days[d]
            daily[d] = (max(x[2] for x in b), min(x[3] for x in b), b[-1][4])
        for n, d in enumerate(ds):
            if d < "2026-01-01" or n < 15:
                continue
            prev = ds[n - 14:n]
            trs, pc = [], daily[ds[n - 15]][2]
            for p in prev:
                h, l, c = daily[p]
                trs.append(max(h - l, abs(h - pc), abs(l - pc))); pc = c
            atr = sum(trs) / len(trs)
            bars = days[d]
            for OR in ORS:
                orb = [x for x in bars if x[0][11:16] < f"09:{15 + OR:02d}"]
                if len(orb) < OR:
                    continue
                past = [sum(x[5] for x in days[p] if x[0][11:16] < f"09:{15 + OR:02d}") for p in prev]
                avgv = sum(past) / len(past)
                if avgv <= 0:
                    continue
                oo, oc = orb[0][1], orb[-1][4]
                hi, lo = max(x[2] for x in orb), min(x[3] for x in orb)
                if oc == oo:
                    continue
                sgn = 1 if oc > oo else -1
                lvl = hi if sgn > 0 else lo
                rest = bars[len(orb):]
                idx = None
                for j, x in enumerate(rest):
                    if x[0][11:16] > "11:00":
                        break
                    if (x[2] >= lvl) if sgn > 0 else (x[3] <= lvl):
                        idx = j; break
                if idx is None:
                    continue
                entry = max(lvl, rest[idx][1]) if sgn > 0 else min(lvl, rest[idx][1])
                rec = {"sym": tok2sym.get(tok, str(tok)), "day": d, "OR": OR, "dir": sgn, "entry": entry,
                       "ts": rest[idx][0], "rvol": sum(x[5] for x in orb) / avgv, "atr": atr,
                       "or_pct": (hi - lo) / entry * 100, "x": {}, "r": {}}
                for sname in STOPS:
                    stop = (entry - sgn * float(sname[3:]) * atr) if sname.startswith("atr") else (lo if sgn > 0 else hi)
                    if sgn * (entry - stop) <= 0:
                        continue
                    rec["r"][sname] = abs(entry - stop)
                    for mode in ("eod", "lock"):
                        rec["x"][f"{sname}_{mode}"] = walk(rest, idx, sgn, entry, stop, mode)
                out.append(rec)
    json.dump(out, open("orb.json", "w"))
    print(len(out), "ORB candidates")


if __name__ == "__main__":
    main()
