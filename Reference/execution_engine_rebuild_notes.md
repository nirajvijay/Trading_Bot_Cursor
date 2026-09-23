# Execution Engine Rebuild — Deferred Design Decisions

Running log of decisions made during design brainstorming for the execution
engine rebuild (paper/live entry, sizing, order lifecycle, trailing, exits —
signal detection stays unchanged). Each entry here is **agreed conceptually
but not yet implemented** — the plan is to build them together once the
core engine skeleton (trigger ingest, VWAP handoff, sizing, entry
submission, order lifecycle) is functional end-to-end.

---

## Entry prioritization when multiple triggers compete for limited capital/slots

**Decided:** 2026-09-21

**Problem:** With ~100 stocks being watched, capital and open-position slots
are shared across all of them. If more than one stock triggers within the
same short window, today's engine has no way to choose the better one — it
processes candidates strictly in the order the database returns them
(`ORDER BY created_at ASC`), so whichever trigger happened to be written
first wins, purely by timing accident.

**Rule agreed on:**

1. **VWAP classification tier ranks first, always.** An ACCEPT-classified
   trigger must never lose a capital/slot allocation to a LIMITED-classified
   trigger that arrived in the same short window (same tick / same second).
2. **Within the same tier, rank by breakout strength** — using
   `breakout_candle_volume` relative to `avg_prior_3_1m_volume`, which
   signal detection already computes per setup (it's how the pattern
   qualified as a trigger in the first place). Not currently passed through
   the handoff to the execution engine — would need adding to
   `TriggerCandidate` / `fetch_triggered_with_vwap_since` in
   `trading_engine_handoff.py`.

**Explicitly rejected:** ranking by risk-per-share / tightness of stop as a
quality signal. A tighter stop just means more shares fit under the same
risk cap — it doesn't mean the setup is better, and using it as a ranking
signal would bias toward stocks with naturally choppy, tight recent ranges.

**Scope of the rule:** this is a narrow exception, not a general scoring
engine. Outside of genuine same-window collisions, the default stays FIFO.
Capital already tied up in earlier, still-open trades is a separate,
already-handled case (existing concurrency/capital caps in
`trading_engine_risk.size_new_trade`) — nothing new needed there.

**Status:** not yet implemented. Belongs in `engine_core.py`'s trigger
ingestion (`ingest_triggers`/`handle_trigger`), alongside entry submission
and sizing.

---

## Entry submission sequence for an ACCEPT-tier trigger

**Decided:** 2026-09-21

**Problem:** "Submit the entry order" isn't one step — it's a chain of
decisions about order type, crash safety, and how to react to slippage and
uncertain broker responses. Worked through end-to-end for the ACCEPT case
(LIMITED follows the same sequence, just with the smaller risk cap).

**Agreed sequence:**

1. **Compute the stop price.** Structural: one tick below the swing low
   (long) or one tick above the swing high (short) — see
   `trading_engine_risk.structural_stop_price`. Fixed once, from market
   structure. Never recalculated off the entry price, and never moved later
   to make a risk number fit (see "explicitly rejected" below).
2. **Compute an estimated quantity** using the risk cap for this VWAP tier
   (₹900 ACCEPT / ₹450 LIMITED) and the trigger price — smallest of
   risk-based / capital-based / notional-based share counts (existing
   `size_new_trade` logic).
3. **Recheck the entry gates right before sending** — pause state, daily
   loss cap, concurrency/one-per-symbol/max-setups-per-day — since time has
   passed since the trigger fired.
4. **Write a durable "about to submit" intent record before calling the
   broker.** Non-negotiable: this is what lets a crash mid-submission lead
   to reconciliation instead of a silent double-order or a silently lost
   position.
5. **Submit a MARKET order** (not LIMIT) for the computed quantity, tagged
   with the trade's ID. Chosen specifically to avoid needing any
   "reprice if the world moved" logic — a market order doesn't wait for a
   price, so the earlier LIMIT-drift/skip-tolerance idea is dropped in
   favor of this.
6. **Classify the broker's response into exactly one of three buckets** —
   the dividing line is "do we know for certain, or don't we":
   - **Clear success** → go to step 7.
   - **Clear, definite rejection** (invalid quantity, insufficient margin,
     or any error the broker guarantees means "never created" — e.g.
     margin should be checked as its own read-only pre-flight query, not
     inferred from the placement call's response) → record the trade as
     rejected with the broker's exact reason, stop. No retry with the same
     numbers. No reconciliation needed — there is no ambiguity to resolve.
   - **Genuinely ambiguous** (timeout, dropped connection, an error with no
     "never created" guarantee) → do not assume either outcome. Query the
     broker's order book directly using the trade's tag. That check always
     resolves to one of exactly two things: the order never reached the
     broker (treat as if nothing happened — safe to retry fresh), or it was
     placed and filled (drop into step 7, identical to a clean success).
     There is no third, "found but still pending" state worth handling
     separately — a market order settles essentially instantly.
7. **Once filled, read the real fill price** and compute the real risk:
   `real risk-per-share = |real fill price − stop|` (stop unchanged from
   step 1), `real total risk = quantity × real risk-per-share`.
8. **Compare real total risk to 1.5× the intended cap** (e.g. ₹1,350 for an
   ACCEPT trade capped at ₹900 — locked in as the threshold, tunable later
   if it proves too strict/loose in paper mode):
   - **≤ 1.5× cap (normal slippage):** accept it, log the real number for
     later review, proceed to step 9.
   - **> 1.5× cap (abnormal):** skip the stop entirely — immediately send a
     market order to flatten the whole position, and flag the trade for
     manual review (distinct reason, e.g. `abnormal_slippage_flatten`).
9. **Place the protective stop-loss order** at the exact structural price
   from step 1 (normal-slippage path only). Position is now protected.

**LIMITED-tier triggers use this identical sequence — no separate code
path.** The only difference is which risk cap gets plugged into step 2
(₹450 instead of ₹900), which automatically scales step 8's abnormal-
slippage line down with it (1.5× of ₹450 = ₹675). Stop placement (step 1),
gate rechecks (step 3), order type (step 5), and response handling (step 6)
are all identical regardless of tier. Considered and rejected: using a
LIMIT order instead of MARKET for LIMITED trades specifically, on the
theory that lower-conviction trades should protect entry price more — not
adopted, since it reintroduces the repricing/staleness problem MARKET
orders were chosen to eliminate, for the weaker half of trades. Cross-tier
competition for capital is already covered by the entry-prioritization
rule above (ACCEPT always outranks LIMITED in a same-window collision), so
no additional capital-guarding behavior is needed here either.

**Explicitly rejected — moving the stop to fit the risk cap after bad
slippage.** E.g. real fill ₹119, structural stop ₹107, cap ₹900 on 300
shares → forcing the arithmetic to fit would mean a stop at ₹116, just ₹3
from entry. That stop has no relationship to market structure anymore and
is essentially guaranteed to get hit by ordinary noise almost immediately —
it converts a slippage problem into a near-certain, meaningless loss while
destroying the entire reason a structural stop exists. The correct way to
keep the exact-cap guarantee (if ever wanted) is trimming *quantity* while
leaving the *real* structural stop untouched — not moving the stop itself.
Not adopted for v1 (see next entry).

**Explicitly rejected for v1 — trimming quantity to enforce the risk cap
exactly on ordinary slippage overshoots.** Adds a second order (the trim)
that must be confirmed before the stop can be placed at all, which
lengthens the window where a filled position sits completely unprotected —
paid on every trade, to correct overshoots that are usually small. Accepting
normal slippage gets the stop on faster. Revisit only if paper-mode data
shows this is a real, recurring problem.

**Explicitly rejected for v1 — bumping an existing open position to free
capital/slots for a better new setup arriving later.** Forces closing a
trade that hasn't hit its stop or target purely because something else
looked better a moment later; introduces whipsaw risk and a live
"how good is this trade right now" scoring subsystem with no evidence yet
that it's needed. Do nothing for v1; review skip logs after real paper-mode
usage before reconsidering. (Reserving capital headroom for high-conviction
setups, and letting winners release capital via partial profit-booking, were
raised as safer alternatives if this ever needs revisiting.)

**Status:** not yet implemented. Belongs in `engine_core.handle_trigger`'s
entry path (currently a `TODO(port)` stub) and the order-lifecycle
follow-up steps (`_drive_open_orders`, `_ensure_protection`).

---

## REJECT / UNAVAILABLE classifications, and the wait-ceiling number

**Decided:** 2026-09-21

**REJECT and UNAVAILABLE are pure skips — no new mechanism needed.** Unlike
ACCEPT/LIMITED, neither leads to an entry, so none of the entry-submission
machinery above (sizing, stop, slippage check, crash safety) applies.
Handling is exactly what's already in `engine_core.handle_trigger`: record
the candidate as skipped with the specific reason (`vwap_reject` or
`vwap_unavailable`), stop. Nothing further to design here.

**The 2-second wait ceiling (`VWAP_WAIT_SECONDS` in `engine_core.py`,
used when a candidate's classification is still `None`) is parked, not
finalized.** Reasoning: VWAP classification has no meaningful *computation*
delay — it's calculated synchronously off the same trigger event, using
data already tracked continuously. The only real gap is structural: the
trigger and the classification are still two separate database writes (to
`live_continuation_decisions` and `live_vwap_qualifications`), not one
atomic write, so there's a genuinely tiny window — milliseconds, from write
contention at most — where a read could catch one without the other. That
means the right ceiling isn't an arbitrary constant; it should be sized as
a small multiple of however often the engine actually polls (its tick
interval), which hasn't been decided yet (see the not-yet-built "wire it
all together into a runnable process" item). **Action: revisit this number
when the run loop / tick interval is designed — set it to roughly 2-3× the
chosen tick interval, not a standalone value carried over from the old
engine's 2-second constant.**

**Status:** not yet implemented / not yet finalized. Ties into
`engine_core.py`'s `VWAP_WAIT_SECONDS` constant and whatever run-loop
design decides the tick interval.

---

## Run loop tick interval, Kite rate limits, and the finalized VWAP wait ceiling

**Decided:** 2026-09-21

**Tick interval: 1 second.** This is the new execution engine's own poll
cadence — how often `ExecutionEngine.tick()` runs, checking the shared
trigger database and the broker. Not to be confused with the observation
runner (signal detection, a separate always-running process reacting to
live market ticks continuously) or VWAP qualification (no loop at all,
computed synchronously off the trigger event) — neither of those is
affected by this number.

**Reasoning:**
- The tightest safety constraint is the unprotected window after a market
  order fills (no stop placed until the next tick notices the fill) — this
  directly caps that window at ~1 second worst case.
- Checking Kite Connect's official rate limits
  (https://kite.trade/docs/connect/v3/exceptions/) confirmed 1 second of
  headroom is safe: **Quote 1 req/sec, Historical candle 3 req/sec, Order
  placement 10 req/sec, all other endpoints 10 req/sec** (also: max 400
  orders/minute, 10 orders/sec, 5000 orders/day system-wide).
- This isn't a high-frequency system (pullback/continuation setups play out
  over minutes), so noticing a fill or a price move ~1 second later doesn't
  meaningfully change trade quality.
- 1 second is imperceptible as a delay for human-issued control commands
  (pause/flatten) on a dashboard.

**Critical batching rule (must hold from day one, not a later optimization):**
the Quote endpoint's 1 req/sec limit sounds alarming until you note it
accepts **up to 500 instruments in a single call** (`/quote`; up to 1000 for
`/quote/ltp` and `/quote/ohlc` — see
https://kite.trade/docs/connect/v3/market-quotes/). **Every price check must
be one single batched call per tick covering everything needed that tick
(all open positions, or the full watchlist) — never one call per position
in a loop.** Checking prices position-by-position would blow past the 1/sec
limit the moment more than one position is open. This is a hard design
constraint on `_apply_trailing` (and any other place that needs live
prices), not an optional efficiency tweak.

**Order-modification limit to remember for trailing (not solved now, just
flagged):** Kite allows a maximum of **25 modifications per order** before
it must be cancelled and re-placed fresh. A long-running trade with frequent
small trailing-stop adjustments could theoretically hit this. Needs a
cancel-and-replace fallback in the trailing design when that's built —
tracked here so it isn't discovered the hard way later.

**VWAP wait ceiling — finalized at 2 seconds (2 ticks).** Previously parked
pending the tick interval decision (see prior entry). With a 1-second tick,
2 seconds means: check now, check once more a second later, then give up
if it's still not there. This is now a principled number tied to the tick
interval (2× tick), not a value copied from the old engine's constant — it
coincidentally lands on the same number, but for a different, load-bearing
reason. If the tick interval ever changes, this ceiling should be
recalculated as roughly 2× the new interval, not left at a stale "2".

**Status:** decided, not yet implemented. `engine_core.py`'s
`VWAP_WAIT_SECONDS` should be set to 2 (already matches). The run loop
itself, the batched quote-fetch helper, and the trailing
cancel-and-replace-on-25-modifications fallback are all not yet built.

---

## Run loop: start/stop design (part 1 of the run-loop design series)

**Decided:** 2026-09-21

This is the first of several run-loop questions being worked through
one at a time (start/stop, tick-overrun behavior, crash-inside-a-tick
behavior, EOD square-off, startup reconciliation — the rest are still
pending).

**New-entry cutoff vs. full stop are different things.** At **2:00 PM**,
the engine stops accepting *new* entries, but keeps watching and managing
any already-open positions (fill-tracking, protection, trailing) — it does
not force-close anything at 2:00, and does not stop running. (Exact detail
of what "watching and managing" covers post-2:00 to be discussed further
later — flagged as open by the user, not yet specified.)

**Start = Arm. There is no separate "watch only, don't trade" mode.** The
instant the engine starts, it is live — the first qualifying trigger it
sees will go through the full entry sequence for real. No rehearsal period.

**Only triggers from the moment of start onward are eligible.** Anything
that triggered before the engine was started is ignored, even if it's
still sitting in the database — same intent as the old engine's
clean-start floor (`_signal_created_at_floor`: later of process start time
and a durable clean-start boundary). Prevents an accidental late start from
immediately firing on a stale, already-passed opportunity.

**Stop button behavior depends on protection state, not a single fixed
action:**
- **All open positions already protected** (stop-loss live at the broker)
  → stop immediately. Positions are left open and untouched — their
  stop-loss is a standing order at the broker/exchange, independent of
  whether our engine process is running, so it keeps working. The only
  thing that stops happening is further improvement (trailing won't tighten
  the stop any further; it freezes wherever it last was).
- **Something is mid-flight, not yet protected** (order submitted but
  unconfirmed, or filled but stop not yet placed) → do not stop instantly.
  Finish that one specific in-progress step first (confirm the fill, place
  the stop), then stop. This should be quick — finishing a step already
  underway, not waiting for the whole trade to close.

**Start is only allowed within a specific window, not simply "market
hours."** Two separate constraints, both must hold:
1. Real NSE cash-session hours: 9:15 AM – 3:30 PM IST. Outside this window,
   there's no live market to trade in at all.
2. **Also blocked after 2:00 PM specifically**, even though that's still
   technically within market hours — since new entries are already cut off
   at 2:00 (see above), a session started after 2:00 could never take a
   single trade, so it's disallowed as pointless rather than merely
   harmless.

Clicking start outside the allowed window **refuses outright with a clear
reason** — it does not queue up and wait for the window to open. If you
want to trade today, you click start once the window is actually open.

**Status:** decided, not yet implemented. Belongs in whatever process
wraps `engine_core.ExecutionEngine` with the actual run loop (not yet
built) and its start/stop control surface.

---

## Run loop: tick-overrun behavior — drift (part 2 of the run-loop design series)

**Decided:** 2026-09-21

**When a tick takes longer than the 1-second interval, the loop drifts
rather than staying clock-aligned.** Concretely: after a tick finishes, if
there's time left before the next scheduled second-mark, wait out the
remainder as usual; if the tick overran, start the next tick immediately
with no extra delay. Ticks stay spaced close to 1 second apart *relative to
each other*, but the absolute clock time they land on can slowly shift
later over the day by however much total time was lost to overruns.

**Example:** loop starts 9:15:00.0. Tick 1 takes 0.3s → tick 2 starts on
schedule at 9:15:01.0. Tick 2 takes 1.4s (slow) → finishes at 9:15:02.4, so
tick 3 starts immediately at 9:15:02.4 instead of the "scheduled" 9:15:02.0
— a 0.4s offset that carries forward for the rest of the session (tick 4
lands at 9:15:03.4, not 9:15:03.0, etc).

**Why drift over staying clock-aligned:** nothing in this design depends on
hitting exact clock-seconds — only on checking roughly once a second.
Staying clock-aligned would mean *skipping* an entire tick outright when one
overruns (e.g., jumping straight from the 9:15:02.4 finish to the 9:15:03.0
mark, skipping a check entirely) rather than just running it slightly late.
Skipping a check is worse for us than delaying one, so drift is preferred.
Total drift accumulated over a full session should be small (a few seconds
at most, since overruns should be rare) and is irrelevant next to things
like the VWAP wait window or trailing responsiveness.

**Status:** decided, not yet implemented.

---

## Run loop: crash-inside-a-tick behavior, tiered by step (part 3 of the run-loop design series)

**Decided:** 2026-09-21

**Each step inside `tick()` fails independently — one step's bug must
never block the others.** `tick()` is already decomposed into separate
steps (feed check, `ingest_triggers`, `_drive_open_orders`,
`_ensure_protection`, `_apply_trailing`). Each should be wrapped in its own
error handling, not one big try/except around the whole tick. Reason: a
bug in a low-stakes step (e.g. a malformed row breaking `ingest_triggers`)
must not be allowed to also skip protection/trailing checks for
already-open, real-money positions that tick — the least dangerous part of
the loop should never be able to silence the most dangerous part.

**A single failure in any step:** log it, skip just that step for this
tick, continue normally otherwise. Treated as likely transient.

**Repeated (consecutive) failures in the *same* step escalate — but the
threshold is tiered by how much real exposure builds up per second the
step stays broken, not one flat number for everything:**

- **`_ensure_protection`** (placing the stop on a freshly-filled position)
  — tightest threshold, **3 consecutive failures (~3 seconds)**. Every
  second this stays broken is a real position sitting with zero
  protection at all — the most dangerous case, least patience.
- **`_drive_open_orders`** (checking whether an entry order filled) —
  **5 consecutive failures (~5 seconds)**. Indirectly delays protection
  (can't protect a fill we don't know happened yet), but one step removed
  from raw exposure.
- **`_apply_trailing`** and **`ingest_triggers`** — **10 consecutive
  failures (~10 seconds)**. Lower stakes: a broken trailing step just
  freezes an already-placed stop rather than removing protection; a broken
  trigger-ingest step just risks a missed trade, not open risk.

**On hitting a step's threshold:** make it loudly visible (not buried in a
log — something surfaced prominently, in the spirit of the old engine's
`last_error`/heartbeat pattern), and specifically for `_ensure_protection`
or `_drive_open_orders`, consider auto-pausing new entries as a precaution
until a human looks at it (existing positions keep being retried
regardless — pausing only affects new entries).

**Status:** decided, not yet implemented.

---

## Run loop: EOD square-off cutoff (part 4 of the run-loop design series)

**Decided:** 2026-09-21

**Every still-open position is proactively force-closed at 3:15 PM IST.**
Verified rather than assumed: Zerodha's current (2026) equity MIS
auto-square-off time is **3:25 PM** (revised up from 3:20 PM), and letting
the broker force-close a position instead of doing it ourselves costs a
real, avoidable penalty — **₹50 + 18% GST per position** — on top of likely
worse execution (every trader's leftover MIS positions get force-closed
simultaneously at 3:25, a rush of market orders hitting at once).
Sources: https://zerodha.com/z-connect/updates/changes-to-the-auto-square-off-timings-for-equity-and-fo
and https://support.zerodha.com/category/trading-and-markets/trading-faqs/market-sessions/articles/intraday-auto-square-off-timings

**Why 3:15 PM specifically:** a ~10-minute buffer before the broker's 3:25
PM forced cutoff, and it also avoids the last, typically choppier/thinner
minutes before close. Since new entries already stop at 2:00 PM, any
position still open at 3:15 has already had over an hour to play out —
this isn't cutting trades short.

**No new mechanism needed.** EOD square-off reuses the same "flatten"
action (market order, fully exit) already designed for the
abnormal-slippage case in the entry-submission notes above — just
triggered by the clock (3:15 PM) instead of by the 1.5x-cap risk check.
The broker's own 3:25 PM auto-square-off remains as a last-resort safety
net for anything that somehow slips through our own 3:15 PM pass, not the
primary mechanism.

**Status:** decided, not yet implemented.

---

## Run loop: continuous broker reconciliation, not a startup-only special case (part 5 of the run-loop design series)

**Decided:** 2026-09-22

**This entry revises "startup reconciliation" from earlier: it's not a
separate, one-time mechanism — it has to be the same thing the engine does
every tick, continuously, for as long as a position is open.** Two
scenarios exposed the gap in treating it as startup-only:

1. **A stop-loss order fills at a different price than the price we set
   it at.** A stop, once triggered, generally executes as a market order
   from that point, so on a fast move it can genuinely fill worse than the
   level we set. Same lesson as the entry-side slippage discussion, now on
   the exit side: **realized P&L must always be calculated from the
   broker's actual reported fill price — never from the price we originally
   set the stop at.** Our recorded stop price is only ever a target; the
   broker's real fill, once it happens, is the truth.

2. **A human manually edits an order/position directly in Kite while the
   engine is running normally — no crash, no restart, nothing to trigger a
   check.** If reconciliation only happens at two special checkpoints
   (post-submission, and once at startup), a manual change made mid-session
   would simply never get noticed. The fix: reconciliation against the
   broker's real state (quantity held, whether the stop order still exists,
   its actual price) must run **every tick, for every open position, always
   — not only when something is flagged uncertain.**

**Net effect: "startup reconciliation" isn't a separate system anymore —
it's just the same continuous, every-tick reconciliation, running for the
first time when the loop starts.** One mechanism, not two. This is the real
job of the not-yet-built `_drive_open_orders` step: ask the broker what's
actually true right now and make local records match it, every tick, for
the entire life of a position — not just "did my submitted order fill."

**Boundary:** this only applies to positions the engine recognizes by its
own trade tag. Unrelated manual activity in the same broker account (trades
that have nothing to do with this system) is ignored — the engine reconciles
what it's responsible for, not the whole account.

**Ties back to the batching rule (see the tick-interval/rate-limit entry
above):** checking the broker's real state every tick, for every open
position, is exactly why that rule matters — one batched call per tick
covering everything, never one call per position, or continuous
reconciliation would itself blow past the rate limit the moment more than
one position is open.

**Status:** decided, not yet implemented. Supersedes the "already
protected → light verification only" framing in the original startup-
reconciliation discussion — every open position gets the full
broker-truth refresh, every tick, not just the uncertain ones.

---

## Trailing removed for now — to be rebuilt from scratch

**Decided:** 2026-09-22

**All trailing-stop logic has been removed from the execution engine for
now**, including the ported `engine_trailing.py` module (which wrapped the
old engine's staged-R trailing math from `trading_engine_risk.py`) and its
wiring into `engine_core.py` (`TrailingPolicy` import/param, the
`_apply_trailing` step and its call in `tick()`). This was a deliberate
removal, not a placeholder gap: trailing is going to be **designed and
built from scratch** later, not carried over from the old engine's
approach.

**What this means concretely, until trailing is redesigned:** a position
that reaches `PROTECTED` stays there with a static stop — it has no
mechanism to tighten as price moves favorably. `ExecutionState.TRAILING`
still exists in `engine_types.py`'s state machine and `engine_orders.py`'s
transition table (harmless to leave — a valid future transition, just
currently unreachable since nothing produces it), so no state-machine
changes were needed, only the policy module and its orchestration wiring.

**Status:** done (code removed, tests updated, full suite still green).
Trailing design is a clean slate for a future session — nothing from the
old engine's staged-R approach should be assumed to carry over.

---

## Status check: what's designed, what's built, what's next

**Decided:** 2026-09-22 (a running status snapshot, not a single design
decision — update or replace this entry rather than piling up duplicates
as things move between these buckets)

**Fully designed (written up above), not yet built in code:**
- Entry submission for ACCEPT/LIMITED — the full 9-step sequence, the
  three-way broker-response classification, the 1.5x-cap slippage handling.
- The run loop itself — 1s tick, drift on overrun, tiered crash-handling
  thresholds, EOD square-off at 3:15 PM, continuous broker reconciliation.

**Explicitly deferred, removed from the current build:**
- Trailing — code pulled out entirely (see prior entry); to be designed
  from scratch in a future session.

**Not yet designed — genuinely next:**
1. **What makes a position actually exit and become `CLOSED`.** Stop-loss
   hits are the main path (continuous reconciliation notices the broker's
   qty go to zero), but the full sequence — recognizing the exit, computing
   final realized P&L from the real fill price, transitioning state — has
   never been walked through end to end.
2. **Daily-loss breach handling.** `engine_core.tick()` already notices a
   breach; "square off everything" is still just a `TODO(port)` comment,
   never designed as a real sequence.
3. **A real, persistent `PositionStore`.** Currently just a `Protocol`
   interface, no actual storage. Needed before startup reconciliation (see
   the run-loop notes above) means anything — there's nothing to reconcile
   against yet.
4. **Command handling** — how arm/pause/flatten actually reach the running
   engine from outside (a dashboard, a file, an API). We decided *what*
   these should mean, not *how* they're delivered.
5. **Wiring everything into one runnable process.** Everything designed so
   far is individual pieces (`engine_*.py` modules); nothing runs
   end-to-end yet. This is the "make it real" step.
6. **The paper-mode side-by-side harness** — running old and new engines
   against the same data and comparing, the agreed way to build trust
   before ever touching live money. Not started.

**Status:** tracking entry. Revisit and update whenever a batch of these
move from "not yet designed" into their own dated decision entries above.

---

## PositionStore design

**Decided:** 2026-09-22

The persistent memory of the execution engine — where positions actually
live on disk, so the engine isn't starting from total amnesia every time it
restarts. Everything in the run-loop notes above (startup reconciliation,
continuous reconciliation) silently depends on this existing.

**1. Technology: SQLite.** Reusing the same approach the old engine already
uses (`trading_engine_store.py`) — no reason to introduce new
infrastructure for this.

**2. Keep both a current-state row and a full event-history log per
position.** The current-state row (state, quantity, prices, order IDs)
answers "what's true right now" quickly and simply. The event-history log
is an append-only diary — a new line added every time something meaningful
happens (submitted, filled, protected, closed), never rewritten — so the
*path* a trade took is recoverable later, not just where it ended up.
Confirmed this isn't a meaningful runtime cost at our scale (a handful of
trades a day, a few extra small writes each) — the "cost" is a little more
code to maintain two things instead of one, not slower or heavier to run.

**3. Every save must be a genuinely durable, committed disk write before
returning — never a fast-but-risky buffered write.** This is what the
crash-safety "write intent before calling the broker" mechanism (from the
entry-submission notes above) actually depends on: if a save could return
"done" before the bytes are truly on disk, a crash in that gap would erase
the exact safety record that mechanism exists to protect. SQLite's default
commit behavior already does this correctly (waits for disk confirmation),
and it's cheap at our scale (milliseconds) — not a performance trade-off,
just the only correct option, so no risky "faster" settings should ever be
used here.

**4. No cross-day setup_id collision concern.** This system is
intraday-only — no position ever spans multiple days, everything is
squared off by 3:15 PM (see EOD square-off notes above) — so every trading
day starts genuinely fresh. `exists(setup_id)` dedup doesn't need special
cross-day scoping.

**5. Single reader/writer while running.** Only the execution engine
process itself touches the store during a live session — no concurrent
writer to design locking around. (A separate dashboard/reporting reader
later, read-only and not concurrent with trading decisions, is fine and
not a new concern — the same pattern the old engine's API already uses.)

**Status:** decided, not yet implemented. Concrete schema/table design and
the actual `PositionStore` implementation (replacing the current bare
`Protocol` in `engine_core.py`) are next.

---

## PositionStore schema — finalized

**Decided:** 2026-09-22

Two tables, per the current-state + event-history decision above. Leaner
than the old engine's `trades` table (~50 columns) — partial-fill/partial-
exit bookkeeping columns (`remaining_entry_qty`, `protected_qty`,
`qty_model_version`, etc.) are deliberately left out until the exit
sequence is actually designed, rather than guessed at now.

**Table 1 — `positions` (current state, one row per trade):**

| Column | Notes |
|---|---|
| `trade_id` (TEXT, primary key) | Also used directly as the broker order tag — one identifier, not a separate `broker_tag` column like the old engine. |
| `setup_id` (TEXT) | Pulled out of the candidate for direct `exists(setup_id)` checks. |
| `session_date` (TEXT) | Filtering/display; reinforces the intraday-only, same-day model. |
| `tradingsymbol` (TEXT) | Filtering/display. |
| `direction` (TEXT) | Filtering/display. |
| `vwap_classification` (TEXT) | Pulled out of the candidate for quick filtering/reporting (e.g. "how many LIMITED trades today") — same reasoning as setup_id/symbol/direction. |
| `state` (TEXT) | The `ExecutionState` value. |
| `qty` (INTEGER) | Current quantity held. |
| `entry_price`, `stop_price` (REAL, nullable) | The real numbers once known. |
| `risk_taken_rupees` (REAL, nullable) | The real post-fill risk computed in the entry-submission sequence (step 7-8 above) — compared against 1.5x cap. Surfaced at the top level since it's central to a decision we spent real design time on, not just buried in an event payload. |
| `entry_order_id`, `stop_order_id`, `exit_order_id` (TEXT, nullable) | For finding the right order at the broker during reconciliation. |
| `realised_pnl` (REAL, nullable) | Final P&L once closed. |
| `is_live` (INTEGER, boolean) | Paper vs. live — which `BrokerPort` implementation produced this trade. Needed to cleanly separate real trades from paper trades later, and for the paper-mode side-by-side comparison harness. |
| `run_id` (TEXT) | Which engine run/session produced this record — needed for the same side-by-side comparison work, and to disambiguate if the engine is restarted mid-day. |
| `candidate_json` (TEXT) | The entire `TriggerCandidate` as JSON — immutable reference data, not worth 14 separate columns. |
| `extra_json` (TEXT) | The flexible bag from `Position.extra` (skip reasons, ad hoc state) without needing schema changes for every small fact. |
| `created_at`, `updated_at` (TEXT) | First written / last touched. |

**Table 2 — `position_events` (append-only diary, many rows per trade):**

| Column | Notes |
|---|---|
| `event_id` (INTEGER, auto-increment primary key) | Ordering key. |
| `trade_id` (TEXT) | Which position this belongs to. |
| `at` (TEXT) | When it happened. |
| `event_type` (TEXT) | e.g. `entry_submitted`, `entry_filled`, `protected`, `abnormal_slippage_flatten`, `closed`, `skipped`. |
| `payload_json` (TEXT) | Event-specific detail (fill price, skip reason, etc.). |

**Deferred: whether `entry_order_id`/`stop_order_id`/`exit_order_id` as
single fixed columns are sufficient, or whether a trade could need multiple
orders per role over its life** (e.g. a cancelled-and-replaced stop after
hitting Kite's 25-modification limit) — explicitly punted to when the
trailing system is actually redesigned, not decided now.

**Performance note (confirmed, not a real concern):** four extra columns
add negligible bytes per row; at this system's actual scale (a handful of
trades/day) the whole `positions` table stays a trivial size indefinitely.
Not a tradeoff worth optimizing against.

**Status:** decided, not yet implemented. Next: the actual `PositionStore`
class implementing this schema (replacing the bare `Protocol` in
`engine_core.py`).

---

## Exit-to-CLOSED design

**Decided:** 2026-09-22

What actually makes a position stop being open and become `CLOSED`. Two
different ways this happens, plus a manual-intervention case that exposed
a real gap in the first version of this design — corrected below.

**Path A — passive: the standing stop-loss fires on its own at the
broker.** We're just watching (via continuous reconciliation); one tick we
notice the broker's real quantity for that symbol has dropped to zero,
without us sending anything new.

**Path B — active: we ourselves decide to exit and send a fresh order.**
Covers every case already designed separately: EOD square-off (3:15 PM),
a future daily-loss breach, a manual "close this position" click, and the
abnormal-slippage flatten from entry submission — all reuse the same
"flatten" action (market order, full quantity).

**New safety rule for path B: cancel the existing stop-loss order first,
and only send the flatten once that cancellation is confirmed.** If a
position already has a live stop at the broker and we also actively decide
to exit it, both could execute — a real risk of selling twice and ending
up net short by accident. The cancel itself uses the exact same three-way
response discipline as every other broker call (clear success / clear
rejection / genuinely ambiguous → reconcile before proceeding) — no new
mechanism, just applying the existing one here too.

**Attribution: look up which order actually closed the position — never
assume.** The first version of this design assumed "no exit order sent by
us → must have been our own stop." That's wrong: a human can place a
completely different order directly in Kite that closes the position,
unrelated to our recorded stop or any exit order we sent. The corrected
rule: when reconciliation notices a position went flat, fetch the broker's
actual recent orders/trades for that symbol and identify the real order
that executed the close, then:
- **Order ID matches something we recognize** (our recorded stop, or an
  exit order we ourselves just sent) → tag the reason accordingly
  (`stop_hit`, `eod_squareoff`, `daily_loss_breach`, `manual_close`,
  `abnormal_slippage_flatten`).
- **Order ID doesn't match anything we recognize at all** → tag it as
  `manual_broker_intervention` — a closure we didn't initiate and can't
  attribute to our own orders.

**Confirmed already covered, no new handling needed: manually changing the
stop-loss's price in Kite, and that modified stop later firing.** The
continuous-reconciliation rule (see the run-loop notes above) already
promises to adopt the broker's real current stop price every tick,
including a human-made edit. By the time a modified stop actually fires,
our own records already reflect the edited price — it's just a normal
`stop_hit` at that point, with the correct real price already in hand.

**Unified finalization sequence — same for every path and every reason,
once the closing order is identified:**
1. Read the real fill price/quantity from whichever order actually
   executed the close (never a planned or target price).
2. Compute realized P&L from the real entry fill and the real exit fill —
   both actual numbers, never theoretical trigger/stop prices.
3. Write a `closed` event to `position_events`, with the real numbers and
   the reason.
4. Update the `positions` row: `state` → `CLOSED`, `realised_pnl` set.

**Status:** decided, not yet implemented.

---

## Daily-loss breach handling

**Decided:** 2026-09-22

Builds directly on the exit-to-CLOSED design above — most of the work here
was already done there; this decides *when* it fires and *how broadly*.

**Trigger: realized losses only, not a forward-looking "worst case"
projection.** `RiskPolicy.check_daily_loss` sums only *realized* losses
from already-closed trades — actual, already-happened numbers — not a
projection of what would happen if every open position hit its stop. The
forward-looking "committed risk" concern is deliberately left to the
existing entry-gating logic (sizing already refuses new entries as
realized-plus-committed risk nears the cap). Actively force-closing
positions that haven't hit their own stop yet is a drastic action, and
should only ever fire on something factual and already-happened — not a
hypothetical.

**Once realized losses hit the cap (₹3000), it's permanent for the rest of
the session.** Realized losses can't decrease, so there's no "un-breaching"
— entries stay paused for the rest of the day once this fires.

**Close every currently open position — all of them, no exceptions, even
ones currently sitting green.** Deliberately rejected the tempting
exception of "let a currently-profitable one ride" — that reintroduces the
same kind of judgment call already rejected elsewhere (bumping, trimming).
The point of a hard daily cap is to stop taking risk of any kind the
moment it's crossed, not to selectively keep risk that currently feels
comfortable.

**Mechanism: identical to exit-to-CLOSED's active path** — cancel any
existing stop-loss order first, then send the market flatten, then the
same unified finalization (real fill price → real P&L → closed event
tagged `daily_loss_breach` → state `CLOSED`).

**Naturally idempotent — no "only do this once" tracking needed.** The
action is always "close everything currently open," checked fresh every
tick. A position that's already `CLOSED` simply stops appearing in that
set — permanently, since it can't reopen — so repeating the same check
every tick does progressively less work as things finish closing, until it
finds nothing left to do. No flag or counter required to avoid duplicate
action.

**Auto-stop, but only once the square-off is fully confirmed complete —
not the instant breach is detected.** At the moment of breach, positions
are still open and mid-closing; stopping the loop right then would leave
them being actively squared off with nobody watching to confirm
completion — the same unprotected-window danger we've been careful about
throughout. But once every previously-open position has actually reached
`CLOSED`, there's no more useful work left for the rest of the day, and
this is already covered by the stop-button rule from the run-loop notes
above ("instant stop is safe once everything is settled" — fully closed is
even more settled than merely protected). **So: pause entries and begin
closing everything immediately on breach → keep running and watching until
every position is confirmed `CLOSED` → then auto-stop**, exactly as if
`stop` had been clicked manually.

**Status:** decided, not yet implemented.

---

## Command handling — finalized

**Decided:** 2026-09-22

How a human tells an *already-running* engine to do something. Note:
START does not belong here — starting has a chicken-and-egg problem (no
running loop exists yet to check for a "please start" instruction), so
it's a process-launch concern that belongs to the not-yet-designed "wiring
into a runnable process" item, not command handling. Everything below
assumes the engine is already alive and ticking.

**Delivery mechanism: a shared `engine_commands` table** (alongside
`positions`/`position_events` in the schema above), written by the
dashboard/API when a human clicks something, and read once per tick by the
engine — the same cheap, once-a-second polling shape already used for
checking triggers. No new architecture needed.

**Final command set — deliberately minimal, no PAUSE button:**

- **STOP / START — a lightweight toggle, not a process-termination
  control.** STOP blocks *new* entries only; the engine keeps running,
  watching, reconciling, and listening for other commands regardless.
  START re-enables new entries. Both are re-clickable as many times as
  wanted **before 2:00 PM**. After 2:00 PM, START can no longer be pressed
  (matches the existing market-hours/2pm start-window rule — applies to
  every click, not just the first one of the day), but the engine keeps
  running normally regardless — new entries are simply already off from
  that point, whether via manual stop or the automatic 2pm cutoff. PAUSE
  was considered and dropped as redundant with this.
- **CLOSE_POSITION — close one specific position.** Uses the existing
  active-exit machinery (cancel stop, then flatten, then the unified
  finalization from the exit-to-CLOSED notes above), tagged
  `manual_close`. The engine keeps running normally afterward — no
  implication that trading is done for the day.
- **KILL_ALL ("Kill It All Now") — a manual emergency button.** This is a
  **third trigger** for the exact same "close everything → confirm every
  position is actually `CLOSED` → then auto-stop" sequence already built
  for daily-loss breach and the 3:15 PM EOD square-off (see those entries
  above) — nothing new to build mechanically, just a third door into the
  same room. Once pressed, the session is permanently over: every open
  position gets closed, and the engine terminates itself once that's
  confirmed complete, exactly like the other two triggers.
- **Explicitly not built: a "close everything but keep trading today"
  action** distinct from KILL_ALL. Considered and rejected as unneeded —
  closing one position at a time via CLOSE_POSITION already covers
  wanting to de-risk without ending the day.

**Revises the original STOP design from the run-loop notes above (part
1).** That version gave STOP protection-aware graceful-termination logic
(instant if everything's already protected, otherwise finish the
in-progress step first, then actually end the process). That design is
now superseded: STOP never terminates the process at all — it only
toggles whether new entries are allowed. The only ways the process
actually terminates itself are the three triggers converging on the
close-everything-then-auto-stop sequence: daily-loss breach, the 3:15 PM
EOD cutoff, or a manual Kill-It-All-Now click.

**Explicit non-trigger, to avoid ambiguity later:** manually closing every
open position (e.g. clicking CLOSE_POSITION repeatedly until none remain)
does **not** itself cause auto-termination. Having zero open positions is
also just the normal state at the start of the day — auto-stop is tied
specifically to the three named triggers, never to "happens to be flat
right now."

**Status:** decided, not yet implemented.

---

## Wiring into a runnable process

**Decided:** 2026-09-22

How clicking start actually causes a real, running program to exist, and
how it's kept safe and observable while it does. Frontend location note:
there is no separate "admin dashboard" — this all lives in the existing
**Execution Desk** tab (alongside the pre-market Checklist and Observation
tabs already in the frontend). "Dashboard" used in earlier discussion of
this topic meant the Execution Desk specifically, not a separate surface.

**The always-on API server is what launches the engine.** It's already
running whenever the Execution Desk is usable at all, so it's the natural
place to own "spawn the trading engine as a real, independent process" —
the engine can't run inline as part of handling one click, since it needs
to keep ticking for hours afterward.

**Start preconditions — three, all checked before allowing start, all
refusing outright rather than queuing and waiting:**
1. Real NSE market hours (9:15 AM - 3:30 PM) — see run-loop notes above.
2. Not after 2:00 PM — see run-loop notes above.
3. **The observation runner must currently be active.** The execution
   engine has no independent connection to the market at all — everything
   it knows about triggers, VWAP, and feed health comes from what the
   observation runner writes to the shared database. So "observation
   runner down" and "feed is stale" are the same underlying fact. Refusing
   start explicitly, with its own clear message, is better UX than letting
   it start into a permanently-stale-feed state that just silently pauses
   forever — but this is a *friendlier front door*, not a replacement for
   the feed-staleness check, which remains the safety net for the
   observation runner dying *after* the engine has already started (no
   "start" click happens in that case to refuse).

**Preventing two engines from ever running at once.** The API server keeps
a durable record of the current engine process's ID (a file, or a store
row). Before a start click does anything, it checks: is there a recorded
process ID, and is that process actually still alive right now? Alive →
refuse. Not recorded, or recorded-but-actually-dead → safe to launch
fresh. This check depends on knowing "actually alive," which is the same
question the crash-detection design below answers.

**Kill-It-All-Now restart rule:** before 2:00 PM, restarting after a
Kill-It-All-Now is allowed. After 2:00 PM, it's permanent for the day,
identical to a daily-loss breach (also naturally self-enforcing: a fresh
process's startup reconciliation would immediately recompute today's
persisted realized losses and re-refuse if actually breached, regardless
of whether a restart is attempted).

**How status is known, without a live connection to the running
process.** The engine writes its own status (state, last tick time, key
numbers) somewhere shared, every tick — the same heartbeat idea the old
engine already uses. The Execution Desk / API server only ever *reads*
this; it never needs to talk to the running engine process directly.

**Detecting a genuine crash — the whole program dying unexpectedly, not a
handled in-tick failure.** Everything designed earlier (tiered per-step
failure handling) assumes the program itself is still alive. A genuine
crash means the entire process stops existing — no handled error, just
gone — and critically, **a crashed process cannot report that it
crashed**, since there's no "it" left to report anything. The only way to
detect this is by its absence: if the heartbeat **stops updating** (no new
write for something like 5-10 seconds, well beyond the normal 1-second
rhythm), that silence itself is the signal — the same idea as a hiking
friend who's supposed to check in hourly going silent for three hours;
the silence is what tells you to worry, not a message asking for help
(which a genuinely crashed process couldn't send anyway).

**No automatic restart after a detected crash.** If the process died from
a real bug, restarting it immediately risks silently crash-looping forever
without a human ever finding out something is seriously wrong — or, if it
crashed mid-action (e.g. mid-order-submission), blindly restarting before
a human understands what happened risks compounding the problem. The
correct response is to make the silence loud and impossible to miss, and
wait for a human to look and manually restart — which conveniently
re-triggers the full startup reconciliation already designed, so a manual
restart after a crash is already safe by design.

**Distinguishing "stopped on purpose" from "crashed" — both are silence,
but only one is a problem.** The engine is *also* designed to intentionally
terminate itself every day (breach, EOD 3:15, or Kill-It-All-Now) — that's
success, not a problem. Without a way to tell these apart, either every
normal end-of-day stop would falsely alarm (training a human to ignore
real alarms over time), or every stop would be assumed fine and a real
crash would be missed. **Fix: the engine's very last action before an
intentional stop is to write a clear "stopping on purpose, here's why,
everything's fine" note.** The crash-detection logic then becomes: heartbeat
went quiet *with* that note present nearby → normal, healthy, done for the
day. Heartbeat went quiet with **no** such note → genuine crash, alert
loudly.

**Status:** decided, not yet implemented.

---

## Testing / paper-mode strategy — replaces the side-by-side harness

**Decided:** 2026-09-22

**The originally-assumed "paper-mode side-by-side harness" (running old and
new engines against the same data and comparing) is dropped entirely.**
The old engine is not being kept running in any capacity going forward —
only the new engine exists. There is nothing to compare it against, so a
comparison harness makes no sense. This supersedes the migration-path
default assumed at the very start of this rebuild (before any of the
detailed design work in this document).

**`FakeBroker` remains, but purely as an internal testing tool — never a
user-facing "paper mode" in the Execution Desk.** It's used in our own
automated tests to check the logic is correct (fast, deterministic, no
real stakes), but there's no switch in the UI to run live trading against
it operationally.

**Real validation happens via genuine `KiteBroker` calls, deliberately
sized tiny — not simulation.** Reasoning: a simulator can only prove *our
own logic* is correct; it can never prove the *actual Kite integration*
(real API behavior, real order confirmations, real timing, real rate
limits) behaves as assumed. Only real calls to the real API can validate
the wiring itself, separate from validating the trading logic.

**"Tiny stakes" is achieved entirely through existing configuration knobs
— no new mechanism, no special "validation mode" code path.** The daily
loss cap and both per-trade risk caps (ACCEPT/LIMITED, i.e. the ₹900/₹450
values used throughout the entry-submission design above) become editable
values, not fixed constants. Set them small to validate cheaply; set them
back to normal size when ready to trade for real — same code path either
way, which is the point: this proves the actual system that will eventually
trade full size, not a separate path that only proves itself.

**Scoping: these values are read once, at the moment `start` is clicked,
and stay fixed for that entire session.** Not adjustable while the engine
is running with open trades — to change them, stop (or let the session end
naturally via one of the auto-stop triggers) and start again with new
values. Avoids the ambiguity of changing a risk number mid-day while a
trade is already sized and open.

**Two safety additions on top of the editable caps:**
- **Sanity check before start is allowed to proceed:** per-trade cap must
  never exceed the daily cap — an obvious-mistake guard.
- **Confirmation step showing the exact values before actually launching**
  ("per-trade risk: ₹X (ACCEPT) / ₹Y (LIMITED), daily cap: ₹Z — start?") —
  a fat-finger guard against starting with the wrong number by accident in
  either direction (too small when meaning to trade normally, or too large
  when meaning to validate cheaply).

**Graduation criteria: judgment-based, not a fixed trade-count or
day-count rule.** Deliberately not automated — "I'll know it when I see
it" after watching enough real, small trades behave correctly.

**Verification workflow: manual and conversational, no new feature
built for it.** After a small live trade, the `position_events` diary
(from the PositionStore schema above) gets pulled up and reviewed —
checked against what Kite's own order history actually shows for that
trade — with Claude's help interpreting it, on request, whenever wanted.
Not a dedicated UI feature; an on-demand review process.

**Status:** decided. This closes out the sixth and final original roadmap
item (in revised form) — see the status-check entry above for the full
six-item list.

---

## Position sizing formula — finalized and implemented

**Decided:** 2026-09-22

Resolves the original `TODO(human)` in `engine_sizing.py` (the one piece
of business logic in the initial boilerplate deliberately left for manual
decision rather than defaulted). Two judgment calls, both settled:

**1. The risk cap covers price risk only — trading costs are additional,
on top of it, never subtracted from it.** `risk_per_share = |entry_price -
stop_price|` alone, with no brokerage/slippage folded in. A trade sized to
the cap can cost slightly more than the cap once real costs are included —
that's accepted as a separate, real expense, not something the cap is
meant to absorb. (Opposite of the old engine's approach, which combined
price risk + estimated cost into one number that had to fit under the
cap — a deliberate change, not an oversight.)

**2. No notional/aggregate-exposure cap — dropped entirely, not carried
forward.** `available_capital_rupees` already represents capital remaining
after other open positions are accounted for, so capital-based sizing
already inherently limits total exposure on its own; a separate notional
ceiling would just be double-counting the same limit two different ways.

**Formula, as implemented:**
- `risk_based_qty = floor(risk_cap_rupees / risk_per_share)`
- `capital_based_qty = floor((available_capital_rupees × leverage_factor) / entry_price)`
- `qty = min(risk_based_qty, capital_based_qty)`, `binding_constraint` names
  whichever of the two actually won ("risk" or "capital" — "notional" is no
  longer a possible value).

**Status: done — implemented and verified**, not just designed. Changed
`RiskCappedSizing.decide()` in `engine_sizing.py`, and removed the now-dead
`notional_based_qty` field from `SizeDecision` in `engine_types.py` rather
than leave an unused field behind. Compiles clean, all existing tests
still pass, and a hand-check (entry ₹110, stop ₹107, ₹900 cap → 300 shares,
risk-bound) matches the earlier worked example from the entry-submission
design above.

---

## Live P&L — direct push, not persisted, not through the heartbeat

**Decided:** 2026-09-22

**Live (unrealised, open-position) P&L is never written to the
`positions` table.** Unlike `realised_pnl`, it has no lasting value — it's
entirely derived from whatever Kite reports at a given instant, so if it's
ever lost or stale, the very next tick's fetch from Kite regenerates it
correctly regardless. Writing it durably every tick, for every open
position, would be pure overhead for a number nobody needs once a
position closes.

**Source: no new Kite call needed.** The continuous-reconciliation fetch
the engine already makes every tick (checking real quantity and stop
existence, per the run-loop notes above) hits Kite's
`/portfolio/positions` endpoint, which already returns a `pnl` field per
position — "net returns on the position," the same number Kite's own
website displays. Reading that field directly, rather than recomputing a
P&L formula ourselves from last price and average price, guarantees exact
parity with the Kite site by construction (copying their number, not
re-deriving it) and costs nothing extra — it comes back on the same call
already being made for safety reasons.

**Delivery: pushed directly to the frontend, not routed through the
heartbeat file at all.** The existing per-tick heartbeat (alive / crashed
/ stopped-on-purpose detection, per the "wiring into a runnable process"
notes above) is completely unchanged by this — P&L is not one of its
fields. Instead, the number the engine already has in memory each tick
gets delivered straight to the Execution Desk directly, with the
heartbeat's job staying scoped exactly to what it already does.

**Total live P&L** (aggregate across all open positions) is free to
compute alongside this — just the sum of the individual `pnl` values
already being read from the same per-tick fetch.

**Status:** decided, not yet implemented.

---

*(Add new dated entries above this line as further design decisions come
up in brainstorming, so nothing agreed on gets lost before implementation.)*
