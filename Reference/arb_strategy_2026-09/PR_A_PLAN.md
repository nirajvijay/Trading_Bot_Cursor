# PR A: ARB core (not yet startable)

Branch `feat/arb-core`, off `production` at 71efa9a. Baseline before any change: 4 known failures, 1,315 passed.

## Goal

Build the ARB decision logic and connect it to the engine behind `strategy_mode="arb"`. **Nothing can start ARB yet:**
- The CLI, the start API and the desk still build `strategy_mode="legacy_vwap"`, so the production engine behaves exactly as today.
- PR C adds lock-rule trailing and the start switch. Without that trailing, ARB's "freed risk" (stop in profit at +1R) would never happen, so ARB must not be startable before PR C.

## What PR A does not do
- No ORB (PR B).
- No lock-rule trailing (PR C).
- No desk UI (PR C). The only frontend change is two close-reason labels.
- No change to legacy behaviour. Every existing test must pass unchanged.

## Decisions (fixed for this PR)

| # | Decision | Why |
|---|---|---|
| D1 | **Hard day stop is checked every tick**, on live MTM. | It protects money the moment the line is crossed, rather than waiting up to a minute. |
| D2 | **Day lock (peak and 40% line) uses one MTM sample per minute**, taken at the first tick of each new minute, which is about the previous 1-minute close. | This is what the backtest did (1-minute closes). A tick-level peak would arm the lock earlier and flatten on noise; the report shows keep ≥ 0.45 is already a cliff. |
| D3 | **MTM is net of estimated charges** (Zerodha intraday formula, `engine_charges.py`, a port of the research `charges.py` that was checked against a Kite contract note). Open positions are priced from Kite LTP (REST, the existing one-read-per-tick `_fresh_ltp`), never from the websocket. | Recommended in the plan; matches the backtest's `net`. |
| D4 | **Stale prices.** An open position with no LTP newer than 3 s counts at its stop (worst case) for the hard stop. The day lock is not evaluated in a minute where any open position is stale. | Never skip the hard stop for lack of a price; never flatten a winning day on a missing price. |
| D5 | **Loser** = a closed trade today with `realised_pnl − estimated charges < 0`, recomputed from the store every tick. | Same as the backtest (`net < 0`). Restart-safe. |
| D6 | **Live risk** of a position: pending or submitted entry → its `risk_cap_rupees`; holding → `max(0, qty × distance from entry to the current stop)`. No stop known → `risk_taken_rupees`, else the cap. | The backtest freed a trade after a 1-minute close ≥ +1R, which is exactly when the lock rule moves the stop to +0.25R. Using the stop Kite holds is broker truth. |
| D7 | **First trigger per stock per day** is read from the live DB (all TRIGGERED rows for that stock today), not from the engine's start floor. | A mid-day restart must not forget earlier triggers. |
| D8 | **Market and sector alignment** come from `live_1m_candles`, using only bars whose minute started before the trigger's minute. Each stock uses its first bar's open of the day and its latest close up to then. The mean is over every stock that has a bar. Sector peers come from `SECTOR_MAP_V1`, excluding the stock itself. No data → the feature counts as not met. | Identical to research `features.py` / `features2.py`. |
| D9 | **Funnel filters:** volume ratio ≥ 2, trigger time (IST, from `trigger_exchange_ts`) < 13:00, stop distance ≥ 0.25% of the trigger price, first trigger of the stock today. Then score (0–3, capped), and at most 2 score-0 trades a day (counting trades actually sent). VWAP is not consulted and not waited for. | `port3.base`, `port4.score`, `combo2.fun_cands`. |
| D10 | **Allowed risk** = min(base × (0.5 if losers ≥ 1), open cap + max(0, MTM) × 1.0 − live risk). Skip if < min risk. | `hybrid_v2.run` with `H=1e9, house=1, open_cap=6000, cut_after=1`. |
| D11 | **risk_scale ∈ {0.05, 0.25, 0.5, 1.0}** multiplies every rupee number: base risks, open cap, min risk, hard stop, lock arm and the closed-loss backstop. The keep fraction, R rules and times never scale. | Rollout stages from IMPLEMENTATION_IDEA §6. |
| D12 | **One-share floor, only when risk_scale < 0.25:** if the size comes out at 0 and one share risks ≤ 1.25 × the allowed risk (and capital allows), buy 1 share. | IMPLEMENTATION_IDEA §6.3. |
| D13 | **Order of candidates** in ARB mode is FIFO by trigger time, not by score. | The backtest processed candidates in time order. Same-second collisions are rare; parity comes first. (This supersedes "rank by score" in the idea plan.) |
| D14 | **Halts are session-permanent and persisted.** New table `arb_day_state` (per session_date): peak, halt reason, time and MTM. On restart, a recorded halt is re-applied at the first tick (square off anything open, no entries). | A restart must not re-open a stopped day or forget the peak. |
| D15 | **Closed-loss backstop:** in ARB mode the existing daily-loss cap equals the scaled ₹6,000 and keeps working exactly as now. | A second, independent guard. |
| D16 | A **stock already open blocks a new entry** in it (the existing `symbol_already_open` gate). | NJ decision 1, recommended option. |
| D17 | **A trigger older than 15 s is skipped** (`arb_trigger_stale`), never entered late. | The backtest entered at the trigger price; a late market order is a different trade. |
| D18 | **A halt starts the flatten before writing anything.** `shutdown_reason` is set first; the halt is persisted after that, even if the event write fails. | A full disk or locked DB must never stop a hard-stop flatten. |
| D19 | **If the day check fails in a tick, ARB takes no entry in that tick.** The trigger is not consumed; it is retried and goes stale after 15 s. | Fail closed: no entry without a working hard stop. |

## Files

**New**
1. `engine_charges.py`: `estimate_intraday_charges(buy_value, sell_value)`, plus the helper `round_trip_charges(direction, qty, entry, exit)`.
2. `engine_arb.py` (pure, no I/O):
   - `ArbSettings`, with `scaled()` and `validate()`.
   - `FunnelFeatures` → `classify_funnel()`, returning a verdict with the reason, score and base risk.
   - `position_live_risk()`, `position_net_pnl()` and `is_loser()`.
   - `allowed_risk()`.
   - `DayMarks` / `day_mtm()`.
   - `DayLockTracker`: minute sampling, peak and lock check.
   - `hard_stop_hit()`.
   - `apply_one_share_floor()`. It raises the risk cap to exactly one share's risk, so the normal sizing gives 1 share and the abnormal-slippage line is judged against that share. No separate sizing class is needed.
3. `engine_arb_context.py`: read-only access to the live DB.
   - `alignment()`: a pure function over bars.
   - `LiveArbContext.features_for(candidate)`, which builds `FunnelFeatures`, including the first-trigger check.

**Changed**
4. `engine_config.py`: `strategy_mode` and `arb: ArbSettings` on `SessionRiskConfig`. The default is `"legacy_vwap"`, and `validate()` checks the ARB settings. `to_risk_limits()` uses the ARB backstop in ARB mode.
5. `engine_exit.py`: `CloseReason.HARD_DAY_STOP` and `CloseReason.DAY_LOCK`, both added to `ACTIVE_EXIT_REASONS`.
6. `engine_entry.submit_entry`: an optional `extra` dict, merged into the position's extra and the `entry_intent` event. Default None, so there is no change for existing callers.
7. `engine_store.py`: the `arb_day_state` table, `load_arb_day()` and `save_arb_day()`.
8. `engine_core.py`, active only in ARB mode:
   - `ingest_triggers` orders candidates FIFO.
   - `handle_trigger` → `_handle_arb_trigger` (no VWAP wait).
   - `_entry_gate` enforces the 13:00 funnel cutoff.
   - `tick` calls `_check_arb_day()` right after `_check_daily_loss()`.
   - `_fresh_ltp` also prices every holding position.
   - It takes an optional `arb_context` constructor argument.
9. `frontend/src/components/execution/format.ts`: labels for the two new close reasons.
10. `engine_priority.py`: `rank_fifo`. `engine_runloop.py`: the step `arb_day_controls` (5-failure escalation).

## Tests (new)
- `tests/test_engine_charges.py`: figures from the research formula, plus a real contract-note example.
- `tests/test_engine_arb.py`: filters, score, allowed risk, cut after loser, house money, freed risk, min risk, scaling, validation, and the day-lock and hard-stop edge cases.
- `tests/test_engine_arb_golden.py`: parity with the backtest, using fixtures exported from the research folder.
  - (a) Every 2026 funnel trigger's filter pass and score.
  - (b) Every one of the 767 ARB trades' `risk_used`, from its recorded day state.
  - (c) Every candidate the backtest skipped for the budget, which must also be skipped.
  - (d) Each day's minute P&L path → the same lock or hard-stop minute and reason.
- `tests/test_engine_arb_context.py`: alignment against research `features.json` values on real candles for sample triggers.
- `tests/test_engine_arb_core.py`: engine integration with FakeBroker and the real store:
  - ARB skip reasons.
  - Sizing from the budget.
  - Half size after a loser.
  - Freed risk lets a new trade in.
  - Hard stop flattens everything.
  - Day lock arms, then flattens.
  - Stale LTP falls back to worst case.
  - A restart restores the peak and the halt.
  - The 13:00 cutoff.
  - Legacy mode is untouched.
- The full suite must match the baseline (the same 4 known failures only).

## Fixture source
`scripts` inside the research folder (`/Users/nj/nifty-radar-research/arb_backtest_2026-09/export_golden.py`) writes `tests/fixtures/arb_golden/*.json`. The generator is copied into `Reference/arb_strategy_2026-09/code/` for traceability.

## Result (26 Sep 2026)

- **New tests: 87, all passing.** Full suite: 1,402 passed; the only failures are the same 4 known ones as the baseline.
- **Golden parity is exact:**
  - 4,217 / 4,217 funnel triggers (869 pass the filters) have the same filter result and score.
  - 1,154 / 1,154 sizing decisions (including 56 budget skips) match.
  - 182 / 182 days have the same halt minute and reason (117 halts: 78 day lock, 39 hard stop).
  - All 8 context samples reproduce the research alignment to 1e-9.
- **The export checks itself.** `export_golden.py` asserts that its replay reproduces `hybrid_v2.run`'s 1,084 trades before it writes anything.
- **Frontend:** `tsc` is clean.
