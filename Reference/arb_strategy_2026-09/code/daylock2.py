"""Day-level equity lock for combined trades (per-minute MTM from 1m closes)."""
import sqlite3
from collections import defaultdict
from charges import intraday_charges
db = sqlite3.connect("file:data/kite_12m.db?mode=ro", uri=True)
ins = sqlite3.connect("data/nifty50_instruments.db")
SYM2TOK = dict(ins.execute("select tradingsymbol, instrument_token from nifty50_instruments"))
_c = {}
def closes(tok, day):
    if (tok, day) not in _c:
        _c[(tok, day)] = [(ct[:16], c) for ct, c in db.execute(
            "select candle_time, close from candles where instrument_token=? and candle_time >= ? and candle_time < ? order by candle_time", (tok, day, day + "T99"))]
    return _c[(tok, day)]
def prepare(trades):
    for x in trades:
        tok = SYM2TOK.get(x["sym"]) or int(x["sym"])
        x["path"] = [(m, c) for m, c in closes(tok, x["day"]) if x["ts"][:16] <= m < x["xt"][:16]]
    return trades
def net_at(x, px, slip_rate):
    e, q = x["e"], x["q"]
    gross = ((px - e) if x["up"] else (e - px)) * q
    bv, sv = (e*q, px*q) if x["up"] else (px*q, e*q)
    return gross - intraday_charges(bv, sv) - x["slip"]
def apply(trades, arm, keep, slip_rate=3e-4):
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
                if px is not None: eq += net_at(x, px, slip_rate)
            peak = max(peak, eq)
            if peak >= arm and eq <= peak * keep: stop_at = m; break
        for x in xs:
            if stop_at is None or x["xt"][:16] <= stop_at: out.append(x); continue
            if x["ts"][:16] > stop_at: continue
            px = x["e"]
            for pm, c in x["path"]:
                if pm <= stop_at: px = c
            y = dict(x); y["net"] = net_at(x, px, slip_rate); y["xt"] = stop_at + ":00"; y["xp"] = px; y["flat"] = True; out.append(y)
    return out
