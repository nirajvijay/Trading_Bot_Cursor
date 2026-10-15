# ARB (Adaptive Risk Budget) — ORB + Funnel + Day lock — 2026-06 order book (backtest)

ORB: top 3 stocks by opening RVOL (≥3×) at 09:20, direction of first 5m candle, entry on break of 5m high/low until 11:00, stop 0.35×ATR14, ₹2,500 risk. Funnel: vol ≥2×, entry <13:00, stop ≥0.25%, VWAP off, skip repeat triggers; risk by quality score 0/1/2/3+ = ₹1,000 (max 2/day) / ₹2,500 / ₹4,500 / ₹6,000. Exits (both): initial stop → at +1R lock +0.25R → after +2R keep 25% of peak → 14:50. Day lock: once day P&L (closed + open) peaks ≥ ₹3,000, flatten everything and stop if it falls to 40% of the peak. ARB loss control: half size after the day's first losing trade; open-risk cap = ₹6,000 + today's profit so far (house money); a trade stops counting against the cap once it has closed a 1m bar at ≥ +1R (its stop is then in profit); HARD DAY STOP — if day P&L (closed + open) hits −₹5,000, flatten everything and stop. ₹5L capital. After charges = gross − Kite charges − 3 bps slippage.

## 2026-06-01 — 7 trades, before charges ₹-2,215, after charges ₹-3,666

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 5.6x) | 2,500 | ASIANPAINT | BUY | 112 | 09:26:00 | 2,777.70 | 2,755.46 | 09:26 | 2,755.46 | STOP (initial) | -1.00 | -2,491 | 250 | **-2,741** |
| 2 | Funnel score 1 | 1,250 ½ | JIOFIN | SELL | 1126 | 09:58:30 | 237.50 | 238.61 | 10:48 | 237.22 | PROFIT LOCK/TRAIL | +0.25 | +315 | 222 | **+93** |
| 3 | Funnel score 1 | 1,250 ½ | BAJAJ-AUTO | BUY | 37 | 10:45:30 | 10,565.00 | 10,532.00 | 10:47 | 10,573.00 | PROFIT LOCK/TRAIL | +0.24 | +296 | 304 | **-8** |
| 4 | Funnel score 1 | 1,250 ½ | SIEMENS | BUY | 33 | 10:47:30 | 3,738.30 | 3,701.50 | 14:23 | 3,747.50 | PROFIT LOCK/TRAIL | +0.25 | +304 | 128 | **+175** |
| 5 | Funnel score 0 | 500 ½ | UNITDSPR | SELL | 44 | 11:44:30 | 1,262.40 | 1,273.60 | 14:50 | 1,251.50 | SQUARE_OFF 14:50 | +0.97 | +480 | 76 | **+404** |
| 6 | Funnel score 0 | 500 ½ | CHOLAFIN | SELL | 119 | 12:28:30 | 1,509.90 | 1,514.10 | 12:44 | 1,508.80 | PROFIT LOCK/TRAIL | +0.26 | +131 | 165 | **-34** |
| 7 | Funnel score 1 | 1,250 ½ | KOTAKBANK | BUY | 1041 | 12:30:30 | 380.10 | 378.90 | 13:02 | 378.90 | STOP (initial) | -1.00 | -1,249 | 306 | **-1,556** |

## 2026-06-02 — 5 trades, before charges ₹+4,570, after charges ₹+2,698

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.1x) | 2,500 | INFY | BUY | 220 | 09:20:00 | 1,222.50 | 1,211.17 | 12:45 | 1,245.70 | DAY LOCK flatten | +2.05 | +5,104 | 225 | **+4,879** |
| 2 | Funnel score 2 | 4,500 | NTPC | SELL | 3103 | 09:57:30 | 369.95 | 371.40 | 10:21 | 369.55 | PROFIT LOCK/TRAIL | +0.28 | +1,241 | 798 | **+443** |
| 3 | ORB (RVOL 3.0x) | 2,500 | HDFCAMC | SELL | 90 | 10:58:00 | 2,486.30 | 2,513.99 | 12:45 | 2,506.30 | DAY LOCK flatten | -0.72 | -1,800 | 194 | **-1,994** |
| 4 | Funnel score 1 | 2,500 | AMBUJACEM | SELL | 1612 | 11:48:30 | 432.95 | 434.50 | 12:45 | 432.75 | PROFIT LOCK/TRAIL | +0.13 | +322 | 504 | **-181** |
| 5 | Funnel score 0 | 1,000 | TMCV | BUY | 425 | 12:44:30 | 372.05 | 369.70 | 12:45 | 371.35 | DAY LOCK flatten | -0.30 | -298 | 150 | **-448** |

## 2026-06-03 — 5 trades, before charges ₹+10,962, after charges ₹+8,941

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.1x) | 2,500 | TCS | SELL | 120 | 09:20:00 | 2,321.70 | 2,342.49 | 14:50 | 2,248.00 | SQUARE_OFF 14:50 | +3.55 | +8,844 | 229 | **+8,615** |
| 2 | Funnel score 1 | 2,500 | ADANIPORTS | SELL | 280 | 11:01:30 | 1,795.00 | 1,803.90 | 12:21 | 1,792.70 | PROFIT LOCK/TRAIL | +0.26 | +644 | 377 | **+267** |
| 3 | Funnel score 1 | 2,500 | HAL | SELL | 211 | 11:36:30 | 4,229.90 | 4,241.70 | 11:51 | 4,226.90 | PROFIT LOCK/TRAIL | +0.25 | +633 | 632 | **+1** |
| 4 | Funnel score 2 | 4,500 | ZYDUSLIFE | BUY | 773 | 11:43:30 | 1,068.50 | 1,065.40 | 12:18 | 1,069.20 | PROFIT LOCK/TRAIL | +0.23 | +541 | 589 | **-48** |
| 5 | Funnel score 1 | 1,250 ½ | BOSCHLTD | BUY | 6 | 12:34:30 | 37,380.00 | 37,175.00 | 12:47 | 37,430.00 | PROFIT LOCK/TRAIL | +0.24 | +300 | 194 | **+106** |

## 2026-06-04 — 2 trades, before charges ₹+3,205, after charges ₹+2,431

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.2x) | 2,500 | RELIANCE | BUY | 274 | 09:57:00 | 1,309.60 | 1,300.49 | 10:28 | 1,305.40 | DAY LOCK flatten | -0.46 | -1,151 | 282 | **-1,432** |
| 2 | Funnel score 2 | 3,504 cap | BAJAJ-AUTO | BUY | 66 | 10:02:30 | 10,291.00 | 10,238.00 | 10:28 | 10,357.00 | DAY LOCK flatten | +1.25 | +4,356 | 493 | **+3,863** |

## 2026-06-05 — 3 trades, before charges ₹+1,148, after charges ₹+347

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.0x) | 2,500 | WIPRO | BUY | 1391 | 09:20:00 | 195.88 | 194.08 | 14:50 | 197.79 | SQUARE_OFF 14:50 | +1.06 | +2,657 | 226 | **+2,430** |
| 2 | Funnel score 1 | 2,500 | HDFCLIFE | BUY | 925 | 10:18:30 | 587.35 | 584.65 | 10:35 | 584.65 | STOP (initial) | -1.00 | -2,498 | 402 | **-2,899** |
| 3 | Funnel score 1 | 1,250 ½ | M&M | BUY | 63 | 10:55:30 | 3,037.90 | 3,018.20 | 14:50 | 3,053.60 | SQUARE_OFF 14:50 | +0.80 | +989 | 173 | **+816** |

## 2026-06-08 — 4 trades, before charges ₹+21,913, after charges ₹+19,993

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | Funnel score 2 | 4,500 | DIVISLAB | SELL | 195 | 10:27:30 | 6,656.50 | 6,679.50 | 14:50 | 6,545.50 | SQUARE_OFF 14:50 | +4.83 | +21,645 | 896 | **+20,749** |
| 2 | Funnel score 1 | 2,500 | AMBUJACEM | BUY | 2272 | 10:53:30 | 417.10 | 416.00 | 12:07 | 417.35 | PROFIT LOCK/TRAIL | +0.23 | +568 | 667 | **-99** |
| 3 | Funnel score 0 | 1,000 | ADANIENSOL | BUY | 144 | 11:41:30 | 1,607.10 | 1,600.20 | 11:45 | 1,608.80 | PROFIT LOCK/TRAIL | +0.25 | +245 | 199 | **+46** |
| 4 | Funnel score 0 | 1,000 | SUNPHARMA | BUY | 94 | 11:48:30 | 1,799.90 | 1,789.30 | 14:50 | 1,794.10 | SQUARE_OFF 14:50 | -0.55 | -545 | 158 | **-703** |

## 2026-06-09 — 3 trades, before charges ₹-2,740, after charges ₹-3,324

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | Funnel score 0 | 1,000 | TMCV | BUY | 465 | 10:45:30 | 368.15 | 366.00 | 11:18 | 366.00 | STOP (initial) | -1.00 | -1,000 | 159 | **-1,159** |
| 2 | Funnel score 0 | 500 ½ | BHARTIARTL | SELL | 51 | 11:25:30 | 1,791.40 | 1,801.20 | 11:35 | 1,801.20 | STOP (initial) | -1.00 | -500 | 107 | **-607** |
| 3 | Funnel score 1 | 1,250 ½ | DMART | BUY | 100 | 12:08:30 | 4,138.30 | 4,125.90 | 12:21 | 4,125.90 | STOP (initial) | -1.00 | -1,240 | 317 | **-1,557** |

## 2026-06-10 — 2 trades, before charges ₹+1,949, after charges ₹+1,451

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.5x) | 2,500 | MUTHOOTFIN | SELL | 76 | 09:23:00 | 2,912.00 | 2,944.47 | 10:58 | 2,889.50 | DAY LOCK flatten | +0.69 | +1,710 | 192 | **+1,518** |
| 2 | Funnel score 0 | 1,000 | ADANIENT | BUY | 133 | 10:30:30 | 2,969.10 | 2,961.60 | 10:32 | 2,970.90 | PROFIT LOCK/TRAIL | +0.24 | +239 | 306 | **-67** |

## 2026-06-11 — 3 trades, before charges ₹+2,705, after charges ₹+2,052

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.1x) | 2,500 | MUTHOOTFIN | BUY | 74 | 09:20:00 | 2,854.00 | 2,820.28 | 10:57 | 2,895.80 | DAY LOCK flatten | +1.24 | +3,093 | 186 | **+2,907** |
| 2 | Funnel score 1 | 2,500 | CGPOWER | BUY | 485 | 10:17:30 | 920.05 | 914.90 | 10:37 | 921.30 | PROFIT LOCK/TRAIL | +0.24 | +606 | 339 | **+267** |
| 3 | Funnel score 0 | 1,000 | MAZDOCK | BUY | 51 | 10:34:30 | 2,376.20 | 2,356.70 | 10:55 | 2,356.70 | STOP (initial) | -1.00 | -994 | 127 | **-1,121** |

## 2026-06-12 — 3 trades, before charges ₹-4,544, after charges ₹-5,358

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.8x) | 2,500 | LT | BUY | 95 | 09:20:00 | 4,002.00 | 3,975.80 | 09:29 | 3,975.80 | STOP (initial) | -1.00 | -2,489 | 295 | **-2,783** |
| 2 | ORB (RVOL 4.1x) | 2,500 | PNB | SELL | 2761 | 09:24:00 | 105.02 | 105.93 | 10:06 | 105.47 | HARD DAY STOP flatten | -0.50 | -1,242 | 237 | **-1,479** |
| 3 | Funnel score 1 | 1,250 ½ | INDIGO | SELL | 77 | 09:59:30 | 4,650.95 | 4,667.05 | 10:06 | 4,661.50 | HARD DAY STOP flatten | -0.66 | -812 | 282 | **-1,095** |

## 2026-06-15 — 4 trades, before charges ₹+1,622, after charges ₹+281

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 7.1x) | 2,500 | ASIANPAINT | SELL | 116 | 09:56:00 | 2,780.00 | 2,801.41 | 10:52 | 2,777.50 | DAY LOCK flatten | +0.12 | +290 | 259 | **+31** |
| 2 | Funnel score 2 | 3,516 cap | BAJAJHLDNG | BUY | 83 | 10:00:30 | 10,501.00 | 10,459.00 | 10:52 | 10,511.00 | PROFIT LOCK/TRAIL | +0.24 | +830 | 618 | **+212** |
| 3 | ORB (RVOL 5.7x) | 2,500 | LT | BUY | 84 | 10:28:00 | 4,194.70 | 4,165.26 | 10:52 | 4,204.00 | DAY LOCK flatten | +0.32 | +781 | 278 | **+503** |
| 4 | ORB (RVOL 9.1x) | 2,500 | CHOLAFIN | BUY | 127 | 10:39:00 | 1,659.00 | 1,639.45 | 10:52 | 1,656.80 | DAY LOCK flatten | -0.11 | -279 | 185 | **-465** |

## 2026-06-16 — 5 trades, before charges ₹-4,178, after charges ₹-5,080

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.5x) | 2,500 | GODREJCP | BUY | 281 | 09:20:00 | 1,052.20 | 1,043.32 | 09:21 | 1,043.32 | STOP (initial) | -1.00 | -2,496 | 240 | **-2,736** |
| 2 | ORB (RVOL 4.3x) | 1,250 ½ | HCLTECH | BUY | 118 | 09:23:00 | 1,150.00 | 1,139.41 | 10:45 | 1,152.65 | PROFIT LOCK/TRAIL | +0.25 | +312 | 136 | **+176** |
| 3 | Funnel score 0 | 500 ½ | ADANIENT | BUY | 35 | 10:43:30 | 2,997.10 | 2,982.90 | 10:52 | 2,982.90 | STOP (initial) | -1.00 | -497 | 116 | **-612** |
| 4 | Funnel score 0 | 500 ½ | BANKBARODA | SELL | 235 | 11:08:30 | 274.09 | 276.21 | 14:31 | 275.40 | HARD DAY STOP flatten | -0.62 | -308 | 88 | **-396** |
| 5 | Funnel score 1 | 1,250 ½ | SHREECEM | BUY | 17 | 11:21:30 | 24,665.00 | 24,595.00 | 11:30 | 24,595.00 | STOP (initial) | -1.00 | -1,190 | 322 | **-1,512** |

## 2026-06-17 — 4 trades, before charges ₹+99, after charges ₹-940

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | Funnel score 1 | 2,500 | ONGC | SELL | 1700 | 10:06:30 | 245.04 | 246.51 | 14:50 | 244.50 | SQUARE_OFF 14:50 | +0.37 | +918 | 319 | **+599** |
| 2 | Funnel score 0 | 1,000 | LODHA | BUY | 142 | 11:14:30 | 916.95 | 909.95 | 12:43 | 909.95 | STOP (initial) | -1.00 | -994 | 132 | **-1,126** |
| 3 | Funnel score 1 | 2,500 | PNB | BUY | 5813 | 11:28:30 | 108.75 | 108.32 | 13:11 | 108.85 | PROFIT LOCK/TRAIL | +0.23 | +581 | 461 | **+120** |
| 4 | Funnel score 1 | 1,001 ½ cap | CANBK | BUY | 902 | 12:46:30 | 135.56 | 134.45 | 14:50 | 135.11 | SQUARE_OFF 14:50 | -0.41 | -406 | 127 | **-533** |

## 2026-06-18 — 5 trades, before charges ₹+3,639, after charges ₹+2,627

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 13.1x) | 2,500 | UNITDSPR | BUY | 265 | 09:20:00 | 1,342.40 | 1,332.99 | 13:20 | 1,354.30 | DAY LOCK flatten | +1.26 | +3,154 | 282 | **+2,872** |
| 2 | ORB (RVOL 8.1x) | 2,500 | TRENT | BUY | 69 | 09:49:00 | 3,149.20 | 3,113.29 | 13:20 | 3,161.50 | DAY LOCK flatten | +0.34 | +849 | 191 | **+658** |
| 3 | Funnel score 1 | 1,028 cap | GRASIM | SELL | 93 | 09:50:30 | 3,120.40 | 3,131.40 | 10:48 | 3,117.60 | PROFIT LOCK/TRAIL | +0.25 | +260 | 238 | **+22** |
| 4 | Funnel score 1 | 593 cap | BAJAJHLDNG | BUY | 12 | 10:05:30 | 10,635.00 | 10,588.00 | 10:13 | 10,588.00 | STOP (initial) | -1.00 | -564 | 131 | **-695** |
| 5 | Funnel score 0 | 500 ½ | SIEMENS | BUY | 50 | 12:55:30 | 3,730.20 | 3,720.30 | 13:20 | 3,729.00 | DAY LOCK flatten | -0.12 | -60 | 170 | **-230** |

## 2026-06-19 — 6 trades, before charges ₹-1,103, after charges ₹-2,007

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 6.5x) | 2,500 | INFY | SELL | 211 | 09:28:00 | 1,033.90 | 1,045.73 | 10:12 | 1,045.73 | STOP (initial) | -1.00 | -2,496 | 191 | **-2,687** |
| 2 | ORB (RVOL 4.9x) | 2,500 | TECHM | BUY | 152 | 09:38:00 | 1,337.60 | 1,321.20 | 14:50 | 1,359.80 | SQUARE_OFF 14:50 | +1.35 | +3,374 | 182 | **+3,193** |
| 3 | Funnel score 1 | 1,011 cap | SIEMENS | BUY | 42 | 09:57:30 | 3,768.60 | 3,744.60 | 12:42 | 3,744.60 | STOP (initial) | -1.00 | -1,008 | 150 | **-1,158** |
| 4 | Funnel score 0 | 500 ½ | TRENT | SELL | 36 | 10:53:30 | 3,165.90 | 3,179.50 | 14:08 | 3,162.40 | PROFIT LOCK/TRAIL | +0.26 | +126 | 121 | **+5** |
| 5 | Funnel score 0 | 500 ½ | JINDALSTEL | BUY | 138 | 10:55:30 | 1,132.50 | 1,128.90 | 11:17 | 1,133.40 | PROFIT LOCK/TRAIL | +0.25 | +124 | 150 | **-26** |
| 6 | ORB (RVOL 4.1x) | 1,250 ½ | LTM | SELL | 26 | 10:59:00 | 3,766.20 | 3,813.26 | 12:49 | 3,813.26 | STOP (initial) | -1.00 | -1,224 | 111 | **-1,335** |

## 2026-06-22 — 4 trades, before charges ₹+13,811, after charges ₹+12,882

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.8x) | 2,500 | CIPLA | BUY | 284 | 09:20:00 | 1,383.90 | 1,375.10 | 14:50 | 1,416.90 | SQUARE_OFF 14:50 | +3.75 | +9,372 | 308 | **+9,064** |
| 2 | ORB (RVOL 4.5x) | 2,500 | TATACAP | SELL | 626 | 09:23:00 | 361.15 | 365.14 | 14:50 | 356.30 | SQUARE_OFF 14:50 | +1.21 | +3,036 | 196 | **+2,840** |
| 3 | Funnel score 0 | 1,000 | NTPC | BUY | 909 | 10:35:30 | 366.55 | 365.45 | 10:48 | 366.80 | PROFIT LOCK/TRAIL | +0.23 | +227 | 265 | **-38** |
| 4 | Funnel score 0 | 500 ½ | ZYDUSLIFE | BUY | 161 | 12:33:30 | 1,072.00 | 1,068.90 | 14:50 | 1,079.30 | SQUARE_OFF 14:50 | +2.35 | +1,175 | 160 | **+1,015** |

## 2026-06-23 — 6 trades, before charges ₹+18,148, after charges ₹+16,126

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 62.8x) | 2,500 | VEDL | SELL | 674 | 09:24:00 | 287.00 | 290.70 | 14:50 | 282.35 | SQUARE_OFF 14:50 | +1.26 | +3,134 | 174 | **+2,961** |
| 2 | Funnel score 1 | 2,500 | SBIN | SELL | 862 | 10:08:30 | 1,039.95 | 1,042.85 | 14:50 | 1,027.25 | SQUARE_OFF 14:50 | +4.38 | +10,947 | 634 | **+10,314** |
| 3 | Funnel score 1 | 2,500 | NTPC | SELL | 2000 | 10:29:30 | 365.95 | 367.20 | 14:22 | 364.90 | PROFIT LOCK/TRAIL | +0.84 | +2,100 | 526 | **+1,574** |
| 4 | Funnel score 0 | 1,000 | MAZDOCK | SELL | 140 | 11:41:30 | 2,540.40 | 2,547.50 | 14:50 | 2,523.60 | SQUARE_OFF 14:50 | +2.37 | +2,352 | 280 | **+2,072** |
| 5 | Funnel score 2 | 4,500 | CIPLA | BUY | 221 | 12:16:30 | 1,458.00 | 1,453.30 | 12:20 | 1,453.30 | STOP (initial) | -1.00 | -1,039 | 258 | **-1,297** |
| 6 | Funnel score 0 | 500 ½ | TMCV | SELL | 384 | 12:48:30 | 404.70 | 406.00 | 14:50 | 403.00 | SQUARE_OFF 14:50 | +1.31 | +653 | 149 | **+503** |

## 2026-06-24 — 4 trades, before charges ₹-4,656, after charges ₹-5,554

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 6.6x) | 2,500 | IRFC | BUY | 3290 | 09:20:00 | 94.14 | 93.38 | 09:53 | 93.38 | STOP (initial) | -1.00 | -2,500 | 249 | **-2,749** |
| 2 | ORB (RVOL 5.0x) | 2,500 | BHARTIARTL | SELL | 203 | 09:26:00 | 1,877.50 | 1,889.80 | 10:43 | 1,883.60 | HARD DAY STOP flatten | -0.50 | -1,238 | 296 | **-1,534** |
| 3 | Funnel score 1 | 1,250 ½ | VEDL | SELL | 403 | 10:11:30 | 279.25 | 282.35 | 10:43 | 280.70 | HARD DAY STOP flatten | -0.47 | -584 | 120 | **-705** |
| 4 | Funnel score 1 | 1,250 ½ | HYUNDAI | SELL | 145 | 10:40:30 | 1,950.40 | 1,959.00 | 10:43 | 1,952.70 | HARD DAY STOP flatten | -0.27 | -334 | 232 | **-566** |

## 2026-06-25 — 8 trades, before charges ₹+5,019, after charges ₹+2,816

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.6x) | 2,500 | CHOLAFIN | SELL | 137 | 09:20:00 | 1,802.70 | 1,820.84 | 14:50 | 1,797.10 | SQUARE_OFF 14:50 | +0.31 | +767 | 209 | **+558** |
| 2 | ORB (RVOL 6.4x) | 2,500 | TATACAP | BUY | 545 | 09:23:00 | 370.10 | 365.52 | 09:38 | 365.52 | STOP (initial) | -1.00 | -2,496 | 179 | **-2,675** |
| 3 | ORB (RVOL 4.6x) | 1,747 cap | INDIGO | BUY | 38 | 09:24:00 | 5,343.00 | 5,297.46 | 14:50 | 5,441.30 | SQUARE_OFF 14:50 | +2.16 | +3,735 | 181 | **+3,554** |
| 4 | Funnel score 1 | 1,250 ½ | TATACAP | SELL | 657 | 10:08:30 | 362.95 | 364.85 | 10:14 | 364.85 | STOP (initial) | -1.00 | -1,248 | 204 | **-1,452** |
| 5 | Funnel score 2 | 2,250 ½ | LODHA | SELL | 535 | 10:20:30 | 954.55 | 958.75 | 10:30 | 953.45 | PROFIT LOCK/TRAIL | +0.26 | +588 | 382 | **+207** |
| 6 | Funnel score 1 | 1,250 ½ | TMCV | BUY | 431 | 10:39:30 | 423.55 | 420.65 | 14:50 | 430.50 | SQUARE_OFF 14:50 | +2.40 | +2,995 | 167 | **+2,829** |
| 7 | Funnel score 2 | 2,250 ½ | JSWSTEEL | SELL | 725 | 11:46:30 | 1,230.60 | 1,233.70 | 13:31 | 1,233.70 | STOP (initial) | -1.00 | -2,248 | 632 | **-2,879** |
| 8 | Funnel score 3 | 1,267 ½ cap | NTPC | SELL | 873 | 11:56:30 | 356.25 | 357.70 | 14:50 | 352.90 | SQUARE_OFF 14:50 | +2.31 | +2,925 | 251 | **+2,674** |

## 2026-06-29 — 5 trades, before charges ₹-1,034, after charges ₹-1,991

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 15.0x) | 2,500 | DRREDDY | BUY | 286 | 09:20:00 | 1,408.80 | 1,400.09 | 09:21 | 1,400.09 | STOP (initial) | -1.00 | -2,492 | 310 | **-2,803** |
| 2 | ORB (RVOL 7.2x) | 1,250 ½ | DIVISLAB | SELL | 26 | 09:26:00 | 6,627.50 | 6,675.09 | 09:47 | 6,675.09 | STOP (initial) | -1.00 | -1,237 | 160 | **-1,397** |
| 3 | ORB (RVOL 5.6x) | 1,250 ½ | TORNTPHARM | BUY | 34 | 09:39:00 | 4,578.90 | 4,542.35 | 10:20 | 4,588.04 | PROFIT LOCK/TRAIL | +0.25 | +311 | 150 | **+161** |
| 4 | Funnel score 1 | 1,250 ½ | CHOLAFIN | SELL | 140 | 09:55:30 | 1,795.50 | 1,804.40 | 14:50 | 1,774.90 | SQUARE_OFF 14:50 | +2.31 | +2,884 | 211 | **+2,673** |
| 5 | Funnel score 0 | 500 ½ | TATAPOWER | SELL | 312 | 11:12:30 | 385.45 | 387.05 | 11:48 | 387.05 | STOP (initial) | -1.00 | -499 | 126 | **-625** |

## 2026-06-30 — 5 trades, before charges ₹-4,036, after charges ₹-5,204

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 12.2x) | 2,500 | EICHERMOT | SELL | 40 | 09:20:00 | 7,069.50 | 7,130.51 | 09:36 | 7,037.75 | PROFIT LOCK/TRAIL | +0.52 | +1,270 | 232 | **+1,038** |
| 2 | ORB (RVOL 4.8x) | 2,500 | TATACONSUM | SELL | 291 | 09:20:00 | 1,075.50 | 1,084.07 | 09:30 | 1,084.07 | STOP (initial) | -1.00 | -2,495 | 252 | **-2,746** |
| 3 | ORB (RVOL 3.7x) | 1,065 cap | NESTLEIND | BUY | 95 | 09:20:00 | 1,408.70 | 1,397.54 | 09:25 | 1,397.54 | STOP (initial) | -1.00 | -1,060 | 134 | **-1,195** |
| 4 | Funnel score 2 | 2,250 ½ | LODHA | BUY | 321 | 10:43:30 | 948.95 | 941.95 | 10:58 | 945.50 | HARD DAY STOP flatten | -0.49 | -1,107 | 246 | **-1,354** |
| 5 | Funnel score 2 | 2,250 ½ | MAZDOCK | BUY | 157 | 10:47:30 | 2,490.00 | 2,475.70 | 10:58 | 2,485.90 | HARD DAY STOP flatten | -0.29 | -644 | 304 | **-947** |

**Month: 93 trades, before charges ₹+64,285, Kite charges ₹15,427, slippage ₹9,336, after charges ₹+39,522**