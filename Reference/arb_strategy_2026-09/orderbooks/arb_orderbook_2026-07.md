# ARB (Adaptive Risk Budget) — ORB + Funnel + Day lock — 2026-07 order book (backtest)

ORB: top 3 stocks by opening RVOL (≥3×) at 09:20, direction of first 5m candle, entry on break of 5m high/low until 11:00, stop 0.35×ATR14, ₹2,500 risk. Funnel: vol ≥2×, entry <13:00, stop ≥0.25%, VWAP off, skip repeat triggers; risk by quality score 0/1/2/3+ = ₹1,000 (max 2/day) / ₹2,500 / ₹4,500 / ₹6,000. Exits (both): initial stop → at +1R lock +0.25R → after +2R keep 25% of peak → 14:50. Day lock: once day P&L (closed + open) peaks ≥ ₹3,000, flatten everything and stop if it falls to 40% of the peak. ARB loss control: half size after the day's first losing trade; open-risk cap = ₹6,000 + today's profit so far (house money); a trade stops counting against the cap once it has closed a 1m bar at ≥ +1R (its stop is then in profit); HARD DAY STOP — if day P&L (closed + open) hits −₹5,000, flatten everything and stop. ₹5L capital. After charges = gross − Kite charges − 3 bps slippage.

## 2026-07-01 — 5 trades, before charges ₹-4,434, after charges ₹-5,496

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.1x) | 2,500 | KOTAKBANK | SELL | 858 | 09:25:00 | 394.60 | 397.51 | 11:50 | 398.00 | STOP (initial) | -1.17 | -2,917 | 269 | **-3,186** |
| 2 | ORB (RVOL 3.1x) | 2,500 | DMART | SELL | 69 | 09:34:00 | 4,241.00 | 4,277.10 | 11:28 | 4,277.10 | STOP (initial) | -1.00 | -2,491 | 239 | **-2,730** |
| 3 | ORB (RVOL 3.8x) | 1,009 cap | CANBK | BUY | 913 | 09:40:00 | 126.50 | 125.40 | 11:50 | 126.91 | HARD DAY STOP flatten | +0.37 | +374 | 122 | **+252** |
| 4 | Funnel score 1 | 1,250 ½ | TMCV | SELL | 308 | 11:35:30 | 424.35 | 428.40 | 11:50 | 423.45 | HARD DAY STOP flatten | +0.22 | +277 | 133 | **+144** |
| 5 | Funnel score 1 | 1,244 ½ cap | MUTHOOTFIN | SELL | 129 | 11:35:30 | 2,970.90 | 2,980.50 | 11:50 | 2,968.40 | HARD DAY STOP flatten | +0.26 | +322 | 298 | **+25** |

## 2026-07-02 — 3 trades, before charges ₹+1,994, after charges ₹+1,259

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.2x) | 2,500 | DMART | SELL | 70 | 09:20:00 | 4,250.00 | 4,285.51 | 09:45 | 4,216.20 | DAY LOCK flatten | +0.95 | +2,366 | 242 | **+2,124** |
| 2 | ORB (RVOL 3.5x) | 2,500 | CANBK | SELL | 2427 | 09:20:00 | 127.78 | 128.81 | 09:45 | 128.12 | DAY LOCK flatten | -0.33 | -825 | 250 | **-1,076** |
| 3 | ORB (RVOL 6.0x) | 2,500 | TVSMOTOR | SELL | 84 | 09:38:00 | 3,557.30 | 3,586.90 | 09:45 | 3,551.90 | DAY LOCK flatten | +0.18 | +454 | 243 | **+210** |

## 2026-07-03 — 4 trades, before charges ₹-4,600, after charges ₹-5,326

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 13.9x) | 2,500 | CGPOWER | SELL | 289 | 09:21:00 | 891.20 | 899.84 | 09:23 | 899.84 | STOP (initial) | -1.00 | -2,496 | 216 | **-2,711** |
| 2 | ORB (RVOL 18.3x) | 1,250 ½ | DMART | SELL | 34 | 09:28:00 | 3,995.00 | 4,031.62 | 10:19 | 4,016.50 | HARD DAY STOP flatten | -0.59 | -731 | 136 | **-867** |
| 3 | ORB (RVOL 7.5x) | 1,250 ½ | UNIONBANK | SELL | 1020 | 09:28:00 | 162.60 | 163.82 | 10:16 | 163.82 | STOP (initial) | -1.00 | -1,249 | 155 | **-1,404** |
| 4 | Funnel score 1 | 1,250 ½ | INDHOTEL | BUY | 357 | 09:46:30 | 730.05 | 726.55 | 10:19 | 729.70 | HARD DAY STOP flatten | -0.10 | -125 | 218 | **-343** |

## 2026-07-06 — 4 trades, before charges ₹-579, after charges ₹-1,620

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | Funnel score 2 | 4,500 | GODREJCP | BUY | 511 | 10:20:30 | 1,099.75 | 1,090.95 | 10:28 | 1,101.95 | PROFIT LOCK/TRAIL | +0.25 | +1,124 | 416 | **+708** |
| 2 | Funnel score 0 | 1,000 | TVSMOTOR | BUY | 38 | 10:39:30 | 3,712.70 | 3,686.80 | 14:50 | 3,698.00 | SQUARE_OFF 14:50 | -0.57 | -559 | 139 | **-698** |
| 3 | Funnel score 2 | 4,500 | MOTHERSON | SELL | 2356 | 10:51:30 | 145.10 | 147.01 | 14:50 | 145.09 | SQUARE_OFF 14:50 | +0.01 | +24 | 270 | **-247** |
| 4 | Funnel score 1 | 1,172 cap | SIEMENS | SELL | 73 | 10:55:30 | 3,526.90 | 3,542.90 | 14:30 | 3,542.90 | STOP (initial) | -1.00 | -1,168 | 216 | **-1,384** |

## 2026-07-07 — 7 trades, before charges ₹+26,563, after charges ₹+23,492

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.0x) | 2,500 | VBL | SELL | 519 | 09:21:00 | 491.50 | 496.31 | 14:50 | 485.50 | SQUARE_OFF 14:50 | +1.25 | +3,114 | 215 | **+2,899** |
| 2 | ORB (RVOL 11.9x) | 2,500 | TRENT | SELL | 80 | 09:45:00 | 3,010.00 | 3,040.99 | 14:50 | 2,946.60 | SQUARE_OFF 14:50 | +2.05 | +5,072 | 204 | **+4,868** |
| 3 | Funnel score 2 | 4,500 | SBILIFE | BUY | 642 | 09:54:30 | 1,829.60 | 1,822.60 | 10:04 | 1,831.30 | PROFIT LOCK/TRAIL | +0.24 | +1,091 | 816 | **+275** |
| 4 | Funnel score 3 | 6,000 | TECHM | BUY | 582 | 09:59:30 | 1,424.60 | 1,418.20 | 14:50 | 1,451.00 | SQUARE_OFF 14:50 | +4.13 | +15,365 | 595 | **+14,770** |
| 5 | Funnel score 2 | 4,500 | TVSMOTOR | BUY | 315 | 10:05:30 | 3,730.10 | 3,717.10 | 10:14 | 3,737.10 | PROFIT LOCK/TRAIL | +0.54 | +2,205 | 817 | **+1,388** |
| 6 | Funnel score 0 | 1,000 | MUTHOOTFIN | SELL | 71 | 11:02:30 | 3,105.80 | 3,119.70 | 11:29 | 3,119.70 | STOP (initial) | -1.00 | -987 | 192 | **-1,179** |
| 7 | Funnel score 0 | 1,000 | ADANIGREEN | BUY | 185 | 11:25:30 | 1,529.30 | 1,523.90 | 11:51 | 1,533.10 | PROFIT LOCK/TRAIL | +0.70 | +703 | 232 | **+471** |

## 2026-07-08 — 4 trades, before charges ₹+12,211, after charges ₹+10,708

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.0x) | 2,500 | ONGC | SELL | 1416 | 09:31:00 | 245.90 | 247.67 | 14:05 | 244.80 | PROFIT LOCK/TRAIL | +0.63 | +1,565 | 275 | **+1,290** |
| 2 | Funnel score 1 | 2,500 | AMBUJACEM | BUY | 925 | 10:16:30 | 433.25 | 430.55 | 13:50 | 430.55 | STOP (initial) | -1.00 | -2,498 | 309 | **-2,807** |
| 3 | Funnel score 1 | 2,500 | MAXHEALTH | SELL | 423 | 11:00:30 | 1,114.20 | 1,120.10 | 14:50 | 1,084.70 | SQUARE_OFF 14:50 | +5.00 | +12,478 | 355 | **+12,123** |
| 4 | Funnel score 1 | 2,500 | BOSCHLTD | SELL | 19 | 11:15:30 | 41,495.00 | 41,625.00 | 11:29 | 41,460.00 | PROFIT LOCK/TRAIL | +0.27 | +665 | 564 | **+101** |

## 2026-07-09 — 5 trades, before charges ₹-3,713, after charges ₹-5,075

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.3x) | 2,500 | TITAN | BUY | 77 | 09:20:00 | 4,662.00 | 4,629.54 | 09:38 | 4,629.54 | STOP (initial) | -1.00 | -2,499 | 282 | **-2,781** |
| 2 | ORB (RVOL 3.2x) | 2,500 | JIOFIN | BUY | 1565 | 09:20:00 | 232.14 | 230.54 | 10:27 | 232.54 | PROFIT LOCK/TRAIL | +0.25 | +625 | 285 | **+339** |
| 3 | Funnel score 2 | 2,250 ½ | INDIGO | SELL | 95 | 09:50:30 | 5,134.50 | 5,158.00 | 10:39 | 5,158.00 | STOP (initial) | -1.00 | -2,232 | 367 | **-2,600** |
| 4 | ORB (RVOL 5.5x) | 1,250 ½ | NTPC | SELL | 549 | 09:53:00 | 345.65 | 347.92 | 10:41 | 345.50 | HARD DAY STOP flatten | +0.07 | +82 | 171 | **-89** |
| 5 | Funnel score 1 | 1,250 ½ | TECHM | BUY | 223 | 10:23:30 | 1,429.10 | 1,423.50 | 10:38 | 1,430.50 | PROFIT LOCK/TRAIL | +0.25 | +312 | 257 | **+56** |

## 2026-07-10 — 6 trades, before charges ₹-3,414, after charges ₹-5,198

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.5x) | 2,500 | LTM | BUY | 58 | 09:20:00 | 4,022.00 | 3,979.54 | 09:24 | 3,979.54 | STOP (initial) | -1.00 | -2,463 | 200 | **-2,662** |
| 2 | Funnel score 0 | 500 ½ | ADANIPORTS | BUY | 36 | 10:35:30 | 1,848.80 | 1,835.00 | 12:45 | 1,838.00 | HARD DAY STOP flatten | -0.78 | -389 | 91 | **-480** |
| 3 | Funnel score 1 | 1,250 ½ | BOSCHLTD | SELL | 8 | 10:57:30 | 42,005.00 | 42,145.00 | 12:45 | 41,890.00 | HARD DAY STOP flatten | +0.82 | +920 | 267 | **+653** |
| 4 | Funnel score 1 | 1,250 ½ | CUMMINSIND | SELL | 67 | 11:19:30 | 5,639.50 | 5,658.00 | 12:02 | 5,634.50 | PROFIT LOCK/TRAIL | +0.27 | +335 | 294 | **+41** |
| 5 | Funnel score 2 | 2,250 ½ | MUTHOOTFIN | SELL | 255 | 12:02:30 | 3,119.50 | 3,128.30 | 12:45 | 3,128.30 | STOP (initial) | -1.00 | -2,244 | 568 | **-2,812** |
| 6 | Funnel score 2 | 2,139 ½ cap | DIVISLAB | SELL | 71 | 12:39:30 | 6,810.50 | 6,840.50 | 12:45 | 6,804.50 | HARD DAY STOP flatten | +0.20 | +426 | 363 | **+63** |

## 2026-07-13 — 2 trades, before charges ₹+1,762, after charges ₹+1,339

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.8x) | 2,500 | BAJAJHLDNG | BUY | 25 | 09:23:00 | 10,574.00 | 10,476.67 | 09:39 | 10,682.00 | DAY LOCK flatten | +1.11 | +2,700 | 221 | **+2,479** |
| 2 | ORB (RVOL 4.6x) | 2,500 | LTM | SELL | 59 | 09:24:00 | 4,000.20 | 4,041.87 | 09:39 | 4,016.10 | DAY LOCK flatten | -0.38 | -938 | 202 | **-1,140** |

## 2026-07-14 — 6 trades, before charges ₹+2,375, after charges ₹+1,255

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 7.0x) | 2,500 | GRASIM | SELL | 122 | 09:20:00 | 3,112.90 | 3,133.26 | 12:36 | 3,110.00 | DAY LOCK flatten | +0.14 | +354 | 296 | **+58** |
| 2 | ORB (RVOL 4.9x) | 2,500 | HCLTECH | BUY | 174 | 09:20:00 | 1,204.00 | 1,189.65 | 10:07 | 1,189.65 | STOP (initial) | -1.00 | -2,496 | 184 | **-2,680** |
| 3 | Funnel score 2 | 2,250 ½ | BAJAJFINSV | SELL | 218 | 10:07:30 | 1,872.60 | 1,882.90 | 12:36 | 1,869.80 | DAY LOCK flatten | +0.27 | +610 | 314 | **+296** |
| 4 | Funnel score 1 | 1,250 ½ | ADANIGREEN | BUY | 82 | 10:12:30 | 1,563.10 | 1,547.90 | 12:36 | 1,605.20 | DAY LOCK flatten | +2.77 | +3,452 | 132 | **+3,320** |
| 5 | Funnel score 0 | 500 ½ | TATASTEEL | BUY | 495 | 10:57:30 | 189.90 | 188.89 | 12:21 | 188.89 | STOP (initial) | -1.00 | -500 | 108 | **-608** |
| 6 | Funnel score 0 | 500 ½ | ADANIENSOL | BUY | 37 | 10:59:30 | 1,664.10 | 1,650.90 | 12:36 | 1,689.90 | DAY LOCK flatten | +1.95 | +955 | 85 | **+870** |

## 2026-07-15 — 4 trades, before charges ₹+1,941, after charges ₹+1,065

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 9.1x) | 2,500 | HYUNDAI | BUY | 136 | 09:20:00 | 2,036.00 | 2,017.68 | 10:46 | 2,038.70 | DAY LOCK flatten | +0.15 | +367 | 228 | **+139** |
| 2 | ORB (RVOL 5.4x) | 2,500 | DIVISLAB | BUY | 45 | 09:20:00 | 7,289.50 | 7,234.35 | 10:46 | 7,284.00 | DAY LOCK flatten | -0.10 | -248 | 262 | **-510** |
| 3 | ORB (RVOL 3.7x) | 1,027 cap | AXISBANK | BUY | 101 | 09:20:00 | 1,337.50 | 1,327.37 | 09:36 | 1,340.03 | PROFIT LOCK/TRAIL | +0.25 | +256 | 136 | **+120** |
| 4 | Funnel score 1 | 1,927 cap | ULTRACEMCO | BUY | 27 | 10:31:30 | 11,516.00 | 11,445.00 | 10:46 | 11,574.00 | DAY LOCK flatten | +0.82 | +1,566 | 251 | **+1,315** |

## 2026-07-16 — 2 trades, before charges ₹+2,902, after charges ₹+2,459

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 14.3x) | 2,500 | HDFCLIFE | SELL | 521 | 09:20:00 | 563.70 | 568.50 | 09:47 | 562.50 | PROFIT LOCK/TRAIL | +0.25 | +625 | 239 | **+385** |
| 2 | ORB (RVOL 5.3x) | 2,500 | HDFCAMC | SELL | 90 | 09:22:00 | 2,647.30 | 2,674.85 | 09:52 | 2,622.00 | DAY LOCK flatten | +0.92 | +2,277 | 203 | **+2,074** |

## 2026-07-17 — 4 trades, before charges ₹-3,483, after charges ₹-4,183

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.2x) | 2,500 | ADANIGREEN | SELL | 128 | 09:21:00 | 1,526.40 | 1,545.81 | 09:31 | 1,545.81 | STOP (initial) | -1.00 | -2,484 | 176 | **-2,659** |
| 2 | Funnel score 1 | 1,250 ½ | BAJAJFINSV | BUY | 192 | 09:50:15 | 1,851.40 | 1,844.90 | 09:56 | 1,844.90 | STOP (initial) | -1.00 | -1,248 | 280 | **-1,528** |
| 3 | Funnel score 0 | 500 ½ | ADANIPOWER | SELL | 609 | 10:49:30 | 214.98 | 215.80 | 13:06 | 214.77 | PROFIT LOCK/TRAIL | +0.26 | +128 | 133 | **-5** |
| 4 | Funnel score 0 | 500 ½ | UNITDSPR | BUY | 71 | 10:52:30 | 1,372.70 | 1,365.70 | 14:07 | 1,374.40 | PROFIT LOCK/TRAIL | +0.24 | +121 | 111 | **+10** |

## 2026-07-20 — 3 trades, before charges ₹+1,942, after charges ₹+1,178

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 14.7x) | 2,500 | PNB | BUY | 3274 | 09:20:00 | 109.89 | 109.13 | 09:42 | 110.62 | DAY LOCK flatten | +0.96 | +2,390 | 284 | **+2,106** |
| 2 | ORB (RVOL 12.7x) | 2,500 | AXISBANK | SELL | 252 | 09:22:00 | 1,262.70 | 1,272.60 | 09:42 | 1,259.40 | DAY LOCK flatten | +0.33 | +832 | 256 | **+575** |
| 3 | ORB (RVOL 4.9x) | 2,196 cap | KOTAKBANK | SELL | 711 | 09:22:00 | 377.20 | 380.28 | 09:42 | 379.00 | DAY LOCK flatten | -0.58 | -1,280 | 223 | **-1,503** |

## 2026-07-21 — 3 trades, before charges ₹+6,332, after charges ₹+5,067

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | Funnel score 1 | 2,500 | PFC | SELL | 2272 | 09:58:30 | 414.95 | 416.05 | 10:06 | 414.65 | PROFIT LOCK/TRAIL | +0.27 | +682 | 665 | **+17** |
| 2 | Funnel score 0 | 1,000 | BPCL | SELL | 1176 | 11:08:30 | 318.65 | 319.50 | 12:16 | 318.40 | PROFIT LOCK/TRAIL | +0.29 | +294 | 293 | **+1** |
| 3 | Funnel score 1 | 2,500 | MUTHOOTFIN | BUY | 130 | 11:46:30 | 3,039.80 | 3,020.70 | 14:50 | 3,081.00 | SQUARE_OFF 14:50 | +2.16 | +5,356 | 308 | **+5,048** |

## 2026-07-22 — 2 trades, before charges ₹+2,115, after charges ₹+1,609

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 7.5x) | 2,500 | BAJAJ-AUTO | BUY | 33 | 09:20:00 | 10,675.00 | 10,599.65 | 09:29 | 10,715.75 | PROFIT LOCK/TRAIL | +0.54 | +1,345 | 278 | **+1,066** |
| 2 | ORB (RVOL 7.2x) | 2,500 | TVSMOTOR | BUY | 70 | 09:20:00 | 3,918.00 | 3,882.53 | 09:29 | 3,929.00 | DAY LOCK flatten | +0.31 | +770 | 227 | **+543** |

## 2026-07-23 — 4 trades, before charges ₹+4,118, after charges ₹+3,007

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 8.5x) | 2,500 | NESTLEIND | SELL | 248 | 09:21:00 | 1,483.30 | 1,493.36 | 10:03 | 1,459.50 | DAY LOCK flatten | +2.37 | +5,902 | 288 | **+5,615** |
| 2 | ORB (RVOL 8.7x) | 2,500 | DRREDDY | BUY | 211 | 09:23:00 | 1,147.00 | 1,135.18 | 10:03 | 1,154.50 | DAY LOCK flatten | +0.63 | +1,582 | 206 | **+1,377** |
| 3 | ORB (RVOL 4.2x) | 2,500 | ADANIGREEN | SELL | 126 | 09:43:00 | 1,403.00 | 1,422.73 | 10:03 | 1,416.50 | DAY LOCK flatten | -0.68 | -1,701 | 163 | **-1,864** |
| 4 | Funnel score 1 | 2,500 | JINDALSTEL | SELL | 595 | 09:55:30 | 1,044.90 | 1,049.10 | 10:03 | 1,047.70 | DAY LOCK flatten | -0.67 | -1,666 | 454 | **-2,120** |

## 2026-07-24 — 2 trades, before charges ₹-4,556, after charges ₹-5,036

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.3x) | 2,500 | INFY | BUY | 251 | 09:20:00 | 1,042.50 | 1,032.56 | 09:26 | 1,034.30 | HARD DAY STOP flatten | -0.82 | -2,058 | 218 | **-2,276** |
| 2 | ORB (RVOL 6.6x) | 2,500 | CIPLA | BUY | 228 | 09:21:00 | 1,441.80 | 1,430.85 | 09:25 | 1,430.85 | STOP (initial) | -1.00 | -2,498 | 262 | **-2,760** |

## 2026-07-27 — 6 trades, before charges ₹+2,831, after charges ₹+1,507

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.5x) | 2,500 | RECLTD | BUY | 899 | 09:22:00 | 365.45 | 362.67 | 10:11 | 362.67 | STOP (initial) | -1.00 | -2,498 | 262 | **-2,760** |
| 2 | ORB (RVOL 3.2x) | 2,500 | HINDZINC | SELL | 611 | 09:30:00 | 537.05 | 541.14 | 12:07 | 531.90 | DAY LOCK flatten | +1.26 | +3,147 | 262 | **+2,885** |
| 3 | Funnel score 1 | 1,003 cap | SIEMENS | BUY | 68 | 10:03:30 | 3,730.10 | 3,715.40 | 10:21 | 3,715.40 | STOP (initial) | -1.00 | -1,000 | 213 | **-1,213** |
| 4 | Funnel score 1 | 1,250 ½ | PIDILITIND | BUY | 271 | 10:23:30 | 1,599.70 | 1,595.10 | 12:07 | 1,612.00 | DAY LOCK flatten | +2.67 | +3,333 | 332 | **+3,001** |
| 5 | Funnel score 0 | 500 ½ | ADANIENSOL | SELL | 58 | 10:54:30 | 1,689.30 | 1,697.90 | 11:19 | 1,687.10 | PROFIT LOCK/TRAIL | +0.26 | +128 | 111 | **+17** |
| 6 | Funnel score 0 | 500 ½ | CGPOWER | BUY | 169 | 11:46:30 | 875.80 | 872.85 | 12:07 | 874.15 | DAY LOCK flatten | -0.56 | -279 | 144 | **-423** |

## 2026-07-28 — 3 trades, before charges ₹+2,118, after charges ₹+1,230

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 7.1x) | 2,500 | COALINDIA | SELL | 1259 | 09:22:00 | 417.50 | 419.49 | 09:30 | 417.00 | PROFIT LOCK/TRAIL | +0.25 | +625 | 391 | **+234** |
| 2 | ORB (RVOL 11.3x) | 2,500 | TATAPOWER | SELL | 1137 | 09:24:00 | 374.70 | 376.90 | 09:30 | 373.70 | DAY LOCK flatten | +0.46 | +1,137 | 327 | **+810** |
| 3 | ORB (RVOL 10.2x) | 1,002 cap | BEL | SELL | 475 | 09:25:00 | 393.55 | 395.66 | 09:30 | 392.80 | DAY LOCK flatten | +0.36 | +356 | 170 | **+186** |

## 2026-07-29 — 2 trades, before charges ₹+2,073, after charges ₹+1,544

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 39.0x) | 2,500 | TATACAP | BUY | 872 | 09:20:00 | 372.00 | 369.13 | 09:27 | 373.48 | PROFIT LOCK/TRAIL | +0.51 | +1,286 | 260 | **+1,026** |
| 2 | ORB (RVOL 8.6x) | 2,500 | HINDUNILVR | BUY | 164 | 09:22:00 | 2,067.00 | 2,051.80 | 09:27 | 2,071.80 | DAY LOCK flatten | +0.32 | +787 | 269 | **+518** |

## 2026-07-30 — 3 trades, before charges ₹-4,638, after charges ₹-5,340

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 17.4x) | 2,500 | ADANIPORTS | SELL | 193 | 09:22:00 | 1,672.20 | 1,685.12 | 09:44 | 1,677.70 | HARD DAY STOP flatten | -0.43 | -1,062 | 259 | **-1,321** |
| 2 | ORB (RVOL 6.0x) | 2,500 | ASIANPAINT | SELL | 130 | 09:23:00 | 2,673.10 | 2,692.21 | 09:29 | 2,692.21 | STOP (initial) | -1.00 | -2,484 | 275 | **-2,759** |
| 3 | ORB (RVOL 7.0x) | 1,250 ½ | EICHERMOT | SELL | 24 | 09:43:00 | 7,735.50 | 7,786.69 | 09:44 | 7,781.00 | HARD DAY STOP flatten | -0.89 | -1,092 | 169 | **-1,261** |

## 2026-07-31 — 7 trades, before charges ₹+13,998, after charges ₹+12,573

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 15.9x) | 2,500 | BAJFINANCE | BUY | 292 | 09:20:00 | 1,098.90 | 1,090.36 | 14:50 | 1,140.00 | SQUARE_OFF 14:50 | +4.81 | +12,001 | 261 | **+11,740** |
| 2 | ORB (RVOL 21.9x) | 2,500 | HYUNDAI | BUY | 142 | 09:30:00 | 2,173.70 | 2,156.15 | 09:37 | 2,156.15 | STOP (initial) | -1.00 | -2,492 | 249 | **-2,741** |
| 3 | ORB (RVOL 7.8x) | 1,250 ½ | BAJAJFINSV | BUY | 92 | 09:40:00 | 1,977.80 | 1,964.28 | 10:55 | 1,981.18 | PROFIT LOCK/TRAIL | +0.25 | +311 | 166 | **+144** |
| 4 | Funnel score 0 | 500 ½ | VEDL | BUY | 400 | 10:37:30 | 266.05 | 264.80 | 11:24 | 266.35 | PROFIT LOCK/TRAIL | +0.24 | +120 | 117 | **+3** |
| 5 | Funnel score 2 | 2,250 ½ | ETERNAL | SELL | 1363 | 10:50:30 | 307.55 | 309.20 | 14:50 | 303.85 | SQUARE_OFF 14:50 | +2.24 | +5,043 | 321 | **+4,722** |
| 6 | Funnel score 1 | 1,250 ½ | SHREECEM | BUY | 6 | 12:28:30 | 26,330.00 | 26,145.00 | 14:20 | 26,145.00 | STOP (initial) | -1.00 | -1,110 | 150 | **-1,260** |
| 7 | Funnel score 0 | 500 ½ | NTPC | BUY | 500 | 12:59:30 | 345.40 | 344.40 | 13:30 | 345.65 | PROFIT LOCK/TRAIL | +0.25 | +125 | 160 | **-35** |

**Month: 91 trades, before charges ₹+55,857, Kite charges ₹14,888, slippage ₹8,949, after charges ₹+32,020**