# ARB (Adaptive Risk Budget) — ORB + Funnel + Day lock — 2026-09 order book (backtest)

ORB: top 3 stocks by opening RVOL (≥3×) at 09:20, direction of first 5m candle, entry on break of 5m high/low until 11:00, stop 0.35×ATR14, ₹2,500 risk. Funnel: vol ≥2×, entry <13:00, stop ≥0.25%, VWAP off, skip repeat triggers; risk by quality score 0/1/2/3+ = ₹1,000 (max 2/day) / ₹2,500 / ₹4,500 / ₹6,000. Exits (both): initial stop → at +1R lock +0.25R → after +2R keep 25% of peak → 14:50. Day lock: once day P&L (closed + open) peaks ≥ ₹3,000, flatten everything and stop if it falls to 40% of the peak. ARB loss control: half size after the day's first losing trade; open-risk cap = ₹6,000 + today's profit so far (house money); a trade stops counting against the cap once it has closed a 1m bar at ≥ +1R (its stop is then in profit); HARD DAY STOP — if day P&L (closed + open) hits −₹5,000, flatten everything and stop. ₹5L capital. After charges = gross − Kite charges − 3 bps slippage.

## 2026-09-01 — 4 trades, before charges ₹-3,996, after charges ₹-5,104

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 12.5x) | 2,500 | PIDILITIND | SELL | 282 | 09:23:00 | 1,644.00 | 1,652.86 | 10:11 | 1,644.70 | HARD DAY STOP flatten | -0.08 | -197 | 351 | **-548** |
| 2 | ORB (RVOL 5.3x) | 2,500 | SUNPHARMA | SELL | 250 | 09:27:00 | 1,934.60 | 1,944.58 | 09:32 | 1,944.58 | STOP (initial) | -1.00 | -2,496 | 365 | **-2,860** |
| 3 | ORB (RVOL 6.6x) | 1,007 cap | ADANIENT | BUY | 38 | 09:29:00 | 2,929.00 | 2,903.14 | 10:11 | 2,908.00 | HARD DAY STOP flatten | -0.81 | -798 | 120 | **-918** |
| 4 | Funnel score 1 | 1,250 ½ | JINDALSTEL | SELL | 297 | 10:06:30 | 1,161.10 | 1,165.30 | 10:11 | 1,162.80 | HARD DAY STOP flatten | -0.40 | -505 | 272 | **-777** |

## 2026-09-02 — 4 trades, before charges ₹+2,711, after charges ₹+1,480

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 30.2x) | 2,500 | COALINDIA | BUY | 1165 | 09:20:00 | 417.75 | 415.61 | 09:22 | 415.61 | STOP (initial) | -1.00 | -2,499 | 366 | **-2,864** |
| 2 | ORB (RVOL 5.0x) | 2,500 | EICHERMOT | SELL | 63 | 09:20:00 | 7,752.50 | 7,792.05 | 10:09 | 7,664.50 | DAY LOCK flatten | +2.23 | +5,544 | 366 | **+5,178** |
| 3 | ORB (RVOL 3.0x) | 1,009 cap | DLF | SELL | 221 | 09:21:00 | 655.10 | 659.67 | 09:42 | 659.67 | STOP (initial) | -1.00 | -1,009 | 142 | **-1,150** |
| 4 | Funnel score 3 | 3,000 ½ | BPCL | BUY | 1500 | 09:56:30 | 316.50 | 314.50 | 10:09 | 316.95 | DAY LOCK flatten | +0.22 | +675 | 358 | **+317** |

## 2026-09-03 — 3 trades, before charges ₹+2,133, after charges ₹+864

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.0x) | 2,500 | SOLARINDS | BUY | 13 | 09:22:00 | 21,325.00 | 21,145.00 | 09:37 | 21,370.00 | PROFIT LOCK/TRAIL | +0.25 | +585 | 228 | **+357** |
| 2 | Funnel score 2 | 4,500 | HYUNDAI | BUY | 338 | 09:56:30 | 2,171.50 | 2,158.20 | 10:01 | 2,178.30 | DAY LOCK flatten | +0.51 | +2,298 | 528 | **+1,770** |
| 3 | Funnel score 1 | 1,975 cap | SHREECEM | SELL | 30 | 09:57:30 | 23,735.00 | 23,800.00 | 10:01 | 23,760.00 | DAY LOCK flatten | -0.38 | -750 | 513 | **-1,263** |

## 2026-09-04 — 4 trades, before charges ₹+2,228, after charges ₹+1,082

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.7x) | 2,500 | ULTRACEMCO | SELL | 41 | 09:20:00 | 11,314.00 | 11,373.92 | 12:41 | 11,311.00 | DAY LOCK flatten | +0.05 | +123 | 351 | **-228** |
| 2 | Funnel score 1 | 2,500 | GRASIM | BUY | 112 | 09:52:30 | 3,325.10 | 3,302.90 | 12:41 | 3,311.10 | DAY LOCK flatten | -0.63 | -1,568 | 291 | **-1,859** |
| 3 | Funnel score 1 | 1,057 cap | PFC | BUY | 1112 | 12:01:30 | 350.95 | 350.00 | 12:41 | 355.15 | DAY LOCK flatten | +4.42 | +4,670 | 305 | **+4,366** |
| 4 | Funnel score 0 | 1,000 | DLF | BUY | 338 | 12:08:30 | 688.70 | 685.75 | 12:25 | 685.75 | STOP (initial) | -1.00 | -997 | 199 | **-1,197** |

## 2026-09-07 — 2 trades, before charges ₹+4,339, after charges ₹+2,672

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | Funnel score 2 | 4,500 | TATASTEEL | SELL | 8653 | 10:06:30 | 185.89 | 186.41 | 10:31 | 185.20 | DAY LOCK flatten | +1.33 | +5,971 | 1,100 | **+4,871** |
| 2 | Funnel score 1 | 2,500 | MAZDOCK | SELL | 320 | 10:28:30 | 2,476.90 | 2,484.70 | 10:31 | 2,482.00 | DAY LOCK flatten | -0.65 | -1,632 | 566 | **-2,198** |

## 2026-09-08 — 3 trades, before charges ₹+6,351, after charges ₹+5,712

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.8x) | 2,500 | HAL | BUY | 82 | 09:20:00 | 4,957.00 | 4,926.69 | 14:50 | 5,040.20 | SQUARE_OFF 14:50 | +2.74 | +6,822 | 315 | **+6,508** |
| 2 | Funnel score 0 | 1,000 | ABB | BUY | 22 | 10:40:30 | 7,456.50 | 7,413.00 | 11:37 | 7,413.00 | STOP (initial) | -1.00 | -957 | 155 | **-1,112** |
| 3 | Funnel score 0 | 500 ½ | IOC | SELL | 1388 | 12:05:30 | 134.41 | 134.77 | 14:16 | 134.06 | PROFIT LOCK/TRAIL | +0.97 | +486 | 170 | **+316** |

## 2026-09-09 — 2 trades, before charges ₹+6,464, after charges ₹+5,726

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.0x) | 2,500 | HDFCLIFE | SELL | 691 | 09:20:00 | 525.90 | 529.51 | 14:50 | 517.45 | SQUARE_OFF 14:50 | +2.34 | +5,839 | 285 | **+5,554** |
| 2 | ORB (RVOL 4.0x) | 2,500 | LT | BUY | 155 | 09:20:00 | 3,993.00 | 3,976.87 | 09:44 | 3,997.03 | PROFIT LOCK/TRAIL | +0.25 | +625 | 453 | **+172** |

## 2026-09-10 — 4 trades, before charges ₹-3,036, after charges ₹-3,906

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.3x) | 2,500 | ONGC | BUY | 2180 | 09:20:00 | 239.75 | 238.60 | 09:21 | 238.60 | STOP (initial) | -1.00 | -2,499 | 389 | **-2,888** |
| 2 | ORB (RVOL 3.1x) | 1,250 ½ | TORNTPHARM | SELL | 37 | 09:22:00 | 4,945.50 | 4,978.70 | 14:50 | 4,933.00 | SQUARE_OFF 14:50 | +0.38 | +462 | 167 | **+296** |
| 3 | Funnel score 0 | 500 ½ | DLF | BUY | 256 | 12:12:30 | 660.45 | 658.50 | 12:54 | 658.50 | STOP (initial) | -1.00 | -499 | 158 | **-657** |
| 4 | Funnel score 0 | 500 ½ | ADANIENSOL | BUY | 119 | 12:16:30 | 1,411.40 | 1,407.20 | 12:55 | 1,407.20 | STOP (initial) | -1.00 | -500 | 157 | **-657** |

## 2026-09-11 — 3 trades, before charges ₹+3,423, after charges ₹+2,642

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 5.3x) | 2,500 | ONGC | BUY | 2116 | 09:20:00 | 241.75 | 240.57 | 09:26 | 242.05 | PROFIT LOCK/TRAIL | +0.25 | +625 | 382 | **+243** |
| 2 | Funnel score 0 | 1,000 | MUTHOOTFIN | BUY | 103 | 10:36:30 | 2,759.60 | 2,749.90 | 14:50 | 2,795.70 | SQUARE_OFF 14:50 | +3.72 | +3,718 | 235 | **+3,483** |
| 3 | Funnel score 0 | 1,000 | SOLARINDS | BUY | 8 | 10:51:30 | 22,490.00 | 22,375.00 | 11:34 | 22,375.00 | STOP (initial) | -1.00 | -920 | 165 | **-1,085** |

## 2026-09-15 — 9 trades, before charges ₹+53,249, after charges ₹+51,115

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 6.1x) | 2,500 | TECHM | BUY | 183 | 09:20:00 | 1,631.90 | 1,618.31 | 09:26 | 1,618.31 | STOP (initial) | -1.00 | -2,487 | 242 | **-2,729** |
| 2 | ORB (RVOL 7.7x) | 1,250 ½ | SOLARINDS | SELL | 6 | 09:31:00 | 21,315.00 | 21,497.10 | 14:50 | 19,235.00 | SQUARE_OFF 14:50 | +11.42 | +12,480 | 130 | **+12,350** |
| 3 | Funnel score 1 | 1,250 ½ | LODHA | SELL | 192 | 09:51:30 | 1,100.00 | 1,106.50 | 14:50 | 1,071.70 | SQUARE_OFF 14:50 | +4.35 | +5,434 | 185 | **+5,248** |
| 4 | Funnel score 2 | 2,250 ½ | HYUNDAI | SELL | 340 | 10:20:30 | 2,201.50 | 2,208.10 | 14:50 | 2,131.80 | SQUARE_OFF 14:50 | +10.56 | +23,698 | 536 | **+23,162** |
| 5 | Funnel score 1 | 1,250 ½ | SHRIRAMFIN | SELL | 219 | 10:21:30 | 1,000.10 | 1,005.80 | 14:50 | 981.50 | SQUARE_OFF 14:50 | +3.26 | +4,073 | 190 | **+3,883** |
| 6 | Funnel score 0 | 500 ½ | ASIANPAINT | SELL | 63 | 10:31:30 | 2,439.90 | 2,447.80 | 14:50 | 2,407.70 | SQUARE_OFF 14:50 | +4.08 | +2,029 | 148 | **+1,881** |
| 7 | ORB (RVOL 7.3x) | 1,250 ½ | TMPV | SELL | 658 | 10:34:00 | 313.35 | 315.25 | 14:50 | 304.50 | SQUARE_OFF 14:50 | +4.66 | +5,823 | 182 | **+5,641** |
| 8 | Funnel score 3 | 3,000 ½ | LTM | BUY | 113 | 10:55:30 | 4,486.50 | 4,460.00 | 11:11 | 4,493.10 | PROFIT LOCK/TRAIL | +0.25 | +746 | 379 | **+367** |
| 9 | Funnel score 0 | 500 ½ | ETERNAL | SELL | 454 | 11:19:30 | 319.05 | 320.15 | 14:50 | 315.85 | SQUARE_OFF 14:50 | +2.91 | +1,453 | 141 | **+1,311** |

## 2026-09-16 — 3 trades, before charges ₹+1,881, after charges ₹+875

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.4x) | 2,500 | SOLARINDS | SELL | 9 | 09:20:00 | 18,810.00 | 19,076.47 | 09:54 | 18,743.38 | PROFIT LOCK/TRAIL | +0.25 | +600 | 158 | **+442** |
| 2 | Funnel score 2 | 4,500 | UNITDSPR | BUY | 576 | 10:29:30 | 1,387.80 | 1,380.00 | 10:37 | 1,387.20 | DAY LOCK flatten | -0.08 | -346 | 571 | **-916** |
| 3 | Funnel score 1 | 2,300 cap | JINDALSTEL | BUY | 319 | 10:32:30 | 1,097.20 | 1,090.00 | 10:37 | 1,102.30 | DAY LOCK flatten | +0.71 | +1,627 | 277 | **+1,349** |

## 2026-09-17 — 2 trades, before charges ₹-4,039, after charges ₹-5,015

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | Funnel score 1 | 2,500 | DMART | BUY | 204 | 10:04:15 | 3,742.10 | 3,729.90 | 10:39 | 3,729.90 | STOP (initial) | -1.00 | -2,489 | 546 | **-3,035** |
| 2 | Funnel score 2 | 3,511 cap | SOLARINDS | SELL | 31 | 10:13:30 | 18,795.00 | 18,905.00 | 11:05 | 18,845.00 | HARD DAY STOP flatten | -0.45 | -1,550 | 430 | **-1,980** |

## 2026-09-18 — 7 trades, before charges ₹-3,811, after charges ₹-5,359

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 22.3x) | 2,500 | LODHA | BUY | 159 | 09:20:00 | 1,129.40 | 1,113.72 | 11:40 | 1,133.32 | PROFIT LOCK/TRAIL | +0.25 | +623 | 165 | **+459** |
| 2 | ORB (RVOL 6.3x) | 2,500 | ZYDUSLIFE | BUY | 236 | 09:22:00 | 1,163.10 | 1,152.52 | 10:01 | 1,152.52 | STOP (initial) | -1.00 | -2,497 | 226 | **-2,723** |
| 3 | ORB (RVOL 5.0x) | 2,500 | BAJAJHLDNG | BUY | 37 | 09:39:00 | 11,225.00 | 11,157.80 | 11:41 | 11,221.00 | HARD DAY STOP flatten | -0.06 | -148 | 319 | **-467** |
| 4 | Funnel score 2 | 1,671 cap | INDHOTEL | BUY | 439 | 09:51:30 | 736.55 | 732.75 | 10:26 | 737.50 | PROFIT LOCK/TRAIL | +0.25 | +417 | 259 | **+158** |
| 5 | Funnel score 1 | 1,250 ½ | TRENT | SELL | 109 | 10:39:30 | 2,776.50 | 2,787.90 | 11:15 | 2,787.90 | STOP (initial) | -1.00 | -1,243 | 246 | **-1,488** |
| 6 | Funnel score 0 | 500 ½ | HAL | BUY | 30 | 10:55:30 | 4,838.00 | 4,821.80 | 11:20 | 4,821.80 | STOP (initial) | -1.00 | -486 | 142 | **-628** |
| 7 | Funnel score 1 | 1,250 ½ | TVSMOTOR | BUY | 53 | 10:58:30 | 4,176.70 | 4,153.30 | 11:41 | 4,167.70 | HARD DAY STOP flatten | -0.38 | -477 | 192 | **-669** |

## 2026-09-21 — 6 trades, before charges ₹+1,100, after charges ₹-334

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.7x) | 2,500 | UNITDSPR | BUY | 246 | 09:28:00 | 1,393.70 | 1,383.54 | 14:50 | 1,403.80 | SQUARE_OFF 14:50 | +0.99 | +2,485 | 272 | **+2,213** |
| 2 | Funnel score 1 | 2,500 | NTPC | SELL | 2631 | 10:25:30 | 326.45 | 327.40 | 13:02 | 327.40 | STOP (initial) | -1.00 | -2,499 | 610 | **-3,110** |
| 3 | Funnel score 1 | 1,001 cap | TATACAP | SELL | 625 | 10:41:30 | 341.15 | 342.75 | 10:58 | 342.75 | STOP (initial) | -1.00 | -1,000 | 186 | **-1,186** |
| 4 | Funnel score 0 | 500 ½ | DMART | BUY | 15 | 11:39:30 | 3,796.60 | 3,764.50 | 14:50 | 3,812.60 | SQUARE_OFF 14:50 | +0.50 | +240 | 78 | **+162** |
| 5 | Funnel score 1 | 520 ½ cap | GODREJCP | SELL | 185 | 12:30:30 | 878.90 | 881.70 | 14:50 | 870.55 | SQUARE_OFF 14:50 | +2.98 | +1,545 | 154 | **+1,391** |
| 6 | Funnel score 0 | 500 ½ | DLF | BUY | 200 | 12:56:30 | 657.95 | 655.45 | 14:50 | 659.60 | SQUARE_OFF 14:50 | +0.66 | +330 | 134 | **+196** |

## 2026-09-22 — 5 trades, before charges ₹+2,177, after charges ₹+1,126

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.7x) | 2,500 | LTM | SELL | 54 | 09:27:00 | 4,100.00 | 4,145.94 | 09:33 | 4,145.94 | STOP (initial) | -1.00 | -2,481 | 192 | **-2,673** |
| 2 | Funnel score 1 | 1,250 ½ | BAJAJHLDNG | BUY | 23 | 10:00:30 | 11,368.00 | 11,314.00 | 11:42 | 11,314.00 | STOP (initial) | -1.00 | -1,242 | 218 | **-1,460** |
| 3 | Funnel score 1 | 1,250 ½ | TMCV | SELL | 625 | 10:33:30 | 445.75 | 447.75 | 12:16 | 445.20 | PROFIT LOCK/TRAIL | +0.28 | +344 | 230 | **+114** |
| 4 | Funnel score 0 | 500 ½ | GAIL | SELL | 735 | 10:42:30 | 172.24 | 172.92 | 14:16 | 171.79 | DAY LOCK flatten | +0.66 | +331 | 131 | **+200** |
| 5 | Funnel score 1 | 1,250 ½ | BPCL | SELL | 1136 | 11:38:30 | 314.45 | 315.55 | 14:16 | 309.85 | DAY LOCK flatten | +4.18 | +5,226 | 281 | **+4,945** |

## 2026-09-23 — 4 trades, before charges ₹+2,426, after charges ₹+1,528

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 6.0x) | 2,500 | RECLTD | BUY | 1392 | 09:20:00 | 309.45 | 307.65 | 11:51 | 312.90 | DAY LOCK flatten | +1.92 | +4,802 | 331 | **+4,472** |
| 2 | ORB (RVOL 8.5x) | 2,500 | TATACAP | SELL | 856 | 09:22:00 | 345.65 | 348.57 | 11:51 | 347.25 | DAY LOCK flatten | -0.55 | -1,370 | 241 | **-1,611** |
| 3 | ORB (RVOL 3.3x) | 1,003 cap | BAJAJFINSV | BUY | 88 | 10:08:00 | 1,862.80 | 1,851.47 | 10:43 | 1,851.47 | STOP (initial) | -1.00 | -997 | 155 | **-1,152** |
| 4 | Funnel score 0 | 500 ½ | DIVISLAB | BUY | 20 | 11:42:30 | 9,479.50 | 9,455.50 | 11:51 | 9,479.00 | DAY LOCK flatten | -0.02 | -10 | 171 | **-181** |

## 2026-09-24 — 8 trades, before charges ₹-3,214, after charges ₹-5,116

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 12.5x) | 2,500 | HDFCLIFE | SELL | 480 | 09:20:00 | 524.35 | 529.56 | 09:30 | 523.05 | PROFIT LOCK/TRAIL | +0.25 | +625 | 213 | **+412** |
| 2 | ORB (RVOL 8.7x) | 2,500 | AXISBANK | SELL | 354 | 09:20:00 | 1,193.40 | 1,200.46 | 09:21 | 1,200.46 | STOP (initial) | -1.00 | -2,499 | 325 | **-2,824** |
| 3 | ORB (RVOL 13.2x) | 1,250 ½ | CHOLAFIN | BUY | 85 | 09:22:00 | 1,674.50 | 1,659.91 | 09:28 | 1,678.15 | PROFIT LOCK/TRAIL | +0.25 | +310 | 141 | **+170** |
| 4 | Funnel score 0 | 500 ½ | AXISBANK | BUY | 156 | 10:33:30 | 1,198.60 | 1,195.40 | 10:41 | 1,195.40 | STOP (initial) | -1.00 | -499 | 170 | **-669** |
| 5 | Funnel score 1 | 1,250 ½ | HDFCLIFE | BUY | 568 | 10:45:30 | 527.05 | 524.85 | 13:45 | 530.30 | HARD DAY STOP flatten | +1.48 | +1,846 | 243 | **+1,603** |
| 6 | Funnel score 1 | 1,250 ½ | HINDZINC | BUY | 781 | 11:02:30 | 593.60 | 592.00 | 11:33 | 592.00 | STOP (initial) | -1.00 | -1,250 | 351 | **-1,601** |
| 7 | Funnel score 1 | 1,250 ½ | CIPLA | SELL | 290 | 11:24:30 | 1,388.10 | 1,392.40 | 11:29 | 1,392.40 | STOP (initial) | -1.00 | -1,247 | 311 | **-1,558** |
| 8 | Funnel score 0 | 500 ½ | DRREDDY | BUY | 128 | 12:23:30 | 1,212.60 | 1,208.70 | 12:28 | 1,208.70 | STOP (initial) | -1.00 | -499 | 149 | **-649** |

## 2026-09-25 — 2 trades, before charges ₹+1,548, after charges ₹+1,097

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | Funnel score 0 | 1,000 | ABB | SELL | 27 | 11:28:30 | 7,086.50 | 7,123.00 | 14:00 | 7,052.50 | DAY LOCK flatten | +0.93 | +918 | 173 | **+745** |
| 2 | Funnel score 0 | 1,000 | SOLARINDS | SELL | 18 | 12:50:30 | 19,585.00 | 19,640.00 | 13:49 | 19,550.00 | PROFIT LOCK/TRAIL | +0.64 | +630 | 278 | **+352** |

**Month: 75 trades, before charges ₹+71,935, Kite charges ₹12,918, slippage ₹7,933, after charges ₹+51,084**