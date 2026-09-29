# Research code for the ARB report

These are copies of the backtest scripts behind `../REPORT.md`. They expect to run from the research folder that holds the data files:

| Needed file | What it is | Size |
|---|---|---|
| `data/kite_12m.db` | Kite 1-minute candles, Oct 2025 – Sep 2026 | 1.4 GB |
| `data/nifty50_instruments.db` | symbol ↔ instrument token | small |
| `t12.json`, `r12.db` | funnel triggers and pullback arms from the engine replay | 2 MB / 256 MB |
| `trail_research.json` | every 2026 funnel trigger × ~227 exit rules | 53 MB |
| `orb.json` | ORB candidates with exits | 19 MB |
| `features.json`, `features2.json` | market / sector alignment per trigger | 2 MB |

**Permanent copy (all data + scripts, 4.9 GB, outside git):**
`/Users/nj/nifty-radar-research/arb_backtest_2026-09/`
Run the scripts from inside that folder. For example, `python3 arb_book.py 2026-09` rebuilds the September order book. Verified on 26 Sep 2026: a rerun there gives the report's numbers exactly (₹43,814/month, worst day −₹5,674).

The scripts also import `engine_sizing`, `engine_stop` and `engine_trailing` from the repo root.

## Pipeline

1. `trail_research.py` → `trail_research.json`: funnel triggers × exit rules (1-minute tick path O → near → far → C).
2. `features.py`, `features2.py` → market breadth and sector alignment per trigger.
3. `orb.py` → `orb.json`: ORB candidates (5m range, RVOL, ATR stops, lock exit).
4. `port3.py`, `port4.py`: funnel filters (`base`) and quality `score`.
5. `combo2.py`: `orb_cands()` + `fun_cands()`, the two candidate streams.
6. `daylock2.py`: per-minute P&L paths, `net_at()`.
7. `redctl.py`: earlier loss controls (full loss control, hard stop only) and `day_manage()` (hard day stop + day lock).
8. **`hybrid_v2.py`: the ARB layer.** Final setting:
   `day_manage(run(H=10**9, house=1.0, open_cap=6000, cut_after=1), arm=3000, keep=0.4, hard=5000, slip=3e-4)`
9. `arb_book.py 2026-MM` → monthly order book. `export_arb.py <out>` and `export_rob.py <out>` → the CSVs in `../data/`.
10. `hy_lab2.py` → the in-day context table (`../data/context_lab_R_by_half.txt`).
11. `charges.py`: Zerodha intraday charges (checked against a Kite contract note).
12. `export_golden.py <repo>/tests/fixtures/arb_golden`: writes the golden fixtures that the live port's parity tests use (`tests/test_engine_arb_golden.py`). It checks its own replay against `hybrid_v2.run` before writing.
