"""Market-breadth features at each 2026 trigger (from the previous completed minute)."""
import json, sqlite3, bisect
from collections import defaultdict
R = json.load(open("trail_research.json"))
need = defaultdict(list)
for i, t in enumerate(R): need[t["session_date"]].append(i)
db = sqlite3.connect("file:data/kite_12m.db?mode=ro", uri=True)
feat = {}
for day, idx in need.items():
    rows = db.execute("select instrument_token, candle_time, open, close from candles where candle_time >= ? and candle_time < ? order by candle_time",
                      (day, day + "T99")).fetchall()
    first, last = {}, {}
    mins, adv, avg = [], [], []
    cur = None
    def flush():
        rets = [last[k] / first[k] - 1 for k in last]
        mins.append(cur); adv.append(sum(r > 0 for r in rets) / len(rets)); avg.append(sum(rets) / len(rets) * 100)
    for tok, ct, o, c in rows:
        if cur is not None and ct != cur: flush()
        cur = ct; first.setdefault(tok, o); last[tok] = c
    if cur: flush()
    sym_open = {}
    for i in idx:
        t = R[i]
        tm = t["trigger_ts"][:16] + ":00+05:30"
        j = bisect.bisect_left(mins, tm) - 1   # last completed minute before trigger minute
        if j < 0: continue
        sgn = 1 if t["direction"] == "UP" else -1
        feat[t["setup_id"]] = {"breadth": adv[j], "mkt_ret": avg[j],
                               # aligned: share of stocks moving in the trade's direction, and market move in trade direction
                               "align_breadth": adv[j] if sgn > 0 else 1 - adv[j], "align_mkt": sgn * avg[j]}
json.dump(feat, open("features.json", "w"))
print(len(feat), "of", len(R))
