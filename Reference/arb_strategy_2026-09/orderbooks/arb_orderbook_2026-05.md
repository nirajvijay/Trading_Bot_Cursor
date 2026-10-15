# ARB (Adaptive Risk Budget) — ORB + Funnel + Day lock — 2026-05 order book (backtest)

ORB: top 3 stocks by opening RVOL (≥3×) at 09:20, direction of first 5m candle, entry on break of 5m high/low until 11:00, stop 0.35×ATR14, ₹2,500 risk. Funnel: vol ≥2×, entry <13:00, stop ≥0.25%, VWAP off, skip repeat triggers; risk by quality score 0/1/2/3+ = ₹1,000 (max 2/day) / ₹2,500 / ₹4,500 / ₹6,000. Exits (both): initial stop → at +1R lock +0.25R → after +2R keep 25% of peak → 14:50. Day lock: once day P&L (closed + open) peaks ≥ ₹3,000, flatten everything and stop if it falls to 40% of the peak. ARB loss control: half size after the day's first losing trade; open-risk cap = ₹6,000 + today's profit so far (house money); a trade stops counting against the cap once it has closed a 1m bar at ≥ +1R (its stop is then in profit); HARD DAY STOP — if day P&L (closed + open) hits −₹5,000, flatten everything and stop. ₹5L capital. After charges = gross − Kite charges − 3 bps slippage.

## 2026-05-04 — 3 trades, before charges ₹+976, after charges ₹+348

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.5x) | 2,500 | DMART | SELL | 60 | 09:21:00 | 4,430.70 | 4,471.90 | 09:53 | 4,432.20 | DAY LOCK flatten | -0.04 | -90 | 221 | **-311** |
| 2 | ORB (RVOL 4.3x) | 2,500 | HINDUNILVR | BUY | 100 | 09:21:00 | 2,321.60 | 2,296.78 | 09:53 | 2,348.00 | DAY LOCK flatten | +1.06 | +2,640 | 200 | **+2,440** |
| 3 | ORB (RVOL 4.6x) | 2,500 | JINDALSTEL | BUY | 192 | 09:49:00 | 1,273.50 | 1,260.52 | 09:53 | 1,265.30 | DAY LOCK flatten | -0.63 | -1,574 | 207 | **-1,781** |

## 2026-05-05 — 3 trades, before charges ₹+1,642, after charges ₹+498

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | Funnel score 1 | 2,500 | ONGC | SELL | 2873 | 10:18:30 | 289.69 | 290.56 | 11:12 | 288.80 | DAY LOCK flatten | +1.02 | +2,557 | 592 | **+1,965** |
| 2 | Funnel score 1 | 2,500 | GODREJCP | BUY | 431 | 10:23:30 | 1,081.75 | 1,075.95 | 11:12 | 1,082.30 | DAY LOCK flatten | +0.09 | +237 | 353 | **-116** |
| 3 | ORB (RVOL 7.4x) | 2,500 | AMBUJACEM | SELL | 536 | 10:42:00 | 432.30 | 436.96 | 11:12 | 434.45 | DAY LOCK flatten | -0.46 | -1,152 | 199 | **-1,351** |

## 2026-05-06 — 5 trades, before charges ₹-4,267, after charges ₹-5,363

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 6.8x) | 2,500 | LT | SELL | 80 | 09:20:00 | 3,900.10 | 3,931.07 | 09:22 | 3,931.07 | STOP (initial) | -1.00 | -2,478 | 251 | **-2,729** |
| 2 | ORB (RVOL 5.3x) | 2,500 | M&M | BUY | 81 | 09:20:00 | 3,304.00 | 3,273.18 | 10:52 | 3,279.20 | HARD DAY STOP flatten | -0.80 | -2,009 | 221 | **-2,230** |
| 3 | Funnel score 1 | 1,250 ½ | BRITANNIA | SELL | 38 | 09:56:30 | 5,806.50 | 5,839.00 | 10:52 | 5,790.00 | HARD DAY STOP flatten | +0.51 | +627 | 192 | **+435** |
| 4 | Funnel score 1 | 1,250 ½ | RECLTD | SELL | 806 | 10:10:30 | 353.90 | 355.45 | 10:52 | 353.15 | HARD DAY STOP flatten | +0.48 | +604 | 234 | **+370** |
| 5 | Funnel score 3 | 1,019 ½ cap | TRENT | BUY | 81 | 10:11:30 | 2,840.10 | 2,827.60 | 10:37 | 2,827.60 | STOP (initial) | -1.00 | -1,012 | 197 | **-1,210** |

## 2026-05-07 — 3 trades, before charges ₹-4,546, after charges ₹-5,130

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 11.9x) | 2,500 | GODREJCP | SELL | 219 | 09:20:00 | 1,036.00 | 1,047.37 | 09:30 | 1,047.37 | STOP (initial) | -1.00 | -2,489 | 196 | **-2,685** |
| 2 | ORB (RVOL 10.8x) | 2,500 | SHREECEM | BUY | 12 | 09:28:00 | 25,895.00 | 25,691.50 | 09:50 | 25,735.00 | HARD DAY STOP flatten | -0.79 | -1,920 | 250 | **-2,170** |
| 3 | ORB (RVOL 6.4x) | 1,250 ½ | HDFCLIFE | BUY | 228 | 09:32:00 | 614.55 | 609.07 | 09:50 | 613.95 | HARD DAY STOP flatten | -0.11 | -137 | 139 | **-276** |

## 2026-05-08 — 2 trades, before charges ₹+1,929, after charges ₹+1,400

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 19.2x) | 2,500 | BRITANNIA | SELL | 57 | 09:20:00 | 5,551.00 | 5,594.75 | 09:45 | 5,566.00 | DAY LOCK flatten | -0.34 | -855 | 255 | **-1,110** |
| 2 | ORB (RVOL 34.9x) | 2,500 | PIDILITIND | SELL | 232 | 09:31:00 | 1,492.70 | 1,503.44 | 09:45 | 1,480.70 | DAY LOCK flatten | +1.12 | +2,784 | 274 | **+2,510** |

## 2026-05-11 — 3 trades, before charges ₹-4,824, after charges ₹-5,382

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 13.7x) | 2,500 | ABB | SELL | 29 | 09:20:00 | 6,340.00 | 6,424.93 | 09:24 | 6,424.93 | STOP (initial) | -1.00 | -2,463 | 168 | **-2,631** |
| 2 | ORB (RVOL 12.2x) | 2,500 | HYUNDAI | BUY | 157 | 09:20:00 | 1,937.30 | 1,921.43 | 09:27 | 1,925.50 | HARD DAY STOP flatten | -0.74 | -1,853 | 246 | **-2,099** |
| 3 | ORB (RVOL 22.4x) | 1,250 ½ | TATACONSUM | BUY | 121 | 09:25:00 | 1,229.00 | 1,218.73 | 09:27 | 1,224.80 | HARD DAY STOP flatten | -0.41 | -508 | 144 | **-652** |

## 2026-05-12 — 2 trades, before charges ₹+1,957, after charges ₹+1,452

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.6x) | 2,500 | ONGC | BUY | 1038 | 09:20:00 | 292.45 | 290.04 | 09:27 | 293.05 | PROFIT LOCK/TRAIL | +0.25 | +625 | 246 | **+379** |
| 2 | ORB (RVOL 6.7x) | 2,500 | INDHOTEL | SELL | 491 | 09:24:00 | 657.00 | 662.09 | 09:48 | 654.29 | PROFIT LOCK/TRAIL | +0.53 | +1,332 | 259 | **+1,073** |

## 2026-05-13 — 2 trades, before charges ₹+2,248, after charges ₹+1,747

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | Funnel score 2 | 4,500 | CUMMINSIND | BUY | 83 | 10:01:30 | 5,226.50 | 5,172.50 | 13:57 | 5,240.50 | DAY LOCK flatten | +0.26 | +1,162 | 332 | **+830** |
| 2 | ORB (RVOL 4.6x) | 1,518 cap | ASIANPAINT | BUY | 71 | 10:06:00 | 2,613.70 | 2,592.48 | 13:57 | 2,629.00 | DAY LOCK flatten | +0.72 | +1,086 | 170 | **+916** |

## 2026-05-14 — 3 trades, before charges ₹+3,209, after charges ₹+2,620

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 7.3x) | 2,500 | TMCV | SELL | 472 | 09:20:00 | 387.35 | 392.64 | 09:36 | 380.45 | DAY LOCK flatten | +1.30 | +3,257 | 167 | **+3,090** |
| 2 | ORB (RVOL 8.3x) | 2,500 | ADANIENT | BUY | 84 | 09:21:00 | 2,602.70 | 2,573.23 | 09:36 | 2,618.30 | DAY LOCK flatten | +0.53 | +1,310 | 191 | **+1,119** |
| 3 | ORB (RVOL 9.7x) | 2,500 | ZYDUSLIFE | BUY | 289 | 09:24:00 | 979.50 | 970.86 | 09:36 | 974.80 | DAY LOCK flatten | -0.54 | -1,358 | 231 | **-1,590** |

## 2026-05-15 — 6 trades, before charges ₹+2,459, after charges ₹+1,158

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 13.4x) | 2,500 | MUTHOOTFIN | SELL | 70 | 09:20:00 | 3,328.10 | 3,363.63 | 09:36 | 3,363.63 | STOP (initial) | -1.00 | -2,487 | 200 | **-2,687** |
| 2 | ORB (RVOL 10.3x) | 2,500 | UNITDSPR | BUY | 237 | 09:20:00 | 1,319.00 | 1,308.46 | 10:27 | 1,321.63 | PROFIT LOCK/TRAIL | +0.25 | +624 | 251 | **+373** |
| 3 | ORB (RVOL 4.8x) | 1,018 cap | ADANIGREEN | SELL | 50 | 09:21:00 | 1,377.20 | 1,397.47 | 09:27 | 1,397.47 | STOP (initial) | -1.00 | -1,014 | 92 | **-1,106** |
| 4 | Funnel score 0 | 500 ½ | APOLLOHOSP | SELL | 12 | 11:06:30 | 8,096.50 | 8,136.00 | 13:08 | 8,058.50 | DAY LOCK flatten | +0.96 | +456 | 111 | **+345** |
| 5 | Funnel score 0 | 500 ½ | HDFCLIFE | SELL | 238 | 11:16:30 | 614.75 | 616.85 | 13:08 | 609.85 | DAY LOCK flatten | +2.33 | +1,166 | 143 | **+1,023** |
| 6 | Funnel score 2 | 2,250 ½ | UNITDSPR | BUY | 523 | 12:55:30 | 1,330.10 | 1,325.80 | 13:08 | 1,337.20 | DAY LOCK flatten | +1.65 | +3,713 | 504 | **+3,209** |

## 2026-05-18 — 3 trades, before charges ₹+4,177, after charges ₹+2,535

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 6.4x) | 2,500 | HAL | SELL | 51 | 09:22:00 | 4,305.20 | 4,354.21 | 10:05 | 4,189.50 | DAY LOCK flatten | +2.36 | +5,901 | 190 | **+5,710** |
| 2 | ORB (RVOL 5.3x) | 2,500 | TATASTEEL | SELL | 1406 | 09:44:00 | 207.50 | 209.28 | 10:05 | 206.77 | DAY LOCK flatten | +0.41 | +1,026 | 239 | **+788** |
| 3 | Funnel score 3 | 6,000 | NESTLEIND | BUY | 1250 | 09:59:30 | 1,426.10 | 1,421.30 | 10:05 | 1,423.90 | DAY LOCK flatten | -0.46 | -2,750 | 1,213 | **-3,963** |

## 2026-05-19 — 5 trades, before charges ₹+3,161, after charges ₹+2,182

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 5.7x) | 2,500 | TECHM | BUY | 179 | 09:20:00 | 1,436.70 | 1,422.80 | 11:47 | 1,449.30 | DAY LOCK flatten | +0.91 | +2,255 | 217 | **+2,039** |
| 2 | ORB (RVOL 5.6x) | 2,500 | TCS | BUY | 128 | 09:20:00 | 2,355.10 | 2,335.67 | 09:39 | 2,359.96 | PROFIT LOCK/TRAIL | +0.25 | +622 | 245 | **+376** |
| 3 | ORB (RVOL 4.8x) | 1,025 cap | ADANIGREEN | SELL | 49 | 09:20:00 | 1,378.80 | 1,399.49 | 09:23 | 1,399.49 | STOP (initial) | -1.00 | -1,014 | 92 | **-1,106** |
| 4 | Funnel score 2 | 2,250 ½ | SOLARINDS | SELL | 21 | 10:06:30 | 18,365.95 | 18,469.05 | 11:47 | 18,292.00 | DAY LOCK flatten | +0.72 | +1,553 | 300 | **+1,253** |
| 5 | Funnel score 0 | 500 ½ | LTM | BUY | 28 | 11:10:30 | 4,270.00 | 4,252.30 | 11:47 | 4,260.90 | DAY LOCK flatten | -0.51 | -255 | 126 | **-381** |

## 2026-05-20 — 5 trades, before charges ₹+22,744, after charges ₹+21,543

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.6x) | 2,500 | APOLLOHOSP | BUY | 54 | 09:21:00 | 7,994.50 | 7,948.35 | 14:50 | 8,115.50 | SQUARE_OFF 14:50 | +2.62 | +6,534 | 332 | **+6,202** |
| 2 | ORB (RVOL 3.5x) | 2,500 | BEL | SELL | 710 | 09:21:00 | 413.00 | 416.52 | 09:32 | 416.52 | STOP (initial) | -1.00 | -2,498 | 239 | **-2,738** |
| 3 | ORB (RVOL 21.7x) | 1,010 cap | ZYDUSLIFE | BUY | 107 | 09:23:00 | 1,079.05 | 1,069.62 | 09:31 | 1,069.62 | STOP (initial) | -1.00 | -1,009 | 122 | **-1,131** |
| 4 | Funnel score 1 | 1,250 ½ | SIEMENS | BUY | 120 | 10:21:30 | 3,533.80 | 3,523.40 | 14:50 | 3,697.10 | SQUARE_OFF 14:50 | +15.70 | +19,596 | 331 | **+19,265** |
| 5 | Funnel score 0 | 500 ½ | INDHOTEL | BUY | 303 | 11:46:30 | 649.35 | 647.70 | 12:13 | 649.75 | PROFIT LOCK/TRAIL | +0.24 | +121 | 176 | **-55** |

## 2026-05-21 — 7 trades, before charges ₹-3,402, after charges ₹-5,003

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 11.5x) | 2,500 | APOLLOHOSP | SELL | 48 | 09:20:00 | 8,075.00 | 8,126.34 | 09:23 | 8,126.34 | STOP (initial) | -1.00 | -2,464 | 302 | **-2,766** |
| 2 | ORB (RVOL 8.2x) | 2,500 | MOTHERSON | SELL | 1599 | 09:20:00 | 135.15 | 136.71 | 09:39 | 134.76 | PROFIT LOCK/TRAIL | +0.25 | +625 | 188 | **+437** |
| 3 | Funnel score 1 | 1,250 ½ | ENRIN | BUY | 42 | 09:58:30 | 3,599.10 | 3,569.90 | 11:19 | 3,620.40 | PROFIT LOCK/TRAIL | +0.73 | +895 | 147 | **+748** |
| 4 | ORB (RVOL 17.3x) | 1,250 ½ | GRASIM | BUY | 53 | 10:06:00 | 3,098.30 | 3,074.76 | 11:39 | 3,102.60 | HARD DAY STOP flatten | +0.18 | +228 | 155 | **+73** |
| 5 | Funnel score 1 | 1,250 ½ | BAJAJHLDNG | BUY | 41 | 10:06:30 | 10,670.00 | 10,640.00 | 10:13 | 10,640.00 | STOP (initial) | -1.00 | -1,230 | 333 | **-1,563** |
| 6 | Funnel score 1 | 1,250 ½ | UNITDSPR | BUY | 320 | 10:31:30 | 1,289.80 | 1,285.90 | 10:39 | 1,285.90 | STOP (initial) | -1.00 | -1,248 | 317 | **-1,565** |
| 7 | Funnel score 0 | 500 ½ | TORNTPHARM | BUY | 38 | 10:37:30 | 4,496.45 | 4,483.45 | 11:39 | 4,491.00 | HARD DAY STOP flatten | -0.42 | -207 | 159 | **-366** |

## 2026-05-22 — 3 trades, before charges ₹+1,830, after charges ₹+1,242

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.8x) | 2,500 | GAIL | BUY | 1858 | 09:22:00 | 153.90 | 152.56 | 09:42 | 154.44 | DAY LOCK flatten | +0.40 | +1,003 | 235 | **+768** |
| 2 | ORB (RVOL 12.7x) | 2,500 | MAXHEALTH | SELL | 269 | 09:25:00 | 1,028.20 | 1,037.48 | 09:42 | 1,021.40 | DAY LOCK flatten | +0.73 | +1,829 | 228 | **+1,601** |
| 3 | ORB (RVOL 12.6x) | 1,004 cap | VBL | SELL | 222 | 09:25:00 | 529.00 | 533.52 | 09:29 | 533.52 | STOP (initial) | -1.00 | -1,003 | 124 | **-1,127** |

## 2026-05-25 — 3 trades, before charges ₹+2,483, after charges ₹+1,729

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 5.6x) | 2,500 | TORNTPHARM | BUY | 63 | 09:20:00 | 4,577.30 | 4,537.87 | 09:30 | 4,645.60 | DAY LOCK flatten | +1.73 | +4,303 | 237 | **+4,065** |
| 2 | ORB (RVOL 12.7x) | 2,500 | DIVISLAB | BUY | 49 | 09:26:00 | 6,849.00 | 6,798.34 | 09:30 | 6,823.00 | DAY LOCK flatten | -0.51 | -1,274 | 267 | **-1,541** |
| 3 | ORB (RVOL 13.1x) | 2,500 | EICHERMOT | BUY | 42 | 09:29:00 | 7,380.00 | 7,320.91 | 09:30 | 7,367.00 | DAY LOCK flatten | -0.22 | -546 | 249 | **-795** |

## 2026-05-26 — 2 trades, before charges ₹+2,087, after charges ₹+1,341

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | Funnel score 1 | 2,500 | TMPV | BUY | 714 | 10:10:30 | 379.55 | 376.05 | 10:49 | 381.70 | DAY LOCK flatten | +0.61 | +1,535 | 225 | **+1,310** |
| 2 | Funnel score 1 | 2,500 | TORNTPHARM | BUY | 160 | 10:40:30 | 4,516.05 | 4,500.45 | 10:49 | 4,519.50 | DAY LOCK flatten | +0.22 | +552 | 521 | **+31** |

## 2026-05-27 — 8 trades, before charges ₹+32,840, after charges ₹+30,311

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 17.0x) | 2,500 | COALINDIA | BUY | 611 | 09:20:00 | 434.80 | 430.72 | 14:50 | 458.15 | SQUARE_OFF 14:50 | +5.72 | +14,267 | 225 | **+14,042** |
| 2 | ORB (RVOL 3.8x) | 2,500 | NTPC | BUY | 895 | 09:20:00 | 398.50 | 395.71 | 09:29 | 399.20 | PROFIT LOCK/TRAIL | +0.25 | +625 | 281 | **+344** |
| 3 | ORB (RVOL 5.0x) | 1,577 cap | SIEMENS | BUY | 31 | 09:21:00 | 3,664.00 | 3,613.37 | 14:50 | 3,867.50 | SQUARE_OFF 14:50 | +4.02 | +6,308 | 123 | **+6,185** |
| 4 | Funnel score 1 | 2,500 | TMPV | BUY | 1562 | 10:27:30 | 389.55 | 387.95 | 14:50 | 398.10 | SQUARE_OFF 14:50 | +5.34 | +13,355 | 449 | **+12,906** |
| 5 | Funnel score 0 | 1,000 | BPCL | BUY | 769 | 10:41:30 | 305.10 | 303.80 | 14:50 | 306.70 | SQUARE_OFF 14:50 | +1.23 | +1,230 | 201 | **+1,029** |
| 6 | Funnel score 1 | 2,500 | SHREECEM | BUY | 27 | 10:57:30 | 25,505.00 | 25,415.00 | 11:14 | 25,525.00 | PROFIT LOCK/TRAIL | +0.22 | +540 | 498 | **+42** |
| 7 | Funnel score 0 | 1,000 | ENRIN | BUY | 29 | 12:16:30 | 3,727.10 | 3,693.10 | 12:25 | 3,693.10 | STOP (initial) | -1.00 | -986 | 118 | **-1,104** |
| 8 | Funnel score 1 | 2,500 | NESTLEIND | SELL | 625 | 12:20:30 | 1,431.40 | 1,435.40 | 12:42 | 1,435.40 | STOP (initial) | -1.00 | -2,500 | 634 | **-3,134** |

## 2026-05-29 — 3 trades, before charges ₹+4,372, after charges ₹+3,656

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 8.0x) | 2,500 | WIPRO | SELL | 1587 | 09:21:00 | 208.10 | 209.67 | 09:48 | 206.35 | DAY LOCK flatten | +1.11 | +2,777 | 264 | **+2,513** |
| 2 | ORB (RVOL 10.3x) | 2,500 | COALINDIA | BUY | 536 | 09:23:00 | 460.80 | 456.14 | 09:48 | 465.90 | DAY LOCK flatten | +1.09 | +2,734 | 209 | **+2,525** |
| 3 | ORB (RVOL 27.6x) | 2,500 | BHARTIARTL | SELL | 165 | 09:30:00 | 1,820.00 | 1,835.14 | 09:48 | 1,826.90 | DAY LOCK flatten | -0.46 | -1,138 | 244 | **-1,382** |

**Month: 71 trades, before charges ₹+71,074, Kite charges ₹11,402, slippage ₹6,789, after charges ₹+52,883**