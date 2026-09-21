# Execution Engine Rebuild — Implementation Plan

Turns every decision in `Reference/execution_engine_rebuild_notes.md` into a
sequenced build. Each phase is independently compilable, independently
testable, and leaves the tree green. Nothing here re-opens a settled design
question; where the notes deliberately deferred something, this plan keeps it
deferred and says so explicitly.

**Ground rules carried through every phase:**

- **Signal detection is untouched.** `vwap_qualifier_v2.py`,
  `intraday_continuation_*`, `intraday_pullback_*`, `intraday_spike_*`,
  `live_observation_runner.py`, `live_candle_pipeline.py` — zero edits.
- **The old engine is deleted** (Phase 13) — `trading_engine_cycle.py` (5961
  lines), `trading_engine_store.py`, `live_trading_engine.py` and ~9 more
  modules, their ~20 test files, the `/trading-engine/*` router, and the old
  Execution Desk frontend. It is not kept running in any capacity.
  **But four `trading_engine_*` modules are shared foundation, not old-engine
  code, and stay:** `trading_engine_broker.py` (`BrokerPort`, `FakeBroker`,
  `KiteBroker` — `engine_core` already imports it), `trading_engine_types.py`
  (`TriggerCandidate`, `BrokerOrder`), `trading_engine_handoff.py`,
  `trading_engine_quotes.py`. Full keep/delete inventory in Phase 13.
- **Trailing stays out.** No `engine_trailing.py`, no `_apply_trailing`.
  `ExecutionState.TRAILING` and its row in `ALLOWED_TRANSITIONS` stay as
  currently-unreachable future states. The Kite 25-modification ceiling and the
  "multiple orders per role" schema question stay parked for the trailing
  redesign.
- **Batching rule is a hard constraint from the first line of code**, not an
  optimization: at most one batched broker read per tick covering all open
  positions. Any helper that takes a single symbol and hits the network is a
  bug.
- **Every broker call uses the same three-way response discipline:** clear
  success / clear-and-definite rejection / genuinely ambiguous → resolve by
  querying the broker, never by assuming.
- **Frontend scope is the Execution Desk tab only** (Phase 12). Header, nav
  bar, Checklist tab, Observation tab: untouched.

---

## Phase 0 — Session risk config, editable caps, and the session clock

Everything downstream reads limits from one place, so this comes first.

**New `engine_config.py`:**

- `SessionRiskConfig` (frozen dataclass): `per_trade_cap_rupees`,
  `per_trade_cap_vwap_limited_rupees`, `daily_loss_cap_rupees`,
  `total_capital_rupees`, `leverage_factor`.
- **Capital defaults: `total_capital_rupees = 300_000`, `leverage_factor = 5.0`**
  → buying power ₹15,00,000. Both the caps and total capital are editable at
  start (Phase 12's start panel); `leverage_factor` is fixed at 5 unless a
  reason to change it appears.
- **Three derived capital numbers, computed not stored** — one definition, used
  identically by sizing and by the desk, so the number the UI shows is the number
  sizing actually used:
  - `buying_power = total_capital × leverage_factor` (₹15L at the defaults)
  - `remaining_capital = total_capital − margin_used` (margin_used summed from
    open positions' broker-reported margin)
  - `remaining_buying_power = remaining_capital × leverage_factor`
- `validate(cfg)` → raises on the sanity rule from the testing-strategy note:
  **either per-trade cap must not exceed the daily cap**; also rejects
  non-positive values and a `leverage_factor < 1`.
- `to_risk_limits()` → the existing `engine_types.RiskLimits`, so
  `engine_risk.RiskPolicy` is unchanged.
- **Read-once semantics:** the config is resolved at start and passed into the
  process as CLI args. The running engine never re-reads it. Changing a cap
  requires a stop and a fresh start — exactly the note's scoping decision.

**New `engine_clock.py`** — one module owning every wall-clock rule, all in
IST (`ZoneInfo("Asia/Kolkata")`), all taking an injectable `now` so tests never
sleep:

| Function | Rule | Source note |
|---|---|---|
| `market_session_open(now)` | 09:15 ≤ t ≤ 15:30 | run-loop part 1 |
| `start_allowed(now)` | market open **and** t < 14:00 | run-loop part 1 |
| `new_entries_allowed(now)` | t < 14:00 | run-loop part 1 |
| `eod_squareoff_due(now)` | t ≥ 15:15 | run-loop part 4 |

Constants named and commented with their reasoning (14:00 entry cutoff, 15:15
chosen as a ~10-minute buffer before Zerodha's 15:25 auto-square-off and its
₹50+GST per-position penalty).

**Tests** — `tests/test_engine_config.py`, `tests/test_engine_clock.py`:
boundary minutes on both sides of 09:15 / 14:00 / 15:15 / 15:30, the
per-trade-exceeds-daily rejection, DST-free IST correctness.

---

## Phase 1 — `SqlitePositionStore`

The persistent memory everything else assumes. Implements the finalized
two-table schema verbatim.

**New `engine_store.py`:**

- `positions` table — exactly the 20 columns from the schema note:
  `trade_id` (PK, also the broker order tag — one identifier, no separate
  `broker_tag`), `setup_id`, `session_date`, `tradingsymbol`, `direction`,
  `vwap_classification`, `state`, `qty`, `entry_price`, `stop_price`,
  `risk_taken_rupees`, `entry_order_id`, `stop_order_id`, `exit_order_id`,
  `realised_pnl`, `is_live`, `run_id`, `candidate_json`, `extra_json`,
  `created_at`, `updated_at`.
- `position_events` table — `event_id` (INTEGER PK AUTOINCREMENT), `trade_id`,
  `at`, `event_type`, `payload_json`.
- **No `live_pnl` column.** Per the live-P&L note, unrealised P&L is never
  persisted.
- **No partial-fill/partial-exit columns.** `remaining_entry_qty`,
  `protected_qty`, `qty_model_version` and friends stay out until the exit
  sequence proves it needs them — the note's explicit instruction.

**Methods (satisfies and extends `engine_core.PositionStore`):**

- `open_positions()` → every row whose state is not in
  `{CLOSED, REJECTED, CANCELLED}`, candidate rehydrated from `candidate_json`.
- `save(position)` → `INSERT ... ON CONFLICT(trade_id) DO UPDATE`, stamping
  `updated_at`.
- `exists(setup_id)` → indexed lookup. **No cross-day scoping** — the system is
  intraday-only, every day starts fresh (schema note point 4).
- `append_event(trade_id, event_type, payload)` → append-only, never rewritten.
- `save_with_event(position, event_type, payload)` → **both writes in one
  SQLite transaction.** This is the load-bearing durability primitive: the
  "write intent before calling the broker" mechanism is worthless if the state
  row and its event can disagree after a crash.
- `closed_today(session_date)` → rows for the daily-loss sum.
- Read helpers for the API: `list_positions(session_date)`,
  `list_events(trade_id)`.

**Durability (schema note point 3):** default journal/synchronous settings —
commit waits for disk. `synchronous=OFF`, `journal_mode=MEMORY` and every other
"faster" knob are forbidden here and a comment in the file says why. WAL is
acceptable (still durable on commit) and matches the single-writer model.

**Concurrency:** single writer (the engine process); the API reads with
`mode=ro`, same pattern the existing store/API already use.

**Tests** — `tests/test_engine_store.py`: round-trip of every state,
`exists()` dedup, event ordering, `open_positions()` excluding all three
terminal states, `save_with_event` atomicity (simulated failure leaves neither
write), reopening a closed file recovers state, candidate JSON round-trips
without loss.

---

## Phase 2 — Entry submission, and prioritization on collision

The 9-step sequence, built as its own module so `engine_core` stays a thin
orchestrator.

### 2a. Two more columns through the handoff (for prioritization)

`live_continuation_decisions` already carries `breakout_candle_volume` and
`avg_prior_3_1m_volume` — and `TRIGGER_WITH_VWAP_SQL` already joins that table
as `d`. So this is **two added SELECT columns, no new join**:

- `trading_engine_types.TriggerCandidate`: add
  `breakout_candle_volume: Optional[int] = None`,
  `avg_prior_3_1m_volume: Optional[float] = None` (defaulted → additive and
  safe, same reasoning that made `vwap_classification` safe).
- `trading_engine_handoff.TRIGGER_WITH_VWAP_SQL`: add `d.breakout_candle_volume`,
  `d.avg_prior_3_1m_volume`; populate them in `fetch_triggered_with_vwap_since`.
  The existing missing-vwap-table fallback path must also fill them (it selects
  from `TRIGGER_SQL`, which does **not** have them) — so add them to
  `TRIGGER_SQL`'s projection too, or `None`-fill in the fallback. Chosen:
  add to both, single code path, and `fetch_triggered_since` keeps its exact
  current return shape because the new fields are defaulted.
- `fetch_triggered_since` and `fetch_vwap_classification` keep working
  unchanged for the old engine.

### 2b. Prioritization (`engine_priority.py`)

`rank_candidates(candidates)` — a pure sort function, no I/O:

1. **VWAP tier first, always.** ACCEPT ranks above LIMITED. An ACCEPT trigger
   can never lose a slot to a LIMITED one in the same window.
2. **Within a tier, by breakout strength** — `breakout_candle_volume /
   avg_prior_3_1m_volume`, descending. Missing or zero denominator ranks last
   within its tier (never crashes, never wins by accident).
3. **FIFO otherwise** — `created_at` ascending is the final tiebreak, and is
   the *only* ordering outside genuine same-window collisions.

Explicitly not implemented: risk-per-share/stop-tightness as a quality signal
(rejected in the notes — a tight stop only means more shares fit, and biases
toward choppy names). No general scoring engine.

`ingest_triggers()` becomes: fetch → filter out `store.exists(setup_id)` →
`rank_candidates(...)` → `handle_trigger` each in rank order.

### 2c. The entry sequence (`engine_entry.py`)

One function, `submit_entry(...)`, executing the notes' 9 steps in order:

1. **Stop price** — `trading_engine_risk.structural_stop_price(direction,
   swing_high, swing_low, tick_size, buffer_ticks)`. Computed once. Never
   recomputed off the fill, never moved to make a risk number fit.
2. **Quantity** — `engine_sizing.RiskCappedSizing.decide(entry_price=trigger
   price, stop_price=step 1, risk_cap_rupees=tier cap, available_capital,
   leverage)`. Already implemented and verified (₹110/₹107/₹900 → 300, risk-bound).
   `qty == 0` → skip, reason `sized_to_zero`.
   **`available_capital_rupees` = `remaining_capital` from Phase 0**, i.e.
   `total_capital − margin_used`, computed from our own persisted records — not
   `broker.available_margins()`. Reasons: it is a pure local read (no network
   call inside the sizing path, so nothing is added to the tick's hot path), it
   can never be `None` and block an entry on a broker hiccup, and it is the same
   number the desk displays. The sizing formula then multiplies by
   `leverage_factor`, so at the defaults a flat account sizes against
   ₹3,00,000 × 5 = ₹15,00,000 of buying power, and a position holding ₹1,00,000
   of margin leaves ₹2,00,000 × 5 = ₹10,00,000 — which is exactly the notes'
   "available capital is what's left after other open positions," and is why no
   separate notional cap is needed.
   `broker.available_margins()` is still used, but only as the read-only
   **margin pre-flight in step 3** — a go/no-go check against broker truth
   immediately before sending, not the sizing input.
3. **Recheck gates right before sending** — entries-not-stopped,
   `engine_clock.new_entries_allowed(now)`, daily-loss not breached, feed
   healthy, one-position-per-symbol, and a read-only **margin pre-flight**
   (`broker.order_margins(...)`) so insufficient margin is discovered as its own
   query rather than inferred from a placement response.
4. **Durable intent before the broker call** — `save_with_event(position in
   PENDING_ENTRY, "entry_intent", {qty, stop, cap, tier})`. Non-negotiable: this
   is what turns a crash mid-submission into reconciliation instead of a silent
   double-order or a lost position. The `trade_id` written here *is* the tag.
5. **MARKET order** — `broker.place_market_mis(tradingsymbol, transaction_type,
   quantity, tag=trade_id)`. Never LIMIT. No reprice-if-the-world-moved logic
   exists anywhere, by design.
6. **Three-way classification of the response:**
   - success → state `ENTRY_SUBMITTED`, record `entry_order_id`, continue.
   - clear, definite rejection (invalid qty, insufficient margin, any error the
     broker guarantees means never-created) → state `REJECTED` with the broker's
     exact reason, event `entry_rejected`, stop. No retry with the same numbers,
     no reconciliation — there is no ambiguity.
   - ambiguous (timeout, dropped connection, `EntryAcceptedVisibilityUnknown`,
     any error without a never-created guarantee) → **do not assume either
     outcome.** `broker.orders_by_tag(trade_id)` resolves it to exactly one of
     two things: nothing at the broker (treat as never happened; safe to retry
     fresh next tick) or placed/filled (proceed identically to success). No
     third "found but pending" case is handled separately — a market order
     settles essentially instantly.
7. **Real fill** — read the broker's actual average fill price and filled
   quantity. `real_risk_per_share = |real_fill − stop|`,
   `risk_taken_rupees = qty × real_risk_per_share`, persisted to the top-level
   column.
8. **1.5× cap check** — `ABNORMAL_SLIPPAGE_MULTIPLE = 1.5`, applied to *this
   trade's tier cap*, so ₹900 → ₹1,350 and ₹450 → ₹675 automatically.
   - ≤ 1.5× → accept, log the real number, go to step 9.
   - > 1.5× → **skip the stop entirely.** Immediately hand off to the shared
     flatten action (Phase 6) with reason `abnormal_slippage_flatten`, and flag
     the trade for manual review.
9. **Protective stop** — `broker.place_slm(..., trigger_price=the exact
   structural price from step 1, tag=...)`, then state `PROTECTED`.
   Normal-slippage path only.

Not implemented, deliberately, per the notes' explicit v1 rejections: moving the
stop to fit the cap; trimming quantity to enforce the cap exactly; bumping an
open position to free capital; LIMIT orders for LIMITED-tier trades.

**LIMITED uses this identical code path** — the only difference is which cap
enters step 2, which scales step 8 automatically.

**Tests** — `tests/test_engine_entry.py` against `FakeBroker` plus fault
injection: ACCEPT and LIMITED cap selection; each of the three response buckets;
ambiguous-then-found-filled vs ambiguous-then-absent; slippage just under and
just over 1.5× for both tiers; intent row present before the broker call (assert
ordering, not just the end state); `qty == 0`; every gate in step 3 blocking
independently. `tests/test_engine_priority.py`: ACCEPT beats LIMITED regardless
of volume ratio; volume ordering within a tier; missing-volume rows sort last;
FIFO tiebreak; unchanged order when everything ties.

---

## Phase 3 — Continuous broker reconciliation

The real job of `_drive_open_orders`, per the part-5 note: not "did my order
fill" but **"ask the broker what's actually true right now and make local
records match, every tick, for the entire life of every open position."**
Startup reconciliation is not a separate mechanism — it is this, running for the
first time.

**New `engine_reconcile.py`:**

- `fetch_broker_truth(broker)` → **one batched read per tick**:
  `broker.list_net_positions()` (net MIS qty by symbol, and the `pnl` field per
  position from `/portfolio/positions`) plus `broker.list_orders()`. Returns a
  `BrokerTruth` snapshot object. This is the only network read in the
  reconciliation path; nothing in it loops per-position over the network.
- `reconcile(position, truth)` → a pure decision function returning an intent:
  - `qty` differs from ours → adopt the broker's number.
  - stop order missing when we expect one → re-place (back to `ENTERED`).
  - stop order exists at a different trigger price (a human edited it in Kite)
    → **adopt the broker's price into our record.** This is what makes a
    manually-edited stop later firing "just a normal `stop_hit` with the correct
    price already in hand."
  - `ENTRY_SUBMITTED` and the entry order now shows filled → apply the fill and
    hand to the post-fill steps 7-9 of Phase 2.
  - net qty is zero for a position we believe is open → **hand to the exit
    finalization path (Phase 5)**, which does the attribution lookup.
- **Boundary:** only positions the engine recognizes by its own `trade_id` tag.
  Unrelated manual activity in the same Kite account is ignored entirely.

**Tests** — `tests/test_engine_reconcile.py`: quantity drift adopted; stop
deleted in Kite → re-place intent; stop price edited in Kite → adopted, not
overwritten; entry fill noticed; flat position routed to exit; an untagged
unrelated position ignored; and a **rate-limit assertion** — a counting fake
broker proves reconciliation of N open positions performs exactly one batched
read regardless of N. That last test is the mechanical guard on the batching
rule.

---

## Phase 4 — Protection

`_ensure_protection` narrows to one job: any position in `ENTERED` gets
`broker.place_slm(...)` at its structural stop, then `PROTECTED`.

Handles `SlPlaceAcceptedVisibilityUnknown` (the broker already raises it): bind
the accepted `order_id` durably and let the next tick's reconciliation confirm
visibility — never invent OPEN state, never place a second stop.

`tests/test_engine_protection.py`: happy path; the visibility-unknown path
placing exactly one stop across two ticks; a broker rejection leaving the
position `ENTERED` and retried next tick (never silently abandoned).

---

## Phase 5 — Exit to CLOSED

**New `engine_exit.py`.** Both paths converge on one finalization.

**Path A — passive.** The standing stop fired on its own; reconciliation
noticed net qty hit zero with nothing sent by us.

**Path B — active.** We decided to exit. Four triggers, one implementation
(Phase 6).

**Attribution — look it up, never assume** (the corrected rule):
`identify_closing_order(position, broker)` fetches the broker's recent
orders/trades for that symbol and finds the order that actually executed the
close.

| Matched order | Reason tag |
|---|---|
| our recorded `stop_order_id` | `stop_hit` |
| an exit order we sent, by trigger | `eod_squareoff` / `daily_loss_breach` / `manual_close` / `abnormal_slippage_flatten` |
| nothing we recognize | `manual_broker_intervention` |

The old assumption "no exit order from us → must have been our stop" is
explicitly not implemented — a human can close a position with an order that has
nothing to do with ours.

**Unified finalization — identical for every path and reason:**

1. Read the **real** fill price and quantity from whichever order actually
   executed the close. Never a planned or target price.
2. `realised_pnl` from the real entry fill and the real exit fill. Both actual
   numbers. Long: `qty × (exit − entry)`; short: `qty × (entry − exit)`.
   The recorded stop price is only ever a target and is never used here.
3. `append_event(trade_id, "closed", {reason, exit_fill, exit_qty,
   closing_order_id, realised_pnl})`.
4. `positions` row → `state = CLOSED`, `realised_pnl` set. One transaction with
   step 3.

**Tests** — `tests/test_engine_exit.py`: stop filling *worse* than its trigger
price → P&L from the real fill, not the stop level (this is the specific bug the
note exists to prevent); each reason tag attributed from a matching order id;
unrecognized order → `manual_broker_intervention`; a manually-edited stop firing
→ `stop_hit` at the edited price; long and short P&L signs.

---

## Phase 6 — The one flatten action (shared by four triggers)

`engine_exit.flatten(position, reason, broker, store)`:

1. **Cancel the existing stop first, and only proceed once the cancellation is
   confirmed.** Without this, the stop and the flatten can both execute and
   leave the account accidentally net short. The cancel uses the same three-way
   response discipline as every other broker call — ambiguous cancel →
   reconcile before sending anything.
2. `broker.flatten_mis(...)` — market order, full quantity, with a tag distinct
   from the entry tag so entry reconciliation can never confuse the exit order
   (the `BrokerPort` contract already requires this).
3. State `EXIT_SUBMITTED`; the next tick's reconciliation sees flat and runs
   Phase 5's finalization with this reason.

Its four callers, all reusing it unchanged: abnormal slippage (Phase 2 step 8),
EOD square-off at 15:15, daily-loss breach, manual `CLOSE_POSITION` / `KILL_ALL`.

`tests/test_engine_flatten.py`: cancel precedes flatten (ordering asserted);
an unconfirmed cancel blocks the flatten; a position with no live stop skips
straight to the flatten; ambiguous cancel resolved by reconciliation.

---

## Phase 7 — Daily-loss breach

`engine_risk.RiskPolicy.check_daily_loss` already sums **realized** losses only
— which is exactly the decided trigger, so no arithmetic changes. It is fed from
`store.closed_today(session_date)`, so a restart mid-day recomputes the same
number from persisted history.

**New `engine_breach.py`** (or a small block in `engine_core`, single caller):

- On breach: entries paused **permanently for the session** (realized losses
  can't decrease, so there is no un-breaching), then `flatten(...)` every
  currently-open position with reason `daily_loss_breach` — **all of them, no
  exceptions, including ones currently green.** The rejected "let the winner
  ride" exception is not implemented.
- **Naturally idempotent, no flag or counter.** The action is always "close
  everything currently open," recomputed fresh each tick; closed positions stop
  appearing in that set permanently, so repeated ticks do progressively less
  work until there is nothing left.
- **Auto-stop only once square-off is confirmed complete** — not the instant
  breach is detected. Stopping at detection would leave positions mid-close with
  nobody confirming completion. So: pause and begin closing immediately → keep
  running and watching → when every previously-open position is confirmed
  `CLOSED`, terminate (Phase 9's intentional-stop path, reason
  `daily_loss_breach`).

Forward-looking "what if every open position hits its stop" projection is
**not** a trigger here — that concern stays in entry-time sizing, per the note.

`tests/test_engine_breach.py`: sub-cap does nothing; at-cap closes everything
including a green position; repeated ticks stay idempotent; auto-stop fires only
after the last position reaches `CLOSED`, not before; entries stay off even if a
later tick recomputes.

---

## Phase 8 — Command handling

**New `engine_commands.py`**, an `engine_commands` table alongside `positions`
and `position_events` in the same SQLite file: `command_id` (PK), `at`, `kind`,
`trade_id` (nullable), `payload_json`, `status`
(`pending`/`applied`/`rejected`), `applied_at`, `result_json`, `actor`.

Written by the API when a human clicks; read once per tick by the engine — the
same cheap once-a-second polling shape already used for triggers. No new
architecture, no IPC, no sockets.

**START is not a command.** Starting has a chicken-and-egg problem — there is no
running loop to read a "please start" row — so it belongs to Phase 9's process
launch.

**The final, deliberately minimal command set:**

- **`STOP` / `START`** — a lightweight entries toggle, **not** process
  termination. `STOP` blocks new entries only; the engine keeps running,
  reconciling, and listening. `START` re-enables them. Both re-clickable freely
  **before 14:00**; after 14:00 `START` is refused (the same window rule, applied
  to every click, not just the day's first), while the engine keeps running
  normally with entries simply off. **No PAUSE** — dropped as redundant.
- **`CLOSE_POSITION`** (needs `trade_id`) — Phase 6 flatten, reason
  `manual_close`. The engine keeps running normally afterward; closing positions
  one by one carries no implication that the day is over.
- **`KILL_ALL`** — the third door into the same room as breach and EOD: close
  everything → confirm every position `CLOSED` → auto-stop. Session permanently
  over.

Not built: a "close everything but keep trading today" action distinct from
`KILL_ALL` — repeated `CLOSE_POSITION` already covers de-risking without ending
the day.

**Explicit non-trigger:** manually closing every position does **not** cause
auto-termination. Zero open positions is also the normal state at 09:15.
Auto-stop is tied to exactly three named triggers, never to "happens to be flat."

**This supersedes the part-1 STOP design.** That earlier version gave STOP
protection-aware graceful termination; STOP now never terminates the process at
all. The only three ways the process ends itself: breach, 15:15 EOD, `KILL_ALL`.

`tests/test_engine_commands.py`: each command applied exactly once (a second
tick does not re-apply); `START` refused after 14:00 while the loop keeps
running; `STOP` leaves reconciliation running; `CLOSE_POSITION` on an unknown
trade_id rejected with a reason, not crashing; `KILL_ALL` reaching the same
auto-stop state as a breach.

---

## Phase 9 — The runnable process

**New `run_execution_engine.py`** — the entrypoint, modeled on
`live_trading_engine.py`'s argparse/daemon shape but composing the new modules.
`live_trading_engine.py` itself is not modified.

Args: `--session-date`, `--run-id`, `--trading-db` (the new store),
`--live-db`, `--status-file`, `--per-trade-cap`, `--limited-per-trade-cap`,
`--daily-loss-cap`, `--total-capital`, `--leverage`, `--live-orders`.
Wires: `SqlitePositionStore`, `FeedMonitor`, `RiskPolicy` (from
`SessionRiskConfig`), `RiskCappedSizing`, `KiteBroker` or `FakeBroker`,
`candidate_source = partial(fetch_triggered_with_vwap_since, live_db,
created_at_gte=start_floor)`.

**Clean-start floor:** only triggers from the moment of start onward are
eligible. `created_at_gte` = process start instant (same intent as the old
`_signal_created_at_floor`). An accidental late start can never fire on a stale
already-passed opportunity.

**Tick interval: 1.0 second.** Justified in the notes against Kite's published
limits (Quote 1/sec but up to 500 instruments per call, order placement 10/sec,
400 orders/min) and against the tightest safety constraint — it caps the
unprotected window after a market fill at ~1 second.

**`VWAP_WAIT_SECONDS` stays 2.0**, now for the load-bearing reason: 2× the tick
interval. A comment records that if the tick interval ever changes this must be
recalculated as ~2× the new interval, not left stale.

**Drift, not clock alignment** (part 2): after each tick, sleep the remainder of
the second if any is left; if the tick overran, start the next one immediately
with no extra delay. Ticks stay ~1s apart relative to each other; absolute clock
time may shift later across the day. Chosen because clock alignment would
*skip* a tick outright on overrun, and skipping a check is worse than running one
late. `next_sleep_seconds(tick_started, tick_finished, interval)` is a pure
function so drift is unit-tested without sleeping.

**Per-step isolation with tiered escalation** (part 3). Each step in `tick()` is
wrapped **individually** — never one try/except around the whole tick — so a
malformed trigger row can never silence protection for a real position:

| Step | Consecutive-failure threshold | Why |
|---|---|---|
| `_ensure_protection` | **3** (~3s) | every second is a position with zero protection |
| `_drive_open_orders` | **5** (~5s) | delays protection, one step removed from raw exposure |
| `ingest_triggers` | **10** (~10s) | risks a missed trade, not open risk |

A single failure: log, skip that step this tick, continue. On reaching a
threshold: surface it loudly (heartbeat `last_error` + a prominent Execution Desk
state, in the spirit of the old engine's pattern), and for `_ensure_protection`
or `_drive_open_orders` **auto-pause new entries** as a precaution until a human
looks. Existing positions keep being retried regardless — pausing only ever
affects new entries.

**Tick step order:** feed health → read commands → continuous reconciliation →
post-fill steps / protection → daily-loss check (+ breach square-off) → EOD
check (15:15) → ingest triggers (gated by entries-allowed). Reconciliation runs
before anything that makes a decision, so every decision that tick is made
against broker truth.

**EOD at 15:15:** flatten every still-open position, then the same
close-everything → confirm-all-`CLOSED` → auto-stop sequence.

**Heartbeat** (`engine_status.py`): written every tick to the status file —
`run_id`, `session_date`, `state`, `updated_at`, `tick_count`, open-position
count, unprotected count, realized loss today, remaining daily room, entries
allowed, `last_error`, escalations. **No P&L field** — that goes through Phase 11
instead, keeping the heartbeat scoped to exactly what it already does.

**Intentional-stop note — the last thing written before any deliberate exit:**
`{"stopped_on_purpose": true, "reason": "daily_loss_breach" | "eod_squareoff" |
"kill_all", "at": ...}`. Crash detection then becomes unambiguous: heartbeat
quiet **with** the note → normal, healthy, done for the day. Heartbeat quiet
**without** it → genuine crash, alert loudly. Without this, either every normal
end-of-day stop false-alarms (training a human to ignore real alarms) or every
stop is assumed fine and a real crash is missed.

**Crash detection is by absence only** — a crashed process cannot report its own
crash. Staleness threshold 5-10s (well beyond the 1s rhythm); the API computes it
from heartbeat age. **No automatic restart**: a real bug would crash-loop
silently, and a crash mid-order-submission must not be compounded by a blind
restart. Make the silence loud; wait for a human. A manual restart is already
safe because it re-runs the full reconciliation.

**Single-instance guard — chosen for zero cost inside the tick loop.** The PID
lives in the heartbeat file, which is already being written every tick, so
recording it adds **no extra write, no extra file, no extra store row, and
nothing at all to the engine's hot path**:

- At spawn, the **API** writes the PID into the heartbeat file (one write, in the
  API process, before the loop exists).
- Each tick, the engine stamps its own `pid` into the heartbeat it is writing
  anyway — free, and it self-heals if the spawn-time write was lost.
- A start click checks two things: is a PID recorded, and is that process
  actually alive right now — `os.kill(pid, 0)`, a signal-free liveness probe
  that costs microseconds and never touches disk. Alive → refuse. Absent, or
  recorded-but-dead → safe to launch fresh.

This is also the more *accurate* option, not just the faster one: a stale PID
file that outlives its process is the classic failure mode of a separate lock
file, and checking real process liveness rather than trusting the record is what
prevents a dead engine from blocking a legitimate restart forever. Liveness is
the same question crash detection answers, so both read one artifact.

**Kill-It-All restart rule:** before 14:00, restarting after a `KILL_ALL` is
allowed. After 14:00 it is permanent for the day — and self-enforcing anyway,
since a fresh process recomputes today's persisted realized losses at startup and
re-refuses if actually breached.

`tests/test_engine_runloop.py`: `next_sleep_seconds` drift arithmetic including
overrun; one step raising does not prevent the others running (assert the others
ran, don't just assert no crash); each tier escalating at its own threshold and
resetting on success; auto-pause on the two dangerous steps only; heartbeat
written every tick; the stop-note written before an intentional exit and absent
on a simulated crash; clean-start floor excluding a pre-start trigger.

---

## Phase 10 — API surface

**New `api/routers/execution.py`**, prefix `/execution`, registered in
`api/main.py` alongside — never replacing — the existing `/trading-engine`
router. Nothing existing is edited, so `tests/test_frontend_route_contracts.py`
and every current API test stay green untouched.

| Endpoint | Purpose |
|---|---|
| `GET /execution/status` | heartbeat-derived: state, alive/crashed/stopped-on-purpose (+ reason), tick age, entries allowed, escalations, `last_error`, realized loss, remaining daily room, session caps in force |
| `GET /execution/positions` | `positions` rows split into open / closed / rejected, plus live P&L per position and the aggregate (Phase 11) |
| `GET /execution/positions/{trade_id}/events` | the `position_events` diary — the review surface the testing-strategy note asks for |
| `GET /execution/preflight` | the three start preconditions evaluated now: market hours, before 14:00, observation runner active; plus "is an engine already alive" |
| `POST /execution/start` | validates `SessionRiskConfig` (incl. per-trade ≤ daily), re-checks all three preconditions and the single-instance guard, then spawns the process |
| `POST /execution/commands` | enqueues `STOP` / `START` / `CLOSE_POSITION` / `KILL_ALL` into `engine_commands` |
| `GET /execution/commands/{id}` | applied / rejected / pending, for click feedback |

**Start precondition 3 — the observation runner must be active.** The execution
engine has no independent market connection; everything it knows comes from what
the observation runner writes to the shared DB, so "runner down" and "feed stale"
are the same fact. Refusing start with its own clear message is a friendlier
front door than starting into a permanently-stale-feed state that silently pauses
forever — but it does **not** replace the feed-staleness check, which remains the
safety net for the runner dying *after* start (when no click happens to refuse).

All three preconditions **refuse outright with a clear reason** — never queue and
wait for the window to open.

`tests/test_execution_api.py`: each precondition refusing independently with its
own message; the cap sanity check rejected at the API boundary; double-start
refused while alive and allowed after the PID is dead; command enqueue and
status; crashed-vs-stopped-on-purpose distinguished from heartbeat + note;
existing `/trading-engine` routes still registered and unchanged.

---

## Phase 11 — Live P&L

**Never written to `positions`.** It is entirely derived from whatever Kite
reports at an instant, so a lost or stale value is regenerated correctly by the
very next tick's fetch. Persisting it every tick for every position would be pure
overhead for a number nobody needs once a position closes.

**No new Kite call.** The Phase 3 reconciliation fetch already hits
`/portfolio/positions`, which returns a `pnl` field per position — "net returns
on the position," the same number Kite's own site shows. We **read that field
directly** rather than recomputing from last price and average price, which
guarantees exact parity with the Kite site by construction (copying their number,
not re-deriving it) at zero extra cost.

**Not routed through the heartbeat.** The heartbeat's job stays exactly what it
already is. Delivery: the engine writes the per-tick in-memory P&L map to its own
small JSON live-mark file — **atomically, temp file + `os.rename`**, so a reader
never catches a half-written file — which `GET /execution/positions` reads and
returns with a clear `as_of` timestamp, so the UI greys out a stale number
instead of showing a confident wrong one. A store row was considered and
rejected: paying a SQLite commit every tick for a value that is deliberately
never persisted is the one write this design has no reason to make.

**Total live P&L** is free alongside it: the sum of the individual `pnl` values
from the same per-tick fetch.

`tests/test_engine_live_pnl.py`: the broker's `pnl` field surfaces unmodified
(no re-derivation); aggregate equals the sum; nothing is written to `positions`;
a stale `as_of` is reported as stale rather than silently served.

---

## Phase 12 — Execution Desk tab, redesigned

**Scope is surgical.** Blast radius, verified in the tree:

- `frontend/src/App.tsx` — **one line** (261-262) swapping which component the
  `'trading'` tab renders.
- **New** `frontend/src/components/execution/` — the new desk and its parts.
- **New** `frontend/src/hooks/useExecutionEngine.ts`.
- **Additive only** in `frontend/src/api/client.ts` and `api/types.ts` — new
  functions and types appended. `fetchTradingControl` and `fetchTradingSetups`
  stay exactly as they are (`tests/test_frontend_route_contracts.py` asserts
  their presence and endpoints).
- `TradingEnginePage.tsx` and `useTradingEngine.ts` are left in place but unused
  for the duration of this phase, then deleted in Phase 13 along with the rest of
  the old stack. Keeping them alive for one phase means the tab is never dead
  mid-build and the redesign lands as a reviewable change on its own.

**Not touched, per your instruction:** `StationConsoleShell.tsx` (header, nav
tabs, clock, broker/feed pills), `PreMarketChecklistPage.tsx` and everything in
`components/checklist/`, `RadarHeatMap`/`RadarStreamWorkstation`/`RadarTable`
(Observation), `TopAppBar`, `AppSidebar`, `index.css` tokens.
`PrivateStatusStrip.tsx` is dead code (imported nowhere in `App.tsx`) and stays
dead and untouched.

**Design language — inherited, not invented.** The redesign uses the existing
`@theme` tokens in `index.css` verbatim (`--color-primary #005db7`,
`--color-positive #006c4a`, `--color-negative #ba1a1a`, `--color-background
#f8f9ff`, surface-container ramp, `--color-outline-variant #e5e7eb`), Inter +
JetBrains Mono via `.font-data`, `.label-caps` for every micro-label,
`.custom-scrollbar`, `.terminal-row` zebra striping, and the checklist tab's card
vocabulary (white card, 1px `outline-variant` border, caps micro-label above a
mono value). No new palette, no new font, no new radius scale — so the tab reads
as the same product as the Checklist and Observation tabs.

**Layout and composition are mine to design freely.** The inherited part is
strictly the *vocabulary* — colors from the existing `@theme` tokens, Inter +
JetBrains Mono, `.label-caps`, and enough shared card/table idiom that the tab
belongs to the same product. Everything else (density, grouping, hierarchy,
whether a thing is a card or a strip or a table, how the danger zone is set
apart, spacing and rhythm) I'll choose for what this specific screen needs: it
is a live risk console, so state and exposure read at a glance and destructive
actions are hard to hit by accident. The structure below is my starting
composition, not a constraint to hold to if something reads better while
building it.

**Structure, top to bottom:**

1. **Session control bar.** Engine state pill driven by real states —
   `STOPPED` / `STARTING` / `RUNNING · entries on` / `RUNNING · entries off` /
   `SQUARING OFF` / `STOPPED · <reason>` / **`CRASHED — NO HEARTBEAT`** as an
   unmissable red banner (the loud silence the notes demand). Tick age shown in
   mono so a stalling loop is visible.
2. **Start panel (visible only when stopped).** Three editable caps — per-trade
   ACCEPT, per-trade LIMITED, daily loss — plus total capital. Live client-side
   sanity check mirroring the server's (per-trade ≤ daily) with the Start button
   disabled and the reason shown. The three preconditions from
   `GET /execution/preflight` rendered as a small checklist with per-item reasons,
   so a refusal is understood before clicking, not after.
3. **Confirmation step before launch** — a modal restating the exact numbers
   ("per-trade risk ₹X ACCEPT / ₹Y LIMITED, daily cap ₹Z — start?"). The
   fat-finger guard the notes specifically ask for, in both directions: starting
   tiny when meaning to trade normally, or full size when meaning to validate
   cheaply.
4. **Risk strip.** Realized loss today vs daily cap (with a proportional bar so
   proximity to breach is visual), remaining daily room, open-position count,
   unprotected count, **total live P&L** with its `as_of` age and a greyed
   treatment when stale, and the capital block from Phase 0 — **total capital
   (editable while stopped), margin used, remaining capital, buying power**.
   These are the derived numbers sizing itself used, read from the same
   definitions, so the desk can never show a capital figure that disagrees with
   what the engine sized against. At the defaults: total ₹3,00,000, buying power
   ₹15,00,000.
5. **Open positions table.** Symbol, direction, qty, real entry fill, stop
   (with a marker when it was adopted from a broker-side edit), state, risk
   taken vs its tier cap, live P&L, and a per-row **Close** button
   (`CLOSE_POSITION`). Unprotected rows keep the existing loud red treatment —
   that pattern already works and is worth carrying over.
   **No trail-stop controls, no auto-trail checkbox** — trailing does not exist
   in this engine, and shipping a control that silently does nothing would be
   worse than shipping none.
6. **Closed positions table.** Symbol, qty, entry fill, exit fill, realized P&L,
   and the **reason tag** from Phase 5's attribution — including
   `manual_broker_intervention`, which is exactly the case a human most needs to
   see surfaced.
7. **Skipped / rejected table.** Reason, with readable labels for
   `vwap_reject`, `vwap_unavailable`, `sized_to_zero`, broker rejections.
8. **Per-position event drawer.** Click a row → the `position_events` diary in
   time order. This is the review surface the testing-strategy note describes
   ("pull up the diary, check it against Kite's own order history") — it needs to
   exist in the UI for that workflow to be pleasant.
9. **Danger zone, visually separated at the bottom.** `STOP`/`START` entries
   toggle (with `START` disabled after 14:00 and the reason shown), and
   **Kill It All Now** behind a typed-confirmation modal that states plainly what
   it does: closes every position and ends the session permanently.

**Polling:** `useExecutionEngine` at 1s while running (matching the engine's own
cadence), backing off when the tab is hidden, and pausing when stopped. Command
clicks are optimistic with `GET /execution/commands/{id}` reconciling the real
outcome, so a rejected command never looks applied.

**Verification:** `npm run build` clean; browser-verified in the preview with
console and network checked, and screenshots at desktop and narrow width.
`verify-release-parity.cjs` will now intentionally differ on the Execution Desk
tab — and that is the point of the change. Its value becomes a **regression guard
on the four untouched tabs**, which must still match the immutable reference
snapshot byte-for-byte; the parity note's own wording ("later intentional UI
changes should be reviewed against their own acceptance criteria") covers this.
The parity script's Execution Desk fixture will need the new `/execution/*`
shapes added so the run doesn't error on an unhandled path.

---

## Phase 13 — Delete the old engine

Runs **after** Phase 12 so the Execution Desk is never dead: the new engine and
the new desk are already working end-to-end when the old one comes out. That also
keeps the deletion a single, self-contained commit that `git revert` restores
cleanly if anything was missed — deleting first would force the rewrite and the
removal into one unreviewable change, with no working desk in between.

### Keep — shared foundation the new engine imports

These are not "old engine," they are the broker/handoff layer the rebuild is
built on. **Not touched:**

| Module | Why it stays |
|---|---|
| `trading_engine_broker.py` | `BrokerPort`, `FakeBroker`, `KiteBroker`, `parse_timestamp_text`, the visibility-unknown exceptions. `engine_core` already imports it. |
| `trading_engine_types.py` | `TriggerCandidate`, `BrokerOrder`, `MarginQuote`, `PositionQuote`, `STOP_ORDER_TYPES`, and the constants `api/admin_config/defaults.py` reads. |
| `trading_engine_handoff.py` | `fetch_triggered_with_vwap_since` — the trigger read path. |
| `trading_engine_quotes.py` | `TouchQuote`, `age_seconds`, used by the broker layer. |

### Extract, then delete

`trading_engine_risk.py` — the new engine needs exactly one function from it,
`structural_stop_price`. Move that function (and only it) into a new
`engine_stop.py`, with its `continuation_features.price_to_ticks` /
`ticks_to_price` imports, plus its own focused test. Then delete
`trading_engine_risk.py`: everything else in it is `TradeRecord`-coupled sizing
and staged-R trailing math that the notes explicitly replaced.

### Delete — Python modules (12)

`trading_engine_cycle.py` (5961), `trading_engine_store.py` (1229),
`trading_engine_preview.py`, `trading_engine_report.py`,
`trading_engine_risk.py` (after the extraction above), `trading_engine_loss.py`,
`trading_engine_control.py`, `trading_engine_ownership.py`,
`trading_engine_paper.py`, `trading_engine_trail_profile.py`,
`trading_engine_v1_paper_clean_start.py`, `live_trading_engine.py`.

### Delete — API layer

`api/routers/trading.py`, `api/queries/trading.py`, `api/schemas/trading.py`,
`api/services/trading_engine_runner.py`,
`api/services/trading_engine_start_lock.py`; unregister the `/trading-engine`
router in `api/main.py`. Phase 10's `/execution/*` router fully replaces it.

**One real coupling to rework, not delete — `api/routers/admin.py`.** It imports
`TradingEngineStore` in three places: `_enqueue(kind)` (line ~44, behind
`/admin/trading/pause` and `/admin/trading/resume`), `load_snapshot(...)` in
`_config_response` (~56), and the `arm_session` endpoint (~191).

- `/admin/trading/pause` → enqueues `STOP` in the new `engine_commands` table.
- `/admin/trading/resume` → enqueues `START`.
- `_config_response`'s `engine_running` / `accepting_triggers` → read from the
  new heartbeat instead of `load_snapshot`.
- `arm_session` → **deleted.** There is no arm concept in the new command set,
  and nothing in the frontend calls it (`client.ts` has no `armSession`).

Repointing rather than removing is deliberate: `AdminConsolePage.tsx` reads
`entries_paused` and drives pause/resume, and the Diagnostics tab is off-limits
for edits — so the endpoints keep their exact request/response shape and the
Diagnostics tab keeps working with **zero frontend changes**.
`api/admin_config/defaults.py` needs no change at all, since the constants it
imports live in the kept `trading_engine_types.py`.

### Delete — tests (~20 files)

`tests/test_trading_engine_*.py` (cycle, api, paper, report_metrics, risk,
handoff, trail_profile, loop, stage2_api, stage2_control, and the wp11-wp110
series), `tests/test_v1_owner_workflow.py`,
`tests/test_v1_paper_clean_start.py`, `tests/test_vwap_v2_fakebroker_validation.py`,
`tests/engine_lifecycle_fixture.py`, `tests/rehearse_host_migration.py`.

**Rewrite, don't delete:** `tests/test_frontend_route_contracts.py` currently
asserts `/trading-engine/control`, `/trading-engine/setups`,
`/trading-engine/trades/{trade_id}/audit` and `/trading-engine/report` are
registered, and that `fetchTradingControl` / `fetchTradingSetups` exist in
`client.ts`. Repoint every one of those assertions at the `/execution/*`
equivalents and the new client functions. The two observation assertions
(`fetchSectorMap`, `fetchSessionClock`) stay exactly as they are.

### Delete — frontend

`TradingEnginePage.tsx`, `hooks/useTradingEngine.ts`,
`PrivateStatusStrip.tsx` (already dead — imported nowhere), and the now-unused
`/trading-engine/*` functions and `Trading*` types in `client.ts` / `types.ts`.
`verify-release-parity.cjs`'s fixture loses its `/trading-engine/` and
`/trading/control` branches and gains `/execution/*`.

### Data, not code

Deleting `trading_engine_store.py` does **not** delete the paper-ledger SQLite
file — past paper trades stay on disk, untouched and still readable by hand. The
new engine writes to its own store path. No migration is attempted: there is
nothing in the old ledger the new engine needs, and inventing a migration would
be inventing a coupling we just removed.

### Acceptance

- `grep -rn "trading_engine_cycle\|trading_engine_store\|live_trading_engine"`
  over `*.py`, `api/`, `tests/` and `frontend/src/` returns **nothing**.
- `python3 -c "import api.main"` clean; full suite green with the deleted tests
  gone and no import errors from what remains.
- Every Execution Desk capability still works against the new stack, and the
  Diagnostics tab's pause/resume still works with no frontend diff.
- Line count removed: roughly 9,000+ lines of Python plus ~20 test files.

---

## Phase 14 — Verification gates

Run at the end of every phase, not just at the end:

```bash
python3 -m py_compile engine_*.py run_execution_engine.py api/routers/execution.py
python3 -m pytest tests/ -q
cd frontend && npm run build
```

Per-phase acceptance:

- Full suite green. **Through Phases 0-12 the rebuild is purely additive**, so
  every pre-existing old-engine test must still pass untouched — if one breaks
  before Phase 13, the new code reached somewhere it shouldn't have. From Phase
  13 on, the old tests are gone by intent and the green bar is the new suite plus
  everything unrelated (observation, checklist, auth, admin).
- `tests/test_import_boundaries.py` green: no `engine_*.py` module imports a
  market-data module, and nothing in signal detection imports the engine.
- The batching assertion in `tests/test_engine_reconcile.py` green — the
  mechanical proof that the rate-limit rule holds.
- No new `NotImplementedError` or `TODO(port)` left behind in a shipped path.

---

## What this plan deliberately does not do

Each of these is a decision already recorded in the notes, not an omission:

- **Trailing** — clean slate for a future session; nothing from the old staged-R
  approach is assumed to carry over.
- **Paper-mode side-by-side harness** — dropped entirely. The old engine is not
  being kept running, so there is nothing to compare against. `FakeBroker`
  survives as an internal test tool only, with **no user-facing paper-mode
  switch** in the desk. Real validation is genuine `KiteBroker` calls sized tiny
  via the editable caps — same code path as full size, which is the whole point.
- **Quantity trimming, stop relocation, position bumping, LIMIT entries for
  LIMITED tier** — all explicitly rejected for v1.
- **Multiple orders per role in the schema** (cancel-and-replace after Kite's
  25-modification ceiling) — parked for the trailing redesign.
- **Graduation criteria to full size** — judgment-based by design, deliberately
  not automated.
- **Migrating old paper-ledger data** — nothing in it the new engine needs
  (Phase 13).

---

## Decisions settled (2026-09-22)

The four code-shape choices this plan originally left open, now closed:

1. **Sizing capital = `total_capital − margin_used`, computed locally**, not
   `broker.available_margins()`. Defaults ₹3,00,000 total at 5× → ₹15,00,000
   buying power; total capital editable at start. No network call in the sizing
   path, can never be `None`, and the desk shows the same number sizing used.
   `available_margins()` stays as the read-only pre-flight go/no-go. (Phase 0,
   Phase 2 step 3.)
2. **PID lives in the heartbeat file**, which is written every tick anyway →
   zero added cost to the tick loop, no extra artifact. Liveness via
   `os.kill(pid, 0)`, so a stale record can never block a legitimate restart.
   (Phase 9.)
3. **Live P&L goes to its own small JSON file**, atomically written (temp +
   rename) once per tick, separate from the heartbeat — the notes are explicit
   that the heartbeat's scope must not grow. A store row was rejected: a SQLite
   commit per tick for a value that is deliberately never persisted is the one
   write this design has no reason to pay for. (Phase 11.)
4. **Full design freedom on the Execution Desk**, constrained only to the
   existing color tokens and typography so it reads as the same product.
   (Phase 12.)

Anything new that comes up mid-build gets raised rather than silently
defaulted — including the small code contributions I'll hand over at genuine
business-logic forks (P&L sign conventions, reason-tag precedence when two
orders could both explain a close).
