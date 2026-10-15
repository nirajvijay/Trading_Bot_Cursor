# ARB (Adaptive Risk Budget) — ORB + Funnel + Day lock — 2026-01 order book (backtest)

ORB: top 3 stocks by opening RVOL (≥3×) at 09:20, direction of first 5m candle, entry on break of 5m high/low until 11:00, stop 0.35×ATR14, ₹2,500 risk. Funnel: vol ≥2×, entry <13:00, stop ≥0.25%, VWAP off, skip repeat triggers; risk by quality score 0/1/2/3+ = ₹1,000 (max 2/day) / ₹2,500 / ₹4,500 / ₹6,000. Exits (both): initial stop → at +1R lock +0.25R → after +2R keep 25% of peak → 14:50. Day lock: once day P&L (closed + open) peaks ≥ ₹3,000, flatten everything and stop if it falls to 40% of the peak. ARB loss control: half size after the day's first losing trade; open-risk cap = ₹6,000 + today's profit so far (house money); a trade stops counting against the cap once it has closed a 1m bar at ≥ +1R (its stop is then in profit); HARD DAY STOP — if day P&L (closed + open) hits −₹5,000, flatten everything and stop. ₹5L capital. After charges = gross − Kite charges − 3 bps slippage.

## 2026-01-01 — 7 trades, before charges ₹+55,110, after charges ₹+53,111

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 277.0x) | 2,500 | ITC | SELL | 2079 | 09:20:00 | 375.80 | 377.00 | 14:50 | 347.40 | SQUARE_OFF 14:50 | +23.62 | +59,044 | 555 | **+58,489** |
| 2 | ORB (RVOL 3.9x) | 2,500 | RECLTD | BUY | 1031 | 09:20:00 | 364.70 | 362.28 | 09:33 | 362.28 | STOP (initial) | -1.00 | -2,499 | 292 | **-2,791** |
| 3 | ORB (RVOL 3.1x) | 1,001 cap | PFC | BUY | 442 | 09:20:00 | 361.00 | 358.74 | 09:35 | 358.74 | STOP (initial) | -1.00 | -1,000 | 152 | **-1,152** |
| 4 | Funnel score 1 | 1,250 ½ | TVSMOTOR | SELL | 90 | 10:21:30 | 3,749.30 | 3,763.10 | 10:35 | 3,763.10 | STOP (initial) | -1.00 | -1,242 | 268 | **-1,510** |
| 5 | Funnel score 2 | 2,250 ½ | TECHM | BUY | 401 | 10:29:30 | 1,561.40 | 1,555.80 | 11:33 | 1,562.80 | PROFIT LOCK/TRAIL | +0.25 | +561 | 458 | **+104** |
| 6 | Funnel score 0 | 500 ½ | PFC | BUY | 526 | 10:50:30 | 358.45 | 357.50 | 11:22 | 358.65 | PROFIT LOCK/TRAIL | +0.21 | +105 | 171 | **-66** |
| 7 | Funnel score 0 | 500 ½ | ADANIENT | SELL | 38 | 11:13:30 | 2,259.90 | 2,273.00 | 14:50 | 2,256.20 | SQUARE_OFF 14:50 | +0.28 | +141 | 103 | **+37** |

## 2026-01-02 — 7 trades, before charges ₹+40,612, after charges ₹+38,453

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.6x) | 2,500 | MOTHERSON | BUY | 3117 | 09:20:00 | 124.40 | 123.60 | 10:20 | 123.60 | STOP (initial) | -1.00 | -2,500 | 300 | **-2,800** |
| 2 | ORB (RVOL 3.5x) | 2,500 | RECLTD | BUY | 969 | 09:21:00 | 373.95 | 371.37 | 14:50 | 381.90 | SQUARE_OFF 14:50 | +3.08 | +7,704 | 287 | **+7,416** |
| 3 | ORB (RVOL 5.3x) | 1,003 cap | BOSCHLTD | BUY | 5 | 09:28:00 | 36,600.00 | 36,402.12 | 14:50 | 39,340.00 | SQUARE_OFF 14:50 | +13.85 | +13,700 | 170 | **+13,530** |
| 4 | Funnel score 2 | 4,500 | BPCL | SELL | 4218 | 10:08:30 | 371.40 | 372.40 | 12:41 | 370.35 | PROFIT LOCK/TRAIL | +1.05 | +4,429 | 1,073 | **+3,356** |
| 5 | Funnel score 1 | 1,250 ½ | COALINDIA | BUY | 925 | 10:29:30 | 408.05 | 406.70 | 14:50 | 426.75 | SQUARE_OFF 14:50 | +13.85 | +17,298 | 299 | **+16,998** |
| 6 | Funnel score 1 | 1,250 ½ | MAXHEALTH | SELL | 10 | 10:46:30 | 1,048.40 | 1,051.80 | 10:50 | 1,051.80 | STOP (initial) | -1.00 | -34 | 14 | **-48** |
| 7 | Funnel score 0 | 500 ½ | GAIL | BUY | 62 | 11:14:30 | 169.94 | 169.30 | 14:50 | 170.20 | SQUARE_OFF 14:50 | +0.41 | +16 | 14 | **+2** |

## 2026-01-05 — 6 trades, before charges ₹+5,395, after charges ₹+3,926

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.9x) | 2,500 | BOSCHLTD | SELL | 9 | 09:20:00 | 38,880.00 | 39,147.50 | 09:29 | 39,147.50 | STOP (initial) | -1.00 | -2,408 | 276 | **-2,684** |
| 2 | ORB (RVOL 4.0x) | 2,500 | ONGC | SELL | 1805 | 09:28:00 | 237.10 | 238.48 | 11:11 | 233.20 | DAY LOCK flatten | +2.82 | +7,040 | 327 | **+6,712** |
| 3 | Funnel score 1 | 1,250 ½ | RELIANCE | SELL | 290 | 09:47:30 | 1,596.40 | 1,600.70 | 11:11 | 1,597.70 | DAY LOCK flatten | -0.30 | -377 | 351 | **-728** |
| 4 | Funnel score 1 | 1,250 ½ | HINDUNILVR | BUY | 113 | 10:08:30 | 2,376.10 | 2,365.10 | 11:11 | 2,391.00 | DAY LOCK flatten | +1.35 | +1,684 | 224 | **+1,460** |
| 5 | Funnel score 0 | 500 ½ | VEDL | SELL | 270 | 10:51:30 | 321.35 | 323.20 | 11:11 | 321.45 | DAY LOCK flatten | -0.05 | -27 | 105 | **-132** |
| 6 | ORB (RVOL 3.4x) | 1,250 ½ | UNIONBANK | BUY | 1359 | 10:58:00 | 156.74 | 155.82 | 11:11 | 156.36 | DAY LOCK flatten | -0.41 | -516 | 186 | **-702** |

## 2026-01-06 — 3 trades, before charges ₹+3,756, after charges ₹+2,693

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 9.9x) | 2,500 | RELIANCE | SELL | 358 | 09:21:00 | 1,524.80 | 1,531.78 | 09:23 | 1,524.75 | PROFIT LOCK/TRAIL | +0.01 | +18 | 404 | **-386** |
| 2 | ORB (RVOL 7.7x) | 2,500 | ICICIBANK | BUY | 472 | 09:21:00 | 1,392.40 | 1,387.11 | 09:41 | 1,402.40 | DAY LOCK flatten | +1.89 | +4,720 | 479 | **+4,241** |
| 3 | ORB (RVOL 13.3x) | 1,250 ½ | UNIONBANK | BUY | 1259 | 09:26:00 | 162.16 | 161.17 | 09:41 | 161.38 | DAY LOCK flatten | -0.79 | -982 | 181 | **-1,163** |

## 2026-01-07 — 6 trades, before charges ₹+34,804, after charges ₹+32,619

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 18.0x) | 2,500 | GODREJCP | SELL | 340 | 09:22:00 | 1,257.30 | 1,264.64 | 14:50 | 1,247.30 | SQUARE_OFF 14:50 | +1.36 | +3,400 | 327 | **+3,073** |
| 2 | ORB (RVOL 9.0x) | 2,500 | TORNTPHARM | BUY | 114 | 09:27:00 | 3,981.00 | 3,959.11 | 14:50 | 4,098.00 | SQUARE_OFF 14:50 | +5.34 | +13,338 | 349 | **+12,989** |
| 3 | ORB (RVOL 24.8x) | 2,500 | TITAN | BUY | 136 | 09:40:00 | 4,280.00 | 4,261.70 | 10:34 | 4,284.58 | PROFIT LOCK/TRAIL | +0.25 | +622 | 428 | **+194** |
| 4 | Funnel score 1 | 2,500 | MAXHEALTH | SELL | 568 | 09:55:30 | 1,040.10 | 1,044.50 | 14:50 | 1,030.00 | SQUARE_OFF 14:50 | +2.30 | +5,737 | 434 | **+5,302** |
| 5 | Funnel score 2 | 2,055 cap | DMART | BUY | 119 | 09:59:30 | 3,735.10 | 3,720.50 | 14:50 | 3,831.40 | SQUARE_OFF 14:50 | +6.60 | +11,460 | 341 | **+11,118** |
| 6 | Funnel score 0 | 1,000 | CUMMINSIND | BUY | 95 | 11:59:30 | 4,138.05 | 4,127.55 | 12:36 | 4,140.65 | PROFIT LOCK/TRAIL | +0.25 | +247 | 305 | **-58** |

## 2026-01-08 — 2 trades, before charges ₹-4,545, after charges ₹-5,273

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 11.7x) | 2,500 | SBILIFE | SELL | 238 | 09:20:00 | 2,073.80 | 2,084.26 | 09:27 | 2,082.40 | HARD DAY STOP flatten | -0.82 | -2,047 | 370 | **-2,417** |
| 2 | ORB (RVOL 11.1x) | 2,500 | ABB | BUY | 89 | 09:20:00 | 5,352.00 | 5,323.93 | 09:27 | 5,323.93 | STOP (initial) | -1.00 | -2,499 | 358 | **-2,856** |

## 2026-01-09 — 4 trades, before charges ₹-3,298, after charges ₹-4,023

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 10.6x) | 2,500 | CGPOWER | BUY | 439 | 09:20:00 | 615.45 | 609.77 | 09:22 | 609.77 | STOP (initial) | -1.00 | -2,496 | 223 | **-2,719** |
| 2 | ORB (RVOL 4.1x) | 1,250 ½ | SIEMENS | BUY | 59 | 09:55:00 | 3,073.90 | 3,052.82 | 10:06 | 3,079.17 | PROFIT LOCK/TRAIL | +0.25 | +311 | 165 | **+146** |
| 3 | ORB (RVOL 3.7x) | 1,250 ½ | ASIANPAINT | BUY | 77 | 10:22:00 | 2,834.70 | 2,818.56 | 11:55 | 2,818.56 | STOP (initial) | -1.00 | -1,243 | 190 | **-1,433** |
| 4 | Funnel score 0 | 500 ½ | HDFCLIFE | SELL | 200 | 12:28:30 | 751.90 | 754.40 | 12:51 | 751.25 | PROFIT LOCK/TRAIL | +0.26 | +130 | 147 | **-17** |

## 2026-01-12 — 2 trades, before charges ₹+1,961, after charges ₹+1,112

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 13.9x) | 2,500 | DMART | BUY | 93 | 09:22:00 | 3,907.00 | 3,880.26 | 09:52 | 3,880.26 | STOP (initial) | -1.00 | -2,487 | 284 | **-2,771** |
| 2 | Funnel score 3 | 3,513 cap | NTPC | BUY | 2341 | 09:49:30 | 336.05 | 334.55 | 10:30 | 337.95 | DAY LOCK flatten | +1.27 | +4,448 | 564 | **+3,884** |

## 2026-01-13 — 3 trades, before charges ₹+3,353, after charges ₹+2,392

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 12.1x) | 2,500 | HCLTECH | SELL | 243 | 09:20:00 | 1,666.10 | 1,676.38 | 09:39 | 1,650.00 | DAY LOCK flatten | +1.57 | +3,912 | 312 | **+3,600** |
| 2 | ORB (RVOL 6.2x) | 2,500 | TCS | SELL | 136 | 09:20:00 | 3,220.10 | 3,238.40 | 09:39 | 3,235.30 | DAY LOCK flatten | -0.83 | -2,067 | 333 | **-2,401** |
| 3 | ORB (RVOL 4.6x) | 2,355 cap | LT | SELL | 104 | 09:21:00 | 3,953.20 | 3,975.68 | 09:39 | 3,938.70 | DAY LOCK flatten | +0.65 | +1,508 | 316 | **+1,192** |

## 2026-01-14 — 4 trades, before charges ₹-3,808, after charges ₹-4,489

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.7x) | 2,500 | ONGC | BUY | 1363 | 09:23:00 | 241.45 | 239.62 | 09:29 | 239.62 | STOP (initial) | -1.00 | -2,499 | 262 | **-2,762** |
| 2 | ORB (RVOL 3.2x) | 1,250 ½ | ADANIENSOL | SELL | 125 | 10:18:00 | 920.10 | 930.09 | 12:30 | 930.09 | STOP (initial) | -1.00 | -1,249 | 122 | **-1,371** |
| 3 | Funnel score 0 | 500 ½ | MOTHERSON | BUY | 1666 | 10:58:30 | 114.65 | 114.35 | 12:07 | 114.90 | PROFIT LOCK/TRAIL | +0.83 | +416 | 173 | **+244** |
| 4 | Funnel score 0 | 500 ½ | DIVISLAB | BUY | 18 | 11:27:30 | 6,447.00 | 6,420.50 | 11:38 | 6,420.50 | STOP (initial) | -1.00 | -477 | 123 | **-600** |

## 2026-01-16 — 3 trades, before charges ₹+1,032, after charges ₹+133

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 7.3x) | 2,500 | TECHM | BUY | 240 | 09:21:00 | 1,590.20 | 1,579.80 | 09:35 | 1,591.20 | DAY LOCK flatten | +0.10 | +240 | 296 | **-56** |
| 2 | ORB (RVOL 21.1x) | 2,500 | HDFCAMC | BUY | 137 | 09:27:00 | 2,608.70 | 2,590.46 | 09:35 | 2,621.90 | DAY LOCK flatten | +0.72 | +1,808 | 282 | **+1,526** |
| 3 | ORB (RVOL 10.0x) | 2,500 | CIPLA | BUY | 299 | 09:34:00 | 1,394.40 | 1,386.04 | 09:35 | 1,391.00 | DAY LOCK flatten | -0.41 | -1,017 | 320 | **-1,337** |

## 2026-01-19 — 3 trades, before charges ₹-4,440, after charges ₹-5,064

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 18.9x) | 2,500 | TECHM | BUY | 209 | 09:20:00 | 1,680.30 | 1,668.37 | 09:37 | 1,675.80 | HARD DAY STOP flatten | -0.38 | -940 | 278 | **-1,218** |
| 2 | ORB (RVOL 17.1x) | 2,500 | CGPOWER | BUY | 370 | 09:20:00 | 606.25 | 599.49 | 09:27 | 599.49 | STOP (initial) | -1.00 | -2,500 | 193 | **-2,693** |
| 3 | ORB (RVOL 6.4x) | 1,007 cap | SUNPHARMA | BUY | 97 | 09:20:00 | 1,657.30 | 1,646.99 | 09:25 | 1,646.99 | STOP (initial) | -1.00 | -1,000 | 152 | **-1,152** |

## 2026-01-20 — 6 trades, before charges ₹+566, after charges ₹-566

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 6.4x) | 2,500 | TATACAP | SELL | 713 | 09:23:00 | 361.65 | 365.15 | 10:02 | 360.77 | PROFIT LOCK/TRAIL | +0.25 | +625 | 216 | **+409** |
| 2 | ORB (RVOL 3.2x) | 2,500 | HINDZINC | BUY | 311 | 09:26:00 | 673.75 | 665.72 | 09:35 | 665.72 | STOP (initial) | -1.00 | -2,497 | 184 | **-2,681** |
| 3 | Funnel score 0 | 500 ½ | SHREECEM | SELL | 5 | 10:31:30 | 27,720.00 | 27,820.00 | 14:50 | 27,225.00 | SQUARE_OFF 14:50 | +4.95 | +2,475 | 138 | **+2,337** |
| 4 | ORB (RVOL 14.5x) | 1,250 ½ | LTM | SELL | 27 | 10:56:00 | 5,979.50 | 6,025.01 | 12:33 | 5,968.12 | PROFIT LOCK/TRAIL | +0.25 | +307 | 153 | **+155** |
| 5 | Funnel score 1 | 1,250 ½ | CIPLA | BUY | 304 | 11:03:30 | 1,389.40 | 1,385.30 | 11:21 | 1,385.30 | STOP (initial) | -1.00 | -1,246 | 323 | **-1,570** |
| 6 | Funnel score 1 | 1,250 ½ | ADANIGREEN | SELL | 122 | 11:12:30 | 900.70 | 910.90 | 14:50 | 893.30 | SQUARE_OFF 14:50 | +0.73 | +903 | 118 | **+784** |

## 2026-01-21 — 3 trades, before charges ₹-3,950, after charges ₹-5,147

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 5.1x) | 2,500 | GAIL | SELL | 2222 | 09:20:00 | 155.82 | 156.94 | 10:13 | 156.76 | HARD DAY STOP flatten | -0.84 | -2,089 | 274 | **-2,363** |
| 2 | ORB (RVOL 3.3x) | 2,500 | BHARTIARTL | BUY | 212 | 09:28:00 | 1,986.80 | 1,975.02 | 09:53 | 1,989.74 | PROFIT LOCK/TRAIL | +0.25 | +624 | 323 | **+301** |
| 3 | Funnel score 1 | 2,500 | ULTRACEMCO | SELL | 71 | 09:54:15 | 11,895.00 | 11,930.00 | 09:56 | 11,930.00 | STOP (initial) | -1.00 | -2,485 | 600 | **-3,085** |

## 2026-01-22 — 7 trades, before charges ₹+11,891, after charges ₹+9,874

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 11.4x) | 2,500 | ETERNAL | SELL | 705 | 09:20:00 | 292.70 | 296.25 | 14:50 | 277.75 | SQUARE_OFF 14:50 | +4.22 | +10,540 | 182 | **+10,358** |
| 2 | ORB (RVOL 10.6x) | 2,500 | DRREDDY | BUY | 294 | 09:20:00 | 1,208.20 | 1,199.70 | 09:31 | 1,210.33 | PROFIT LOCK/TRAIL | +0.25 | +625 | 280 | **+345** |
| 3 | ORB (RVOL 4.0x) | 1,001 cap | AXISBANK | BUY | 121 | 09:20:00 | 1,295.00 | 1,286.75 | 10:28 | 1,286.75 | STOP (initial) | -1.00 | -998 | 150 | **-1,148** |
| 4 | Funnel score 2 | 4,500 | GAIL | SELL | 7142 | 09:54:30 | 158.54 | 159.17 | 10:07 | 158.38 | PROFIT LOCK/TRAIL | +0.25 | +1,143 | 789 | **+354** |
| 5 | Funnel score 1 | 1,250 ½ | HYUNDAI | SELL | 158 | 10:38:30 | 2,270.50 | 2,278.40 | 10:42 | 2,268.50 | PROFIT LOCK/TRAIL | +0.25 | +316 | 283 | **+33** |
| 6 | Funnel score 0 | 500 ½ | MUTHOOTFIN | SELL | 48 | 11:18:30 | 3,819.60 | 3,829.90 | 11:22 | 3,817.10 | PROFIT LOCK/TRAIL | +0.24 | +120 | 167 | **-47** |
| 7 | Funnel score 0 | 500 ½ | TMCV | SELL | 416 | 11:30:30 | 438.10 | 439.30 | 12:01 | 437.75 | PROFIT LOCK/TRAIL | +0.29 | +146 | 166 | **-21** |

## 2026-01-23 — 6 trades, before charges ₹+14,804, after charges ₹+13,336

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 7.2x) | 2,500 | DRREDDY | BUY | 259 | 09:21:00 | 1,241.00 | 1,231.37 | 09:41 | 1,231.37 | STOP (initial) | -1.00 | -2,494 | 258 | **-2,751** |
| 2 | ORB (RVOL 5.1x) | 2,500 | DLF | SELL | 369 | 09:36:00 | 610.80 | 617.57 | 14:50 | 593.05 | SQUARE_OFF 14:50 | +2.62 | +6,550 | 194 | **+6,355** |
| 3 | ORB (RVOL 3.1x) | 1,250 ½ | ADANIGREEN | SELL | 133 | 09:45:00 | 886.90 | 896.27 | 14:50 | 778.60 | SQUARE_OFF 14:50 | +11.56 | +14,404 | 123 | **+14,281** |
| 4 | Funnel score 1 | 1,250 ½ | TATACAP | BUY | 1136 | 11:32:30 | 357.40 | 356.30 | 12:08 | 356.30 | STOP (initial) | -1.00 | -1,250 | 312 | **-1,562** |
| 5 | Funnel score 1 | 1,250 ½ | BAJAJHLDNG | SELL | 43 | 11:39:30 | 10,642.00 | 10,671.00 | 12:19 | 10,671.00 | STOP (initial) | -1.00 | -1,247 | 347 | **-1,594** |
| 6 | Funnel score 1 | 1,250 ½ | BOSCHLTD | BUY | 8 | 12:46:30 | 35,470.00 | 35,325.00 | 13:22 | 35,325.00 | STOP (initial) | -1.00 | -1,160 | 234 | **-1,394** |

## 2026-01-27 — 3 trades, before charges ₹-4,764, after charges ₹-5,232

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 13.7x) | 2,500 | ADANIGREEN | SELL | 199 | 09:21:00 | 772.80 | 785.33 | 09:25 | 784.50 | HARD DAY STOP flatten | -0.93 | -2,328 | 148 | **-2,476** |
| 2 | ORB (RVOL 12.7x) | 2,500 | ADANIENT | SELL | 106 | 09:21:00 | 1,895.70 | 1,919.14 | 09:25 | 1,914.70 | HARD DAY STOP flatten | -0.81 | -2,014 | 179 | **-2,193** |
| 3 | ORB (RVOL 8.1x) | 1,021 cap | AXISBANK | BUY | 111 | 09:21:00 | 1,308.30 | 1,299.16 | 09:25 | 1,304.50 | HARD DAY STOP flatten | -0.42 | -422 | 142 | **-563** |

## 2026-01-28 — 3 trades, before charges ₹+1,902, after charges ₹+1,331

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 13.2x) | 2,500 | TATACONSUM | SELL | 254 | 09:20:00 | 1,143.00 | 1,152.82 | 09:25 | 1,140.55 | PROFIT LOCK/TRAIL | +0.25 | +624 | 238 | **+386** |
| 2 | ORB (RVOL 3.8x) | 2,500 | M&M | BUY | 78 | 09:20:00 | 3,417.30 | 3,385.53 | 09:25 | 3,436.60 | DAY LOCK flatten | +0.61 | +1,505 | 222 | **+1,283** |
| 3 | ORB (RVOL 3.5x) | 1,027 cap | ADANIPORTS | BUY | 71 | 09:20:00 | 1,377.50 | 1,363.15 | 09:25 | 1,374.30 | DAY LOCK flatten | -0.22 | -227 | 111 | **-338** |

## 2026-01-29 — 4 trades, before charges ₹+3,257, after charges ₹+2,320

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.8x) | 2,500 | JINDALSTEL | BUY | 217 | 09:21:00 | 1,146.90 | 1,135.40 | 11:01 | 1,152.80 | DAY LOCK flatten | +0.51 | +1,280 | 211 | **+1,070** |
| 2 | ORB (RVOL 5.3x) | 2,500 | HINDZINC | BUY | 230 | 09:29:00 | 707.00 | 696.16 | 10:18 | 709.71 | PROFIT LOCK/TRAIL | +0.25 | +623 | 154 | **+469** |
| 3 | ORB (RVOL 3.8x) | 1,931 cap | BEL | SELL | 453 | 09:30:00 | 447.75 | 452.01 | 10:28 | 445.38 | PROFIT LOCK/TRAIL | +0.56 | +1,076 | 180 | **+896** |
| 4 | Funnel score 1 | 2,500 | RECLTD | SELL | 1388 | 10:11:30 | 379.75 | 381.55 | 11:01 | 379.55 | DAY LOCK flatten | +0.11 | +278 | 393 | **-115** |

## 2026-01-30 — 6 trades, before charges ₹-4,067, after charges ₹-5,149

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.3x) | 2,500 | VEDL | SELL | 581 | 09:20:00 | 375.95 | 380.25 | 09:23 | 380.25 | STOP (initial) | -1.00 | -2,500 | 191 | **-2,691** |
| 2 | ORB (RVOL 4.0x) | 1,250 ½ | PFC | SELL | 320 | 09:27:00 | 380.15 | 384.05 | 10:47 | 380.20 | HARD DAY STOP flatten | -0.01 | -16 | 127 | **-143** |
| 3 | Funnel score 1 | 1,250 ½ | ADANIGREEN | BUY | 297 | 09:49:30 | 862.10 | 857.90 | 10:47 | 858.40 | HARD DAY STOP flatten | -0.88 | -1,099 | 215 | **-1,314** |
| 4 | Funnel score 1 | 1,250 ½ | BANKBARODA | BUY | 1116 | 09:52:30 | 293.36 | 292.24 | 09:57 | 293.64 | PROFIT LOCK/TRAIL | +0.25 | +312 | 262 | **+51** |
| 5 | ORB (RVOL 3.6x) | 1,250 ½ | ABB | BUY | 19 | 10:13:00 | 5,505.50 | 5,440.06 | 10:47 | 5,530.50 | HARD DAY STOP flatten | +0.38 | +475 | 115 | **+360** |
| 6 | Funnel score 1 | 1,250 ½ | ENRIN | BUY | 77 | 10:19:30 | 2,470.00 | 2,453.90 | 10:46 | 2,453.90 | STOP (initial) | -1.00 | -1,240 | 171 | **-1,411** |

**Month: 88 trades, before charges ₹+149,570, Kite charges ₹14,449, slippage ₹8,763, after charges ₹+126,358**