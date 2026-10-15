# ARB (Adaptive Risk Budget) — ORB + Funnel + Day lock — 2026-08 order book (backtest)

ORB: top 3 stocks by opening RVOL (≥3×) at 09:20, direction of first 5m candle, entry on break of 5m high/low until 11:00, stop 0.35×ATR14, ₹2,500 risk. Funnel: vol ≥2×, entry <13:00, stop ≥0.25%, VWAP off, skip repeat triggers; risk by quality score 0/1/2/3+ = ₹1,000 (max 2/day) / ₹2,500 / ₹4,500 / ₹6,000. Exits (both): initial stop → at +1R lock +0.25R → after +2R keep 25% of peak → 14:50. Day lock: once day P&L (closed + open) peaks ≥ ₹3,000, flatten everything and stop if it falls to 40% of the peak. ARB loss control: half size after the day's first losing trade; open-risk cap = ₹6,000 + today's profit so far (house money); a trade stops counting against the cap once it has closed a 1m bar at ≥ +1R (its stop is then in profit); HARD DAY STOP — if day P&L (closed + open) hits −₹5,000, flatten everything and stop. ₹5L capital. After charges = gross − Kite charges − 3 bps slippage.

## 2026-08-03 — 5 trades, before charges ₹+3,445, after charges ₹+2,281

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 20.8x) | 2,500 | GAIL | SELL | 1849 | 09:20:00 | 175.35 | 176.70 | 10:46 | 173.83 | DAY LOCK flatten | +1.12 | +2,810 | 260 | **+2,551** |
| 2 | ORB (RVOL 21.3x) | 2,500 | MUTHOOTFIN | SELL | 91 | 09:29:00 | 2,812.30 | 2,839.70 | 09:32 | 2,786.70 | PROFIT LOCK/TRAIL | +0.93 | +2,330 | 215 | **+2,115** |
| 3 | ORB (RVOL 13.2x) | 1,487 cap | ITC | BUY | 926 | 09:29:00 | 289.50 | 287.89 | 09:29 | 287.89 | STOP (initial) | -1.00 | -1,486 | 223 | **-1,709** |
| 4 | Funnel score 1 | 1,250 ½ | TITAN | BUY | 82 | 10:16:30 | 4,898.45 | 4,883.25 | 10:46 | 4,894.50 | DAY LOCK flatten | -0.26 | -324 | 310 | **-634** |
| 5 | Funnel score 0 | 500 ½ | AMBUJACEM | BUY | 384 | 10:30:30 | 439.25 | 437.95 | 10:41 | 439.55 | PROFIT LOCK/TRAIL | +0.23 | +115 | 157 | **-42** |

## 2026-08-04 — 3 trades, before charges ₹+1,792, after charges ₹+627

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.0x) | 2,500 | SIEMENS | BUY | 64 | 09:21:00 | 3,961.00 | 3,921.99 | 09:51 | 4,021.00 | DAY LOCK flatten | +1.54 | +3,840 | 214 | **+3,626** |
| 2 | ORB (RVOL 4.3x) | 2,500 | POWERGRID | SELL | 1515 | 09:25:00 | 285.50 | 287.15 | 09:51 | 286.35 | DAY LOCK flatten | -0.52 | -1,288 | 330 | **-1,618** |
| 3 | Funnel score 1 | 2,500 | MUTHOOTFIN | SELL | 304 | 09:51:30 | 2,880.10 | 2,888.30 | 09:51 | 2,882.60 | DAY LOCK flatten | -0.30 | -760 | 620 | **-1,380** |

## 2026-08-05 — 8 trades, before charges ₹+12,260, after charges ₹+9,901

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.5x) | 2,500 | APOLLOHOSP | SELL | 58 | 09:22:00 | 8,832.50 | 8,875.20 | 09:41 | 8,875.20 | STOP (initial) | -1.00 | -2,477 | 382 | **-2,859** |
| 2 | ORB (RVOL 7.3x) | 2,500 | BHARTIARTL | SELL | 236 | 09:23:00 | 1,975.30 | 1,985.89 | 09:53 | 1,972.65 | PROFIT LOCK/TRAIL | +0.25 | +625 | 353 | **+272** |
| 3 | ORB (RVOL 7.3x) | 1,250 ½ | ONGC | SELL | 880 | 09:58:00 | 240.19 | 241.61 | 14:50 | 239.63 | SQUARE_OFF 14:50 | +0.39 | +493 | 185 | **+307** |
| 4 | Funnel score 1 | 1,250 ½ | TRENT | BUY | 131 | 10:25:30 | 3,134.10 | 3,124.60 | 10:35 | 3,124.60 | STOP (initial) | -1.00 | -1,244 | 315 | **-1,560** |
| 5 | Funnel score 1 | 1,250 ½ | ENRIN | SELL | 147 | 10:41:30 | 3,320.20 | 3,328.70 | 10:49 | 3,318.00 | PROFIT LOCK/TRAIL | +0.26 | +323 | 367 | **-44** |
| 6 | Funnel score 0 | 500 ½ | HINDZINC | BUY | 222 | 10:43:30 | 572.90 | 570.65 | 11:34 | 573.45 | PROFIT LOCK/TRAIL | +0.24 | +122 | 131 | **-9** |
| 7 | Funnel score 0 | 500 ½ | BAJAJHLDNG | SELL | 17 | 12:37:30 | 11,430.00 | 11,459.00 | 13:18 | 11,459.00 | STOP (initial) | -1.00 | -493 | 175 | **-668** |
| 8 | Funnel score 2 | 2,250 ½ | TATACAP | BUY | 1666 | 12:46:30 | 365.25 | 363.90 | 14:50 | 374.20 | SQUARE_OFF 14:50 | +6.63 | +14,911 | 450 | **+14,461** |

## 2026-08-06 — 5 trades, before charges ₹+21,115, after charges ₹+19,987

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 6.8x) | 2,500 | HAL | BUY | 88 | 09:20:00 | 4,759.30 | 4,731.12 | 14:50 | 4,942.80 | SQUARE_OFF 14:50 | +6.51 | +16,148 | 327 | **+15,821** |
| 2 | ORB (RVOL 20.3x) | 2,500 | CUMMINSIND | BUY | 53 | 09:22:00 | 5,291.00 | 5,244.49 | 14:50 | 5,371.00 | SQUARE_OFF 14:50 | +1.72 | +4,240 | 231 | **+4,009** |
| 3 | ORB (RVOL 5.8x) | 1,160 cap | HINDZINC | SELL | 252 | 09:23:00 | 597.00 | 601.59 | 14:50 | 591.10 | SQUARE_OFF 14:50 | +1.29 | +1,487 | 146 | **+1,341** |
| 4 | Funnel score 0 | 1,000 | NTPC | BUY | 869 | 11:25:30 | 344.90 | 343.75 | 12:23 | 343.75 | STOP (initial) | -1.00 | -999 | 244 | **-1,243** |
| 5 | Funnel score 0 | 1,000 | IOC | BUY | 1408 | 11:53:30 | 143.72 | 143.01 | 13:14 | 143.89 | PROFIT LOCK/TRAIL | +0.24 | +239 | 180 | **+59** |

## 2026-08-07 — 6 trades, before charges ₹+2,356, after charges ₹+1,177

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 28.2x) | 2,500 | BRITANNIA | BUY | 67 | 09:20:00 | 5,650.00 | 5,612.79 | 09:21 | 5,612.79 | STOP (initial) | -1.00 | -2,493 | 294 | **-2,787** |
| 2 | ORB (RVOL 17.4x) | 1,250 ½ | ENRIN | BUY | 34 | 09:37:00 | 3,591.70 | 3,555.63 | 09:47 | 3,600.72 | PROFIT LOCK/TRAIL | +0.25 | +307 | 128 | **+179** |
| 3 | ORB (RVOL 10.4x) | 1,250 ½ | MOTHERSON | BUY | 774 | 09:54:00 | 163.80 | 162.19 | 13:04 | 168.30 | DAY LOCK flatten | +2.79 | +3,483 | 132 | **+3,351** |
| 4 | Funnel score 0 | 500 ½ | CHOLAFIN | SELL | 62 | 11:20:30 | 1,897.10 | 1,905.10 | 13:04 | 1,882.10 | DAY LOCK flatten | +1.88 | +930 | 124 | **+806** |
| 5 | Funnel score 0 | 500 ½ | BRITANNIA | SELL | 30 | 11:50:30 | 5,566.00 | 5,582.50 | 13:04 | 5,520.00 | DAY LOCK flatten | +2.79 | +1,380 | 157 | **+1,223** |
| 6 | Funnel score 1 | 1,250 ½ | TMCV | BUY | 1000 | 12:57:30 | 454.05 | 452.80 | 13:04 | 452.80 | STOP (initial) | -1.00 | -1,250 | 344 | **-1,594** |

## 2026-08-10 — 4 trades, before charges ₹+2,271, after charges ₹+1,263

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 6.5x) | 2,500 | PFC | SELL | 924 | 09:20:00 | 404.00 | 406.70 | 09:26 | 406.70 | STOP (initial) | -1.00 | -2,498 | 291 | **-2,789** |
| 2 | ORB (RVOL 5.7x) | 2,500 | SBIN | SELL | 336 | 09:20:00 | 1,097.80 | 1,105.22 | 09:59 | 1,081.70 | DAY LOCK flatten | +2.17 | +5,410 | 288 | **+5,121** |
| 3 | ORB (RVOL 10.6x) | 1,250 ½ | TITAN | BUY | 49 | 09:50:00 | 5,037.20 | 5,011.84 | 09:59 | 5,036.50 | DAY LOCK flatten | -0.03 | -34 | 209 | **-243** |
| 4 | Funnel score 1 | 1,250 ½ | TMPV | SELL | 757 | 09:56:30 | 347.95 | 349.60 | 09:59 | 348.75 | DAY LOCK flatten | -0.48 | -606 | 220 | **-826** |

## 2026-08-11 — 7 trades, before charges ₹+4,455, after charges ₹+3,023

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.3x) | 2,500 | PFC | SELL | 737 | 09:21:00 | 384.15 | 387.54 | 14:50 | 381.30 | SQUARE_OFF 14:50 | +0.84 | +2,100 | 232 | **+1,868** |
| 2 | ORB (RVOL 3.6x) | 2,500 | ADANIGREEN | SELL | 168 | 09:24:00 | 1,372.70 | 1,387.57 | 14:50 | 1,361.20 | SQUARE_OFF 14:50 | +0.77 | +1,932 | 199 | **+1,733** |
| 3 | ORB (RVOL 4.2x) | 1,002 cap | ADANIENT | SELL | 44 | 09:25:00 | 3,011.00 | 3,033.28 | 10:19 | 3,033.28 | STOP (initial) | -1.00 | -980 | 134 | **-1,114** |
| 4 | Funnel score 1 | 1,002 ½ cap | DIVISLAB | BUY | 25 | 10:36:30 | 8,398.00 | 8,358.00 | 14:50 | 8,506.00 | SQUARE_OFF 14:50 | +2.70 | +2,700 | 185 | **+2,515** |
| 5 | Funnel score 1 | 1,250 ½ | SBIN | BUY | 367 | 11:14:30 | 1,069.50 | 1,066.10 | 11:45 | 1,066.10 | STOP (initial) | -1.00 | -1,248 | 304 | **-1,552** |
| 6 | Funnel score 0 | 500 ½ | HAL | SELL | 16 | 11:57:30 | 4,879.90 | 4,909.60 | 13:47 | 4,909.60 | STOP (initial) | -1.00 | -475 | 98 | **-574** |
| 7 | Funnel score 1 | 1,250 ½ | TATACONSUM | BUY | 328 | 12:31:30 | 1,083.90 | 1,080.10 | 14:50 | 1,085.20 | SQUARE_OFF 14:50 | +0.34 | +426 | 280 | **+146** |

## 2026-08-12 — 7 trades, before charges ₹-2,685, after charges ₹-3,988

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 106.8x) | 2,500 | GODREJCP | BUY | 292 | 09:20:00 | 943.30 | 934.74 | 09:23 | 934.74 | STOP (initial) | -1.00 | -2,499 | 226 | **-2,725** |
| 2 | ORB (RVOL 10.8x) | 2,500 | ZYDUSLIFE | SELL | 266 | 09:20:00 | 1,172.10 | 1,181.47 | 09:31 | 1,166.82 | PROFIT LOCK/TRAIL | +0.56 | +1,403 | 251 | **+1,152** |
| 3 | ORB (RVOL 4.5x) | 1,250 ½ | SIEMENS | BUY | 31 | 09:25:00 | 4,055.00 | 4,015.81 | 09:27 | 4,064.80 | PROFIT LOCK/TRAIL | +0.25 | +304 | 130 | **+173** |
| 4 | Funnel score 1 | 1,250 ½ | MOTHERSON | SELL | 984 | 09:50:30 | 170.16 | 171.43 | 14:50 | 169.05 | SQUARE_OFF 14:50 | +0.87 | +1,092 | 157 | **+935** |
| 5 | Funnel score 1 | 1,250 ½ | DMART | BUY | 73 | 11:18:30 | 4,061.10 | 4,044.10 | 11:21 | 4,044.10 | STOP (initial) | -1.00 | -1,241 | 241 | **-1,482** |
| 6 | Funnel score 1 | 1,250 ½ | TATACAP | BUY | 625 | 11:27:30 | 368.95 | 366.95 | 13:58 | 366.95 | STOP (initial) | -1.00 | -1,250 | 198 | **-1,448** |
| 7 | Funnel score 0 | 500 ½ | TITAN | SELL | 16 | 12:25:30 | 5,001.55 | 5,032.45 | 14:02 | 5,032.45 | STOP (initial) | -1.00 | -494 | 99 | **-594** |

## 2026-08-13 — 3 trades, before charges ₹-4,420, after charges ₹-5,086

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 12.2x) | 2,500 | JIOFIN | SELL | 1103 | 09:20:00 | 257.00 | 259.27 | 09:35 | 258.90 | HARD DAY STOP flatten | -0.84 | -2,096 | 234 | **-2,329** |
| 2 | ORB (RVOL 5.5x) | 2,500 | HAL | SELL | 64 | 09:20:00 | 4,915.20 | 4,954.21 | 09:24 | 4,954.21 | STOP (initial) | -1.00 | -2,497 | 254 | **-2,751** |
| 3 | ORB (RVOL 7.5x) | 1,250 ½ | APOLLOHOSP | SELL | 23 | 09:35:00 | 8,662.50 | 8,715.40 | 09:35 | 8,655.00 | HARD DAY STOP flatten | +0.14 | +172 | 178 | **-5** |

## 2026-08-14 — 4 trades, before charges ₹-4,099, after charges ₹-5,010

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 16.9x) | 2,500 | SOLARINDS | SELL | 13 | 09:20:00 | 19,560.00 | 19,746.55 | 09:48 | 19,746.55 | STOP (initial) | -1.00 | -2,425 | 215 | **-2,640** |
| 2 | ORB (RVOL 4.9x) | 2,500 | ADANIENSOL | BUY | 154 | 09:20:00 | 1,597.90 | 1,581.71 | 13:01 | 1,601.40 | HARD DAY STOP flatten | +0.22 | +539 | 208 | **+331** |
| 3 | Funnel score 1 | 1,250 ½ | MAXHEALTH | BUY | 227 | 10:24:30 | 1,003.10 | 997.60 | 13:01 | 1,003.30 | HARD DAY STOP flatten | +0.04 | +45 | 197 | **-151** |
| 4 | Funnel score 3 | 2,259 ½ cap | TATAPOWER | BUY | 961 | 11:44:30 | 390.00 | 387.65 | 12:56 | 387.65 | STOP (initial) | -1.00 | -2,258 | 292 | **-2,550** |

## 2026-08-17 — 4 trades, before charges ₹+7,945, after charges ₹+6,837

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.1x) | 2,500 | HAL | BUY | 60 | 09:20:00 | 5,116.00 | 5,074.44 | 10:31 | 5,074.44 | STOP (initial) | -1.00 | -2,494 | 247 | **-2,741** |
| 2 | Funnel score 0 | 500 ½ | ULTRACEMCO | SELL | 12 | 10:39:30 | 11,581.00 | 11,622.00 | 10:52 | 11,622.00 | STOP (initial) | -1.00 | -492 | 138 | **-630** |
| 3 | Funnel score 3 | 3,000 ½ | JINDALSTEL | BUY | 697 | 11:12:30 | 1,099.50 | 1,095.20 | 14:50 | 1,115.90 | SQUARE_OFF 14:50 | +3.81 | +11,431 | 552 | **+10,879** |
| 4 | Funnel score 0 | 500 ½ | PFC | SELL | 500 | 11:31:30 | 373.75 | 374.75 | 12:46 | 374.75 | STOP (initial) | -1.00 | -500 | 170 | **-670** |

## 2026-08-18 — 2 trades, before charges ₹+6,208, after charges ₹+5,676

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | Funnel score 0 | 1,000 | ITC | SELL | 1249 | 11:20:30 | 271.95 | 272.75 | 14:50 | 270.40 | SQUARE_OFF 14:50 | +1.94 | +1,936 | 269 | **+1,667** |
| 2 | Funnel score 0 | 1,000 | TATACAP | BUY | 909 | 12:48:30 | 358.50 | 357.40 | 14:50 | 363.20 | SQUARE_OFF 14:50 | +4.27 | +4,272 | 262 | **+4,010** |

## 2026-08-19 — 1 trades, before charges ₹-3,925, after charges ₹-5,135

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | Funnel score 3 | 6,000 | BAJAJHLDNG | BUY | 157 | 10:22:30 | 11,325.00 | 11,287.00 | 10:38 | 11,300.00 | HARD DAY STOP flatten | -0.66 | -3,925 | 1,210 | **-5,135** |

## 2026-08-20 — 5 trades, before charges ₹-3,021, after charges ₹-4,096

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.2x) | 2,500 | MUTHOOTFIN | BUY | 77 | 09:20:00 | 2,969.00 | 2,936.88 | 14:50 | 2,967.00 | SQUARE_OFF 14:50 | -0.06 | -154 | 197 | **-351** |
| 2 | ORB (RVOL 3.3x) | 2,500 | JSWSTEEL | SELL | 286 | 09:50:00 | 1,284.60 | 1,293.31 | 10:17 | 1,293.31 | STOP (initial) | -1.00 | -2,492 | 288 | **-2,780** |
| 3 | Funnel score 0 | 500 ½ | TMCV | BUY | 147 | 12:15:30 | 485.45 | 482.05 | 13:07 | 482.05 | STOP (initial) | -1.00 | -500 | 94 | **-594** |
| 4 | Funnel score 1 | 1,250 ½ | HDFCAMC | BUY | 189 | 12:25:30 | 2,593.50 | 2,586.90 | 12:37 | 2,596.80 | PROFIT LOCK/TRAIL | +0.50 | +624 | 369 | **+255** |
| 5 | Funnel score 0 | 500 ½ | RECLTD | SELL | 370 | 12:34:30 | 327.50 | 328.85 | 12:39 | 328.85 | STOP (initial) | -1.00 | -500 | 127 | **-626** |

## 2026-08-21 — 2 trades, before charges ₹+3,436, after charges ₹+1,715

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | Funnel score 2 | 4,500 | JINDALSTEL | SELL | 1500 | 10:08:30 | 1,126.90 | 1,129.90 | 10:16 | 1,124.80 | DAY LOCK flatten | +0.70 | +3,150 | 1,155 | **+1,995** |
| 2 | Funnel score 2 | 4,500 | MUTHOOTFIN | BUY | 260 | 10:11:30 | 3,047.60 | 3,030.30 | 10:16 | 3,048.70 | DAY LOCK flatten | +0.06 | +286 | 566 | **-280** |

## 2026-08-24 — 5 trades, before charges ₹+2,464, after charges ₹+1,058

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.3x) | 2,500 | CIPLA | SELL | 369 | 09:21:00 | 1,411.50 | 1,418.27 | 09:29 | 1,418.27 | STOP (initial) | -1.00 | -2,497 | 389 | **-2,886** |
| 2 | ORB (RVOL 3.5x) | 2,500 | BANKBARODA | SELL | 1840 | 09:23:00 | 244.34 | 245.70 | 12:00 | 241.26 | DAY LOCK flatten | +2.27 | +5,667 | 340 | **+5,327** |
| 3 | Funnel score 0 | 500 ½ | KOTAKBANK | BUY | 370 | 10:30:30 | 402.95 | 401.60 | 10:59 | 401.60 | STOP (initial) | -1.00 | -500 | 144 | **-644** |
| 4 | Funnel score 0 | 500 ½ | MAZDOCK | SELL | 60 | 10:34:30 | 2,542.70 | 2,550.90 | 11:56 | 2,537.80 | PROFIT LOCK/TRAIL | +0.60 | +294 | 147 | **+147** |
| 5 | Funnel score 2 | 2,250 ½ | CUMMINSIND | BUY | 100 | 11:55:30 | 5,167.00 | 5,144.50 | 12:00 | 5,162.00 | DAY LOCK flatten | -0.22 | -500 | 386 | **-886** |

## 2026-08-25 — 3 trades, before charges ₹+26,665, after charges ₹+24,993

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | Funnel score 1 | 2,500 | SIEMENS | BUY | 182 | 10:36:30 | 4,079.70 | 4,066.00 | 12:56 | 4,087.50 | PROFIT LOCK/TRAIL | +0.57 | +1,420 | 534 | **+886** |
| 2 | Funnel score 2 | 3,507 cap | ADANIENSOL | BUY | 876 | 10:55:30 | 1,545.60 | 1,541.60 | 14:50 | 1,573.80 | SQUARE_OFF 14:50 | +7.05 | +24,703 | 942 | **+23,762** |
| 3 | Funnel score 0 | 1,000 | HDFCAMC | BUY | 86 | 12:32:30 | 2,654.80 | 2,643.20 | 13:33 | 2,661.10 | PROFIT LOCK/TRAIL | +0.54 | +542 | 197 | **+345** |

## 2026-08-26 — 2 trades, before charges ₹+3,223, after charges ₹+2,617

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 31.4x) | 2,500 | VBL | SELL | 799 | 09:20:00 | 433.20 | 436.33 | 09:33 | 431.21 | PROFIT LOCK/TRAIL | +0.64 | +1,588 | 274 | **+1,314** |
| 2 | ORB (RVOL 5.3x) | 2,500 | SBILIFE | BUY | 244 | 09:20:00 | 1,775.70 | 1,765.47 | 09:33 | 1,782.40 | DAY LOCK flatten | +0.65 | +1,635 | 332 | **+1,303** |

## 2026-08-27 — 3 trades, before charges ₹+2,291, after charges ₹+1,335

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 5.2x) | 2,500 | JSWSTEEL | SELL | 299 | 09:23:00 | 1,339.00 | 1,347.34 | 10:03 | 1,340.80 | DAY LOCK flatten | -0.22 | -538 | 309 | **-847** |
| 2 | ORB (RVOL 7.5x) | 2,500 | KOTAKBANK | BUY | 1000 | 09:40:00 | 423.40 | 420.90 | 10:03 | 426.05 | DAY LOCK flatten | +1.06 | +2,650 | 326 | **+2,324** |
| 3 | ORB (RVOL 15.2x) | 2,500 | TATAPOWER | SELL | 1194 | 09:59:00 | 349.20 | 351.29 | 10:03 | 349.05 | DAY LOCK flatten | +0.07 | +179 | 320 | **-141** |

## 2026-08-28 — 3 trades, before charges ₹-4,817, after charges ₹-5,674

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.9x) | 2,500 | M&M | SELL | 135 | 09:26:00 | 3,309.70 | 3,328.14 | 09:40 | 3,328.14 | STOP (initial) | -1.00 | -2,489 | 340 | **-2,829** |
| 2 | ORB (RVOL 3.6x) | 2,500 | BHARTIARTL | BUY | 226 | 09:27:00 | 1,893.00 | 1,881.96 | 09:46 | 1,883.50 | HARD DAY STOP flatten | -0.86 | -2,147 | 326 | **-2,473** |
| 3 | ORB (RVOL 12.5x) | 1,250 ½ | ADANIPORTS | SELL | 129 | 09:45:00 | 1,703.00 | 1,712.63 | 09:46 | 1,704.40 | HARD DAY STOP flatten | -0.15 | -181 | 192 | **-372** |

## 2026-08-31 — 4 trades, before charges ₹-3,828, after charges ₹-5,144

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 7.3x) | 2,500 | HDFCBANK | BUY | 823 | 09:23:00 | 738.00 | 734.96 | 09:23 | 734.96 | STOP (initial) | -1.00 | -2,500 | 444 | **-2,944** |
| 2 | Funnel score 1 | 1,250 ½ | DLF | SELL | 490 | 09:56:30 | 665.00 | 667.55 | 10:26 | 667.55 | STOP (initial) | -1.00 | -1,250 | 260 | **-1,510** |
| 3 | Funnel score 1 | 1,250 ½ | BANKBARODA | SELL | 1388 | 10:08:30 | 237.57 | 238.47 | 10:26 | 237.34 | HARD DAY STOP flatten | +0.26 | +319 | 263 | **+56** |
| 4 | Funnel score 1 | 1,250 ½ | ENRIN | SELL | 142 | 10:15:30 | 3,250.20 | 3,259.00 | 10:26 | 3,253.00 | HARD DAY STOP flatten | -0.32 | -398 | 349 | **-747** |

**Month: 86 trades, before charges ₹+73,132, Kite charges ₹15,295, slippage ₹9,479, after charges ₹+48,358**