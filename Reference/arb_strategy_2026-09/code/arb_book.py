import csv, sys
from collections import defaultdict, Counter
from redctl import day_manage
from hybrid_v2 import run
from charges import intraday_charges
MON = sys.argv[1]; OUT = f"arb_orderbook_{MON}"
kw = dict(OR=5, key="atr0.35_lock", rv=3.0, N=3)
tr = [x for x in day_manage(run(H=10**9, house=1.0, open_cap=6000, cut_after=1, cut_mult=0.5), arm=3000, keep=0.4, hard=5000) if x["day"].startswith(MON)]
tr.sort(key=lambda x: x["ts"])
rows = []
for x in tr:
    e, q, xp = x["e"], x["q"], x["xp"]; per = (xp - e) if x["up"] else (e - xp)
    gross = per * q; bv, sv = (e*q, xp*q) if x["up"] else (xp*q, e*q); chg = intraday_charges(bv, sv)
    why = x["flatwhy"] if x.get("flat") else ("SQUARE_OFF 14:50" if x["xt"][11:16] >= "14:50" else ("STOP (initial)" if per < 0 else "PROFIT LOCK/TRAIL"))
    strat = f"ORB (RVOL {x['rvol']:.1f}x)" if x["src"] == "ORB" else f"Funnel score {x['sc']}"
    rows.append(dict(date=x["day"], strategy=strat, risk_rupees=round(x["risk_used"]), size_note=("HALF (after a loss)" if x["cut"] else "") + (" capped by open-risk" if x["risk_used"] < x["base_risk"] - 1 else ""), symbol=x["sym"], side="BUY" if x["up"] else "SELL", qty=q,
        entry_time=x["ts"][11:19], entry_price=round(e, 2), initial_stop=round(x["stop"], 2), exit_side="SELL" if x["up"] else "BUY",
        exit_time=x["xt"][11:16], exit_price=round(xp, 2), exit_reason=why, position_value=round(e*q),
        R_multiple=round(per/abs(e - x["stop"]), 2), pnl_before_charges=round(gross, 2), charges=round(chg, 2),
        slippage_3bps=round(x["slip"], 2), pnl_after_charges=round(x["net"], 2)))
w = csv.DictWriter(open(f"{OUT}.csv", "w"), fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
L = [f"# ARB (Adaptive Risk Budget) — ORB + Funnel + Day lock — {MON} order book (backtest)", "",
     "ORB: top 3 stocks by opening RVOL (≥3×) at 09:20, direction of first 5m candle, entry on break of 5m high/low until 11:00, stop 0.35×ATR14, ₹2,500 risk. "
     "Funnel: vol ≥2×, entry <13:00, stop ≥0.25%, VWAP off, skip repeat triggers; risk by quality score 0/1/2/3+ = ₹1,000 (max 2/day) / ₹2,500 / ₹4,500 / ₹6,000. "
     "Exits (both): initial stop → at +1R lock +0.25R → after +2R keep 25% of peak → 14:50. "
     "Day lock: once day P&L (closed + open) peaks ≥ ₹3,000, flatten everything and stop if it falls to 40% of the peak. "
     "ARB loss control: half size after the day's first losing trade; open-risk cap = ₹6,000 + today's profit so far (house money); a trade stops counting against the cap once it has closed a 1m bar at ≥ +1R (its stop is then in profit); HARD DAY STOP — if day P&L (closed + open) hits −₹5,000, flatten everything and stop. ₹5L capital. "
     "After charges = gross − Kite charges − 3 bps slippage.", ""]
for day in sorted({r["date"] for r in rows}):
    dr = [r for r in rows if r["date"] == day]
    L += [f"## {day} — {len(dr)} trades, before charges ₹{sum(r['pnl_before_charges'] for r in dr):+,.0f}, after charges ₹{sum(r['pnl_after_charges'] for r in dr):+,.0f}", "",
          "| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |",
          "|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|"]
    for i, r in enumerate(dr, 1):
        L.append(f"| {i} | {r['strategy']} | {r['risk_rupees']:,}{' ½' if r['size_note'].startswith('HALF') else ''}{' cap' if 'capped' in r['size_note'] else ''} | {r['symbol']} | {r['side']} | {r['qty']} | {r['entry_time']} | {r['entry_price']:,.2f} | {r['initial_stop']:,.2f} | {r['exit_time']} | {r['exit_price']:,.2f} | {r['exit_reason']} | {r['R_multiple']:+.2f} | {r['pnl_before_charges']:+,.0f} | {r['charges']+r['slippage_3bps']:,.0f} | **{r['pnl_after_charges']:+,.0f}** |")
    L.append("")
g = sum(r["pnl_before_charges"] for r in rows); c = sum(r["charges"] for r in rows); s = sum(r["slippage_3bps"] for r in rows)
L.append(f"**Month: {len(rows)} trades, before charges ₹{g:+,.0f}, Kite charges ₹{c:,.0f}, slippage ₹{s:,.0f}, after charges ₹{g-c-s:+,.0f}**")
open(f"{OUT}.md", "w").write("\n".join(L))
print(f"{len(rows)} trades gross {g:+.0f} charges {c:.0f} slip {s:.0f} net {g-c-s:+.0f} winners {sum(r['pnl_after_charges']>0 for r in rows)}")
print(Counter(r["exit_reason"] for r in rows))
for k in ("ORB", "Funnel"):
    t=[r for r in rows if r["strategy"].startswith(k)]; print(k, len(t), f"{sum(r['pnl_after_charges'] for r in t):+.0f}")
for day in sorted({r["date"] for r in rows}):
    dr=[r for r in rows if r["date"]==day]
    print(day, len(dr), f"{sum(r['pnl_before_charges'] for r in dr):+.0f} {sum(r['pnl_after_charges'] for r in dr):+.0f} | " + " · ".join(f"{r['symbol']} {r['side'][0]} {'ORB' if r['strategy'].startswith('ORB') else 'F'+r['strategy'][-1]} {r['pnl_after_charges']:+.0f} {r['exit_reason'].split()[0].split('/')[0]}{' ½' if r['size_note'].startswith('HALF') else ''}" for r in dr))
