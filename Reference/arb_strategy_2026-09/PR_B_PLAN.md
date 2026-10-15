# PR B: ORB detector, handoff and Strategy tab (signals only)

Branch `feat/arb-orb-detector`, on top of `feat/arb-core` (PR A) at c4d8500. Baseline before any change: 4 known failures, 1,402 passed.

## Goal

- **Observation side:** detect ORB signals live, record them, and show them on a new Strategy tab.
- **Engine side:** an engine in ARB mode can take an ORB trigger (routing, stop, cutoff, budget).
- **Still not startable.** Nothing can start ARB yet; PR C adds the switch. In production the only new behaviour is that the observation runner also writes the ORB tables.

## What PR B does not do
- No ARB start switch, no lock-rule trailing, no desk changes, no Performance tab (PR C).
- No change to the morning checklist workflow.
- No Kite calls and no orders from the detector. It only reads ticks the runner already receives.
- No change to legacy trading. Every existing test passes unchanged.

## Measured before building

Three checks on the research data decided the design. Each is reproducible from the research folder.

**1. The backtest's "top 3" used the future.**
- `combo2.orb_cands` ranked by RVOL among the stocks in `orb.json`, and `orb.json` only holds stock-days that broke out by 11:00. At 09:20 nobody knows which stocks will break out.
- Of 895 stock-days with RVOL ≥ 3 at 09:20, 643 broke out (72%).
- The rule that can be followed live (rank at 09:20, arm the top 3) gives the result below. Run `orb_selection_check.py` to reproduce it.

| Jan–Sep 2026, after charges | As reported | Live rule, 3 bps | Live rule, 5 bps |
|---|--:|--:|--:|
| Average per month | ₹43,814 | **₹41,944** | ₹35,075 |
| Jun–Sep average per month | ₹42,746 | ₹49,489 | ₹39,816 |
| Profitable months | 9 / 9 | 9 / 9 | 9 / 9 |
| Weakest month | +₹7,780 | +₹8,063 | +₹4,317 |
| Green days | 62% | 60% | 59% |
| Worst day | −₹5,674 | −₹5,970 | −₹6,331 |
| Max drawdown | −₹21,579 | −₹26,146 | −₹31,010 |
| ORB trades | 374 | 307 | 309 |

- The average barely moves, but single months move a lot (January ₹126k → ₹78k, September ₹51k → ₹90k). Which trades are taken changes "half size after a loser" for the rest of that day, so the result is path-dependent.
- Five other causal rules (arm more stocks, take the first 3 or 4 to break out) gave ₹34k–₹42k a month. None beat the simple one.

**2. The system's live candles are not Kite's candles in the first five minutes.** Compared on 23–25 Sep 2026, 100 stocks:

| Opening range, live candles vs Kite candles | Result |
|---|---|
| Volume of the 09:16–09:19 bars | identical (ratio 1.000) |
| Volume of the 09:15 bar | live is 1.22× Kite at the median, up to 2.75× |
| Range high or low | equal for only 11–33% of stocks; Kite's is wider by 2–6 bps at the median, up to 142 bps |
| Direction (close vs open) | differs for 12–23% of stocks |

- **Why the volume differs:** the live 09:15 candle counts the pre-open auction; Kite's does not.
- **Why the range differs:** live candles are built from last-traded prices on ticks that arrive about once a second, so they miss the true high and low.
- **Effect on those three days:** a detector on live candles would have armed BAJFINANCE in the wrong direction on the 23rd, and two stocks on the 25th where the backtest arms none.

**3. 39% of ORB breakouts fire inside the 09:20 minute,** and 66% by 09:25. Waiting for every stock's 09:19 candle to close takes 4–7 seconds. Reading ticks takes about one.

A side finding: since 3 Aug 2026 the candles of 97 of the 100 stocks end at 15:14 (the closing auction). That is a complete day, so the history checks must not demand 15:29.

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **Selection is causal.** At 09:20, rank the valid opening ranges with RVOL ≥ 3 and arm the top 3. A stock that never breaks out still uses its slot. | A live system cannot rank on what happens later. Tested at ₹41,944 a month (table above). |
| D2 | **The opening range comes from the exchange's own numbers on ticks:** the day open, high, low and cumulative volume on the stock's last tick stamped before 09:20:00. The close is that tick's last price. | At 09:19:59 the day's OHLC is the opening range, exactly. Live candles are not (finding 2). It also survives a feed gap inside the range. |
| D3 | **Opening volume leaves out the pre-open auction:** cumulative volume before 09:20 minus the cumulative volume on the last tick stamped 09:00–09:15. A stock with no such tick is left out. | This is how Kite's 09:15 candle counts volume (finding 2), and the baseline is built from Kite candles. |
| D4 | **Direction** is the sign of (last price before 09:20 − day open). **Level** is the day high for a long, the day low for a short. No direction means no arm. | Research `orb.py`. |
| D5 | **Ranking moment:** when every stock has ticked at or after 09:20:00, or the first tick stamped 09:20:01, whichever comes first. A stock whose last range tick is over 20 s old is left out. A ranking that can only run after 09:20:30 arms nothing. | Arms within about a second (finding 3) without ranking on a half-seen range. |
| D6 | **Trigger:** the first tick at or beyond the level, counted in whole ticks, from 09:20:00 until 11:01:00. Arms still waiting then are written off as EXPIRED. | The backtest used `>=` and took breakouts in bars labelled up to 11:00. |
| D7 | **A first tick more than 0.10 × ATR beyond the level is MISSED,** and that arm is finished. | After a restart or a feed gap the breakout may be long gone; chasing it is not the tested trade. The backtest's largest gap entry was 0.04 ATR. |
| D7a | **Only a fresh tick can trigger.** A tick that reaches the detector more than 10 s after its exchange time (a backlog, or an old snapshot replayed on reconnect) is not judged; the next fresh tick decides, under D7. A tick stamped more than 30 s ahead of the clock is ignored, so it can neither rank early nor expire the arms. | The engine treats a trigger as new for 15 s from when it was written, so the detector must not write one from an old price. |
| D8 | **Stop** = trigger price − 0.35 × ATR14, moved onto the tick grid away from the trade. The detector fixes it when the breakout fires and the engine uses it as given. Sizing uses the trigger price. | The backtest measured both from the entry. An ORB stop is never structural. |
| D9 | **Baselines are computed when observation starts,** read-only from the historical candle DB: ATR14 is the mean of 14 true ranges (15 sessions), and the opening volume is averaged over 14 sessions. A baseline is ready only if there are 15 sessions, the latest is the calendar's previous trading day or newer, no session's candles stop before 15:00, and every volume day has opening candles. Calendar special sessions are skipped. | Same arithmetic as the backtest. No checklist stage is needed (this replaces the "pre-market job" in the idea plan), and stale or cut-short history leaves a stock out instead of ranking it on a guess. |
| D10 | **Written before acted on.** The day's ranking (every stock) and its arms go in one transaction, once a day, before anything is armed in memory. Each arm gets one outcome row, written before it is announced. A restart resumes from the database and never ranks again. | A crash can never produce a second ranking or a second trigger. |
| D11 | **ORB can never stop observation.** Every entry point swallows its own errors. A failed write is tried once, never per tick. After 25 failures the detector switches itself off for the day. | The same runner feeds live trading. |
| D12 | **Engine:** an ORB trigger takes a flat ₹2,500 × stage as its base risk, then the same ARB budget as the funnel (half size after a loser, open-risk cap, minimum risk, one-share floor). No filters, no score, and it does not use the two-a-day score-0 allowance. Entry is refused at or after 11:01, and a trigger over 15 s old is skipped. | `hybrid_v2.run`: `base = orb_risk`. |
| D13 | **A legacy session never trades ORB.** Its candidate source returns continuation rows only, and the engine skips any other strategy as `strategy_not_enabled`. | Two independent guards. |
| D14 | **In ARB, a trigger whose timestamp cannot be read is stale.** | Tightens PR A, where such a trigger was given a default age. |
| D15 | **Strategy tab:** the ORB board and the funnel feed, display only. The funnel feed runs each continuation trigger through PR A's filters and score, before the budget and the score-0 limit. The market breadth gauge waits for PR C. | Lets both signal sources be watched before ARB trades. |
| D16 | **Post-market parity check** (`scripts/orb_parity_check.py`): rebuild the morning from Kite candles as the backtest does and compare it with what the detector recorded. Read-only. | Turns the three assumptions below into measurements. |

## Files

**New**
1. `orb_config.py`: the rule numbers and the live-only guards.
2. `orb_types.py`: shared value types and reason codes.
3. `orb_baselines.py`: ATR14 and average opening volume, and the readiness checks.
4. `orb_rules.py` (pure): the opening range from a tick snapshot or from candles, the ranking, the stop, the breakout test.
5. `orb_writer.py`: five tables in the observation DB: `live_orb_baselines`, `live_orb_ranges`, `live_orb_arms`, `live_orb_decisions`, `live_orb_status`.
6. `orb_detector.py`: the tick-driven state machine.
7. `orb_parity.py` and `scripts/orb_parity_check.py`: the post-market comparison.
8. `api/queries/strategy.py`, `api/schemas/strategy.py`, `api/routers/strategy.py`: `GET /api/v1/strategy/orb` and `/strategy/funnel`.
9. `frontend/src/components/strategy/StrategyPage.tsx`, `frontend/src/hooks/useStrategy.ts`.

**Changed**
10. `live_observation_runner.py`: builds the detector (`start_orb_detector`) and adds it as a tick consumer after continuation.
11. `trading_engine_types.py`: `TriggerCandidate` gains `strategy`, `planned_stop`, `orb_level`, `orb_rvol`, `orb_atr14`, `orb_rank`, all with defaults.
12. `trading_engine_handoff.py`: `fetch_orb_triggered_since` and `fetch_arb_candidates_since`.
13. `engine_entry.py`: `compute_stop_price` uses the planned stop for ORB (`planned_stop_price`).
14. `engine_arb.py`: `orb_cutoff` and three skip reasons.
15. `engine_core.py`: the ORB branch of the ARB trigger handler, the cutoff by strategy, the legacy guard, D14.
16. Frontend shell: the Strategy tab, its footer, and five skip-reason labels.

## Tests (new: 163)
- `test_orb_golden.py`: parity with the backtest (below).
- `test_orb_baselines.py`, `test_orb_rules.py`, `test_orb_writer.py`, `test_orb_detector.py`: unit tests, including restarts, write failures, a late start, a silent stock, auction volume, a tick backlog and future-stamped ticks.
- `test_engine_arb_orb.py`: ORB through the engine (sizing, half size, open-risk cap, cutoffs, refusals, micro stage, legacy guard), the handoff, and one end-to-end run from ticks to a protected position.
- `test_strategy_api.py`, `test_orb_parity.py`, `test_orb_runner_wiring.py`.

## Result (12 Oct 2026)

- **Full suite: 1,565 passed.** The only failures are the same 4 known ones as the baseline.
- **Golden parity is exact** (fixtures exported by `export_orb_golden.py`, which checks itself against `orb.json` first):
  - 431 / 431 armed stock-days have the same ATR14 and average opening volume, to the last bit.
  - 182 / 182 days arm the same stocks, in the same order, at the same levels and RVOL.
  - 431 / 431 armed stock-days have the same breakout minute, entry and stop (314 break out).
- **The detector itself, on real days.** Ticks were synthesised from the 12-month candles (day OHLC, cumulative volume, a made-up auction volume on top) and fed to `OrbDetector` for all 182 days: it armed the same stocks at the same levels on every day and triggered in the same minute for all 314 breakouts.
- **The baseline loader on a copy of the production history DB:** 100 of 100 stocks ready in 1.4 s, values identical to the research ones.
- **Frontend:** `tsc` and the production build are clean. The tab was checked in a browser against a local API with a throwaway database.

## Not yet verified (only a live morning can)

Each of these is a reason the detector records every stock's range and the parity check exists.

1. **Kite sends a tick carrying the auction volume before 09:15 for every stock.** If one is missing, that stock is left out for the day (safe, but a missed signal).
2. **The day open on ticks equals the open of Kite's 09:15 candle.** If not, direction can differ when the range closes within a tick or two of its open (4% of armed stocks closed within 10 bps of it).
3. **One second of grace is enough** for every stock's last pre-09:20 tick to arrive.
4. **Kite's tick timestamps stay within 10 s of the server clock** for a stock in play. If they lag more, its ticks are counted as stale and it never triggers (visible as `stale_ticks` in the runner's end-of-day ORB line, and in the parity check as "live ARMED, replay broke out").
5. **How far past the level the first tick is.** The backtest assumed a fill at the level. The arm row holds the level and the outcome row the trigger price, so PR C's slippage figure for ORB must run from the level to the fill.

**How to check after B is deployed:** each evening, once that day's candles are collected, run

```
python scripts/orb_parity_check.py --session-date YYYY-MM-DD
```

It prints, for every stock, how the live range compares with Kite's candles, whether the same stocks were armed, and whether each breakout matched.

## One thing to know when running it
**Observation must be connected before 09:15.** The auction volume comes from pre-open ticks, so a runner that connects after the open has nothing to subtract and arms nothing that day. The Strategy tab then shows "No usable opening ranges".

## Left for PR C
- Start an engine in ARB mode with `fetch_arb_candidates_since` as its candidate source.
- Lock-rule trailing, the desk's ARB strip and columns, the stage selector, the Performance tab, `arb_daily_check`.
- The market breadth gauge on the Strategy tab, and the engine's decision next to each signal.
