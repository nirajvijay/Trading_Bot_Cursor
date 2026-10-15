# ARB (Adaptive Risk Budget) — ORB + Funnel + Day lock — 2026-02 order book (backtest)

ORB: top 3 stocks by opening RVOL (≥3×) at 09:20, direction of first 5m candle, entry on break of 5m high/low until 11:00, stop 0.35×ATR14, ₹2,500 risk. Funnel: vol ≥2×, entry <13:00, stop ≥0.25%, VWAP off, skip repeat triggers; risk by quality score 0/1/2/3+ = ₹1,000 (max 2/day) / ₹2,500 / ₹4,500 / ₹6,000. Exits (both): initial stop → at +1R lock +0.25R → after +2R keep 25% of peak → 14:50. Day lock: once day P&L (closed + open) peaks ≥ ₹3,000, flatten everything and stop if it falls to 40% of the peak. ARB loss control: half size after the day's first losing trade; open-risk cap = ₹6,000 + today's profit so far (house money); a trade stops counting against the cap once it has closed a 1m bar at ≥ +1R (its stop is then in profit); HARD DAY STOP — if day P&L (closed + open) hits −₹5,000, flatten everything and stop. ₹5L capital. After charges = gross − Kite charges − 3 bps slippage.

## 2026-02-01 — 5 trades, before charges ₹-1,205, after charges ₹-2,068

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.1x) | 2,500 | JINDALSTEL | BUY | 206 | 09:36:00 | 1,125.30 | 1,113.21 | 10:08 | 1,128.32 | PROFIT LOCK/TRAIL | +0.25 | +623 | 199 | **+423** |
| 2 | ORB (RVOL 4.6x) | 2,500 | BAJAJ-AUTO | SELL | 35 | 09:40:00 | 9,650.50 | 9,721.67 | 09:40 | 9,721.67 | STOP (initial) | -1.00 | -2,491 | 268 | **-2,759** |
| 3 | Funnel score 1 | 1,250 ½ | BAJAJ-AUTO | SELL | 9 | 11:14:30 | 9,636.45 | 9,762.05 | 14:36 | 9,521.20 | PROFIT LOCK/TRAIL | +0.92 | +1,037 | 105 | **+933** |
| 4 | Funnel score 0 | 500 ½ | MOTHERSON | BUY | 1562 | 11:34:30 | 113.81 | 113.49 | 11:42 | 113.49 | STOP (initial) | -1.00 | -500 | 163 | **-663** |
| 5 | Funnel score 0 | 500 ½ | BRITANNIA | SELL | 21 | 11:45:30 | 5,852.00 | 5,875.50 | 12:10 | 5,846.00 | PROFIT LOCK/TRAIL | +0.26 | +126 | 128 | **-2** |

## 2026-02-02 — 2 trades, before charges ₹+1,391, after charges ₹+1,019

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | Funnel score 1 | 2,500 | ONGC | SELL | 1302 | 09:45:30 | 246.24 | 248.16 | 10:36 | 245.10 | DAY LOCK flatten | +0.59 | +1,484 | 257 | **+1,227** |
| 2 | Funnel score 0 | 1,000 | TATACONSUM | BUY | 93 | 10:35:30 | 1,112.50 | 1,101.80 | 10:36 | 1,111.50 | DAY LOCK flatten | -0.09 | -93 | 115 | **-208** |

## 2026-02-03 — 8 trades, before charges ₹+5,554, after charges ₹+3,909

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 9.1x) | 2,500 | MOTHERSON | SELL | 1877 | 09:20:00 | 122.05 | 123.38 | 09:31 | 121.72 | PROFIT LOCK/TRAIL | +0.25 | +619 | 197 | **+422** |
| 2 | ORB (RVOL 9.1x) | 2,500 | ADANIENT | BUY | 93 | 09:31:00 | 2,194.90 | 2,168.23 | 09:33 | 2,201.57 | PROFIT LOCK/TRAIL | +0.25 | +620 | 181 | **+439** |
| 3 | ORB (RVOL 7.3x) | 2,500 | RELIANCE | SELL | 204 | 09:42:00 | 1,436.90 | 1,449.11 | 10:22 | 1,449.11 | STOP (initial) | -1.00 | -2,491 | 239 | **-2,730** |
| 4 | Funnel score 1 | 2,500 | HAL | SELL | 101 | 09:58:30 | 4,444.30 | 4,469.00 | 10:25 | 4,438.10 | PROFIT LOCK/TRAIL | +0.25 | +626 | 340 | **+286** |
| 5 | Funnel score 0 | 500 ½ | GAIL | SELL | 657 | 10:36:30 | 157.50 | 158.26 | 11:02 | 157.30 | PROFIT LOCK/TRAIL | +0.26 | +131 | 115 | **+16** |
| 6 | Funnel score 2 | 2,250 ½ | ADANIPOWER | BUY | 2848 | 11:27:30 | 144.48 | 143.69 | 12:31 | 145.75 | DAY LOCK flatten | +1.61 | +3,617 | 318 | **+3,299** |
| 7 | Funnel score 1 | 1,250 ½ | ADANIENSOL | BUY | 164 | 11:27:30 | 959.95 | 952.35 | 12:31 | 974.25 | DAY LOCK flatten | +1.88 | +2,345 | 151 | **+2,194** |
| 8 | Funnel score 0 | 500 ½ | DLF | SELL | 131 | 12:02:30 | 654.85 | 658.65 | 12:31 | 654.20 | DAY LOCK flatten | +0.17 | +85 | 103 | **-18** |

## 2026-02-04 — 7 trades, before charges ₹+17,584, after charges ₹+16,082

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 9.4x) | 2,500 | VBL | SELL | 427 | 09:20:00 | 438.20 | 444.04 | 09:21 | 444.04 | STOP (initial) | -1.00 | -2,495 | 170 | **-2,665** |
| 2 | ORB (RVOL 6.8x) | 2,500 | PIDILITIND | BUY | 219 | 09:20:00 | 1,448.00 | 1,436.61 | 09:38 | 1,450.85 | PROFIT LOCK/TRAIL | +0.25 | +623 | 255 | **+368** |
| 3 | Funnel score 1 | 1,250 ½ | BPCL | BUY | 961 | 10:01:30 | 381.30 | 380.00 | 10:37 | 381.60 | PROFIT LOCK/TRAIL | +0.23 | +288 | 288 | **+1** |
| 4 | ORB (RVOL 4.8x) | 1,250 ½ | TCS | SELL | 50 | 10:59:00 | 3,031.20 | 3,055.90 | 14:50 | 3,007.70 | SQUARE_OFF 14:50 | +0.95 | +1,175 | 147 | **+1,028** |
| 5 | Funnel score 0 | 500 ½ | ADANIENT | SELL | 41 | 11:28:30 | 2,194.90 | 2,207.00 | 11:41 | 2,207.00 | STOP (initial) | -1.00 | -496 | 106 | **-602** |
| 6 | Funnel score 0 | 500 ½ | ADANIPOWER | BUY | 1249 | 11:31:30 | 147.99 | 147.59 | 14:50 | 154.57 | SQUARE_OFF 14:50 | +16.45 | +8,218 | 171 | **+8,048** |
| 7 | Funnel score 2 | 2,250 ½ | BOSCHLTD | SELL | 13 | 12:30:30 | 37,595.00 | 37,760.00 | 14:50 | 36,805.00 | SQUARE_OFF 14:50 | +4.79 | +10,270 | 366 | **+9,904** |

## 2026-02-05 — 4 trades, before charges ₹-4,209, after charges ₹-5,114

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 9.0x) | 2,500 | TRENT | BUY | 90 | 09:20:00 | 2,673.00 | 2,645.47 | 09:30 | 2,679.88 | PROFIT LOCK/TRAIL | +0.25 | +619 | 204 | **+415** |
| 2 | ORB (RVOL 7.0x) | 2,500 | BAJAJHLDNG | BUY | 31 | 09:23:00 | 11,167.00 | 11,087.95 | 09:30 | 11,087.95 | STOP (initial) | -1.00 | -2,451 | 273 | **-2,723** |
| 3 | ORB (RVOL 4.6x) | 1,250 ½ | CUMMINSIND | BUY | 27 | 09:31:00 | 4,190.00 | 4,145.17 | 10:07 | 4,164.00 | HARD DAY STOP flatten | -0.58 | -702 | 121 | **-823** |
| 4 | Funnel score 2 | 2,250 ½ | HDFCAMC | BUY | 147 | 09:48:30 | 2,709.70 | 2,694.40 | 10:07 | 2,698.30 | HARD DAY STOP flatten | -0.75 | -1,676 | 307 | **-1,983** |

## 2026-02-06 — 6 trades, before charges ₹+1,674, after charges ₹-208

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.9x) | 2,500 | TMPV | BUY | 553 | 09:20:00 | 376.00 | 371.48 | 09:36 | 377.13 | PROFIT LOCK/TRAIL | +0.25 | +625 | 183 | **+441** |
| 2 | ORB (RVOL 3.2x) | 2,500 | ENRIN | BUY | 61 | 09:20:00 | 2,676.60 | 2,635.85 | 10:25 | 2,686.00 | DAY LOCK flatten | +0.23 | +573 | 154 | **+419** |
| 3 | ORB (RVOL 13.4x) | 1,016 cap | ADANIPORTS | BUY | 47 | 09:21:00 | 1,575.50 | 1,553.94 | 10:25 | 1,556.40 | DAY LOCK flatten | -0.89 | -898 | 95 | **-993** |
| 4 | Funnel score 2 | 2,501 cap | BRITANNIA | SELL | 161 | 09:56:30 | 5,830.00 | 5,845.50 | 10:00 | 5,828.00 | PROFIT LOCK/TRAIL | +0.13 | +322 | 662 | **-340** |
| 5 | Funnel score 2 | 2,250 ½ | LTM | BUY | 114 | 10:17:30 | 5,559.10 | 5,539.40 | 10:25 | 5,568.50 | DAY LOCK flatten | +0.48 | +1,072 | 463 | **+609** |
| 6 | Funnel score 2 | 2,250 ½ | LODHA | BUY | 405 | 10:21:30 | 1,043.75 | 1,038.20 | 10:25 | 1,043.70 | DAY LOCK flatten | -0.01 | -20 | 325 | **-345** |

## 2026-02-09 — 4 trades, before charges ₹-1,639, after charges ₹-2,261

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 11.3x) | 2,500 | SBIN | BUY | 233 | 09:22:00 | 1,135.00 | 1,124.31 | 09:53 | 1,124.31 | STOP (initial) | -1.00 | -2,491 | 219 | **-2,710** |
| 2 | Funnel score 0 | 500 ½ | ADANIENT | BUY | 45 | 10:43:30 | 2,271.00 | 2,260.10 | 11:00 | 2,273.70 | PROFIT LOCK/TRAIL | +0.25 | +122 | 115 | **+7** |
| 3 | ORB (RVOL 10.1x) | 1,250 ½ | SHREECEM | BUY | 5 | 10:56:00 | 26,970.00 | 26,759.38 | 14:50 | 27,215.00 | SQUARE_OFF 14:50 | +1.16 | +1,225 | 136 | **+1,089** |
| 4 | Funnel score 0 | 500 ½ | SIEMENS | BUY | 52 | 12:12:30 | 3,122.10 | 3,112.60 | 12:15 | 3,112.60 | STOP (initial) | -1.00 | -494 | 153 | **-647** |

## 2026-02-10 — 7 trades, before charges ₹-1,906, after charges ₹-3,592

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.6x) | 2,500 | ZYDUSLIFE | SELL | 364 | 09:39:00 | 903.75 | 910.62 | 10:17 | 902.03 | PROFIT LOCK/TRAIL | +0.25 | +625 | 262 | **+362** |
| 2 | Funnel score 1 | 2,500 | TECHM | BUY | 200 | 10:06:30 | 1,615.40 | 1,602.90 | 10:13 | 1,602.90 | STOP (initial) | -1.00 | -2,500 | 258 | **-2,758** |
| 3 | Funnel score 1 | 1,626 cap | HINDUNILVR | BUY | 200 | 10:09:30 | 2,447.10 | 2,439.00 | 11:12 | 2,439.00 | STOP (initial) | -1.00 | -1,620 | 368 | **-1,988** |
| 4 | Funnel score 1 | 1,250 ½ | BAJAJ-AUTO | BUY | 36 | 10:26:30 | 9,740.05 | 9,705.95 | 14:50 | 9,777.50 | SQUARE_OFF 14:50 | +1.10 | +1,348 | 278 | **+1,071** |
| 5 | Funnel score 1 | 1,250 ½ | ADANIPORTS | BUY | 176 | 11:49:30 | 1,554.70 | 1,547.60 | 13:21 | 1,556.40 | PROFIT LOCK/TRAIL | +0.24 | +299 | 226 | **+73** |
| 6 | Funnel score 0 | 500 ½ | TATACAP | BUY | 333 | 12:00:30 | 354.00 | 352.50 | 14:50 | 355.30 | SQUARE_OFF 14:50 | +0.87 | +433 | 125 | **+308** |
| 7 | Funnel score 0 | 500 ½ | CUMMINSIND | BUY | 42 | 12:22:30 | 4,398.45 | 4,386.75 | 13:02 | 4,386.75 | STOP (initial) | -1.00 | -491 | 168 | **-660** |

## 2026-02-11 — 3 trades, before charges ₹+2,910, after charges ₹+2,204

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 10.8x) | 2,500 | EICHERMOT | BUY | 34 | 09:20:00 | 7,707.00 | 7,633.91 | 09:31 | 7,750.00 | DAY LOCK flatten | +0.59 | +1,462 | 219 | **+1,243** |
| 2 | ORB (RVOL 10.5x) | 2,500 | APOLLOHOSP | BUY | 49 | 09:21:00 | 7,550.00 | 7,499.60 | 09:31 | 7,585.50 | DAY LOCK flatten | +0.70 | +1,740 | 290 | **+1,450** |
| 3 | ORB (RVOL 11.9x) | 2,209 cap | TITAN | SELL | 53 | 09:23:00 | 4,313.50 | 4,354.57 | 09:31 | 4,319.00 | DAY LOCK flatten | -0.13 | -292 | 197 | **-488** |

## 2026-02-12 — 5 trades, before charges ₹-2,283, after charges ₹-3,098

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.0x) | 2,500 | INFY | SELL | 148 | 09:21:00 | 1,383.00 | 1,399.83 | 10:15 | 1,378.79 | PROFIT LOCK/TRAIL | +0.25 | +623 | 181 | **+442** |
| 2 | ORB (RVOL 3.6x) | 2,500 | TECHM | SELL | 149 | 09:23:00 | 1,531.80 | 1,548.47 | 09:27 | 1,548.47 | STOP (initial) | -1.00 | -2,485 | 197 | **-2,681** |
| 3 | ORB (RVOL 3.3x) | 1,250 ½ | EICHERMOT | BUY | 16 | 09:45:00 | 7,865.00 | 7,787.01 | 14:50 | 7,908.00 | SQUARE_OFF 14:50 | +0.55 | +688 | 130 | **+558** |
| 4 | Funnel score 0 | 500 ½ | TITAN | SELL | 40 | 10:45:30 | 4,228.05 | 4,240.45 | 11:33 | 4,224.90 | PROFIT LOCK/TRAIL | +0.25 | +126 | 158 | **-32** |
| 5 | Funnel score 1 | 1,250 ½ | HINDUNILVR | BUY | 64 | 12:36:30 | 2,426.40 | 2,407.10 | 14:33 | 2,407.10 | STOP (initial) | -1.00 | -1,235 | 149 | **-1,385** |

## 2026-02-13 — 4 trades, before charges ₹+1,961, after charges ₹+1,013

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 10.5x) | 2,500 | MUTHOOTFIN | SELL | 40 | 09:24:00 | 3,660.30 | 3,721.58 | 10:36 | 3,627.50 | DAY LOCK flatten | +0.54 | +1,312 | 143 | **+1,169** |
| 2 | Funnel score 1 | 2,500 | ZYDUSLIFE | SELL | 724 | 10:02:30 | 903.00 | 906.45 | 10:31 | 902.10 | PROFIT LOCK/TRAIL | +0.26 | +652 | 475 | **+176** |
| 3 | ORB (RVOL 6.8x) | 2,500 | INFY | BUY | 133 | 10:35:00 | 1,296.00 | 1,277.29 | 10:36 | 1,293.20 | DAY LOCK flatten | -0.15 | -372 | 160 | **-532** |
| 4 | ORB (RVOL 5.3x) | 2,500 | LTM | BUY | 37 | 10:35:00 | 5,075.00 | 5,008.44 | 10:36 | 5,085.00 | DAY LOCK flatten | +0.15 | +370 | 171 | **+199** |

## 2026-02-16 — 5 trades, before charges ₹-2,511, after charges ₹-3,470

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 13.5x) | 2,500 | TORNTPHARM | BUY | 72 | 09:21:00 | 4,259.90 | 4,225.57 | 09:51 | 4,268.48 | PROFIT LOCK/TRAIL | +0.25 | +618 | 248 | **+370** |
| 2 | ORB (RVOL 4.5x) | 2,500 | ENRIN | BUY | 58 | 09:21:00 | 2,849.00 | 2,806.04 | 09:30 | 2,806.04 | STOP (initial) | -1.00 | -2,492 | 155 | **-2,647** |
| 3 | Funnel score 0 | 500 ½ | BAJAJHLDNG | BUY | 8 | 10:47:30 | 11,112.00 | 11,053.00 | 11:09 | 11,053.00 | STOP (initial) | -1.00 | -472 | 106 | **-578** |
| 4 | Funnel score 1 | 1,250 ½ | TATACONSUM | SELL | 304 | 11:28:30 | 1,134.90 | 1,139.00 | 12:05 | 1,133.80 | PROFIT LOCK/TRAIL | +0.27 | +334 | 272 | **+62** |
| 5 | Funnel score 0 | 500 ½ | SHRIRAMFIN | BUY | 185 | 11:44:30 | 1,078.40 | 1,075.70 | 11:51 | 1,075.70 | STOP (initial) | -1.00 | -500 | 178 | **-677** |

## 2026-02-17 — 3 trades, before charges ₹-1,574, after charges ₹-2,896

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | Funnel score 1 | 2,500 | LTM | BUY | 73 | 09:55:30 | 5,195.10 | 5,160.90 | 10:44 | 5,160.90 | STOP (initial) | -1.00 | -2,497 | 294 | **-2,791** |
| 2 | Funnel score 1 | 2,500 | ADANIPOWER | BUY | 6944 | 09:57:30 | 143.91 | 143.55 | 10:07 | 144.00 | PROFIT LOCK/TRAIL | +0.25 | +625 | 702 | **-77** |
| 3 | Funnel score 1 | 1,250 ½ | RECLTD | BUY | 1190 | 10:55:30 | 357.05 | 356.00 | 12:27 | 357.30 | PROFIT LOCK/TRAIL | +0.24 | +298 | 325 | **-28** |

## 2026-02-18 — 3 trades, before charges ₹-3,195, after charges ₹-4,156

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | Funnel score 1 | 2,500 | GODREJCP | SELL | 641 | 10:18:30 | 1,201.95 | 1,205.85 | 10:57 | 1,205.85 | STOP (initial) | -1.00 | -2,500 | 552 | **-3,052** |
| 2 | Funnel score 0 | 1,000 | BEL | SELL | 645 | 10:43:30 | 446.95 | 448.50 | 10:52 | 448.50 | STOP (initial) | -1.00 | -1,000 | 236 | **-1,236** |
| 3 | Funnel score 1 | 1,250 ½ | HDFCLIFE | BUY | 265 | 12:46:30 | 719.20 | 714.50 | 13:11 | 720.35 | PROFIT LOCK/TRAIL | +0.24 | +305 | 173 | **+132** |

## 2026-02-19 — 3 trades, before charges ₹+19,164, after charges ₹+17,777

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | Funnel score 2 | 4,500 | ONGC | BUY | 3846 | 11:11:30 | 269.66 | 268.49 | 14:50 | 275.15 | SQUARE_OFF 14:50 | +4.69 | +21,115 | 733 | **+20,382** |
| 2 | Funnel score 2 | 2,081 cap | GAIL | BUY | 4427 | 11:12:30 | 169.21 | 168.74 | 11:15 | 168.74 | STOP (initial) | -1.00 | -2,081 | 537 | **-2,618** |
| 3 | Funnel score 0 | 500 ½ | LODHA | SELL | 100 | 12:38:30 | 1,071.35 | 1,076.35 | 13:08 | 1,070.05 | PROFIT LOCK/TRAIL | +0.26 | +130 | 117 | **+13** |

## 2026-02-20 — 4 trades, before charges ₹+5,618, after charges ₹+4,219

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 14.7x) | 2,500 | ABB | BUY | 45 | 09:37:00 | 5,993.00 | 5,937.69 | 12:27 | 6,185.00 | DAY LOCK flatten | +3.47 | +8,640 | 227 | **+8,413** |
| 2 | Funnel score 0 | 1,000 | TMCV | BUY | 444 | 11:23:30 | 479.65 | 477.40 | 12:27 | 481.00 | DAY LOCK flatten | +0.60 | +599 | 186 | **+413** |
| 3 | Funnel score 0 | 1,000 | BAJFINANCE | BUY | 183 | 12:01:30 | 1,030.95 | 1,025.50 | 12:27 | 1,035.70 | DAY LOCK flatten | +0.87 | +869 | 171 | **+698** |
| 4 | Funnel score 2 | 4,500 | GRASIM | SELL | 412 | 12:08:30 | 2,846.10 | 2,857.00 | 12:27 | 2,857.00 | STOP (initial) | -1.00 | -4,491 | 815 | **-5,306** |

## 2026-02-23 — 3 trades, before charges ₹+3,281, after charges ₹+2,327

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 4.5x) | 2,500 | ABB | SELL | 38 | 09:20:00 | 5,955.50 | 6,019.88 | 12:17 | 5,860.00 | DAY LOCK flatten | +1.48 | +3,629 | 196 | **+3,433** |
| 2 | Funnel score 1 | 2,500 | BRITANNIA | SELL | 108 | 10:06:30 | 6,085.50 | 6,108.50 | 12:11 | 6,079.50 | PROFIT LOCK/TRAIL | +0.26 | +648 | 478 | **+170** |
| 3 | Funnel score 0 | 1,000 | TRENT | BUY | 131 | 11:05:30 | 2,718.10 | 2,710.50 | 11:08 | 2,710.50 | STOP (initial) | -1.00 | -996 | 281 | **-1,276** |

## 2026-02-24 — 6 trades, before charges ₹-3,293, after charges ₹-5,204

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.3x) | 2,500 | BAJAJ-AUTO | SELL | 40 | 09:22:00 | 9,685.00 | 9,747.26 | 09:29 | 9,747.26 | STOP (initial) | -1.00 | -2,490 | 302 | **-2,792** |
| 2 | Funnel score 1 | 1,250 ½ | SIEMENS | SELL | 96 | 09:57:30 | 3,315.90 | 3,328.80 | 11:14 | 3,307.70 | PROFIT LOCK/TRAIL | +0.64 | +787 | 256 | **+531** |
| 3 | Funnel score 1 | 1,250 ½ | TORNTPHARM | BUY | 105 | 10:02:30 | 4,357.95 | 4,346.05 | 10:07 | 4,346.05 | STOP (initial) | -1.00 | -1,250 | 347 | **-1,596** |
| 4 | Funnel score 1 | 1,250 ½ | CHOLAFIN | SELL | 204 | 10:09:30 | 1,674.00 | 1,680.10 | 10:22 | 1,672.40 | PROFIT LOCK/TRAIL | +0.26 | +326 | 270 | **+56** |
| 5 | Funnel score 1 | 1,250 ½ | ENRIN | BUY | 132 | 10:20:30 | 2,847.40 | 2,838.00 | 10:37 | 2,853.00 | PROFIT LOCK/TRAIL | +0.60 | +739 | 293 | **+446** |
| 6 | Funnel score 2 | 2,250 ½ | TVSMOTOR | BUY | 158 | 10:56:30 | 3,831.50 | 3,817.30 | 12:01 | 3,822.60 | HARD DAY STOP flatten | -0.63 | -1,406 | 443 | **-1,849** |

## 2026-02-25 — 5 trades, before charges ₹+3,137, after charges ₹+1,608

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 7.2x) | 2,500 | IRFC | SELL | 3380 | 09:20:00 | 105.15 | 105.89 | 13:30 | 104.80 | DAY LOCK flatten | +0.47 | +1,183 | 280 | **+903** |
| 2 | ORB (RVOL 4.7x) | 2,500 | CUMMINSIND | SELL | 48 | 09:22:00 | 4,911.60 | 4,963.08 | 13:30 | 4,897.50 | DAY LOCK flatten | +0.27 | +677 | 202 | **+475** |
| 3 | ORB (RVOL 4.2x) | 1,029 cap | LT | BUY | 38 | 09:25:00 | 4,293.40 | 4,266.53 | 09:53 | 4,300.12 | PROFIT LOCK/TRAIL | +0.25 | +255 | 154 | **+101** |
| 4 | Funnel score 1 | 1,029 cap | COALINDIA | BUY | 490 | 10:06:30 | 435.05 | 432.95 | 12:42 | 435.55 | PROFIT LOCK/TRAIL | +0.24 | +245 | 186 | **+59** |
| 5 | Funnel score 2 | 2,966 cap | ADANIPOWER | SELL | 7061 | 12:35:30 | 142.50 | 142.92 | 13:30 | 142.39 | PROFIT LOCK/TRAIL | +0.26 | +777 | 706 | **+70** |

## 2026-02-26 — 2 trades, before charges ₹-4,628, after charges ₹-5,076

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 5.9x) | 2,500 | ETERNAL | BUY | 724 | 09:20:00 | 252.80 | 249.35 | 09:34 | 249.35 | STOP (initial) | -1.00 | -2,499 | 166 | **-2,664** |
| 2 | ORB (RVOL 3.4x) | 2,500 | BANKBARODA | BUY | 1151 | 09:23:00 | 312.20 | 310.03 | 09:52 | 310.35 | HARD DAY STOP flatten | -0.85 | -2,129 | 282 | **-2,411** |

## 2026-02-27 — 2 trades, before charges ₹+3,059, after charges ₹+2,319

| # | Strategy | Risk ₹ | Symbol | Side | Qty | Entry time | Entry ₹ | Stop ₹ | Exit time | Exit ₹ | Exit reason | R | Before charges ₹ | Charges+slip ₹ | After charges ₹ |
|---|---|--:|---|---|--:|---|--:|--:|---|--:|---|--:|--:|--:|--:|
| 1 | ORB (RVOL 3.0x) | 2,500 | TATACONSUM | SELL | 338 | 09:20:00 | 1,148.80 | 1,156.18 | 14:09 | 1,146.96 | PROFIT LOCK/TRAIL | +0.25 | +623 | 302 | **+322** |
| 2 | Funnel score 1 | 2,500 | ENRIN | BUY | 203 | 11:42:30 | 2,936.50 | 2,924.20 | 14:10 | 2,948.50 | DAY LOCK flatten | +0.98 | +2,436 | 439 | **+1,997** |

**Month: 91 trades, before charges ₹+38,890, Kite charges ₹14,737, slippage ₹8,819, after charges ₹+15,334**