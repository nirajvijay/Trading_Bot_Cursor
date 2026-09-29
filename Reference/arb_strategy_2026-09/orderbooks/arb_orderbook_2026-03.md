# ARB (Adaptive Risk Budget) — ORB + Funnel + Day lock — 2026-03 order book (backtest)

ORB: top 3 stocks by opening RVOL (≥3×) at 09:20, direction of first 5m candle, entry on break of 5m high/low until 11:00, stop 0.35×ATR14, ₹2,500 risk. Funnel: vol ≥2×, entry <13:00, stop ≥0.25%, VWAP off, skip repeat triggers; risk by quality score 0/1/2/3+ = ₹1,000 (max 2/day) / ₹2,500 / ₹4,500 / ₹6,000. Exits (both): initial stop → at +1R lock +0.25R → after +2R keep 25% of peak → 14:50. Day lock: once day P&L (closed + open) peaks ≥ ₹3,000, flatten everything and stop if it falls to 40% of the peak. ARB loss control: half size after the day's first losing trade; open-risk cap = ₹6,000 + today's profit so far (house money); a trade stops counting against the cap once it has closed a 1m bar at ≥ +1R (its stop is then in profit); HARD DAY STOP — if day P&L (closed + open) hits −₹5,000, flatten everything and stop. ₹5L capital. After charges = gross − Kite charges − 3 bps slippage.

## 2026-03-02 — 4 trades, before charges ₹+3,693, after charges ₹+2,708

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 5.6x) | 2,500 | TATACAP | BUY | 1011 | 09:20:00 | 328.25 | 325.78 | 10:08 | 328.87 | PROFIT LOCK/TRAIL | +0.25 | +625 | 265 | **+360** |
| 2 | ORB (RVOL 4.6x) | 2,500 | INDIGO | BUY | 79 | 09:21:00 | 4,622.20 | 4,590.87 | 09:53 | 4,630.03 | PROFIT LOCK/TRAIL | +0.25 | +619 | 286 | **+333** |
| 3 | ORB (RVOL 3.4x) | 1,026 cap | ONGC | SELL | 443 | 09:22:00 | 286.30 | 288.61 | 10:36 | 280.30 | DAY LOCK flatten | +2.59 | +2,658 | 131 | **+2,527** |
| 4 | Funnel score 1 | 2,500 | NTPC | BUY | 1041 | 10:31:30 | 377.05 | 374.65 | 10:36 | 376.85 | DAY LOCK flatten | -0.08 | -208 | 304 | **-513** |

## 2026-03-04 — 4 trades, before charges ₹-4,367, after charges ₹-5,129

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 11.0x) | 2,500 | INDIGO | SELL | 65 | 09:27:00 | 4,321.10 | 4,359.02 | 10:49 | 4,321.40 | HARD DAY STOP flatten | -0.01 | -20 | 230 | **-250** |
| 2 | ORB (RVOL 13.7x) | 2,500 | SOLARINDS | BUY | 19 | 09:37:00 | 14,347.00 | 14,217.73 | 10:49 | 14,217.73 | STOP (initial) | -1.00 | -2,456 | 225 | **-2,682** |
| 3 | ORB (RVOL 16.4x) | 1,843 cap | LT | SELL | 54 | 09:50:00 | 3,776.30 | 3,810.20 | 10:31 | 3,810.20 | STOP (initial) | -1.00 | -1,831 | 181 | **-2,011** |
| 4 | Funnel score 0 | 500 ½ | WIPRO | SELL | 609 | 10:35:30 | 195.24 | 196.06 | 10:49 | 195.34 | HARD DAY STOP flatten | -0.12 | -61 | 126 | **-187** |

## 2026-03-05 — 5 trades, before charges ₹-4,004, after charges ₹-5,236

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 5.5x) | 2,500 | SOLARINDS | BUY | 17 | 09:20:00 | 14,962.00 | 14,822.42 | 09:28 | 14,822.42 | STOP (initial) | -1.00 | -2,373 | 213 | **-2,586** |
| 2 | ORB (RVOL 3.1x) | 2,500 | COALINDIA | BUY | 691 | 09:20:00 | 445.50 | 441.89 | 09:43 | 447.52 | PROFIT LOCK/TRAIL | +0.56 | +1,399 | 249 | **+1,151** |
| 3 | ORB (RVOL 3.2x) | 1,199 cap | INDIGO | SELL | 29 | 09:21:00 | 4,339.00 | 4,380.15 | 10:08 | 4,380.15 | STOP (initial) | -1.00 | -1,193 | 129 | **-1,323** |
| 4 | Funnel score 1 | 1,250 ½ | INDIGO | BUY | 89 | 10:08:30 | 4,379.05 | 4,365.15 | 10:14 | 4,365.15 | STOP (initial) | -1.00 | -1,237 | 302 | **-1,539** |
| 5 | Funnel score 1 | 1,250 ½ | SUNPHARMA | SELL | 250 | 10:26:30 | 1,778.50 | 1,783.50 | 10:27 | 1,780.90 | HARD DAY STOP flatten | -0.48 | -600 | 338 | **-938** |

## 2026-03-06 — 5 trades, before charges ₹+4,783, after charges ₹+3,854

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 13.9x) | 2,500 | MAZDOCK | BUY | 90 | 09:20:00 | 2,420.00 | 2,392.37 | 12:14 | 2,484.80 | DAY LOCK flatten | +2.35 | +5,832 | 192 | **+5,640** |
| 2 | ORB (RVOL 4.9x) | 2,500 | ICICIBANK | SELL | 317 | 09:20:00 | 1,322.90 | 1,330.78 | 09:33 | 1,330.78 | STOP (initial) | -1.00 | -2,497 | 322 | **-2,820** |
| 3 | ORB (RVOL 3.8x) | 1,016 cap | BPCL | SELL | 284 | 09:21:00 | 357.05 | 360.62 | 12:14 | 353.80 | DAY LOCK flatten | +0.91 | +923 | 113 | **+810** |
| 4 | Funnel score 1 | 1,250 ½ | VEDL | BUY | 409 | 10:18:30 | 376.25 | 373.20 | 12:14 | 378.75 | DAY LOCK flatten | +0.82 | +1,022 | 149 | **+874** |
| 5 | Funnel score 0 | 500 ½ | RELIANCE | SELL | 113 | 11:24:30 | 1,415.00 | 1,419.40 | 11:37 | 1,419.40 | STOP (initial) | -1.00 | -497 | 152 | **-649** |

## 2026-03-09 — 3 trades, before charges ₹+1,024, after charges ₹+394

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 7.0x) | 2,500 | BPCL | SELL | 675 | 09:21:00 | 326.15 | 329.85 | 09:33 | 325.95 | DAY LOCK flatten | +0.05 | +135 | 192 | **-57** |
| 2 | ORB (RVOL 5.6x) | 2,500 | INDIGO | SELL | 54 | 09:21:00 | 4,100.00 | 4,146.09 | 09:33 | 4,067.60 | DAY LOCK flatten | +0.70 | +1,750 | 192 | **+1,557** |
| 3 | ORB (RVOL 6.1x) | 2,500 | ASIANPAINT | SELL | 141 | 09:23:00 | 2,169.00 | 2,186.63 | 09:33 | 2,175.10 | DAY LOCK flatten | -0.35 | -860 | 247 | **-1,107** |

## 2026-03-10 — 4 trades, before charges ₹-476, after charges ₹-1,319

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.6x) | 2,500 | BPCL | SELL | 581 | 09:23:00 | 328.95 | 333.25 | 14:50 | 325.50 | SQUARE_OFF 14:50 | +0.80 | +2,004 | 173 | **+1,832** |
| 2 | Funnel score 1 | 2,500 | ABB | SELL | 104 | 10:07:30 | 6,159.50 | 6,183.50 | 10:42 | 6,183.50 | STOP (initial) | -1.00 | -2,496 | 466 | **-2,962** |
| 3 | Funnel score 0 | 500 ½ | SHRIRAMFIN | BUY | 33 | 10:47:30 | 1,043.70 | 1,028.60 | 14:50 | 1,059.20 | SQUARE_OFF 14:50 | +1.03 | +512 | 47 | **+464** |
| 4 | Funnel score 0 | 500 ½ | ADANIENT | SELL | 84 | 12:15:30 | 1,986.70 | 1,992.60 | 12:56 | 1,992.60 | STOP (initial) | -1.00 | -496 | 157 | **-652** |

## 2026-03-11 — 7 trades, before charges ₹-845, after charges ₹-3,015

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | Funnel score 1 | 2,500 | JINDALSTEL | SELL | 657 | 10:10:30 | 1,191.00 | 1,194.80 | 10:17 | 1,194.80 | STOP (initial) | -1.00 | -2,497 | 561 | **-3,057** |
| 2 | Funnel score 2 | 2,250 ½ | DRREDDY | BUY | 661 | 10:17:30 | 1,326.90 | 1,323.50 | 10:46 | 1,329.10 | PROFIT LOCK/TRAIL | +0.65 | +1,454 | 622 | **+832** |
| 3 | Funnel score 1 | 1,250 ½ | VBL | SELL | 757 | 10:20:30 | 438.50 | 440.15 | 11:00 | 438.05 | PROFIT LOCK/TRAIL | +0.27 | +341 | 265 | **+76** |
| 4 | Funnel score 1 | 1,250 ½ | MAZDOCK | SELL | 119 | 10:21:30 | 2,461.50 | 2,472.00 | 10:27 | 2,472.00 | STOP (initial) | -1.00 | -1,250 | 239 | **-1,489** |
| 5 | Funnel score 3 | 1,254 ½ cap | ADANIPOWER | BUY | 1929 | 10:22:30 | 140.64 | 139.99 | 10:39 | 141.00 | PROFIT LOCK/TRAIL | +0.55 | +694 | 225 | **+470** |
| 6 | Funnel score 0 | 500 ½ | HINDZINC | SELL | 294 | 11:20:30 | 593.25 | 594.95 | 11:41 | 594.95 | STOP (initial) | -1.00 | -500 | 162 | **-661** |
| 7 | Funnel score 0 | 500 ½ | HDFCAMC | SELL | 32 | 11:55:30 | 2,430.30 | 2,445.60 | 14:50 | 2,401.80 | SQUARE_OFF 14:50 | +1.86 | +912 | 97 | **+815** |

## 2026-03-12 — 10 trades, before charges ₹+14,850, after charges ₹+12,795

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.0x) | 2,500 | JINDALSTEL | SELL | 214 | 09:20:00 | 1,164.00 | 1,175.66 | 09:26 | 1,175.66 | STOP (initial) | -1.00 | -2,495 | 211 | **-2,705** |
| 2 | ORB (RVOL 3.5x) | 1,250 ½ | BRITANNIA | SELL | 31 | 09:34:00 | 5,830.00 | 5,869.68 | 09:59 | 5,869.68 | STOP (initial) | -1.00 | -1,230 | 165 | **-1,395** |
| 3 | Funnel score 1 | 1,250 ½ | VBL | SELL | 233 | 10:23:30 | 415.15 | 420.50 | 14:50 | 414.30 | SQUARE_OFF 14:50 | +0.16 | +198 | 110 | **+88** |
| 4 | Funnel score 3 | 3,000 ½ | BPCL | BUY | 1333 | 10:26:30 | 324.30 | 322.05 | 10:28 | 324.85 | PROFIT LOCK/TRAIL | +0.24 | +733 | 330 | **+403** |
| 5 | Funnel score 3 | 1,754 ½ cap | COALINDIA | BUY | 899 | 10:26:30 | 450.95 | 449.00 | 14:50 | 469.70 | SQUARE_OFF 14:50 | +9.62 | +16,856 | 318 | **+16,538** |
| 6 | Funnel score 1 | 1,250 ½ | DRREDDY | BUY | 173 | 10:34:30 | 1,320.50 | 1,313.30 | 12:34 | 1,322.30 | PROFIT LOCK/TRAIL | +0.25 | +311 | 197 | **+115** |
| 7 | Funnel score 0 | 500 ½ | ADANIGREEN | BUY | 161 | 10:57:30 | 860.60 | 857.50 | 11:06 | 862.30 | PROFIT LOCK/TRAIL | +0.55 | +274 | 138 | **+136** |
| 8 | Funnel score 0 | 500 ½ | CUMMINSIND | BUY | 20 | 11:00:30 | 4,626.85 | 4,602.65 | 14:50 | 4,760.30 | SQUARE_OFF 14:50 | +5.51 | +2,669 | 109 | **+2,560** |
| 9 | Funnel score 1 | 1,250 ½ | SBIN | SELL | 347 | 11:36:30 | 1,074.40 | 1,078.00 | 12:22 | 1,078.00 | STOP (initial) | -1.00 | -1,249 | 291 | **-1,540** |
| 10 | Funnel score 1 | 1,250 ½ | APOLLOHOSP | SELL | 28 | 11:48:30 | 7,549.00 | 7,592.50 | 12:57 | 7,592.50 | STOP (initial) | -1.00 | -1,218 | 185 | **-1,403** |

## 2026-03-13 — 3 trades, before charges ₹+2,893, after charges ₹+1,756

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.0x) | 2,500 | M&M | BUY | 73 | 09:27:00 | 3,005.50 | 2,971.63 | 10:42 | 3,000.10 | DAY LOCK flatten | -0.16 | -394 | 191 | **-586** |
| 2 | Funnel score 1 | 2,500 | ZYDUSLIFE | SELL | 819 | 09:51:30 | 910.65 | 913.70 | 10:42 | 905.50 | DAY LOCK flatten | +1.69 | +4,218 | 535 | **+3,683** |
| 3 | Funnel score 1 | 2,500 | JSWSTEEL | SELL | 490 | 10:35:30 | 1,132.00 | 1,137.10 | 10:42 | 1,133.90 | DAY LOCK flatten | -0.37 | -931 | 411 | **-1,342** |

## 2026-03-16 — 7 trades, before charges ₹-1,726, after charges ₹-3,393

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.7x) | 2,500 | HDFCAMC | SELL | 95 | 09:20:00 | 2,306.00 | 2,332.12 | 09:38 | 2,332.12 | STOP (initial) | -1.00 | -2,481 | 191 | **-2,673** |
| 2 | ORB (RVOL 5.2x) | 2,500 | ADANIPOWER | SELL | 1430 | 09:21:00 | 148.80 | 150.55 | 09:27 | 148.36 | PROFIT LOCK/TRAIL | +0.25 | +625 | 186 | **+439** |
| 3 | ORB (RVOL 4.0x) | 1,250 ½ | MUTHOOTFIN | BUY | 30 | 09:57:00 | 3,360.00 | 3,319.36 | 10:15 | 3,319.36 | STOP (initial) | -1.00 | -1,219 | 113 | **-1,332** |
| 4 | Funnel score 3 | 3,000 ½ | HYUNDAI | SELL | 454 | 09:59:30 | 1,953.90 | 1,960.50 | 10:00 | 1,952.20 | PROFIT LOCK/TRAIL | +0.26 | +772 | 629 | **+143** |
| 5 | Funnel score 0 | 500 ½ | PFC | SELL | 270 | 10:39:30 | 399.15 | 401.00 | 10:58 | 398.65 | PROFIT LOCK/TRAIL | +0.27 | +135 | 118 | **+17** |
| 6 | Funnel score 1 | 1,250 ½ | MUTHOOTFIN | BUY | 100 | 11:15:30 | 3,327.60 | 3,315.10 | 12:04 | 3,330.70 | PROFIT LOCK/TRAIL | +0.25 | +310 | 265 | **+45** |
| 7 | Funnel score 0 | 500 ½ | SUNPHARMA | SELL | 102 | 11:37:30 | 1,778.40 | 1,783.30 | 11:50 | 1,777.10 | PROFIT LOCK/TRAIL | +0.27 | +133 | 165 | **-33** |

## 2026-03-17 — 3 trades, before charges ₹-3,495, after charges ₹-4,105

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | Funnel score 1 | 2,500 | ASIANPAINT | BUY | 208 | 10:20:30 | 2,259.90 | 2,247.90 | 10:33 | 2,247.90 | STOP (initial) | -1.00 | -2,496 | 354 | **-2,850** |
| 2 | Funnel score 0 | 500 ½ | HDFCLIFE | BUY | 256 | 10:55:30 | 632.90 | 630.95 | 11:08 | 630.95 | STOP (initial) | -1.00 | -499 | 153 | **-652** |
| 3 | Funnel score 0 | 500 ½ | ADANIGREEN | SELL | 98 | 12:17:30 | 848.90 | 854.00 | 12:47 | 854.00 | STOP (initial) | -1.00 | -500 | 102 | **-602** |

## 2026-03-18 — 1 trades, before charges ₹+1,699, after charges ₹+1,251

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | Funnel score 2 | 4,500 | INDIGO | SELL | 141 | 09:45:30 | 4,351.55 | 4,383.45 | 10:14 | 4,339.50 | DAY LOCK flatten | +0.38 | +1,699 | 448 | **+1,251** |

## 2026-03-19 — 2 trades, before charges ₹+1,480, after charges ₹+1,058

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.2x) | 2,500 | HDFCAMC | BUY | 93 | 09:22:00 | 2,364.90 | 2,338.09 | 09:30 | 2,374.10 | DAY LOCK flatten | +0.34 | +856 | 192 | **+664** |
| 2 | ORB (RVOL 9.6x) | 2,500 | HDFCBANK | BUY | 345 | 09:23:00 | 806.85 | 799.62 | 09:30 | 808.66 | PROFIT LOCK/TRAIL | +0.25 | +624 | 230 | **+394** |

## 2026-03-20 — 5 trades, before charges ₹-3,640, after charges ₹-5,010

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.1x) | 2,500 | TATAPOWER | BUY | 576 | 09:20:00 | 411.85 | 407.51 | 10:18 | 412.93 | PROFIT LOCK/TRAIL | +0.25 | +625 | 202 | **+423** |
| 2 | ORB (RVOL 3.2x) | 2,500 | HDFCBANK | BUY | 290 | 09:20:00 | 792.00 | 783.39 | 10:08 | 783.39 | STOP (initial) | -1.00 | -2,497 | 197 | **-2,694** |
| 3 | ORB (RVOL 6.4x) | 2,027 cap | LTM | SELL | 42 | 09:38:00 | 4,070.00 | 4,117.23 | 10:24 | 4,106.60 | HARD DAY STOP flatten | -0.77 | -1,537 | 159 | **-1,697** |
| 4 | Funnel score 3 | 3,000 ½ | BAJAJFINSV | SELL | 491 | 10:13:30 | 1,716.30 | 1,722.40 | 10:24 | 1,716.80 | HARD DAY STOP flatten | -0.08 | -246 | 599 | **-845** |
| 5 | Funnel score 2 | 1,021 ½ cap | LODHA | SELL | 304 | 10:17:30 | 825.35 | 828.70 | 10:24 | 825.30 | HARD DAY STOP flatten | +0.01 | +15 | 212 | **-197** |

## 2026-03-23 — 5 trades, before charges ₹+10,941, after charges ₹+9,158

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.8x) | 2,500 | GRASIM | SELL | 87 | 09:20:00 | 2,571.40 | 2,599.87 | 14:50 | 2,527.90 | SQUARE_OFF 14:50 | +1.53 | +3,784 | 194 | **+3,591** |
| 2 | ORB (RVOL 5.1x) | 2,500 | MUTHOOTFIN | SELL | 59 | 09:27:00 | 3,160.20 | 3,202.22 | 14:50 | 3,118.20 | SQUARE_OFF 14:50 | +1.00 | +2,478 | 170 | **+2,308** |
| 3 | ORB (RVOL 3.2x) | 2,500 | MAXHEALTH | SELL | 249 | 09:55:00 | 950.30 | 960.31 | 12:37 | 947.80 | PROFIT LOCK/TRAIL | +0.25 | +623 | 202 | **+421** |
| 4 | Funnel score 2 | 3,926 cap | UNITDSPR | BUY | 754 | 10:01:30 | 1,277.30 | 1,272.10 | 10:12 | 1,278.60 | PROFIT LOCK/TRAIL | +0.25 | +980 | 678 | **+302** |
| 5 | Funnel score 1 | 2,500 | ENRIN | SELL | 277 | 10:13:30 | 2,710.10 | 2,719.10 | 13:50 | 2,699.00 | PROFIT LOCK/TRAIL | +1.23 | +3,075 | 538 | **+2,536** |

## 2026-03-24 — 4 trades, before charges ₹+4,554, after charges ₹+3,366

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.6x) | 2,500 | LODHA | SELL | 188 | 09:20:00 | 732.20 | 745.47 | 11:18 | 722.60 | DAY LOCK flatten | +0.72 | +1,805 | 137 | **+1,668** |
| 2 | ORB (RVOL 3.0x) | 2,500 | GAIL | SELL | 1132 | 09:20:00 | 137.30 | 139.51 | 11:18 | 135.79 | DAY LOCK flatten | +0.68 | +1,709 | 149 | **+1,560** |
| 3 | Funnel score 1 | 2,500 | ENRIN | SELL | 304 | 10:11:30 | 2,701.80 | 2,710.00 | 10:48 | 2,696.90 | PROFIT LOCK/TRAIL | +0.60 | +1,490 | 585 | **+905** |
| 4 | Funnel score 1 | 2,500 | SHREECEM | BUY | 18 | 11:12:30 | 22,820.00 | 22,685.00 | 11:18 | 22,795.00 | DAY LOCK flatten | -0.19 | -450 | 316 | **-766** |

## 2026-03-25 — 4 trades, before charges ₹-2,910, after charges ₹-3,475

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 5.5x) | 2,500 | LODHA | BUY | 203 | 09:20:00 | 752.60 | 740.32 | 11:47 | 740.32 | STOP (initial) | -1.00 | -2,494 | 147 | **-2,641** |
| 2 | ORB (RVOL 12.6x) | 2,500 | UNITDSPR | SELL | 154 | 09:22:00 | 1,321.00 | 1,337.22 | 14:50 | 1,314.10 | SQUARE_OFF 14:50 | +0.43 | +1,063 | 180 | **+882** |
| 3 | ORB (RVOL 4.8x) | 1,008 cap | ENRIN | BUY | 27 | 09:43:00 | 2,750.90 | 2,714.48 | 12:19 | 2,714.48 | STOP (initial) | -1.00 | -983 | 95 | **-1,078** |
| 4 | Funnel score 0 | 500 ½ | TRENT | BUY | 62 | 12:44:30 | 2,350.80 | 2,342.80 | 13:31 | 2,342.80 | STOP (initial) | -1.00 | -496 | 142 | **-638** |

## 2026-03-27 — 3 trades, before charges ₹+8,030, after charges ₹+7,287

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | Funnel score 1 | 2,500 | RELIANCE | SELL | 396 | 09:57:30 | 1,375.60 | 1,381.90 | 14:50 | 1,348.50 | SQUARE_OFF 14:50 | +4.30 | +10,732 | 403 | **+10,329** |
| 2 | ORB (RVOL 3.4x) | 2,500 | TATACAP | SELL | 771 | 10:07:00 | 315.75 | 318.99 | 12:34 | 318.99 | STOP (initial) | -1.00 | -2,500 | 207 | **-2,707** |
| 3 | ORB (RVOL 3.2x) | 1,005 cap | CIPLA | BUY | 106 | 10:47:00 | 1,239.90 | 1,230.46 | 14:50 | 1,238.00 | SQUARE_OFF 14:50 | -0.20 | -201 | 133 | **-335** |

## 2026-03-30 — 2 trades, before charges ₹-4,769, after charges ₹-5,167

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.8x) | 2,500 | KOTAKBANK | SELL | 700 | 09:20:00 | 352.95 | 356.52 | 09:34 | 356.20 | HARD DAY STOP flatten | -0.91 | -2,275 | 209 | **-2,484** |
| 2 | ORB (RVOL 3.8x) | 2,500 | AXISBANK | SELL | 187 | 09:20:00 | 1,157.50 | 1,170.84 | 09:34 | 1,170.84 | STOP (initial) | -1.00 | -2,494 | 189 | **-2,684** |

**Month: 81 trades, before charges ₹+27,715, Kite charges ₹12,546, slippage ₹7,389, after charges ₹+7,780**