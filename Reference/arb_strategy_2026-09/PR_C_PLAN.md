# PR C: start switch, lock-rule trailing, desk and Performance tab

Branch `feat/arb-start-switch`, on top of `feat/arb-orb-detector` (PR B) at 336e621. Baseline before any change: 4 known failures, 1,565 passed.

## Goal

After this PR an ARB session can be started, **at Micro (5%) only and with real Kite orders only**. Legacy VWAP stays the default everywhere: a start that does not ask for ARB is exactly what it was.

- **Engine:** ARB positions trail by the backtest's lock rule instead of the 0.5R ladder.
- **Start:** the CLI, the launcher, the start API and the desk can start ARB. The 09:15 autostart launches whichever strategy was saved on the desk.
- **Desk:** an ARB risk-budget strip, and source, lock stage and "freed" on each position.
- **Performance tab:** the forward test against the backtest, with the promotion gates.
- **Post-market check:** `arb_daily_check` replays the day with the same rules and compares it with what the engine did.

## What PR C does not do
- It does not unlock any stage above Micro. That is one constant, changed in a later PR after the gates pass.
- It adds no timer or cron. The daily check is run from the Performance tab or by hand.
- It changes nothing in a legacy session. Every existing test passes unchanged.

## Measured before building

All three are reproducible from the research folder (`export_lock_golden.py`, and the fixture summary it writes).

**1. "One tick inside the bar close" almost never matters.**
The lock rule never puts a stop closer than one tick inside the last bar's close. A live engine cannot place a stop at or through the market, so the worry was that this needs a new market-exit path. Measured on all 1,183 candidates of 2026: 0 funnel trades and 2 ORB trades ever exit on such a stop. Dropping it entirely moves the result from ₹41,944 to ₹41,889 a month. So no new exit path: a stop that is at or through the market is refused and tried again on the next tick.

**2. A live stop must be on the tick grid, and that costs about ₹1,100 a month.**
The backtest left ORB stops off the grid (for example 377.0025) and rounded a short's lock stop toward the market. Kite only accepts stops on the grid, and this engine always rounds a stop away from the market. With every stop on the grid, away from the market, the backtest gives ₹40,803 a month instead of ₹41,944.

**3. Three trades were lucky floating-point accidents, worth about ₹1,300 a month.**
The backtest tested "peak ≥ +1R" in floating point. When the peak touched +1R exactly, rounding error sometimes made it "not yet", and three such trades then ran to +8.4R, +5.3R and +4.9R. The rule as written arms at exactly +1R. Compared exactly, the same backtest gives ₹39,532 a month.

So the backtest of the rule that is actually live:

| Jan–Sep 2026, after charges | Report | PR B (live ORB rule) | **PR C (live exits), 3 bps** | PR C, 5 bps |
|---|--:|--:|--:|--:|
| Average per month | ₹43,814 | ₹41,944 | **₹39,532** | ₹33,230 |
| Jun–Sep average per month | ₹42,746 | ₹49,489 | ₹47,974 | ₹39,427 |
| Profitable months | 9 / 9 | 9 / 9 | 9 / 9 | 9 / 9 |
| Weakest month | +₹7,780 | +₹8,063 | +₹7,825 | +₹4,150 |
| Green days | 62% | 60% | 59% | 59% |
| Worst day | −₹5,674 | −₹5,970 | −₹5,970 | −₹6,330 |
| Days worse than −₹6,000 | 0 | 0 | 0 | 1 |
| Max drawdown | −₹21,579 | −₹26,146 | −₹26,741 | −₹31,302 |
| Trades | 767 | 739 | 740 | 735 |

Two more measurements shaped the design:
- **"The entry bar counts only its close"** changes none of the 869 funnel exits (the backtest's funnel walk used a slightly different rule for that one bar), so one rule serves both strategies.
- **How often a stop is moved:** 659 of 1,183 trades get at least one lock move; the median is 1, the 95th percentile 14, the maximum 47. 29 trades (2.5%) need 20 or more, which is where the engine cancels and re-places the stop because of Kite's 25-modification limit. That path already exists and is unchanged.

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **The lock rule is judged once per completed 1-minute bar**, from `live_1m_candles`. Peak = each bar's high (long) or low (short) after the entry minute; the entry minute counts its close only. +1R → stop at entry +0.25R; +2R → stop keeps 25% of the peak gain; never closer than one tick inside that bar's close; only ever tightens. | This is the tested exit. A tick-level version is a different strategy. |
| D2 | **The rule is a fold with no memory.** Each tick it folds the bars since entry (only the new ones, from a cache). After a restart it is rebuilt from the entry minute and gives the same stop. | Nothing to persist, nothing to lose, and the post-market check can re-derive every move. |
| D3 | **R is taken exactly** (decimal), from the entry fill and the initial stop. Every target is on the tick grid, away from the market. | A float subtraction turns 1.6 into 1.5999…, which flips "≥ +1R" (finding 3; the first version of this PR had exactly that bug, caught by the fixture). |
| D4 | **Bars the candle builder marked partial are not used.** | A bar that may be missing prices must never move a stop. The peak can only be understated, so a lock can only be late. |
| D5 | **Kite's LTP is used for one thing: refusing a stop at or through the market.** No price this tick means no move. There is no new market-exit path. | Finding 1. |
| D6 | **The entry minute is when the engine applied the fill**, stamped on the position (`entry_filled_at`). | The bars after it are entirely after the trade. |
| D7 | **A failed bar read is counted and loud, and never pauses entries.** The stop stays where it was; other positions are still tried. | Same policy as the existing trail step. |
| D8 | **An ARB session trails every position by the lock rule; a legacy session keeps the 0.5R ladder.** Manual nudges and the auto-trail switch work as before in both. | One rule per session. |
| D9 | **ARB starts only with real orders.** The launcher refuses ARB without them. | No paper phase (NJ). |
| D10 | **ARB starts only at the unlocked stage** (`engine_config.ARB_UNLOCKED_SCALE = 0.05`). The CLI, the launcher, the API and the saved autostart choice all go through `validate_start`. | A hand-launched process must not be able to trade bigger. Raising the stage is a reviewed code change. |
| D11 | **One strategy per day.** A start is refused if the other strategy already held a position today, and a legacy start is refused on a day ARB has stopped. An ARB restart on a stopped day is allowed: it restores the halt and only flattens. | ARB's budget, loser count and day stop read every trade of the day; a legacy session must never trade on a halted day. |
| D12 | **Autostart launches what was saved on the desk** (a small JSON file next to the engine DB). Default, and what a missing or unreadable file means: legacy. | ORB breakouts start at 09:20, so an ARB day only works if the 09:15 autostart itself launches ARB. |
| D13 | **Slippage is measured against the backtest's assumption:** the trigger price for a funnel trade, the opening range level for ORB. Positive is worse. | The whole edge depends on slippage (3 bps → ₹39.5k, 5 bps → ₹33.2k). |
| D14 | **The daily check replays the day with the live rules and ideal fills**, on the day's own live candles, and compares decisions: entered or skipped, and the rupee risk within one share's worth. A signal the engine never saw counts as not entered. | Finds a port bug or a real live-versus-backtest difference in days, at micro money. |
| D15 | **Promotion gates are shown, never acted on.** | The size is never raised automatically. |

## Files

**New**
1. `engine_lock.py` (pure): the lock rule as `advance` / `fold`, the bar key, and `stage_of_stop` for the desk.
2. `arb_replay.py` (pure): `simulate_exit` (the backtest's bar walk on the live rule) and `replay_day` (budget, exits, day controls).
3. `arb_daily_check.py` and `scripts/arb_daily_check.py`: the post-market comparison; stores its result in `arb_daily_checks` in the engine DB.
4. `arb_metrics.py` (pure): source label, slippage, R.
5. `api/services/execution_settings.py`: what autostart launches.
6. `api/queries/performance.py`, `api/routers/performance.py`: `GET /performance/summary`, `POST /performance/check`.
7. Frontend: `ArbBudgetStrip.tsx`, `performance/PerformancePage.tsx`, `usePerformance.ts`.

**Changed**
8. `engine_core.py`: lock-rule trailing in ARB mode, the fill-time stamp, `arb_snapshot()` for the desk, the session capital on each ARB trade.
9. `engine_arb_context.py`: `lock_bars` (read-only, by the candle table's primary key).
10. `engine_config.py`: the stages, `ARB_UNLOCKED_SCALE`, `validate_start`, `arb_session_config`.
11. `run_execution_engine.py`: `--strategy-mode`, `--arb-risk-scale`; an ARB engine gets both signal sources and the market context; the heartbeat carries the strategy, the capital and ARB's day state.
12. `engine_status.py`: the new heartbeat fields.
13. `api/services/execution_engine_runner.py`: the command's strategy arguments, D9, D11.
14. `api/services/execution_autostart.py`: D12.
15. `api/routers/execution.py`, `api/schemas/execution.py`: ARB start, `GET`/`POST /execution/settings`, ARB fields on status and positions.
16. `api/queries/strategy.py`, `api/routers/strategy.py`: the engine's decision on each signal, and `GET /strategy/breadth`.
17. Charges tab: source and close reason.
18. Frontend: start panel (strategy, stage, autostart), positions table badges, Strategy tab (Engine column, market breadth), the Performance tab in the shell.

## Tests (new: 104)
- `test_arb_replay_golden.py`: parity with the backtest (below).
- `test_engine_arb_lock.py`: the rule's edges, the bar reader, the engine (when it moves a stop, when it refuses, restarts, failures, shorts), and the desk snapshot.
- `test_arb_start_switch.py`: config, CLI, launcher guards, settings, autostart, the start and settings API, ARB columns on positions.
- `test_arb_daily_check.py`: one end-to-end day (the real detector, handoff, engine and store, then the check), the comparison rules, storage, the script, the API.
- `test_performance_api.py`: the summary, the gates, slippage, engine decisions, market breadth.
- Two more in `test_frontend_route_contracts.py`: the client's new paths exist, and the new routes need a website session.

## Result (13 Oct 2026)

- **Full suite: 1,669 passed.** The only failures are the same 4 known ones as the baseline.
- **Golden parity is exact** (`tests/fixtures/lock_golden`, written by `export_lock_golden.py`, which imports nothing from the repo's lock rule and checks its own walks against the backtest's stored exits first):
  - 1,183 / 1,183 candidates exit at the same price in the same minute.
  - 817 of them are untouched by the grid and the exact thresholds; for those the live rule gives the backtest's own stored exit.
  - 182 / 182 days take the same 740 trades at the same size and halt in the same minute for the same reason (109 halts).
- **The tests are sensitive.** Changing any of the four rule constants, or the entry-bar rule, breaks between 11 and 324 of the 1,183 exits.
- **Frontend:** `tsc` and the production build are clean. The desk, the start panel, the Strategy tab and the Performance tab were checked in a browser against a local API with a throwaway database.

## Not yet verified (only live trading can)

1. **Live candles against Kite's.** The lock rule reads the system's own 1-minute candles. PR B measured that they miss the true high and low by a few basis points, so a lock can arm a bar later than in the backtest. The daily check and the R-versus-replay gate measure the effect.
2. **Fill slippage**, the largest unknown: 3 bps → ₹39.5k a month, 5 bps → ₹33.2k.
3. **How quickly a lock stop reaches Kite** after the bar closes (the candle is written about a second into the next minute, then one modification).
4. Everything PR B listed about ORB ticks.

## Running it

- **Start ARB by hand:** Execution Desk → Start a session → ARB → Micro 5% → Start ARB. Real orders; the dialog lists the numbers.
- **Start ARB every morning:** on the same panel, "Autostart ARB at Micro 5% instead". The engine must be up before 09:20 for ORB, and observation before 09:15 (PR B).
- **After the close:** Performance → Check a day → Run check. Or:

```
python scripts/arb_daily_check.py --session-date YYYY-MM-DD
```

- **Raising the stage later:** when every gate on the Performance tab is met, change `ARB_UNLOCKED_SCALE` in `engine_config.py` in its own PR.

## Things to know
- **At Micro, charges are about 0.2R a trade**, so the day P&L after charges runs lower than the gross figure next to it, and stages are judged in R before charges.
- **A day ARB stopped stays stopped.** Neither strategy can be started on it, except an ARB restart that only finishes the flatten.
- **`Reference/arb_strategy_2026-09/data/` is still not in git** (gitignored, 7 files).
