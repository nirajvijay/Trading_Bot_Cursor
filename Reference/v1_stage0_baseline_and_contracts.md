# Nifty 100 Radar V1 — Stage 0 Baseline & Frozen Interface Contracts

**Status:** Stage 0 complete (inspection + contract freeze only).
**Date (IST context):** 2026-09-08
**Workspace HEAD:** `e4aa813e08059d3cd17fb785eae0af90eb9c2d29`
**Deployed `current`:** `/opt/nifty-radar/releases/e4aa813e08059d3cd17fb785eae0af90eb9c2d29` (matches HEAD)
**Branch:** `feat/vwap-qualifier`
**Product contract:** Astra V1 replacement plan (owner-approved)
**Execution:** Stage 0 reviewed by owner (2026-09-08). This document is the frozen contract; Stage 1 proceeds under isolated test DBs only until authorized otherwise.

Clarifications applied (owner, 2026-09-08):

1. “Stop does not flatten” is **not** a defect. **Stop Engine** = disarm + drain while continuing position management; **Close All & Pause** = explicit liquidation.
2. Freeze contracts for: status strip, sector map, Admin draft/saved/effective, trade audit, control commands, **manual trade-preview/approval**, **session arming**, **command outcomes**, **configuration-application**, PAPER/LIVE broker boundary.
3. Manual and Autopilot use the **same** execution/risk path (decided).
4. PAPER must **block Kite order writes** while allowing **simulated** order operations.
5. “Five fills/day” = **five distinct setups** that receive an entry fill — **not** five partial-fill events.
6. 1–2s P&L/sync refresh is a **healthy-connectivity target**, not a guaranteed SLA; **display actual freshness**.
7. Preserve **existing saved settings**; record baseline + contracts in this Reference document.

Owner review corrections (2026-09-08, post Stage 0):

1. **Separate entry authorization from position management.** Pause, Disarm, Stop Engine, and restart recovery must still permit protective stops, cancellation, reconciliation, and exits for existing LIVE (and any already-filled) exposure. Entry gates never strand unprotected fills.
2. **Partial fills and protection are independent.** A partially filled entry may already have protected quantity while its remainder is pending. Lifecycle must not delay protection until entry completion.
3. **Live-mode evidence:** an unset service `TRADING_ENGINE_LIVE_ORDERS` does **not** prove live orders are impossible. Document the actual authorization path (§1.1.1).
4. **ADANIPORTS provenance before reconciliation.** Latest run PAPER does not prove this row belongs to it. Link trades to originating run/mode and order evidence. Never reconcile a paper trade against live broker positions or auto-delete the row. Does not block isolated development.
5. **Test claim corrected:** three existing unit tests passing ≠ reproduction/validation of all gaps. Label static findings separately; add failing/pending regression cases during Stage 1 for remaining gaps.

---

## 1. Baseline evidence pack

### 1.1 Checkout and runtime

| Item | Value |
|---|---|
| Git HEAD | `e4aa813` — *Fix Admin Console save UX: manual save only with dirty-state warning.* |
| `/opt/nifty-radar/current` | Symlink to release named for the same commit |
| API service | `nifty-radar-api.service` — uvicorn `api.main:app` on `127.0.0.1:8000`, user `nifty-radar`, `WorkingDirectory=/opt/nifty-radar/current` |
| Env file | `/opt/nifty-radar/secrets/api.env` |
| `APP_ENV` | `production` |
| `NIFTY_RADAR_DATA_ROOT` | `/opt/nifty-radar/data` |
| `LOCAL_DATA_DIR` | `/opt/nifty-radar/data/local` |
| `TRADING_ENGINE_LIVE_ORDERS` in `api.env` | **unset** at service process level (see §1.1.1 — this is **not** proof live orders cannot run) |
| Observation / trading processes | **Not** separate systemd units; started via API as child processes with status under `/tmp/runner_status.json` and `/tmp/trading_engine_status.json` |

#### 1.1.1 Live-order authorization path (actual)

Unset `TRADING_ENGINE_LIVE_ORDERS` on the **API service** only means the API process default reader returns false. It does **not** make live placement impossible.

Actual path today:

1. Owner calls `POST /trading-engine/start` with `confirm_live_orders=true` (`TradingStartRequest`).
2. `start_trading_engine()` sets `live_wanted = bool(confirm_live_orders)`.
3. Child env is forced: `TRADING_ENGINE_LIVE_ORDERS=true|false` for that process, **and** CLI gets `--live-orders` when live is wanted (`api/services/trading_engine_runner.py`).
4. `live_trading_engine.py` builds `KiteBroker(..., live_orders_enabled=True)` when `--live-orders` is set; `engine_runs.live_orders_enabled` is persisted per run.
5. Host evidence: an earlier run on `2026-08-21` has `live_orders_enabled=1` in `trading_engine.db` while a later `2026-09-02` run is paper (`live_orders_enabled=0`).

**Implication:** LIVE capability is **request-gated per engine start**, not solely by the standing service env file. Stage 1+ must keep per-run / per-trade provenance of the mode that created exposure.

### 1.2 Code defaults (when no saved override)

From `trading_engine_types.py` / `api/admin_config/defaults.py` / `vwap_qualifier_v2_config.py`:

| Key | Code default |
|---|---|
| `DEFAULT_TOTAL_CAPITAL` | ₹300,000 |
| `PER_TRADE_RISK_CAP` / admin `per_trade_risk_cap_inr` | ₹900 |
| `LIMITED_PER_TRADE_RISK_CAP` / admin `limited_per_trade_risk_cap_inr` | ₹450 |
| `DAILY_LOSS_CAP` / admin `daily_loss_cap_inr` | ₹3,000 |
| `vwap_accept_gap_exclusive_max` | 0.0022 (0.22%) |
| `vwap_limited_gap_inclusive_max` | 0.0040 (0.40%) |
| `DEMO_LEVERAGE_FACTOR` | **5.0** (demo/paper capital math only; **must not** be treated as live buying power) |

### 1.3 Saved Admin settings (preserved — do not overwrite in Stage 0/1 migrations without merge)

Path: `/opt/nifty-radar/data/config/admin_config.db`
Active version: `06508a4189ef467898e2f04c758c2010` (created 2026-09-01 by `nj`)
`entries_paused`: **0** (accepting)

| Key | Saved effective value |
|---|---|
| `per_trade_risk_cap_inr` | **900.0** |
| `limited_per_trade_risk_cap_inr` | **450.0** |
| `daily_loss_cap_inr` | **2995.0** (intentionally ≠ code default 3000) |
| `vwap_accept_gap_exclusive_max` | **0.0022** |
| `vwap_limited_gap_inclusive_max` | **0.004** |

Control log shows pause/resume and a rollback test by `live_test` on 2026-09-02. **Migrations must preserve this DB and merge new keys with defaults only for missing keys.**

### 1.4 Trading engine DB snapshot (host)

Path: `/opt/nifty-radar/data/local/trading_engine.db`

- Latest run: session `2026-09-02`, `status=stopped`, `live_orders_enabled=0`, `total_capital=300000`, `leverage=5.0`, `consume_new_triggers=0`.
- Trade counts: `closed=11`, `skipped=18`, **`protected_open=1`**.
- Leftover row: `trade_id=1b41dae050df4a05823622ca7aba3e70`, symbol **ADANIPORTS**, qty **145**, session **2026-09-02**, still `protected_open` after engine stop.

#### 1.4.1 ADANIPORTS provenance (unresolved — do not auto-reconcile)

| Fact | Value |
|---|---|
| `trade_id` | `1b41dae050df4a05823622ca7aba3e70` |
| Symbol / qty / status | ADANIPORTS / 145 / `protected_open` |
| `session_date` | `2026-09-02` |
| `entry_time` / `updated_at` | `2026-09-02T06:41:24+00:00` |
| Schema gap | Trades table historically has **no `run_id`** column linking to `engine_runs` |

**Correct interpretation:** The latest run on that session is PAPER (`live_orders_enabled=0`), but that does **not** prove this trade was created by that run or in PAPER mode. Same calendar session can contain multiple runs (host also has prior LIVE runs on other dates). Without run/mode/order-id provenance join, mode is **unknown**.

**Rules (frozen):**

- Establish provenance (originating `run_id`, `live_orders_enabled` at entry, entry/sl order ids, broker tags) **before** any broker reconciliation decision.
- **Never** reconcile a known-PAPER trade against live Kite positions.
- **Never** auto-delete this row to “clean” the DB.
- Does **not** block isolated Stage 1 development on temp test databases.

### 1.5 Frontend / API surface today (pre-IA)

Private tabs (`AppTab`): `radar | checklist | auth | trading | admin`.
Post-login default tab: **radar** (not checklist).
Trading commands API today: start/stop/capital/trail-stop/auto-trail.
Command kinds in types: `trail_stop | stop_engine | set_auto_trail` only.
Sector map module: **absent** (`config/nifty100_symbols.py` flat 100 only).
Observation `can_start`: blocked unless checklist OK **and** market open window currently described as **09:15–15:30 IST**.

### 1.6 Stage 0 verification methods (labeled separately)

#### 1.6.1 Existing unit tests executed (smoke only — not full gap validation)

```
tests.test_trading_engine_cycle.CycleTests.test_external_flatten_closes_active_trade ... ok
tests.test_trading_engine_cycle.CycleTests.test_restart_reconcile_no_second_market ... ok
tests.test_trading_engine_cycle.CycleTests.test_stale_stop_engine_does_not_pause_new_run ... ok
```

These three tests confirm **narrow existing behaviors** only. Passing them does **not** reproduce or validate the gap inventory in §2.

#### 1.6.2 Static code inspection findings (not runtime defect reproduction)

- `BrokerOrder` lacks `filled_quantity` / pending qty fields (pre–WP-1.1).
- `ENTRY_COMPLETE == {"COMPLETE"}` only; no partial-fill branch in `_apply_entry_order` (pre–WP-1.1).
- No `square_off` / `close_all` / `close_position` / `14:45` / `15:15` in `trading_engine_cycle.py`.
- Stop API message documents non-flatten drain (aligned with Stop Engine semantics).
- `qty_mismatch` event is logged without entry-pause / durable incident per Astra.

Stage 1 must add **explicit regression tests** (passing for fixed items; failing or `@expectedFailure` for still-open gaps).

---

## 2. Gap inventory (classified)

### 2.1 Not defects (required semantics — implement/clarify in Stage 1+)

| Item | Notes |
|---|---|
| Stop Engine does not flatten | Matches Astra/owner: disarm + drain; keep managing until flat & reconciled, then stop process. Current API message is directionally correct; missing formal drain state machine & Close All. |
| Demo 5× leverage in PAPER math | Allowed for simulated capital; **forbidden** as live buying power. |

### 2.2 Gaps vs V1 contract (Stage 1+ work — evidence-backed)

| ID | Gap | Evidence |
|---|---|---|
| G1 | No partial-fill quantity model | `BrokerOrder` has `quantity` only; FakeBroker entry COMPLETE/OPEN; `ENTRY_COMPLETE={"COMPLETE"}` only |
| G2 | No Close Position / Close All & Pause commands | `CommandKind` and trading router lack them |
| G3 | No session square-off / entry cutoff enforcement in cycle | No `14:45` / `15:15` / `square_off` in `trading_engine_cycle.py` |
| G4 | No MANUAL approval / preview / arming product model | Engine auto-consumes triggers when `consume_new_triggers`; no approve command |
| G5 | No named PAPER/LIVE × MANUAL/AUTOPILOT session arming | Live = env + `confirm_live_orders`; pause = admin `entries_paused` |
| G6 | PAPER does not hard-block Kite write endpoints | `KiteBroker` always has place/modify/cancel; selection is process flag, not adapter refuse |
| G7 | No max concurrent positions / five-setup fill cap in risk module | Not present in `trading_engine_risk.py` |
| G8 | qty_mismatch logs event but does not pause entries / raise durable incident per Astra | `_reconcile_external_exit` appends `qty_mismatch` then continues if `net != 0` |
| G9 | Command API returns immediate `"success": True` on enqueue | `trail-stop` / `auto-trail` respond before broker confirm |
| G10 | Observation start window ≠ 09:00 waiting semantics | Readiness uses market-open ≈ 09:15–15:30; no “waiting for websocket/data” states |
| G11 | Sector map not versioned beside universe | No `nifty100_sector_map` module |
| G12 | Stale logical open trade after stopped run | `protected_open` ADANIPORTS on 2026-09-02 in host DB |
| G13 | Trade audit incomplete vs V1 | Has `initial_stop`, events, admin version fields; lacks immutable setup blob, requested vs confirmed stop, stop-est P&L fields, trail event schema as first-class |
| G14 | Admin config keys incomplete vs Trading Bot Values | Only risk + VWAP floats + `entries_paused` |

### 2.3 Existing strengths to reuse

- Observation ≠ execution separation; import-boundary tests.
- FakeBroker + external flatten unit test (passes).
- Restart reconcile avoids duplicate market entry (passes).
- Admin versioning, pause/resume, step-up, audit log.
- VWAP gate + `admin_config_version_id` stamped on trades.
- Owner web auth + MFA + CSRF.

---

## 3. Frozen interface contracts (V1)

These names/semantics are frozen for Stage 1+ implementation. Wire formats may use JSON field names in `snake_case` as below.

### 3.1 Status strip (all private pages)

`StatusStripView` (read model; unknown/stale must **not** render as healthy zeros):

| Field | Type / values | Notes |
|---|---|---|
| `execution_mode` | `PAPER` \| `LIVE` | Destination of order writes |
| `entry_mode` | `MANUAL` \| `AUTOPILOT` | Approval policy; independent of PAPER/LIVE |
| `entry_permission` | `armed` \| `paused` \| `disarmed` | Session entry authorization |
| `engine_state` | `stopped` \| `starting` \| `running` \| `pausing` \| `stopping` \| `error` \| `critical` | Process + drain |
| `feed_age_seconds` | number \| null | null ⇒ show unknown, not 0 |
| `feed_status` | `live` \| `waiting_market_data` \| `connecting` \| `stale` \| `down` \| `unknown` | Pre-09:15 waiting ≠ healthy live |
| `sync_age_seconds` | number \| null | Broker reconcile freshness |
| `mark_age_seconds` | number \| null | Mark used for open P&L |
| `open_pnl` | number \| null | null/STALE if mark stale |
| `unresolved_incident` | bool + summary | Protection/reconcile conflicts |
| `as_of` | ISO timestamp | Strip generation time |

**P&L freshness:** under healthy connectivity, target refresh on the order of **1–2 seconds** while positions/orders active; **always display `mark_age_seconds` / STALE**. Not a hard SLA.

### 3.2 Sector map

- Module (planned): `config/nifty100_sector_map.py` (or equivalent) **beside** `config/nifty100_symbols.py`.
- Version id + source date required.
- Exact owner map (17 sectors, 100 symbols) as in Astra plan — omitted here only by reference; implementation must embed the exact lists.
- Invariants: 100 unique symbols; each symbol in exactly one sector; set-equality with universe; mismatch ⇒ checklist/observation readiness **blocked** with precise reason.
- Rebalance: bump universe + sector versions together; retain historical versions for reports.

### 3.3 Session arming contract

`SessionArm` (persisted intent before engine accepts new entries):

| Field | Values |
|---|---|
| `session_date` | IST trading date |
| `execution_mode` | `PAPER` \| `LIVE` |
| `entry_mode` | `MANUAL` \| `AUTOPILOT` |
| `armed` | bool |
| `config_version_id` | admin saved version applied to this arm |
| `live_confirmation` | required true when `LIVE` |
| `created_at` / `created_by` | audit |

**Rules:**

- Arming requires: checklist readiness (as applicable), no unresolved execution/protection incidents, engine not in `critical`, LIVE prerequisites when LIVE (token, static egress / permitted order behavior checks as implemented).
- **PAPER↔LIVE switch** blocked while open positions, pending/unknown commands, unresolved orders, or reconcile incidents exist. Requires stopped, flat, reconciled engine + new arm. **No automatic forced liquidation on mode switch.**
- **MANUAL↔AUTOPILOT:** switching to MANUAL cancels unfilled automatic entry remainders and queued automatic entry intentions; filled qty remains managed; new setups need approval.

### 3.4 Control semantics (frozen)

**Hard split:** *entry authorization* (may new risk be taken?) vs *position management* (protect/reconcile/exit what is already on).

| Control | Entry authorization | Position management (fills / LIVE exposure) |
|---|---|---|
| **Pause Entries** | Block new submissions; cancel outstanding **unfilled** entry remainders & queued entry intentions | **Must continue** protective stops, stop modifies, cancellation of unprotected orphans, reconciliation, and exits |
| **Close All & Pause** | Pause + cancel entry remainders | **Liquidate** engine-managed positions; confirm remaining qty/orders |
| **Disarm** | Revoke session entry authorization; cancel pending **unfilled** entries | **Must continue** management until flat |
| **Switch to Manual** | Cancel automatic entry remainders & queued auto intentions | Continue management; new setups require approval |
| **Stop Engine** | Disarm + cancel unfilled entries; enter `stopping`/draining | **Must continue** management until flat and reconciled, then stop process |
| **Restart recovery** | Start **entry-paused** (no new entries until rearmed) | Reconcile and **continue** protection/exits for existing exposure before any rearm |

**Never** convert an already-filled (including partial) trade into `skipped` with qty zero because entries were paused. Pause/Disarm/Stop/restart only stop **new** entry risk.

**Stop Engine ≠ Close All & Pause.** Close All explicitly liquidates.

### 3.5 Manual trade preview / approval (same path as Autopilot)

**Decided:** Autopilot submissions and Manual approvals call the **identical** risk sizing, reservation, entry, protection, and exit pipeline. UI click is not a second engine.

`TradePreviewRequest` → `TradePreviewResponse` (read-only; no broker write):

- Inputs: `setup_id` (and/or symbol+session), optional `qty_override`, optional `stop_tighten` (never widen vs machine structural stop in V1).
- Outputs: direction, mark/quote + `quote_age_seconds`, structural stop, proposed qty, notional, margin estimate, risk ₹, VWAP class, config version, eligibility, blockers, setup expiry / `signal_age_seconds`, estimated P&L if stop hits.
- Preview must revalidate freshness; expired setups are not approvable.

`TradeApproveCommand` (Manual only):

- Body: `setup_id`, optional overrides from preview, `client_command_id` (idempotent).
- Behavior: revalidate everything → same submit path as Autopilot would use for that setup.
- Cannot revive expired setups; cannot bypass risk/session gates.

`AutopilotEntryIntention`:

- Created only when `entry_mode=AUTOPILOT` and `entry_permission=armed` and setup eligible.
- Enters the **same** submit path; no separate “fast” risk.

### 3.6 Command outcome contract

All mutating trading/admin safety actions use durable commands:

```text
CommandRecord:
  command_id (server)
  client_command_id (optional, unique per owner session action)
  kind
  trade_id? / setup_id?
  payload
  state: queued | running | awaiting_broker | succeeded | failed | cancelled | unknown_needs_reconcile
  created_at, updated_at
  result_summary?
  broker_order_ids[]?
```

**API rule:** HTTP acceptance of a command means **accepted for processing**, not broker success.
UI must show command `state` through broker confirmation. Do not label trail/close/entry as successful merely because the request was accepted or enqueued.

Initial `kind` set (extensible):

- `approve_entry` | `submit_entry` (internal) | `cancel_entry_remainder`
- `trail_stop` | `set_auto_trail`
- `close_position` | `close_all`
- `pause_entries` | `resume_entries` | `disarm`
- `stop_engine`
- `reconcile_now` (recovery)

### 3.7 Configuration application contract (Admin)

**Layers:**

1. **Draft** — UI form only; not live.
2. **Saved** — versioned payload after Save (audit + parent version).
3. **Effective** — what strip/Desk/engine read for *new* decisions after apply rules.
4. **Trade-recorded profile** — stamped on each trade at acceptance; immutable for that trade’s original risk/VWAP/trail profile fields.

**Buttons:** `Save` (validate + new version), `Discard` (draft ← saved). Pause/Close All/Disarm/Resume are **direct audited commands**, not draft fields.

**Apply policy after Save:**

| Change | Effective |
|---|---|
| Pause/resume, disarm, close-all, recovery commands | Immediate command path |
| Risk-budget **reductions** | Immediate for **new** entries; if existing commitments exceed reduced allowance → block further entries |
| Risk **increases**, capital, concurrency/fill limits, VWAP/strategy, trailing profile, execution preference | **Next arm / next session** |
| Open trade profile | Retained; explicit tighten-stop / close remain available |

**Preserve saved settings:** migrations add keys with code defaults only when absent; never clobber `daily_loss_cap_inr=2995` etc.

**Stale version:** reject Save if client base version ≠ current active version.

### 3.8 Trading bot values (config keys — freeze names)

Minimum V1 saved keys (floats/ints/bools as appropriate); defaults = code defaults unless saved:

- `allocated_capital_inr` (seed from `DEFAULT_TOTAL_CAPITAL` / run capital — migrate carefully)
- `per_trade_risk_cap_inr`
- `limited_per_trade_risk_cap_inr`
- `daily_loss_cap_inr`
- `max_concurrent_positions` (default **2**)
- `max_filled_setups_per_day` (default **5** = five distinct `setup_id`s with an entry fill)
- `one_position_or_unresolved_entry_per_symbol` (default true)
- `aggregate_notional_cap_equals_allocated_capital` (default true)
- `entry_cutoff_ist` (default `14:45`)
- `square_off_ist` (default `15:15`)
- `auto_trail_default_enabled` (default true)
- trailing profile params matching staged-R table
- `setup_expiry_seconds` (default 30)
- `max_quote_age_seconds` (default 2)
- `max_entry_drift_r` (default 0.1)
- `entry_remainder_cancel_seconds` (default 5)
- `protection_confirm_deadline_seconds` (default 5)
- VWAP thresholds (existing keys)
- `preferred_execution_mode` `PAPER`\|`LIVE` (preference only; LIVE still gated)

### 3.9 Trade audit record (Desk)

Per trade, persist and display:

**Provenance (required going forward):**

- `run_id` of the engine run that created the trade  
- `entry_live_orders_enabled` (bool) — mode at entry submit  
- `entry_order_id`, `sl_order_id`, `broker_tag`  
- Never assume mode from “latest run on session date” alone  

**Immutable original machine setup** (written once at accept/submit; never overwritten):

- `setup_id`, signal identity, direction, entry reason, structural stop, initial sizing decision, VWAP result, `admin_config_version_id`, trailing profile id/params, rule versions.

**Mutable / append-only:**

- `requested_stop` vs `broker_confirmed_stop`
- trail events: `{at, source: auto|manual|external, from, to, result}`
- qty: `intended_qty`, `filled_qty`, `remaining_entry_qty`, `protected_qty`, `remaining_position_qty`
- live mark P&L vs `est_pnl_at_original_stop` vs `est_pnl_at_current_confirmed_stop`
- skip/reject/close reasons (including VWAP classes and `external_exit`)

### 3.10 Execution lifecycle states (engine)

Entry progress and protection progress are **independent dimensions** (not a single linear gate).

**Quantity fields (required):**

- `intended_qty` — sized target at submit  
- `filled_qty` — broker-confirmed entry fills so far  
- `remaining_entry_qty` — still working / unfilled entry remainder  
- `protected_qty` — quantity covered by a working/confirmed protective stop  

**Rules:**

- On any increase in `filled_qty`, **immediately** drive protection so `protected_qty` tracks filled quantity (place or modify stop). Do **not** wait for entry completion / remainder cancel.
- A trade may be `partial_entry` **and** already have `protected_qty > 0` while `remaining_entry_qty > 0`.
- `qty` in legacy rows is treated as intended size; migrations backfill `intended_qty`/`filled_qty`/`protected_qty` safely without touching host production DB in Stage 1 isolated work.

**Status vocabulary (overall phase labels):**

`candidate` → `entry_submitting` / `submission_unknown` → `partial_entry` → `entry_filled` (remainder done) → … → `exit_pending` / `partial_exit` → `closed`  
Plus: `protection_pending` (filled but stop not yet confirmed), `protected_open`, `skipped`, `rejected`, `reconciliation_required`.

Protection may advance to `protected_open` (or equivalent protected condition) **while** entry status is still `partial_entry`.

### 3.11 PAPER vs LIVE broker boundary

| Mode | Allowed |
|---|---|
| **PAPER** | Simulated place/modify/cancel/poll against FakeBroker (or equivalent). **Must refuse** any Kite order-placement, modification, or cancellation endpoint. May use live **market data** and read-only account checks. |
| **LIVE** | Kite writes only when armed LIVE + env/prereq gates + explicit confirmation. |

Paper ledgers/summaries remain distinct from live.

### 3.12 Observation start states (09:00)

When checklist-ready on a configured session:

- Allow Start from **09:00 IST**.
- UI/API phases: `connecting_websocket` → `connected_waiting_market_data` → `receiving_market_data` → `failed`.
- Expected pre-09:15 silence is **not** healthy live feed and must **not** trigger trading-window stale-feed exit logic early.

### 3.13 Risk accounting notes (frozen definitions)

- Reserve risk before submission; include pending entries, partial fills, open downside, estimated costs.
- Profits do not replenish daily loss budget.
- Net session P&L (realized + unrealized) to daily threshold ⇒ pause entries + request managed exits.
- **max_filled_setups_per_day:** count distinct `setup_id` with at least one entry fill (partial or full). Further partials on same setup do not consume extra fill slots.
- Do not use `DEMO_LEVERAGE_FACTOR` as live buying power.

### 3.14 Staged R trailing (policy freeze)

`R = |actual average entry − original structural stop|` frozen after entry fill/remainder cancel reconciled.

| Favorable move | Desired stop |
|---|---|
| Below +1R | Original structural stop |
| +1R to &lt; +2R | Tightest of existing, cost-adjusted BE, 1R behind extreme |
| +2R+ | Tightest of existing, cost-adjusted BE, 0.5R behind extreme |

Mirror shorts; persist extreme; ≤1 modify / 2s / ≥2 ticks improvement; never widen confirmed stop; if price already through desired exit → full exit. Auto-trail default on; manual tighten does not redefine R or erase setup.

---

## 4. Blockers and owner attention

### 4.1 Blockers to Stage 1 start

**None technical** for beginning Stage 1 **after owner review of this document**, provided owner accepts:

- Leftover `protected_open` ADANIPORTS row is historical evidence / future reconcile work — **not** auto-deleted in Stage 0.
- Saved `daily_loss_cap_inr=2995` remains authoritative until owner Saves a new version.

### 4.2 Non-blocking follow-ups

| Item | Owner? |
|---|---|
| Public homepage copy | Yes, before public publish |
| When to allow supervised AUTOPILOT live | Later gate after manual live evidence |
| Whether to manually reconcile/cleanup ADANIPORTS row via broker checklist now | Optional ops; Stage 1 restart reconcile should handle class of issue |

### 4.3 Assumed decisions (already frozen — do not reopen in Stage 1)

- Manual ≡ Autopilot execution/risk path.
- Stop Engine drain vs Close All liquidate.
- Five fills = five setups.
- PAPER blocks Kite writes.
- Display freshness; 1–2s is a target only.

---

## 5. Stage 0 gate assessment

| Gate criterion | Result |
|---|---|
| Checkout/release confirmed | **PASS** — workspace = `current` = `e4aa813` |
| Saved defaults/settings inventoried & preserve rule stated | **PASS** — admin DB read; preserve/merge policy frozen |
| Current flows mapped | **PASS** — API tabs, commands, observation readiness, engine consume path |
| Critical gaps reproduced with evidence | **PASS** — static + 3 unit tests; classified vs non-defects |
| Interface contracts frozen (incl. preview/arming/commands/config apply) | **PASS** — §3 of this document |
| Owner clarifications incorporated | **PASS** |
| Stage 1 started | **NOT STARTED** (per order) |

**Stage 0 exit: READY FOR OWNER REVIEW.**
**Recommendation:** Approve this pack → authorize Stage 1 (execution safety), starting with durable lifecycle + partial fills + command outcome model, without clobbering saved admin settings.

---

## 6. Document control

| Version | Date | Author | Note |
|---|---|---|---|
| 1.0 | 2026-09-08 | Cursor Stage 0 | Baseline + frozen contracts |
| 1.1 | 2026-09-08 | Cursor | Owner review corrections: entry vs management split; independent partial protection; live auth path; ADANIPORTS provenance; test-claim labeling |
| 1.2 | 2026-09-08 | Cursor | **WP-1.1 accepted** (dev checkpoint). See §7. |

Astra remains product authority; this file is the Stage 0 evidence + implementation contract freeze for Cursor.

---

## 7. WP-1.1 development checkpoint (accepted)

**Status:** Accepted for development checkpoint (not a deployment/live-trading authorization).  
**Scope:** Durable trade lifecycle, independent qty/protection model, exit attribution, provisional vs confirmed P&L, Kite modify state machine, IST broker timestamp convention.  
**Boundary:** Isolated test DBs + FakeBroker / mocked Kite only. No live DB migration, no deploy, no live order writes.

### 7.1 Acceptance test command and results

```text
python3 -m unittest \
  tests.test_trading_engine_wp11_lifecycle \
  tests.test_trading_engine_cycle \
  tests.test_trading_engine_broker \
  tests.test_trading_engine_risk \
  tests.test_trading_engine_store

Ran 100 tests in 15.666s
OK (expected failures=2)
```

### 7.2 Deferred expected-failure placeholders (not WP-1.1 safety claims)

These remain `@unittest.expectedFailure` in `tests/test_trading_engine_wp11_lifecycle.py` (`PendingGapRegressionTests`) until later work packages:

1. `test_close_all_command_kind_exists` — Close All / close-position command surface (WP-1.4).
2. `test_square_off_helpers_present_in_cycle` — session square-off / cutoff helpers (WP-1.4).

### 7.3 Preserve rule (unchanged)

- Saved host `daily_loss_cap_inr=2995` must not be clobbered by migrations.
- Host trading history (including leftover `protected_open` evidence rows) must not be auto-deleted.
