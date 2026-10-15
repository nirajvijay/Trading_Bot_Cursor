"""Sector features at each 2026 trigger (last completed minute before the trigger minute)."""
import json, sqlite3, sys, bisect
from collections import defaultdict
sys.path.insert(0, "/Users/nj/nifty-radar-project")
from config.nifty100_sector_map import SECTOR_MAP_V1
SEC = {s: name for name, syms in SECTOR_MAP_V1 for s in syms.split()}
R = json.load(open("trail_research.json"))
ins = sqlite3.connect("data/nifty50_instruments.db")
tok2sym = dict((tok, sym) for sym, tok in ins.execute("select tradingsymbol, instrument_token from nifty50_instruments"))
need = defaultdict(list)
for i, t in enumerate(R): need[t["session_date"]].append(i)
db = sqlite3.connect("file:data/kite_12m.db?mode=ro", uri=True)
feat = {}
for day, idx in need.items():
    rows = db.execute("select instrument_token, candle_time, open, close from candles where candle_time >= ? and candle_time < ? order by candle_time",
                      (day, day + "T99")).fetchall()
    first, last, snaps, mins = {}, {}, [], []
    cur = None
    for tok, ct, o, c in rows:
        if cur is not None and ct != cur:
            mins.append(cur); snaps.append({k: last[k] / first[k] - 1 for k in last})
        cur = ct; first.setdefault(tok, o); last[tok] = c
    mins.append(cur); snaps.append({k: last[k] / first[k] - 1 for k in last})
    for i in idx:
        t = R[i]
        j = bisect.bisect_left(mins, t["trigger_ts"][:16] + ":00+05:30") - 1
        if j < 0: continue
        snap = snaps[j]; sym = t["tradingsymbol"]; sec = SEC.get(sym)
        sgn = 1 if t["direction"] == "UP" else -1
        peers = [v for k, v in snap.items() if SEC.get(tok2sym.get(k)) == sec and tok2sym.get(k) != sym]
        own = [v for k, v in snap.items() if tok2sym.get(k) == sym]
        mkt = sum(snap.values()) / len(snap)
        feat[t["setup_id"]] = {"sector": sec, "n_peers": len(peers),
                               "align_sec": sgn * sum(peers) / len(peers) * 100 if peers else None,
                               "sec_adv": (sum(v > 0 for v in peers) / len(peers) if sgn > 0 else sum(v < 0 for v in peers) / len(peers)) if peers else None,
                               "align_own": sgn * own[0] * 100 if own else None,
                               "align_mkt2": sgn * mkt * 100}
json.dump(feat, open("features2.json", "w"))
print(len(feat), "features;", sum(f["align_sec"] is None for f in feat.values()), "without peers")
