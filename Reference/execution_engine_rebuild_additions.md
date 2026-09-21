# Decisions made during the build that aren't in the design notes

`Reference/execution_engine_rebuild_notes.md` settled the design. Implementing
it surfaced questions the notes didn't reach — mostly "what happens when this
particular thing fails". Everything here was decided while building, and each
one is listed with why, so any of it can be overruled cheaply.

Nothing below contradicts a decision in the notes. Where the notes were
explicit, the code follows them exactly.

---

## 1. Safety gaps the notes didn't cover

**A failed broker read is never acted on.** `BrokerTruth.ok` is false when the
positions or orders book can't be read, and `reconcile()` returns a no-op for
every position in that case. The notes said to adopt broker truth every tick;
they didn't say what to do when the fetch itself fails. Without this, a broker
outage looks exactly like every position having gone flat, and the engine would
close the whole book on an API hiccup. This is the single most dangerous
failure mode in the design and it has its own test.

**`PROTECTED` → `ENTERED` when the stop vanishes.** The notes said to re-place
a stop that disappeared from the broker. They didn't say the *state* had to
change. It does: "protected" means a stop is live at the broker, so a position
whose stop is gone is genuinely unprotected and must say so — otherwise the
unprotected count on the desk lies at exactly the moment it matters most.

**An entry that fills while being cancelled is not marked cancelled.** During a
square-off, unfilled entries get cancelled rather than flattened. If the cancel
response comes back `COMPLETE`, the order actually filled in the gap — so it is
recorded as `entry_cancel_raced_fill` and left for reconciliation to pick up as
a real position. Marking it cancelled would lose a live position.

**The exit order carries a different tag from the entry.** `flatten_tag_for()`
appends `-x`. The `BrokerPort` contract already required this, and the reason
turned out to be load-bearing: reconciliation's tag fallback looks for a
non-stop order under the trade id, so a same-tagged exit would be read as the
entry filling and would corrupt the entry price of a position that is actually
closing.

**A margin pre-flight that can't be read does not block the trade.** The notes
said margin should be its own read-only query rather than inferred from the
placement response. They didn't say what a *failed* read means. It fails open:
the placement's own definite-rejection path catches genuinely insufficient
margin, and refusing on a broker hiccup would silently drop good trades.

**Liveness needs both halves.** "Is the engine running" checks a fresh
heartbeat *and* that the recorded pid still exists. A fresh heartbeat from a
process that has since been killed would block a legitimate restart; a live
process with a silent heartbeat is wedged rather than working.

---

## 2. The state machine

The notes never enumerated the legal transition graph for the new paths, so
`engine_orders.ALLOWED_TRANSITIONS` gained five, each with a comment:

| Added | Why it's needed |
|---|---|
| `PENDING_ENTRY → REJECTED` | a clear, definite broker rejection before any fill |
| `ENTERED → EXIT_SUBMITTED` | abnormal slippage skips the stop and flattens; also a square-off firing before the stop is live |
| `ENTERED → CLOSED` | reconciliation finds it already flat — a human closed it while unprotected |
| `PROTECTED → ENTERED` | the stop vanished at the broker (see above) |
| `PROTECTED`/`TRAILING → CLOSED` | the passive path: the standing stop fired on its own |

---

## 3. Attribution details

**`select_closing_order` precedence.** The notes said attribution must be
looked up and never assumed, but not what to do when more than one completed
order could explain a close — the real case being a stop firing in the gap
between our cancel request and its confirmation. Resolution, in order:
earliest fill wins (a later fill opened *new* opposite exposure rather than
closing this trade); an order with no broker timestamp can't win on chronology;
a full-size fill beats a partial; and an unrecognized order wins an outright
tie, since a closure we didn't initiate is the fact a human most needs
surfaced.

**Every contender is recorded.** When several orders compete, all of them are
written onto the position so the collision reaches the diary instead of being
silently resolved away. A double exit leaves accidental opposite exposure and
is worth a human noticing.

**A seventh close reason: `UNATTRIBUTED`.** The notes listed `stop_hit`, the
four we initiate, and `manual_broker_intervention`. They didn't cover "flat at
the broker, but no completed order explains it". Rather than guess, that is
tagged honestly and no P&L is invented from a price we never saw.

---

## 4. Things kept deliberately separate

**`entries_stopped` and `entries_paused` are two flags, not one.** The notes
described the human's STOP toggle and the engine's self-protective pausing, but
not that collapsing them loses information. They mean different things: one is
a choice, the other is a symptom. The desk shows different text for each, and a
breach makes the pause permanent while STOP stays re-clickable.

**`absent` is a third liveness state.** The notes distinguished crashed from
stopped-on-purpose. "Never started today" is neither, and alarming about it
would be noise — so `assess()` returns `absent` for no heartbeat at all.

**Engine-level diary events use a `__engine__` pseudo trade id.** Breaches,
step escalations and shutdown milestones aren't about one position but belong in
the same append-only log. The notes didn't say where they go.

---

## 5. Filled-in specifics

- **Two more tick steps got failure thresholds.** The notes tiered three steps
  (protection 3, reconciliation 5, ingest 10). The implementation also has a
  commands step and a square-off step: commands got 10 (a missed click is
  recoverable), square-off got 5 (it matters during a breach, but the retry
  *is* the mechanism).
- **Two new skip reasons:** `sized_to_zero` and `no_structural_stop`, both
  detected before anything reaches the broker.
- **`leverage_factor` must be at least 1**, and `remaining_capital` is floored
  at zero so an over-committed account sizes to zero rather than negative.
  Negative `margin_used` is ignored rather than crediting capital.
- **Heartbeat and live-mark files are written atomically** (temp file + fsync +
  rename). The notes specified a separate live-mark file but not that a reader
  must never catch a half-written one.
- **Live marks carry a `complete` flag** so a partial set of marks shows as
  partial rather than as a confidently wrong total.
- **Stored candidates tolerate schema drift both ways** — unknown keys dropped,
  missing keys defaulted — so adding a field to `TriggerCandidate` never makes
  yesterday's rows unreadable.
- **An unknown command kind is rejected rather than left pending**, so one bad
  row can't wedge the queue behind it.
- **Every command resolves exactly once** (`PendingCommand`). Re-applying
  `CLOSE_POSITION` or `KILL_ALL` would act twice.
- **`start` reports every failing precondition**, not just the first. Being
  told one problem at a time is a worse experience than seeing all of them.

---

## 6. Two bugs found while building

**Python 3.9 str-enum stringification.** `str(CommandKind.STOP)` returns
`"CommandKind.STOP"` on 3.9, not `"stop"` — `str`-mixin enums only stringify to
their value from 3.11. This broke 23 tests at once. Fixed at the one site that
round-tripped an enum through `str()`, and every other enum conversion was
audited (the rest read strings from SQLite or JSON, which is safe).

**Off-tick swing prices are refused, not rounded.** `structural_stop_price`
returns `None` when a swing price isn't a multiple of the tick size, because
`price_to_ticks` raises. This is correct and worth stating: rounding would
invent a stop price the exchange rejects. The caller treats it as "don't enter".

---

## 7. Frontend decisions (the notes only specified colours and text)

- **Live P&L greys out and is labelled stale past 5 seconds** rather than shown
  as fact.
- **Surprising close reasons are highlighted** —
  `manual_broker_intervention`, `unattributed` and
  `abnormal_slippage_flatten` — because those are the ones worth noticing.
- **A stop adopted from a broker-side edit gets a `↺` marker** so an unexpected
  stop price is traceable.
- **Kill It All Now needs the word `KILL` typed**, and the modal states the
  three things it will do.
- **The start panel mirrors the server's cap validation** client-side, so a bad
  number is caught before the round trip. The server still enforces it.
- **Failed and ambiguous diary events are styled as alarming**, and a long
  broker rejection message gets full width instead of being truncated.
- **A stale heartbeat claiming `entries_allowed` is not believed** — the API
  ands it with actually-running, because a dead engine allows nothing.

---

## 8. Housekeeping the deletion forced

- **`admin_trail_profile.py`** — the Admin config store still persists and
  validates trailing-profile values and the Diagnostics tab still displays
  them, so those two functions were moved out of the deleted module rather than
  removed. Explicitly vestigial: no engine code reads them. Dropping the keys
  would change saved admin payloads and that tab's contents, which is a
  separate decision from the rebuild.
- **`api/routers/admin.py` repointed, not deleted.**
  `/admin/trading/pause` → `STOP`, `/admin/trading/resume` → `START`, engine
  state from the heartbeat. Same request/response shapes, so the Diagnostics tab
  needed zero frontend edits — which was the point, since that tab was off
  limits. Its `arm_session` endpoint is gone: no arm concept exists any more and
  nothing in the frontend called it.
- **60 lines of dead paper-ledger config removed** from `api/config.py` — eight
  helpers that, after the deletion, referenced only each other.
- **Three local dev dependencies installed**: `argon2-cffi`, `pyotp` and
  `eval_type_backport`. Twelve test files couldn't even be imported without the
  first two, which aborts the whole pytest run and hid the real suite. The third
  is needed because this machine runs Python 3.9 while `api/schemas/admin.py`
  uses 3.10+ union syntax — production runs 3.10+, so that annotation is not
  broken and was left alone.

---

## 9. Still open

- **`AppFooter` says "TRADING ENGINE V1"** and "DEMO 5X UNLESS LIVE KITE ORDERS
  IS CHECKED". It's a global footer on every tab, so changing it touches tabs
  that were off limits. Flagged, not touched.
- **The parity script's reference snapshot** now differs on the Execution Desk
  by design. Its remaining value is as a regression guard on the four untouched
  tabs; its fixture was updated to the `/execution/*` shapes so the run doesn't
  error on an unhandled path.
- **Trailing** — unchanged from the notes: a clean slate, nothing carried over.
  `ExecutionState.TRAILING` exists in the state machine as a reachable-in-future
  state that nothing currently produces.
