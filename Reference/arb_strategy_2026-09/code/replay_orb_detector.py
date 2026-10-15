"""One-off check: drive the real OrbDetector with ticks synthesised from the
12-month candles, every 2026 day, and compare its arms and breakouts with the
golden fixture (which is the backtest's own answer).

Ticks carry what Kite ticks carry: last price, the day's open/high/low so far,
and cumulative volume (with a made-up pre-open auction volume on top, which
the detector must subtract).  Run from the research folder.
"""
import json, sqlite3, sys, time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, "/Users/nj/nifty-radar-project")
from orb_baselines import load_orb_baselines
from orb_config import OrbConfig
from orb_detector import OrbDetector
from orb_writer import OrbWriter
from tick_event import Ohlc, TickEvent

IST = ZoneInfo("Asia/Kolkata")
FIX = Path("/Users/nj/nifty-radar-project/tests/fixtures/orb_golden")
days_fix = {d["day"]: d for d in json.loads((FIX / "days.json").read_text())}
hits = {(s, d): hit for s, d, _dir, _lvl, _atr, _bars, hit in json.loads((FIX / "breakouts.json").read_text())}
HIST = Path("data/kite_12m.db")
db = sqlite3.connect(f"file:{HIST}?mode=ro", uri=True)
ins = sqlite3.connect("data/nifty50_instruments.db")
tok2sym = dict((t, s) for s, t in ins.execute("select tradingsymbol, instrument_token from nifty50_instruments"))
tokens = {t: tok2sym[t] for (t,) in db.execute("select distinct instrument_token from candles where candle_time >= '2026-09-01'")}
# The synthetic path jumps straight to a bar's high or low, so do not judge overshoot here.
CFG = OrbConfig(max_overshoot_atr=1e9)
only = sys.argv[1:] or sorted(days_fix)
t0 = time.time(); bad_arms = []; bad_trig = []; n_arms = n_trig = 0
for day in only:
    y, m, d = (int(x) for x in day.split("-"))
    baselines = load_orb_baselines(HIST, day, tokens, config=CFG)
    writer = OrbWriter(conn=sqlite3.connect(":memory:", check_same_thread=False))
    decisions = []
    clock = {}
    det = OrbDetector(session_date=day, token_to_symbol=tokens, tick_sizes={t: 0.01 for t in tokens},
                      baselines=baselines, writer=writer, config=CFG, now_fn=lambda: clock["now"],
                      on_decision=lambda arm, dec: decisions.append((arm.tradingsymbol, dec)))
    bars = defaultdict(list)
    for tok, ct, o, h, l, c, v in db.execute(
            "select instrument_token, candle_time, open, high, low, close, volume from candles "
            "where candle_time >= ? and candle_time < ? order by candle_time", (day + "T09:15", day + "T11:04")):
        bars[tok].append((ct[11:16], o, h, l, c, v))
    events = []          # (time, order, token, price, day_open, day_high, day_low, cum_volume)
    for tok, bs in bars.items():
        P = 1000 + tok % 977
        events.append(((9, 8, 5), 0, tok, bs[0][1], bs[0][1], bs[0][1], bs[0][1], P))
        cum = P; hi = -1e18; lo = 1e18; day_open = bs[0][1]
        for hm, o, h, l, c, v in bs:
            hh, mm = int(hm[:2]), int(hm[3:])
            path = (o, l, h, c) if c >= o else (o, h, l, c)
            if hm < "09:20":
                hi = max(hi, h); lo = min(lo, l); cum += v
                events.append(((hh, mm, 59), 1, tok, c, day_open, hi, lo, cum))
            else:
                for i, px in enumerate(path):
                    hi = max(hi, px); lo = min(lo, px)
                    events.append(((hh, mm, (0, 20, 40, 59)[i]), 2 + i, tok, px, day_open, hi, lo, cum + v * (i + 1) // 4))
                cum += v
    events.sort(key=lambda e: (e[0], e[1], e[2]))
    for seq, (hms, _o, tok, px, do, dh, dl, vol) in enumerate(events, 1):
        when = datetime(y, m, d, *hms, tzinfo=IST)
        clock["now"] = when
        det.on_tick(TickEvent(seq, tok, px, when, when, vol, 1, px, Ohlc(do, dh, dl, do)))
    got = [[a.tradingsymbol, a.direction, a.level, a.rvol, a.rank] for a in det.arms()]
    exp = days_fix[day]["expected"]
    n_arms += len(exp)
    if got != exp:
        bad_arms.append((day, got, exp))
    trig = {s: dec for s, dec in decisions if dec.decision_type == "TRIGGERED"}
    for sym, *_ in exp:
        hit = hits[(sym, day)]
        dec = trig.get(sym)
        n_trig += hit is not None
        got_min = None if dec is None else dec.exchange_ts.strftime("%H:%M")
        if (hit is None) != (dec is None) or (hit is not None and got_min != hit[0]):
            bad_trig.append((day, sym, got_min, hit and hit[0]))
    if det.metrics.tick_failures or det.metrics.write_failures:
        bad_arms.append((day, "failures", det.metrics))
    writer.close()
print(f"days {len(only)} | arms expected {n_arms}, days with different arms {len(bad_arms)} | "
      f"breakouts expected {n_trig}, different {len(bad_trig)} | {time.time() - t0:.0f}s")
for b in bad_arms[:6]: print("ARMS", b)
for b in bad_trig[:12]: print("TRIG", b)
