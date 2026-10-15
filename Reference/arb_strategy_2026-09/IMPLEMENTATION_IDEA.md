# Bringing ARB into the production engine: detailed idea plan

Written 26 Sep 2026, against `production` at 71efa9a (#51). It builds on `REPORT.md` in this folder.

## 0. The gap in one table

| Area | Production today | ARB needs |
|---|---|---|
| Signals | Only continuation (pullback-breakout) triggers | The same triggers (called "funnel" in the report) **plus a new ORB signal** |
| Entry gate | VWAP must be ACCEPT or LIMITED | VWAP **off**. Funnel filters instead: volume ≥ 2×, before 13:00, stop ≥ 0.25%, first trigger per stock |
| Risk per trade | Fixed: ₹900 ACCEPT / ₹450 LIMITED | By strategy and score: ORB ₹2,500; funnel ₹1,000 / 2,500 / 4,500 / 6,000 by score, then adjusted by the ARB budget |
| Stop | Structural (pullback swing) | Funnel: structural (unchanged). ORB: 0.35 × ATR14 (daily) |
| Trailing | 0.5R ladder on live LTP every tick | Lock rule: at +1R lock +0.25R; after +2R keep 25% of the peak gain. Judged on 1-minute bar closes |
| Day risk | Closed-loss cap (Kite realised), then pause and square off | Hard day stop −₹5,000 on **MTM** (open + closed), a day lock (3,000 / 40%), and the closed-loss cap kept at ₹6,000 as a backstop |
| Portfolio risk | None beyond capital | Open-risk cap ₹6,000 + house money; a trade is "freed" once its stop is in profit; ½ size after the first loser; skip below ₹500 |
| Entry window | Until 14:00 | ORB until 11:00, funnel until 13:00 (EOD square-off stays 14:50) |
| Capital | ₹3L default | ₹5L at 5× |

**One principle changes.** `engine_core` says live P&L is "display only… never read by stops, reconciliation, the risk cap". ARB's hard stop and day lock are *decisions made on live MTM*, so MTM must become a decision input, with the same care the stop logic gets (one Kite read per tick, no websocket prices).

---

## 1. What we change (existing files, same role)

### 1.1 `engine_config.py`: `SessionRiskConfig`
Add an `ArbConfig` (frozen dataclass), fixed at start like everything else:

```
strategy_mode        "legacy_vwap" | "arb"      (choose at start; legacy stays as a fallback)
orb_risk             2500
funnel_risks         (1000, 2500, 4500, 6000)   by score 0 / 1 / 2 / 3+
max_score0_per_day   2
open_risk_cap        6000
house_money_mult     1.0
free_at_r            1.0        (proxy: stop is at or beyond entry + 0.25R, see 2.3)
cut_after_losers     1   cut_mult 0.5
min_risk             500
hard_day_stop        5000       (MTM)
day_lock_arm         3000   day_lock_keep 0.40
closed_loss_backstop 6000       (= the existing daily_loss_cap)
orb_cutoff 11:00   funnel_cutoff 13:00
risk_scale           1.0        (rollout stage: 0.05 micro / 0.25 / 0.5 / 1.0; scales every rupee figure above, see 6)
one_share_floor      1.25       (micro only: trade 1 share if its risk ≤ 1.25 × allowed risk, see 6.3)
```
`validate()` gains: keep 0.35–0.40 (the report shows a cliff at ≥ 0.45 with 5 bps), cap ≥ max funnel risk, hard stop < backstop.

### 1.2 `engine_core.handle_trigger`
Today it maps the VWAP class to a cap. In ARB mode:
1. Run the candidate through `engine_arb.classify()` (filters, score, base risk). Skip with a clear reason if it fails a filter.
2. Ask `engine_arb.allowed_risk(day_state, base)` for the rupee cap. Skip `arb_below_min_risk` if under ₹500.
3. Call `_enter(candidate, risk_cap_rupees=cap)` as now. Sizing, the margin preflight, the 1.5× abnormal-slippage check and protection all stay untouched. The 1.5× line scales with the dynamic cap automatically.
4. Stamp `strategy`, `score`, `base_risk`, `risk_used`, `size_mode` and `budget_snapshot` into `position.extra` for the desk and for the audit.

### 1.3 `engine_core.tick`: day controls
After `_check_daily_loss()`, add `_check_arb_day()`:
- Compute day MTM (2.2), update the stored peak.
- MTM ≤ −hard_day_stop → `_begin_shutdown(CloseReason.HARD_DAY_STOP)`.
- Peak ≥ arm and MTM ≤ keep × peak → `_begin_shutdown(CloseReason.DAY_LOCK)`.

Both reuse the existing shutdown → `squareoff_all` path, which is already idempotent and session-permanent (it matches "flatten and stop for the day").

### 1.4 `engine_exit.CloseReason`
Add `HARD_DAY_STOP` and `DAY_LOCK`, so the desk, events and Charges tab can tell them from EOD and daily-loss exits.

### 1.5 `engine_clock`
Keep 14:50 square-off. Add per-strategy cutoffs, checked in `_entry_gate` (the late re-check before sending): ORB < 11:00, funnel < 13:00. The global `ENTRY_CUTOFF_IST` (14:00) stays as the outer bound.

### 1.6 `engine_priority`
The VWAP tier is irrelevant in ARB. **Decided in PR A: plain FIFO (`rank_fifo`)**, because the backtest took candidates in time order. Ranking by score was considered; same-second collisions are rare, and parity with the tested strategy comes first.

### 1.7 `trading_engine_types.TriggerCandidate` and `trading_engine_handoff`
Add defaulted fields: `strategy` ("FUNNEL" | "ORB"), `planned_stop` (ORB's ATR stop, so `compute_stop_price` does not have to fake a swing), `rvol`, `atr14`. The handoff reads ORB rows too (a second query or UNION), and still reads the VWAP class for the record only.

### 1.8 `engine_entry.compute_stop_price`
If `planned_stop` is set, use it (rounded to tick, away from the market); otherwise keep the structural stop. The "never move the stop to fit risk" rule still holds, because the ORB stop is fixed before entry.

### 1.9 `run_execution_engine.py`, `api/routers/execution.py`, admin config
Pass the ARB config through CLI args and the start endpoint. The admin-config snapshot records it, so every trade row carries the exact settings it ran with (the existing `risk_limits_json` pattern).

---

## 2. What we rewrite

### 2.1 Trailing: `engine_trailing` schedule → lock rule
`schedule_stop()` (0.5R steps) is replaced for ARB trades by `lock_stop()`:
- Track `peak` per position from **completed 1-minute bars** (high for longs, low for shorts), read from `live_1m_candles`, which the observation runner already writes.
- After a bar closes with gain ≥ +1R: target = entry + 0.25R.
- After peak gain ≥ +2R: target = entry + max(25% × (peak − entry), 0.25R).
- Cap the target one tick inside the bar close, as the backtest does (`c − 0.05`).

Everything else in the module stays as it is: `resolve_stop`, favourable-only, the floor, the 25-modification cap handling and `move_stop`. The rule moves the stop less often than the 0.5R ladder does, which also eases the Kite modification cap.

Why bar closes and not LTP: the ₹43.8k/month figure was tested on 1-minute closes. A tick-level version is a different, untested strategy. The 0.5R ladder stays selectable for legacy mode.

### 2.2 Day MTM becomes a decision number
New `engine_day_pnl.py` (pure), fed each tick:
- **Closed:** Kite's realised figure per stock (the existing `STOCK_DAY_KEY` pinning) minus estimated charges (reuse `api/services/trade_charges` / the `charges.py` formula).
- **Open:** Σ qty × (LTP − entry) × sign, with LTP from the **same single REST read** `_fresh_ltp` already makes (extend its symbol set to every holding position, not only movable ones), minus estimated round-trip charges.
- **If LTP is missing this tick,** use the last good value if it is under 3 s old. Otherwise mark MTM stale: the hard stop then falls back to worst case (every open position at its stop), and the day lock does not fire on stale data.

The existing `live_pnl` display path can then read this same number, which also fixes the "desk P&L ~1 s behind Kite" TODO.

### 2.3 "Live risk" definition
Research: a trade stops counting once a 1-minute bar closes at ≥ +1R. In production that is exactly the moment the lock rule moves the stop to +0.25R. So define it from broker truth:

> live risk of a position = max(0, `current_risk_rupees`(entry, broker-confirmed stop, qty))

That uses the existing function in `engine_trailing`. A trade whose confirmed stop is in profit counts 0. A pending or unfilled entry counts its full sized risk (conservative). This is simpler and safer than re-deriving "has it closed a bar at +1R".

---

## 3. What is new

### 3.1 `engine_arb.py`: the ARB brain (pure, no I/O)
```
DayState:  mtm, mtm_peak, mtm_stale, closed_losers, score0_taken,
           traded_symbols_today, open_live_risk, halted_reason
classify(candidate, context) -> Verdict(ok, strategy, score, base_risk, reason)
allowed_risk(state, base, cfg) -> float
    risk = base × (cut_mult if closed_losers ≥ cut_after else 1)
    room = open_risk_cap + max(0, mtm) × house_money_mult − open_live_risk
    risk = min(risk, room) × risk_scale
    return 0 if risk < min_risk
day_action(state, cfg) -> None | HARD_DAY_STOP | DAY_LOCK
```
It is a direct port of `hybrid_v2.run` and `redctl.day_manage`, so it can be golden-tested against the backtest (see 5).

**Loser definition:** a closed trade with net P&L < 0 after charges. It is recomputed from the store every tick, so a restart cannot lose it.

### 3.2 `engine_context.py`: market and sector alignment (for the score)
From `live_1m_candles` for the watchlist, at the **last completed minute before the trigger**:
- `align_mkt` = mean %-change-from-open across the watchlist, signed to the trade direction.
- `align_sector` = the same over sector peers (`config/nifty100_sector_map.py`, pinned `SECTOR_MAP_VERSION`), excluding the stock itself.

Score = [align_mkt < −0.2%] + [align_mkt < −0.2% and align_sector > 0] + [vol ratio ≥ 6] + [trigger < 10:30].
Store the raw numbers on the position, not just the score, so live and backtest can be compared.

### 3.3 ORB detector (observation side): `orb_detector.py` + `orb_writer.py`
It lives with signal detection, like the continuation engine, so execution only ever *consumes* triggers.
- **Pre-market (morning checklist job):** for each stock, from the historical DB, compute `atr14_daily` and `avg_or_vol_14` (mean first-5-minute volume over 14 sessions). Store in a new `orb_baselines` table and show them on the checklist ("ORB baselines ready: 50/50").
- **09:20:** from the first 5-minute bar (09:15–09:20): OR high/low, direction = sign(close − open), RVOL = OR volume / avg_or_vol_14. Keep RVOL ≥ 3 and the top 3 by RVOL. Write `live_orb_arms` (level, direction, ATR stop distance, rvol, rank).
- **09:20–11:00:** on ticks, the first trade through the level writes a `TRIGGERED` row in `live_orb_decisions`. Unfired arms expire at 11:00 (`EXPIRED`).
- **Entry:** the engine enters at MARKET on that row, exactly like a funnel trigger. It is a synthetic stop-entry. The backtest assumed a fill at the level + 3 bps; detection-to-fill latency is the main live risk, so it is measured per trade (3.6).

A resting SL-M entry order at Kite was considered and not recommended. It bypasses the engine's gates (budget, cut after a loser, hard stop) at the moment of fill, and it would sit unmanaged if the engine died.

### 3.4 Funnel filter module (inside `engine_arb.classify`)
- Volume ratio ≥ 2 (fields already on the candidate).
- Trigger before 13:00.
- Stop distance ≥ 0.25% of price (from the structural stop).
- First funnel trigger for this stock today (the store already knows).
- At most 2 score-0 trades a day.

### 3.5 Persistent day state
New `arb_day_state` table in the engine DB (one row per session_date and run): mtm_peak, lock_armed_at, halted_reason, last_mtm. Written each tick when changed. On restart the peak is restored. Without it a mid-day restart would reset the peak and disarm the day lock.

### 3.6 Execution-quality log
Per trade, record: signal price (level or trigger), signal time, order time, fill price, slippage in bps, and exit slippage vs the stop. The whole edge depends on slippage (3 bps → ₹43.8k/mo; 5 bps → ₹31k), so this is the most important forward-test measurement.

### 3.7 Post-market parity check (no paper trading)
There is no paper or shadow phase: ARB is tested with **real Kite orders at micro size** (section 6). To check the live engine is really running the backtested strategy, a new `scripts/arb_daily_check.py` runs after the close. It is read-only, places no orders and does not simulate a paper account.
- **Input:** the day's live 1-minute candles and triggers from the observation DB, and the engine DB's positions and events.
- **Replay:** it runs the backtest logic (`hybrid_v2.run` + `day_manage`, the same code as the report) over the same day at the same `risk_scale`.
- **Output:** a diff, candidate by candidate: entered vs skipped, risk_used, stop, exit reason, R before charges, and day halts.
- **Where it goes:** the diff is written to the engine DB, so the Performance tab can show it.

A mismatch is either a bug in the port or a real live-vs-backtest difference (latency, fill, data). Either way it is found in days, at micro money.

---

## 4. UI: new and changed tabs

### 4.1 New top-level tab: **Strategy** (`AppTab` += `'strategy'`)
The day's decision board, read-only:
- **ORB panel (from 09:20):** the top-3 table: symbol, direction, OR high/low, level, RVOL, ATR stop, state (ARMED → TRIGGERED → ENTERED / SKIPPED(reason) / EXPIRED 11:00), plus the other RVOL ≥ 3 names ranked 4+ as "not selected".
- **Funnel feed:** every continuation trigger with filter ticks (vol ×, time, stop %, first-of-day), the score breakdown (mkt ✓, sector ✓, vol6 ✓, <10:30 ✓ → score 2), base risk → ARB-adjusted risk → the decision (ENTERED / SKIPPED: `below_min_risk`, `filter_stop_pct`, `score0_limit`, `day_halted`…).
- **Market breadth gauge:** the live align value for longs and shorts, and the sector heat strip (reuse `SectorBoard`).

### 4.2 Execution Desk: new **ARB Budget strip** (beside `RiskStrip`)
- **Day MTM** with the peak, and a bar from −₹5,000 (hard stop) to the lock line.
- **Day lock:** "not armed (needs ₹3,000)" → "ARMED: flatten below ₹X" (= 40% of peak).
- **Open risk:** used / cap, e.g. ₹4,200 / ₹6,000 + ₹1,800 house = ₹7,800, and the room left.
- **Size mode:** FULL or HALF (after the loser at 10:42 in RELIANCE); losers today; score-0 used 1/2.
- **Halted banner:** "Stopped for the day: DAY_LOCK at 12:31, locked +₹2,140".

### 4.3 Execution Desk: positions table columns
Strategy (ORB / F0–F3), base → used risk (and why it was cut: half / cap), R now, lock stage (initial / +0.25R locked / give-back 25%), a "FREED" badge when it no longer counts toward risk, and slippage bps at entry.

### 4.4 Start panel
- Strategy mode: **ARB** / Legacy VWAP.
- Capital ₹5L.
- A **Stage** selector: Micro 5% / 25% / 50% / 100% (section 6). It defaults to the current stage stored in admin config. Lowering is always allowed. Raising shows the promotion gates (met / not met) and needs a typed confirmation.
- ARB parameters in a read-only "Advanced" expander. Parameters are changed in code or admin config, not casually at start, because the plateau tests only cover certain ranges.
- The confirm dialog lists the scaled ARB numbers (e.g. at Micro: ORB ₹125, hard stop ₹250) and that orders are LIVE. ARB mode can only start with live orders on; there is no paper ARB.

### 4.5 New tab: **Performance** (forward test vs backtest)
- Month and day P&L after charges, green-day %, worst day, drawdown, against the report's expectations (₹43.8k/mo, worst day −₹5.7k, ~62% green).
- R and P&L by source (ORB, F0–F3), against the report's source table.
- **Slippage tracker:** median and mean entry/exit bps; alerts if the 20-trade rolling mean > 4 bps.
- Day-control log: how many hard stops and day locks fired, and P&L at each.
- **Parity panel:** the daily `arb_daily_check` diff (3.7): match rate, a list of mismatches, and live vs replay R.
- **Stage panel:** the current stage, days and trades done in it, and each promotion gate as met / not met (6.2).
- **Micro caveat:** P&L is also shown in **R before charges**, because at micro size charges are about twice as large in R (6.3).

### 4.6 Charges tab
Add the strategy and close reason (DAY_LOCK / HARD_DAY_STOP) columns. No other change.

---

## 5. Testing

1. **Unit (pure):** `engine_arb`, `engine_context`, the lock rule and `engine_day_pnl`, table-driven, including restart and stale-LTP cases.
2. **Golden parity test (the key one):** take 10–15 days from `arb_all_trades_2026_3bps.csv` / `candidates_2026.csv`, feed the same candidates in time order into `engine_arb` with a fake clock and a price path from `kite_12m.db`, and assert the **same skips, same risk_used, same day halts** as the order books. This proves the port is the strategy that was backtested.
3. **Engine integration:** the existing `tests/` fake broker, with scenarios for hard stop with 3 open positions, day lock arm → flatten, cut after loser, freed trade letting a new one in, ORB trigger at 10:59:58 vs 11:00:01, restart mid-day keeps the peak.
4. **Baseline:** full suite vs the known 4 pre-existing failures.

---

## 6. Rollout: real Kite orders from day one, at micro size

No paper or shadow phase. The first ARB trade is a real Kite MIS order, just a very small one. Size then rises in stages, and only when measured gates pass.

### 6.1 Build (no ARB trades yet)

| PR | What | Behaviour in production |
|---|---|---|
| A | Types, config, `engine_arb`, `engine_context`, day MTM + `arb_day_state`, close reasons, unit + golden parity tests | Legacy mode stays the default: unchanged |
| B | ORB baselines + detector + writer + handoff; Strategy tab | ORB arms and triggers show on the Strategy tab; the engine does not trade them yet |
| C | Lock-rule trailing, ARB desk strip, positions columns, start panel with stage, Performance tab, `arb_daily_check` | ARB can be started, at Micro only |

After C is deployed (after hours, with approval), the next session starts ARB at Micro with live orders.

### 6.2 Stages

All rupee figures are the full settings × `risk_scale`. The day-lock keep (40%), the R rules and the time rules never scale.

| Setting | Full (100%) | **Micro 5%** | 25% | 50% |
|---|---|---|---|---|
| ORB risk | ₹2,500 | **₹125** | ₹625 | ₹1,250 |
| Funnel risk, score 0 / 1 / 2 / 3+ | ₹1,000 / 2,500 / 4,500 / 6,000 | **₹50 / 125 / 225 / 300** | ₹250 / 625 / 1,125 / 1,500 | ₹500 / 1,250 / 2,250 / 3,000 |
| Open-risk cap (+ house money) | ₹6,000 | **₹300** | ₹1,500 | ₹3,000 |
| Min risk to trade | ₹500 | **₹25** | ₹125 | ₹250 |
| Hard day stop (MTM) | −₹5,000 | **−₹250** | −₹1,250 | −₹2,500 |
| Day lock arms at | ₹3,000 | **₹150** | ₹750 | ₹1,500 |
| Closed-loss backstop | ₹6,000 | **₹300** | ₹1,500 | ₹3,000 |

Estimates, from re-sizing the 767 backtest trades (Jan–Sep 2026, 3 bps slippage) at each scale with Kite's real charges formula. Day controls were not re-run, so these are approximate:

| Stage | Median order value | Net per month | Worst day | Charges per trade |
|---|---|---|---|---|
| Micro 5% | ~₹12.8k | ~+₹1.2k (3 of 9 months slightly red, −₹250 to −₹510) | ~−₹320 | ~0.20R |
| 25% | ~₹68k | ~+₹8.5k | ~−₹1.5k | ~0.17R |
| 100% | ~₹2.7L | ₹43.8k (report) | −₹5.7k | ~0.10R |

**Micro realistically costs at most a few thousand rupees in a bad month,** and it exercises every real path: Kite fills, SL-M/SL stops, modifications, day-control flattens and restarts.

### 6.3 Micro-size details
- **Why 5% and not smaller.** At 5%, only ~3% of backtest trades end up with no tradable quantity. At 2% that rises to 13%, which would mean testing a different trade mix from the backtest.
- **One-share floor.** If `floor(risk / risk-per-share)` is 0 and one share's risk is ≤ 1.25 × the allowed risk, the engine buys 1 share. Otherwise it skips with `micro_qty_below_one`, recorded so the parity check can explain it. The 1.5× abnormal-slippage check is then judged against that one share's risk. At 25% and above this floor switches off.
- **Charges are bigger in R at micro.** Kite brokerage is 0.03% capped at ₹20 an order. Large orders hit the cap; micro orders never do. So charges are ~0.20R at micro vs ~0.10R at full size. Day MTM includes charges, so the hard stop and day lock fire slightly earlier at micro. That is conservative, and acceptable. **Stages are judged in R before charges, not in rupees.**
- **What micro cannot show.** The market impact of a ₹2.7L order. On Nifty 50 names this should be small, but slippage is re-checked at 50% and 100% (the gate below applies at every stage).
- **One strategy at a time.** Legacy VWAP mode is off while ARB is live. Mixing them would blur every number.

### 6.4 Promotion gates (all must pass to move up one stage)

| Gate | Micro → 25% | 25% → 50% | 50% → 100% |
|---|---|---|---|
| Minimum time in stage | 15 trading days **and** ≥ 40 filled trades | 20 days, ≥ 50 trades | 20 days, ≥ 50 trades |
| Safety incidents (position unprotected > 12 s, orphan order, duplicate entry, a missed or wrong day halt, restart that lost day state) | 0 | 0 | 0 |
| Parity: live decisions match the post-market replay | ≥ 95% of candidates | ≥ 95% | ≥ 95% |
| Entry slippage | median ≤ 3 bps, mean ≤ 5 bps | same | same |
| Result in R before charges | ≥ 70% of the replay's R for the same days | same | same |

The one-share floor and qty rounding make risk_used differ slightly from the replay; a match means the same enter/skip decision and risk within one share.

### 6.5 Demotion and halt rules
- **Any safety incident:** stop ARB for the session, fix it, and restart the current stage's day count from zero.
- **Rolling 20-trade mean entry slippage > 6 bps:** drop one stage.
- **Month loss worse than 4 × the stage's hard day stop** (−₹1,000 at Micro, −₹20,000 at 100%): drop one stage and review.
- **Parity < 90% on any week:** pause promotion until the cause is understood.

Earliest path to full size: ~15 + 20 + 20 trading days ≈ 2.5–3 months after PR C, if every gate passes the first time.

Every deploy happens after market hours, with explicit approval each time, through the existing GitHub deploy workflow.

## 7. Open decisions for NJ

1. **ORB and funnel on the same stock on the same day.** The `symbol_already_open` gate blocks a second concurrent position in one stock. Keep that (recommended), or allow a funnel trade after the ORB trade closes?
2. **MTM net of estimated charges** (recommended; matches the backtest) or gross like Kite's screen?
3. **Legacy VWAP mode:** keep it selectable, or retire it once ARB is live?
