# ARB (Adaptive Risk Budget) — ORB + Funnel + Day lock — 2026-04 order book (backtest)

ORB: top 3 stocks by opening RVOL (≥3×) at 09:20, direction of first 5m candle, entry on break of 5m high/low until 11:00, stop 0.35×ATR14, ₹2,500 risk. Funnel: vol ≥2×, entry <13:00, stop ≥0.25%, VWAP off, skip repeat triggers; risk by quality score 0/1/2/3+ = ₹1,000 (max 2/day) / ₹2,500 / ₹4,500 / ₹6,000. Exits (both): initial stop → at +1R lock +0.25R → after +2R keep 25% of peak → 14:50. Day lock: once day P&L (closed + open) peaks ≥ ₹3,000, flatten everything and stop if it falls to 40% of the peak. ARB loss control: half size after the day's first losing trade; open-risk cap = ₹6,000 + today's profit so far (house money); a trade stops counting against the cap once it has closed a 1m bar at ≥ +1R (its stop is then in profit); HARD DAY STOP — if day P&L (closed + open) hits −₹5,000, flatten everything and stop. ₹5L capital. After charges = gross − Kite charges − 3 bps slippage.

## 2026-04-01 — 5 trades, before charges ₹-4,018, after charges ₹-5,038

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 5.0x) | 2,500 | TRENT | BUY | 82 | 09:22:00 | 2,349.30 | 2,319.03 | 11:02 | 2,319.03 | STOP (initial) | -1.00 | -2,482 | 173 | **-2,655** |
| 2 | ORB (RVOL 5.3x) | 2,500 | NESTLEIND | BUY | 276 | 09:28:00 | 1,190.40 | 1,181.37 | 09:33 | 1,181.37 | STOP (initial) | -1.00 | -2,494 | 262 | **-2,756** |
| 3 | ORB (RVOL 13.6x) | 1,250 ½ | DMART | BUY | 29 | 09:50:00 | 4,229.00 | 4,186.13 | 11:01 | 4,239.72 | PROFIT LOCK/TRAIL | +0.25 | +311 | 128 | **+183** |
| 4 | Funnel score 1 | 1,250 ½ | AXISBANK | BUY | 240 | 09:51:30 | 1,200.10 | 1,194.90 | 10:06 | 1,201.40 | PROFIT LOCK/TRAIL | +0.25 | +312 | 236 | **+76** |
| 5 | Funnel score 1 | 1,250 ½ | HDFCLIFE | SELL | 446 | 10:21:30 | 587.85 | 590.65 | 10:34 | 587.10 | PROFIT LOCK/TRAIL | +0.27 | +334 | 219 | **+115** |

## 2026-04-02 — 7 trades, before charges ₹+15,637, after charges ₹+13,927

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.2x) | 2,500 | BOSCHLTD | BUY | 7 | 09:20:00 | 30,445.00 | 30,100.12 | 14:50 | 32,050.00 | SQUARE_OFF 14:50 | +4.65 | +11,235 | 189 | **+11,046** |
| 2 | ORB (RVOL 3.0x) | 2,500 | DMART | BUY | 51 | 09:21:00 | 4,256.00 | 4,207.41 | 10:04 | 4,268.15 | PROFIT LOCK/TRAIL | +0.25 | +620 | 190 | **+430** |
| 3 | Funnel score 1 | 2,500 | BAJAJ-AUTO | SELL | 66 | 10:06:30 | 8,685.95 | 8,723.55 | 11:57 | 8,676.50 | PROFIT LOCK/TRAIL | +0.25 | +624 | 422 | **+202** |
| 4 | Funnel score 2 | 1,104 cap | ADANIPORTS | BUY | 306 | 10:26:30 | 1,344.80 | 1,341.20 | 10:46 | 1,345.70 | PROFIT LOCK/TRAIL | +0.25 | +275 | 316 | **-41** |
| 5 | Funnel score 2 | 1,104 ½ cap | LTM | BUY | 84 | 10:57:30 | 4,105.10 | 4,092.00 | 11:02 | 4,108.30 | PROFIT LOCK/TRAIL | +0.24 | +269 | 272 | **-4** |
| 6 | Funnel score 0 | 500 ½ | TATACONSUM | SELL | 192 | 12:11:30 | 1,020.40 | 1,023.00 | 12:17 | 1,019.70 | PROFIT LOCK/TRAIL | +0.27 | +134 | 176 | **-41** |
| 7 | Funnel score 1 | 1,250 ½ | UNIONBANK | BUY | 905 | 12:55:30 | 162.62 | 161.24 | 14:50 | 165.36 | SQUARE_OFF 14:50 | +1.99 | +2,480 | 143 | **+2,336** |

## 2026-04-06 — 3 trades, before charges ₹+2,099, after charges ₹+1,572

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 5.7x) | 2,500 | TRENT | BUY | 73 | 09:20:00 | 2,468.20 | 2,434.09 | 10:06 | 2,499.30 | DAY LOCK flatten | +0.91 | +2,270 | 166 | **+2,104** |
| 2 | ORB (RVOL 3.2x) | 2,500 | BOSCHLTD | BUY | 6 | 09:20:00 | 32,790.00 | 32,404.75 | 10:04 | 32,886.31 | PROFIT LOCK/TRAIL | +0.25 | +578 | 176 | **+402** |
| 3 | ORB (RVOL 7.2x) | 2,500 | DMART | SELL | 48 | 09:38:00 | 4,384.30 | 4,435.88 | 10:06 | 4,399.90 | DAY LOCK flatten | -0.30 | -749 | 185 | **-934** |

## 2026-04-07 — 2 trades, before charges ₹+1,164, after charges ₹+764

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 10.9x) | 2,500 | GODREJCP | BUY | 254 | 09:20:00 | 1,024.00 | 1,014.16 | 09:28 | 1,033.85 | DAY LOCK flatten | +1.00 | +2,502 | 219 | **+2,283** |
| 2 | ORB (RVOL 4.5x) | 2,500 | HINDALCO | BUY | 214 | 09:20:00 | 964.25 | 952.61 | 09:28 | 958.00 | DAY LOCK flatten | -0.54 | -1,338 | 181 | **-1,519** |

## 2026-04-08 — 3 trades, before charges ₹+2,218, after charges ₹+1,673

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 8.7x) | 2,500 | TITAN | BUY | 51 | 09:20:00 | 4,385.40 | 4,337.11 | 09:38 | 4,425.30 | DAY LOCK flatten | +0.83 | +2,035 | 194 | **+1,841** |
| 2 | ORB (RVOL 6.5x) | 2,500 | INDIGO | BUY | 36 | 09:20:00 | 4,695.60 | 4,627.42 | 09:38 | 4,673.20 | DAY LOCK flatten | -0.33 | -806 | 158 | **-964** |
| 3 | ORB (RVOL 7.7x) | 2,500 | PIDILITIND | SELL | 165 | 09:24:00 | 1,359.10 | 1,374.16 | 09:38 | 1,353.10 | DAY LOCK flatten | +0.40 | +990 | 194 | **+796** |

## 2026-04-09 — 8 trades, before charges ₹-3,207, after charges ₹-5,058

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.8x) | 2,500 | NTPC | BUY | 761 | 09:24:00 | 381.05 | 377.77 | 14:00 | 379.30 | HARD DAY STOP flatten | -0.53 | -1,332 | 237 | **-1,569** |
| 2 | Funnel score 1 | 2,500 | ADANIGREEN | SELL | 561 | 09:49:30 | 1,025.30 | 1,029.75 | 09:59 | 1,029.75 | STOP (initial) | -1.00 | -2,496 | 424 | **-2,920** |
| 3 | ORB (RVOL 12.5x) | 1,250 ½ | BOSCHLTD | BUY | 2 | 10:18:00 | 36,930.00 | 36,483.50 | 13:39 | 37,041.62 | PROFIT LOCK/TRAIL | +0.25 | +223 | 96 | **+127** |
| 4 | Funnel score 0 | 500 ½ | ASIANPAINT | SELL | 47 | 10:31:30 | 2,266.70 | 2,277.30 | 11:27 | 2,277.30 | STOP (initial) | -1.00 | -498 | 117 | **-615** |
| 5 | Funnel score 1 | 1,250 ½ | CANBK | SELL | 3472 | 11:06:30 | 135.42 | 135.78 | 11:55 | 135.22 | PROFIT LOCK/TRAIL | +0.56 | +694 | 355 | **+339** |
| 6 | Funnel score 1 | 860 ½ cap | DMART | BUY | 43 | 11:06:30 | 4,404.50 | 4,384.90 | 14:00 | 4,395.00 | HARD DAY STOP flatten | -0.48 | -408 | 171 | **-580** |
| 7 | Funnel score 0 | 500 ½ | HDFCAMC | BUY | 55 | 11:53:30 | 2,490.80 | 2,481.80 | 12:48 | 2,481.80 | STOP (initial) | -1.00 | -495 | 137 | **-632** |
| 8 | Funnel score 1 | 1,250 ½ | SHREECEM | BUY | 17 | 12:28:30 | 24,005.00 | 23,935.00 | 14:00 | 24,070.00 | HARD DAY STOP flatten | +0.93 | +1,105 | 314 | **+791** |

## 2026-04-10 — 4 trades, before charges ₹+4,324, after charges ₹+2,609

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 5.4x) | 2,500 | WIPRO | SELL | 1331 | 09:26:00 | 205.50 | 207.38 | 10:09 | 203.35 | DAY LOCK flatten | +1.15 | +2,862 | 226 | **+2,636** |
| 2 | ORB (RVOL 4.8x) | 2,500 | TCS | SELL | 107 | 09:35:00 | 2,530.10 | 2,553.38 | 10:09 | 2,513.10 | DAY LOCK flatten | +0.73 | +1,819 | 225 | **+1,594** |
| 3 | Funnel score 2 | 4,500 | PFC | SELL | 2368 | 10:05:30 | 429.30 | 431.20 | 10:09 | 429.10 | DAY LOCK flatten | +0.11 | +474 | 712 | **-239** |
| 4 | Funnel score 3 | 2,558 cap | JINDALSTEL | SELL | 639 | 10:05:30 | 1,208.10 | 1,212.10 | 10:09 | 1,209.40 | DAY LOCK flatten | -0.33 | -831 | 553 | **-1,383** |

## 2026-04-13 — 3 trades, before charges ₹+1,714, after charges ₹+577

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.4x) | 2,500 | ADANIENSOL | BUY | 145 | 09:23:00 | 1,140.00 | 1,122.87 | 10:41 | 1,160.20 | DAY LOCK flatten | +1.18 | +2,929 | 156 | **+2,773** |
| 2 | Funnel score 1 | 2,500 | BOSCHLTD | BUY | 20 | 10:33:30 | 37,125.00 | 37,005.00 | 10:41 | 37,070.00 | DAY LOCK flatten | -0.46 | -1,100 | 532 | **-1,632** |
| 3 | Funnel score 1 | 2,500 | CUMMINSIND | SELL | 121 | 10:41:30 | 5,064.95 | 5,085.55 | 10:41 | 5,065.90 | DAY LOCK flatten | -0.05 | -115 | 448 | **-563** |

## 2026-04-15 — 5 trades, before charges ₹-2,168, after charges ₹-3,223

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 6.0x) | 2,500 | DRREDDY | SELL | 183 | 09:20:00 | 1,198.80 | 1,212.43 | 09:45 | 1,212.43 | STOP (initial) | -1.00 | -2,495 | 192 | **-2,686** |
| 2 | ORB (RVOL 3.3x) | 1,250 ½ | TATACAP | BUY | 363 | 09:52:00 | 329.25 | 325.81 | 14:50 | 330.60 | SQUARE_OFF 14:50 | +0.39 | +490 | 126 | **+364** |
| 3 | Funnel score 1 | 1,250 ½ | GAIL | BUY | 2717 | 10:20:30 | 156.95 | 156.49 | 10:44 | 156.49 | STOP (initial) | -1.00 | -1,250 | 326 | **-1,576** |
| 4 | Funnel score 0 | 500 ½ | TITAN | BUY | 41 | 10:44:30 | 4,497.05 | 4,484.95 | 12:23 | 4,503.10 | PROFIT LOCK/TRAIL | +0.50 | +248 | 168 | **+80** |
| 5 | Funnel score 1 | 1,250 ½ | UNIONBANK | SELL | 1644 | 10:51:30 | 182.44 | 183.20 | 12:00 | 181.93 | PROFIT LOCK/TRAIL | +0.67 | +838 | 244 | **+595** |

## 2026-04-16 — 1 trades, before charges ₹+1,124, after charges ₹+273

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | Funnel score 2 | 4,500 | ADANIPORTS | BUY | 803 | 09:46:30 | 1,528.10 | 1,522.50 | 09:56 | 1,529.50 | PROFIT LOCK/TRAIL | +0.25 | +1,124 | 851 | **+273** |

## 2026-04-17 — 6 trades, before charges ₹+11,025, after charges ₹+8,836

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.5x) | 2,500 | HDFCLIFE | SELL | 348 | 09:20:00 | 607.20 | 614.37 | 11:10 | 614.37 | STOP (initial) | -1.00 | -2,493 | 185 | **-2,679** |
| 2 | Funnel score 1 | 2,500 | BPCL | BUY | 2777 | 10:20:30 | 309.55 | 308.65 | 14:50 | 312.75 | SQUARE_OFF 14:50 | +3.56 | +8,886 | 613 | **+8,274** |
| 3 | Funnel score 2 | 1,007 cap | HYUNDAI | BUY | 113 | 10:22:30 | 1,878.70 | 1,869.80 | 14:50 | 1,900.40 | SQUARE_OFF 14:50 | +2.44 | +2,452 | 187 | **+2,265** |
| 4 | Funnel score 1 | 2,500 | ADANIENT | SELL | 277 | 11:03:30 | 2,199.90 | 2,208.90 | 13:09 | 2,197.60 | PROFIT LOCK/TRAIL | +0.26 | +637 | 446 | **+192** |
| 5 | Funnel score 2 | 3,520 cap | LODHA | SELL | 703 | 11:05:30 | 863.85 | 866.25 | 11:13 | 863.20 | PROFIT LOCK/TRAIL | +0.27 | +457 | 445 | **+12** |
| 6 | Funnel score 1 | 1,250 ½ | RECLTD | BUY | 1086 | 12:21:30 | 373.55 | 372.40 | 14:04 | 374.55 | PROFIT LOCK/TRAIL | +0.87 | +1,086 | 313 | **+773** |

## 2026-04-20 — 5 trades, before charges ₹+384, after charges ₹-699

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 5.1x) | 2,500 | TVSMOTOR | BUY | 61 | 09:25:00 | 3,762.30 | 3,721.42 | 09:38 | 3,721.42 | STOP (initial) | -1.00 | -2,494 | 197 | **-2,691** |
| 2 | Funnel score 0 | 500 ½ | PFC | BUY | 181 | 10:52:30 | 468.30 | 465.55 | 14:50 | 474.25 | SQUARE_OFF 14:50 | +2.16 | +1,077 | 103 | **+974** |
| 3 | Funnel score 0 | 500 ½ | PNB | BUY | 1020 | 11:00:30 | 111.76 | 111.27 | 11:48 | 111.27 | STOP (initial) | -1.00 | -500 | 121 | **-621** |
| 4 | Funnel score 1 | 1,250 ½ | TITAN | SELL | 104 | 12:36:30 | 4,491.95 | 4,503.95 | 14:13 | 4,485.85 | PROFIT LOCK/TRAIL | +0.51 | +634 | 353 | **+281** |
| 5 | Funnel score 1 | 1,250 ½ | TATACAP | BUY | 1190 | 12:42:30 | 336.00 | 334.95 | 14:50 | 337.40 | SQUARE_OFF 14:50 | +1.33 | +1,666 | 309 | **+1,357** |

## 2026-04-21 — 4 trades, before charges ₹+3,823, after charges ₹+2,779

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.2x) | 2,500 | ENRIN | BUY | 67 | 09:21:00 | 3,208.20 | 3,170.96 | 11:13 | 3,226.10 | DAY LOCK flatten | +0.48 | +1,199 | 188 | **+1,011** |
| 2 | ORB (RVOL 8.4x) | 2,500 | SBILIFE | SELL | 138 | 09:26:00 | 1,920.70 | 1,938.69 | 11:13 | 1,909.10 | DAY LOCK flatten | +0.64 | +1,601 | 220 | **+1,380** |
| 3 | Funnel score 1 | 2,500 | SOLARINDS | BUY | 40 | 10:12:30 | 15,084.05 | 15,021.95 | 10:59 | 15,099.55 | PROFIT LOCK/TRAIL | +0.25 | +620 | 442 | **+178** |
| 4 | ORB (RVOL 5.2x) | 2,500 | ADANIPORTS | BUY | 139 | 10:39:00 | 1,605.00 | 1,587.12 | 11:13 | 1,607.90 | DAY LOCK flatten | +0.16 | +403 | 194 | **+209** |

## 2026-04-22 — 5 trades, before charges ₹-1,933, after charges ₹-2,705

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 6.9x) | 2,500 | HCLTECH | SELL | 181 | 09:20:00 | 1,315.00 | 1,328.79 | 09:25 | 1,311.55 | PROFIT LOCK/TRAIL | +0.25 | +624 | 203 | **+420** |
| 2 | ORB (RVOL 4.2x) | 2,500 | NESTLEIND | BUY | 201 | 09:20:00 | 1,419.00 | 1,406.57 | 09:28 | 1,406.57 | STOP (initial) | -1.00 | -2,499 | 234 | **-2,733** |
| 3 | Funnel score 0 | 500 ½ | CANBK | BUY | 434 | 10:46:30 | 141.04 | 139.89 | 12:09 | 141.32 | PROFIT LOCK/TRAIL | +0.24 | +122 | 83 | **+38** |
| 4 | Funnel score 0 | 500 ½ | ADANIGREEN | BUY | 41 | 11:32:30 | 1,185.05 | 1,173.00 | 11:53 | 1,173.00 | STOP (initial) | -1.00 | -494 | 65 | **-559** |
| 5 | Funnel score 1 | 1,250 ½ | ENRIN | SELL | 67 | 12:53:30 | 3,183.70 | 3,202.20 | 12:58 | 3,179.00 | PROFIT LOCK/TRAIL | +0.25 | +315 | 186 | **+129** |

## 2026-04-23 — 6 trades, before charges ₹+12,153, after charges ₹+11,061

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.6x) | 2,500 | DRREDDY | BUY | 218 | 09:20:00 | 1,256.80 | 1,245.35 | 14:50 | 1,322.70 | SQUARE_OFF 14:50 | +5.75 | +14,366 | 230 | **+14,136** |
| 2 | ORB (RVOL 3.7x) | 2,500 | HCLTECH | SELL | 145 | 09:26:00 | 1,280.30 | 1,297.44 | 14:50 | 1,279.90 | SQUARE_OFF 14:50 | +0.02 | +58 | 169 | **-111** |
| 3 | ORB (RVOL 7.2x) | 2,500 | TRENT | SELL | 65 | 09:33:00 | 2,920.10 | 2,958.24 | 13:24 | 2,898.75 | PROFIT LOCK/TRAIL | +0.56 | +1,388 | 171 | **+1,216** |
| 4 | Funnel score 0 | 1,000 | ENRIN | SELL | 51 | 10:57:30 | 3,234.60 | 3,254.20 | 11:07 | 3,254.20 | STOP (initial) | -1.00 | -1,000 | 155 | **-1,155** |
| 5 | Funnel score 2 | 2,250 ½ | BAJAJHLDNG | BUY | 24 | 11:17:30 | 10,495.05 | 10,404.95 | 12:03 | 10,404.95 | STOP (initial) | -1.00 | -2,162 | 212 | **-2,374** |
| 6 | Funnel score 0 | 500 ½ | INDIGO | SELL | 36 | 12:31:30 | 4,554.25 | 4,568.05 | 13:37 | 4,568.05 | STOP (initial) | -1.00 | -497 | 155 | **-651** |

## 2026-04-24 — 5 trades, before charges ₹+1,175, after charges ₹+275

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 5.4x) | 2,500 | CIPLA | SELL | 276 | 09:20:00 | 1,260.00 | 1,269.03 | 09:26 | 1,269.03 | STOP (initial) | -1.00 | -2,492 | 276 | **-2,768** |
| 2 | ORB (RVOL 14.2x) | 2,500 | TATACAP | SELL | 911 | 09:21:00 | 340.20 | 342.94 | 09:38 | 339.51 | PROFIT LOCK/TRAIL | +0.25 | +625 | 249 | **+375** |
| 3 | ORB (RVOL 3.7x) | 1,009 cap | DRREDDY | BUY | 77 | 09:24:00 | 1,313.10 | 1,300.10 | 09:54 | 1,316.35 | PROFIT LOCK/TRAIL | +0.25 | +250 | 113 | **+137** |
| 4 | Funnel score 0 | 500 ½ | TATACONSUM | SELL | 125 | 10:32:30 | 1,178.90 | 1,182.90 | 14:50 | 1,168.80 | SQUARE_OFF 14:50 | +2.53 | +1,262 | 143 | **+1,119** |
| 5 | Funnel score 0 | 500 ½ | HCLTECH | SELL | 90 | 10:46:30 | 1,221.90 | 1,227.40 | 14:50 | 1,204.90 | SQUARE_OFF 14:50 | +3.09 | +1,530 | 118 | **+1,412** |

## 2026-04-27 — 7 trades, before charges ₹-3,676, after charges ₹-5,144

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 6.9x) | 2,500 | SUNPHARMA | BUY | 170 | 09:20:00 | 1,699.00 | 1,684.34 | 09:29 | 1,684.34 | STOP (initial) | -1.00 | -2,493 | 236 | **-2,729** |
| 2 | ORB (RVOL 3.4x) | 1,250 ½ | SHRIRAMFIN | SELL | 95 | 09:36:00 | 973.35 | 986.38 | 11:37 | 973.25 | HARD DAY STOP flatten | +0.01 | +10 | 108 | **-98** |
| 3 | Funnel score 1 | 1,250 ½ | BAJAJHLDNG | BUY | 42 | 10:02:30 | 10,380.05 | 10,350.95 | 10:25 | 10,387.30 | PROFIT LOCK/TRAIL | +0.25 | +304 | 333 | **-28** |
| 4 | Funnel score 2 | 2,250 ½ | HDFCLIFE | BUY | 671 | 10:23:30 | 600.60 | 597.25 | 11:37 | 597.70 | HARD DAY STOP flatten | -0.87 | -1,946 | 310 | **-2,256** |
| 5 | Funnel score 0 | 500 ½ | ADANIENSOL | BUY | 33 | 10:36:30 | 1,436.90 | 1,421.95 | 11:37 | 1,430.80 | HARD DAY STOP flatten | -0.41 | -201 | 64 | **-266** |
| 6 | Funnel score 0 | 500 ½ | POWERGRID | BUY | 270 | 11:14:30 | 323.05 | 321.20 | 11:22 | 323.50 | PROFIT LOCK/TRAIL | +0.24 | +122 | 105 | **+17** |
| 7 | Funnel score 3 | 2,021 ½ cap | TMCV | SELL | 962 | 11:23:30 | 421.95 | 424.05 | 11:27 | 421.40 | PROFIT LOCK/TRAIL | +0.26 | +529 | 312 | **+217** |

## 2026-04-28 — 6 trades, before charges ₹+3,991, after charges ₹+2,844

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.9x) | 2,500 | SUNPHARMA | BUY | 144 | 09:20:00 | 1,728.80 | 1,711.51 | 12:20 | 1,750.70 | DAY LOCK flatten | +1.27 | +3,154 | 211 | **+2,943** |
| 2 | ORB (RVOL 5.0x) | 2,500 | ULTRACEMCO | SELL | 24 | 09:22:00 | 11,696.00 | 11,799.70 | 12:20 | 11,614.00 | DAY LOCK flatten | +0.79 | +1,968 | 230 | **+1,738** |
| 3 | ORB (RVOL 4.0x) | 2,108 cap | GRASIM | BUY | 92 | 10:02:00 | 2,829.50 | 2,806.67 | 12:20 | 2,824.80 | DAY LOCK flatten | -0.21 | -432 | 218 | **-650** |
| 4 | Funnel score 1 | 859 cap | BANKBARODA | BUY | 547 | 10:25:30 | 262.86 | 261.29 | 11:28 | 261.29 | STOP (initial) | -1.00 | -859 | 141 | **-1,000** |
| 5 | Funnel score 1 | 1,250 ½ | JINDALSTEL | SELL | 176 | 12:02:30 | 1,274.30 | 1,281.40 | 12:20 | 1,274.90 | DAY LOCK flatten | -0.08 | -106 | 194 | **-300** |
| 6 | Funnel score 0 | 500 ½ | ONGC | BUY | 543 | 12:15:30 | 296.61 | 295.69 | 12:20 | 297.10 | DAY LOCK flatten | +0.53 | +266 | 153 | **+113** |

## 2026-04-29 — 3 trades, before charges ₹+1,682, after charges ₹+1,168

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.7x) | 2,500 | RECLTD | SELL | 616 | 09:20:00 | 365.10 | 369.15 | 09:25 | 362.20 | DAY LOCK flatten | +0.72 | +1,786 | 194 | **+1,592** |
| 2 | ORB (RVOL 11.4x) | 2,500 | ETERNAL | SELL | 814 | 09:21:00 | 257.00 | 260.07 | 09:25 | 257.90 | DAY LOCK flatten | -0.29 | -733 | 184 | **-916** |
| 3 | ORB (RVOL 3.8x) | 1,004 cap | ONGC | SELL | 449 | 09:21:00 | 302.50 | 304.73 | 09:25 | 301.10 | DAY LOCK flatten | +0.63 | +629 | 136 | **+493** |

## 2026-04-30 — 3 trades, before charges ₹-4,626, after charges ₹-5,508

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 11.4x) | 2,500 | BAJFINANCE | BUY | 303 | 09:20:00 | 969.80 | 961.57 | 09:29 | 961.45 | STOP (initial) | -1.01 | -2,530 | 239 | **-2,769** |
| 2 | ORB (RVOL 4.8x) | 2,500 | BAJAJFINSV | BUY | 181 | 09:24:00 | 1,790.00 | 1,776.23 | 09:49 | 1,787.00 | HARD DAY STOP flatten | -0.22 | -543 | 260 | **-803** |
| 3 | Funnel score 2 | 2,250 ½ | BAJAJHLDNG | BUY | 50 | 09:46:30 | 10,304.55 | 10,259.95 | 09:49 | 10,273.50 | HARD DAY STOP flatten | -0.70 | -1,552 | 383 | **-1,936** |

**Month: 91 trades, before charges ₹+42,887, Kite charges ₹13,823, slippage ₹8,079, after charges ₹+20,984**