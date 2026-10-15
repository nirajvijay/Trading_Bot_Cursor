# ARB — Adaptive Risk Budget intraday system

**NSE cash intraday · Nifty 100 universe · Zerodha Kite · ₹5L capital at 5× leverage**
Research report · built 26 Sep 2026 · backtest data: 1-minute candles, Jan–Sep 2026

> **Status: backtest only.** All parameters were chosen by looking at 2026 data (in-sample). Treat these numbers as a best case until a paper or small-size forward test confirms them. This is research, not investment advice.

---

## 1. Summary

ARB combines **two entry engines** that tend to make money on different days. **Three layers of risk control** sit on top of them.

| Part | What it does |
|---|---|
| **Engine A: ORB "stocks in play"** | Morning breakout of the first 5-minute range, only in the 3 stocks with the most unusual opening volume |
| **Engine B: continuation funnel with quality score** | The live spike → pullback → continuation trigger, filtered, and sized by a 0–3 quality score |
| **Per-trade exit** | Initial structural stop → lock +0.25R at +1R → keep 25% of peak gain after +2R → square off at 14:50 |
| **Day lock** | Once the day is up ≥ ₹3,000, flatten everything if it falls back to 40% of its peak |
| **ARB loss control (new)** | Half size after the day's first loss. An **open-risk cap that grows with the day's profit** (₹6,000 + profit so far). **Trades whose stop is already in profit use none of the cap.** Hard day stop at −₹5,000 (open + closed P&L) |

### Headline results (after Kite charges and slippage)

| | 3 bps slippage | 5 bps slippage |
|---|--:|--:|
| **Jun–Sep average per month** | **₹42,746** | **₹31,159** |
| Jan–Sep average per month | ₹43,814 | ₹35,300 |
| Profitable months | 9 / 9 | 9 / 9 |
| Weakest month | +₹7,780 (Mar) | +₹4,464 (Mar) |
| Green days | 62% | 60% |
| Average red day | −₹4,138 | −₹4,211 |
| **Worst day** | **−₹5,674** | **−₹5,893** |
| Days worse than −₹6,000 | 0 | 0 |
| Max drawdown (daily equity) | −₹21,579 | −₹23,922 |
| Trades per month | ~85 | ~84 |

**Why it matters:** ARB is the only version tested that has both of these:
- the **small losses of "full loss control"**: no day worse than −₹5.9k;
- the **profit of "hard stop only"**: about ₹43k/month for Jun–Sep.

---

## 2. How the idea was found

### 2.1 The two earlier versions and their weak points

| Version | Rules | Strength | Weakness |
|---|---|---|---|
| Full loss control | ½ size after 1st loss + open risk ≤ ₹6k + hard −₹5k | Worst day −₹5.7k | July only +₹4k; at 5 bps July was −₹1.9k |
| Hard stop only | hard −₹5k only | Jun–Sep ₹43.6k | Worst day −₹7.2k, 4 days < −₹6k; profit leans on September (+₹102k) |

### 2.2 The diagnosis

Trades were grouped by what had already happened that day. The result is measured in R (multiples of the risk taken), so position sizing doesn't affect the comparison. The full table is in `data/context_lab_R_by_half.txt`.

| Trade taken… | Jan–Apr | May–Sep |
|---|--:|--:|
| ORB, after a loss earlier that day | +0.74R | +0.63R |
| Funnel score 1, after a loss | +0.25R | +0.14R |
| Funnel score 3, after a loss | +1.78R | +1.20R |
| Funnel score 0, after a loss | +0.11R | −0.35R |

**Conclusion:** after a loss, good trades are still good. The fixed ₹6k cap was blocking them for the wrong reason. A trade whose stop has already moved to +0.25R carries **no downside risk**, but the old cap still counted its full original risk. On a strong day, the account hit the cap while it was actually carrying no risk at all.

### 2.3 The fix ("adaptive risk budget")

1. **Free risk:** a trade counts against the cap only until it has closed a 1-minute bar at ≥ +1R. That is exactly when its exit rule moves the stop to +0.25R.
2. **House money:** cap = ₹6,000 + max(0, today's P&L). A losing day never raises the cap.
3. Everything else from full loss control stays the same, including the −₹5k hard stop. So the worst day is still bounded.

This is how prop-trading desks size positions: from the day's remaining loss allowance rather than a fixed number (see §11 Sources).

---

## 3. Full rulebook (what the live system must do)

### 3.1 Universe and data
- Nifty 100 stocks in the live watchlist (`nifty50_instruments.db`).
- 1-minute candles from Kite. Daily ATR14 is computed from 1-minute data.
- Session: entries from 09:20, **no new entries after 14:00** (engine rule; in practice ORB stops at 11:00 and the funnel at 13:00). **Square off at 14:50.**

### 3.2 Engine A — ORB stocks in play
| Rule | Value |
|---|---|
| Opening range | First 5 minutes (09:15–09:19) |
| RVOL | Volume in the first 5 minutes ÷ average first-5-minute volume over the previous 14 sessions |
| Stock selection | RVOL ≥ 3.0; take the **top 3 by RVOL** that day |
| Direction | Direction of the first 5-minute candle (close > open → long only; close < open → short only; doji → skip) |
| Entry | Stop-entry at the opening-range high (long) or low (short), valid until **11:00** |
| Initial stop | Entry ∓ **0.35 × ATR14 (daily)** |
| Base risk | **₹2,500** |
| Backtest rule | If the entry bar also trades through the stop, the trade counts as a stop-out (conservative) |

### 3.3 Engine B — continuation funnel with quality score
The existing spike → pullback → continuation engines generate triggers; the structural stop comes from `engine_stop`. A trigger is used only if all of these hold:
- breakout 1-minute volume ≥ **2×** the average of the prior 3 one-minute bars;
- trigger time **before 13:00**;
- stop distance ≥ **0.25%** of price;
- **VWAP filter OFF** (it showed no edge);
- only the **first** trigger per stock per day.

**Quality score** (0–3; score 4 counts as 3):

| +1 point if… | Definition |
|---|---|
| Market against the trade | average % change from the open across the watchlist, signed in the trade's direction, at the previous completed minute, is **< −0.2%** |
| Market against AND sector with the trade | the above, plus the average of the stock's sector peers (signed) is **> 0** (`config/nifty100_sector_map.py`) |
| Strong volume | breakout volume ≥ **6×** the average of the prior 3 bars |
| Early | trigger before **10:30** |

| Score | 0 | 1 | 2 | 3+ |
|---|--:|--:|--:|--:|
| Base risk | ₹1,000 (max 2 per day) | ₹2,500 | ₹4,500 | ₹6,000 |

### 3.4 Per-trade exit (both engines) — `BEG_be1_l0.25_a2_f0.25`
Updated at each 1-minute bar close. The stop only ever moves in the trade's favour and is never placed at or through the last price.
1. The initial stop stays until the peak gain reaches **+1R**.
2. At +1R: stop → entry **+0.25R** (profit locked).
3. After +2R: stop → entry + **25% of the peak gain** (trails with the peak).
4. If still open at **14:50**: square off at market.

### 3.5 Position sizing (`RiskCappedSizing`)
qty = floor(risk ÷ |entry − stop|), limited by available margin = ₹5,00,000 × 5 − margin used by open positions. One position per symbol at a time.

### 3.6 ARB day-level risk control — the core of this report
Checked at every new entry:

```
day_pnl      = closed P&L after charges + open P&L at the last 1m close
live_risk    = Σ qty × |entry − initial stop|   over open trades that have NOT yet closed a 1m bar ≥ +1R
losers_today = number of closed losing trades today

risk = base risk (from the engine/score)
if losers_today ≥ 1:            risk × 0.5                        # half size after the first loss
cap  = 6000 + max(0, day_pnl) × 1.0                               # house money
risk = min(risk, cap − live_risk)
if risk < 500:                  skip the trade
```

Checked continuously (every minute in the backtest; on every tick live):

```
HARD DAY STOP : if day_pnl ≤ −5,000              → flatten everything, no more trades today
DAY LOCK      : peak = max day_pnl so far
                if peak ≥ 3,000 and day_pnl ≤ 0.40 × peak → flatten everything, no more trades today
```

Also: stop taking new trades if closed losses today reach ₹6,000 (backstop; the hard stop fires first in practice).

### 3.7 Capital
- ₹5L margin → ₹25L buying power at 5×. ₹4L also works, at about ₹4k/month less (see robustness).
- Keep about a ₹1L buffer outside the trading capital: the worst drawdown was about ₹22–24k, and live results will be worse than the backtest.

---

## 4. Results, month by month (ARB, after charges)

| Month | Trades | Days | Green days | P&L @3 bps | Worst day @3 bps | P&L @5 bps |
|---|--:|--:|--:|--:|--:|--:|
| Jan 2026 | 88 | 20 | 12 | **+1,26,357** | −5,273 | +1,20,851 |
| Feb | 91 | 21 | 10 | **+15,334** | −5,204 | +10,850 |
| Mar | 81 | 19 | 10 | **+7,780** | −5,236 | +4,464 |
| Apr | 91 | 20 | 13 | **+20,984** | −5,508 | +6,213 |
| May | 71 | 19 | 15 | **+52,883** | −5,382 | +50,690 |
| Jun | 93 | 21 | 12 | **+39,522** | −5,554 | +23,384 |
| Jul | 91 | 23 | 15 | **+32,020** | −5,496 | +25,990 |
| Aug | 86 | 21 | 14 | **+48,358** | −5,674 | +28,292 |
| Sep (to 25th) | 75 | 18 | 12 | **+51,084** | −5,359 | +46,969 |

Per-day details for every month are in `orderbooks/` and `data/daily_results.csv`.

### 4.1 Comparison with the other versions — Jun–Sep 2026, 3 bps

| | No loss control | Full loss control | Hard stop only | **ARB** |
|---|--:|--:|--:|--:|
| June | +39,896 | +36,801 | +36,703 | **+39,522** |
| July | +34,872 | +3,983 | +17,020 | **+32,020** |
| August | +22,632 | +48,268 | +18,386 | **+48,358** |
| September | +90,113 | +45,837 | +1,02,342 | **+51,084** |
| **Average** | 46,878 | 33,722 | 43,613 | **42,746** |
| Green days | 59/83 | 51/83 | 50/83 | **53/83** |
| Worst day | −13,963 | −5,674 | −7,222 | **−5,674** |

### 4.2 Whole of 2026 (Jan–Sep), all versions

| Version | Slippage | Avg / month | Worst month | Green days | Avg red day | Worst day | Days < −6k | Max DD | Worst week |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|
| **ARB** | 3 bps | **43,814** | +7,780 | 62% | −4,138 | **−5,674** | **0** | **−21,579** | −16,758 |
| Full loss control | 3 bps | 40,766 | +3,983 | 62% | −4,109 | −5,674 | 0 | −25,799 | −16,884 |
| Hard stop only | 3 bps | 32,398 | +4,007 | 62% | −4,935 | −7,222 | 4 | −30,538 | −19,888 |
| No loss control | 3 bps | 50,677 | +11,019 | 72% | −6,378 | −13,963 | 27 | −31,243 | −18,604 |
| **ARB** | 5 bps | **35,300** | +4,464 | 60% | −4,211 | **−5,893** | **0** | **−23,922** | −17,289 |
| Full loss control | 5 bps | 33,952 | −1,942 | 60% | −4,236 | −5,893 | 0 | −27,407 | −17,386 |
| Hard stop only | 5 bps | 32,032 | +530 | 62% | −5,147 | −7,689 | 9 | −31,941 | −20,269 |
| No loss control | 5 bps | 51,273 | +4,527 | 71% | −6,595 | −14,596 | 29 | −34,569 | −20,325 |

"No loss control" makes more money and has more green days, but its red days average −₹6.5k and reach −₹14k. That is the risk you asked to remove.

### 4.3 Where the money comes from (ARB, Jan–Sep, 3 bps)

| Source | Trades | Net P&L | Win rate | Avg per trade |
|---|--:|--:|--:|--:|
| ORB | 374 | +1,69,562 | 52% | +453 |
| Funnel score 3 | 17 | +35,575 | 65% | +2,093 |
| Funnel score 2 | 61 | +1,26,797 | 59% | +2,079 |
| Funnel score 1 | 181 | +59,609 | 52% | +329 |
| Funnel score 0 | 134 | +2,780 | 40% | +21 |
| **Total** | **767** | **+3,94,323** | **51%** | avg win +2,421 / avg loss −1,449 · profit factor 1.72 |

- Costs over 9 months: Kite charges ₹1,25,495 + slippage ₹75,536. These already sit inside the totals above.
- Days: 182. Median day +₹1,142. Average green day +₹6,017. Best day +₹53,111 (15 Sep).
- Exits: 189 trades closed by the day lock, 73 by the hard day stop, and the rest by their own stop, trail or the 14:50 square-off.
- 292 trades were taken at half size. The cap limited 81 trades. **43 trades were allowed only because of house money or freed risk** (open risk would have been above ₹6k under the old cap).

---

## 5. Robustness (does it survive small changes?)

The full table is in `data/robustness_tests.csv`.

**Neighbourhood of the chosen setting** (Jun–Sep average per month, 3 bps / 5 bps):

| house money × | cap ₹5k | cap ₹6k (chosen) | cap ₹7k |
|---|--:|--:|--:|
| 0.5 | 38.2k / 28.0k | 41.8k / 30.0k | 45.7k / 31.8k |
| **1.0 (chosen)** | 39.0k / 27.7k | **42.7k / 31.2k** | 45.7k / 33.2k |
| 1.5 | 39.0k / 28.6k | 43.3k / 31.2k | 45.6k / 32.9k |
| 2.0 | 38.8k / 28.8k | 43.9k / 31.4k | 45.6k / 32.9k |

- **Plateau:** house money 0.5–2× and a cap of ₹6k–7k all work; freeing risk at 0.75R or 1R gives the same results. The worst day stays between −₹5.7k and −₹6.0k in every cell.
- **Weak spot 1:** a ₹5k cap is too tight. Some months go negative, so don't go below ₹6k.
- **Weak spot 2 — the day lock is fragile:** keep must stay at **0.35–0.40**. At 0.45–0.5, the 5 bps results collapse to about ₹10k/month for Jun–Sep. Do not "tighten" the day lock.
- Hard stop −₹4.5k: worst day −₹5.5k but profit falls to ₹38k. −₹6k: +₹1k/month, but the worst day becomes −₹8.1k. −₹5k is the balance point.
- ₹4L capital: ₹39.6k/month (3 bps), same worst day.

---

## 6. Ideas tested and rejected

| Idea | Result |
|---|---|
| Live VWAP filter | No edge (≈0R after charges over 12 months) |
| VWAP mean reversion (fade 2–3σ stretches) | ≈0 gross edge on these stocks |
| Cross-sectional, sector or market momentum baskets | No edge |
| Trading *with* the market direction (funnel) | Worse; trading *against* it with the sector on your side is the edge |
| Engine's 0.5R trailing schedule, fixed R targets, ATR/bar/percent trails | Worse than the lock + 25% give-back exit |
| Predicting red days from the open (gap, first-5m move, weekday) | No predictive power |
| Letting ORB skip the half-size cut | Similar average, but one month at −₹11.5k (−₹14.4k at 5 bps); 8/9 months |
| Skipping score-0 funnel trades after a loss | Slightly worse |
| No new entries after 12:00 | Smoother months (all ≥ +₹26k at 5 bps) but less profit; a reasonable conservative variant |
| Halving trades in the same direction as the last loser | Worst day −₹6.9k |
| Sizing every trade from the remaining −₹5k budget | Worse (₹34.8k Jan–Sep, 8/9) |
| Half-size → ×0.66 | Worse; 8/9 months |
| Bigger risk per trade (ORB ₹3k, funnel ×1.25) | Worse and deeper drawdowns |

---

## 7. What you give up (known weaknesses)

1. **Green days stay around 60–62%.** Breakout systems earn from a few big days. About 3 in 10 days hit the −₹5k hard stop or finish red. Every rule that pushed green days much higher cost more profit than it saved.
2. **Profit is uneven across months.** January was +₹1.26L and March +₹7.8k. At 5 bps, March and April were only +₹4–6k. Expect some months well below ₹25k.
3. **Slippage decides the result.** Going from 3 to 5 bps costs about ₹11k/month. The measured live median is about 1 bp, but one fill was 107 bp. The ORB stop-entry fills at 09:20–11:00 are the most exposed.
4. **The hard stop can be overrun.** The backtest flattens at the 1-minute close. A fast move, or several positions exiting at once, can make a day worse than −₹5.9k live.
5. **Everything was fitted on 2026 data** (in-sample): filters, score thresholds, risk levels and exit. Expect live results to be lower.
6. **Backtest mechanics.** Intrabar order is simulated (O → near extreme → far extreme → C). ORB entries count as a stop-out if the entry bar touches the stop. Charges use the Zerodha intraday formula checked against a Kite contract note. No partial fills, freeze limits or order-rejection effects.
7. **Concentration.** One big day (15 Sep, +₹51k) was the whole of September's profit. Missing that one day (system down, late login) would change the month completely.

---

## 8. What must be built into the live engine

| # | Change | Where |
|---|---|---|
| 1 | ORB engine: RVOL ranking at 09:20, 5m range, stop-entry orders until 11:00, 0.35×ATR14 stop | new engine |
| 2 | Funnel filters: vol ≥ 2×, before 13:00, stop ≥ 0.25%, VWAP off, first trigger per stock per day | continuation gating |
| 3 | Quality score (market/sector alignment, vol ≥ 6×, before 10:30) → risk ₹1,000/2,500/4,500/6,000, max 2 score-0 trades/day | sizing |
| 4 | Exit rule: +1R → +0.25R lock; after +2R keep 25% of peak; 14:50 square-off | trailing module (replaces the 0.5R schedule) |
| 5 | **Live day P&L** (closed + open, after estimated charges), updated on every tick | risk service |
| 6 | **Live risk per open trade from its *current* stop**; a trade whose stop is in profit counts as 0 | risk service (same as the "current risk" desk TODO) |
| 7 | ARB entry gate: half size after the 1st loss; cap = ₹6k + max(0, day P&L); skip if < ₹500 | entry gate |
| 8 | Hard day stop −₹5k and day lock (₹3k / 40%): flatten all, block new entries for the day | risk service |
| 9 | Logging for every decision (base risk, cut, cap, live risk, day P&L) so live results can be compared with this report | audit log |

### Recommended rollout
1. **Paper mode** for 2–3 weeks: compare daily decisions with a replay of the same days.
2. **Quarter size** (all rupee amounts ÷ 4) for 3–4 weeks: measure real round-trip slippage per trade.
3. Scale up only if live slippage is ≤ 3 bps and the live results track the backtest.

---

## 9. Folder contents

```
arb_strategy_2026-09/
├── REPORT.md                          ← this document
├── orderbooks/
│   └── arb_orderbook_2026-01 … 09     every trade, per day, before and after charges (.md readable, .csv for Excel)
├── data/
│   ├── summary_by_version.csv         headline stats: ARB vs full loss control vs hard stop only vs no loss control, 3 & 5 bps
│   ├── monthly_results.csv            per month, per version, per slippage
│   ├── daily_results.csv              per day, per version, per slippage
│   ├── arb_all_trades_2026_3bps.csv   all 767 ARB trades with risk used, half-size flag, day P&L and live risk at entry
│   ├── candidates_2026.csv            every ORB and funnel signal before risk management (input to the ARB layer)
│   ├── robustness_tests.csv           neighbourhood, day-lock, hard-stop and rejected-idea tests
│   └── context_lab_R_by_half.txt      R-results by in-day context, Jan–Apr vs May–Sep
└── code/                              research scripts (see code/README.md)
```

## 10. Glossary
- **R**: the rupee risk of a trade (qty × distance from entry to initial stop). +2R = made twice the risk.
- **RVOL**: relative volume; today's opening volume ÷ the normal opening volume.
- **Day P&L / MTM**: closed P&L plus open positions valued at the latest price, after charges.
- **bps**: basis points; 3 bps = 0.03% of the traded value, per trade, as slippage.
- **Green day**: day that ends with P&L > 0 after charges.

## 11. Sources
- Zarattini, Barbon, Aziz — *A Profitable Day Trading Strategy for the U.S. Equity Market* (ORB on stocks in play, 5-minute range best): https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4729284
- QuantConnect replication of ORB for stocks in play: https://www.quantconnect.com/research/18444/opening-range-breakout-for-stocks-in-play/
- Daily loss limits for day traders: https://tradethatswing.com/setting-a-daily-loss-limit-when-day-trading/
- Sizing from the daily loss limit / remaining drawdown (prop-firm practice): https://thortradecopier.com/blog/position-sizing-funded-accounts-1-percent-rule
