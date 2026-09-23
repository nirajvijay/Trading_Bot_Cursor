# Execution Engine Rebuild — Design Notes, in Plain Language

A simpler walkthrough of `execution_engine_rebuild_notes.md` — same decisions,
no jargon, short bullets instead of long paragraphs.

---

## How trades get picked when two setups fire at once

- If two stocks trigger in the same second, **ACCEPT always wins over
  LIMITED** — no exceptions.
- If both are the same tier, the one with the **stronger breakout volume**
  wins.
- Otherwise, whoever triggered first gets the trade.
- We do **not** rank by tight stop-loss — a tight stop just means more shares
  fit, not a better trade.

## How we enter a trade

1. Work out the stop price first, from the chart (swing high/low) — never
   change it later.
2. Work out how many shares to buy, based on the risk cap for that trade's
   tier.
3. Double-check nothing has changed (paused? daily loss hit? still open
   hours?) right before sending.
4. **Save "I'm about to do this" to disk before calling the broker** — so if
   we crash mid-order, we can tell what happened instead of guessing.
5. Send a **MARKET order** (not limit) — no messing about with re-pricing.
6. The broker's reply is always one of three things:
   - **Success** → move on.
   - **Definitely rejected** (bad quantity, no margin) → record it, stop,
     don't retry.
   - **Unclear** (timeout, dropped connection) → ask the broker "did this
     actually happen?" instead of guessing.
7. Once filled, use the **real fill price** to work out real risk.
8. If the real risk is more than **1.5× the cap** (bad slippage) → skip the
   stop, flatten immediately, flag for review.
9. Otherwise → place the protective stop at the price from step 1.

LIMITED trades go through the exact same steps — just a smaller risk cap.

**Things we decided *not* to do:** move the stop to force the math to fit,
trim the quantity after a bad fill, or bump an existing trade to make room for
a "better" one.

## What happens to REJECT / no-verdict-yet triggers

- REJECT → just skip it, done.
- No VWAP verdict yet → wait up to **2 seconds** (checked twice, 1 second
  apart), then give up and skip it as "unavailable."

## The run loop (the engine's heartbeat)

- Checks everything **once per second**.
- If a check runs long, the next one starts right away instead of waiting —
  we'd rather run a bit late than skip a check.
- If one part of the check breaks, the other parts still run. Each part gets
  its own patience level before it "escalates":
  - Placing stops: 3 failures in a row → alarm (most dangerous, least
    patience)
  - Checking fills: 5 failures
  - Reading triggers: 10 failures (least dangerous)
- **New entries stop at 2:00 PM.** The engine keeps managing open trades
  after that.
- **Everything still open gets closed at 3:15 PM** — 10 minutes before
  Zerodha's own forced close, which costs money and gets bad prices.
- Starting the engine is only allowed between 9:15 AM and 2:00 PM. Outside
  that window, it just refuses — it doesn't wait around.
- Once started, it's **live immediately** — no practice mode.
- Only triggers from *after* you clicked start count. Old ones sitting in the
  database are ignored.

## Checking in with the broker

- **Every single tick**, for every open trade, we ask the broker "what's
  actually true right now?" — not just when something looks off.
- If the real quantity or stop price is different from what we think, **we
  trust the broker**, not our own memory.
- We always check **prices for everything in one batched call**, never one
  call per trade — otherwise we'd hit Kite's rate limits.

## How a trade actually closes

- Either the stop fires on its own, or we actively decide to close it (end of
  day, daily loss hit, manual close, bad slippage).
- If we're closing it ourselves: **cancel the stop first**, then send the
  market order — otherwise both could fire and we'd end up short.
- We look up **which order actually closed it** rather than assuming — a
  human might've closed it manually in Kite, and we need to know that.
- P&L is always calculated from the **real fill prices**, never the price we
  originally planned.

## Daily loss limit

- Based on **realized losses only** (money already lost), not "what if
  everything hits its stop."
- Once you hit the ₹3,000 cap, it's **locked for the rest of the day** — no
  coming back from it.
- Every open trade gets closed, **even ones currently in profit** — no
  exceptions.
- Once everything is confirmed closed, the engine **stops itself
  automatically**.

## The buttons a human can press

- **Stop / Start** — just toggles whether new trades are taken. Doesn't shut
  the engine down.
- **Close one position** — closes just that trade, engine keeps running.
- **Kill It All Now** — the "emergency button": closes everything, confirms
  it's all closed, then shuts the engine down for the day.
- No "pause" button — Stop already covers that.

## Storage (where the data lives)

- Uses the same kind of database (SQLite) the old engine used.
- Two tables: one shows **what's true right now**, the other is a
  **permanent diary** of everything that happened, in order.
- Every save is **guaranteed to hit disk** before moving on — no shortcuts,
  because a crash mid-write must never lose data.

## How we'll know the engine is alive

- It writes a small "I'm alive" file every second.
- If that file goes quiet for too long **with no explanation** → treat it as
  crashed, alarm loudly.
- If it goes quiet **with a note saying "I stopped on purpose, here's why"**
  → that's fine, no alarm.
- If it crashes for real, we **don't auto-restart it** — a human needs to
  look first.

## Position sizing (how many shares to buy)

- Whichever is smaller: what your **risk cap** allows, or what your
  **capital** allows.
- Trading costs (brokerage etc.) are **on top of** the risk cap, not squeezed
  inside it.
- We dropped the old third check ("total exposure cap") — the capital check
  already covers that on its own.

## Testing before using real money

- No more "run the old and new engine side by side" plan — the old engine is
  being retired, so there's nothing to compare against.
- Instead: **use real Kite orders, but with tiny amounts.** Same code path as
  full-size trading — just smaller numbers, so we're actually proving the
  real system works, not a fake version of it.
- Before starting, there's a **confirmation screen** showing the exact
  numbers, so you can't accidentally start with the wrong risk amount.

## Live profit/loss (P&L)

- Never saved to the database — it's just whatever Kite says right now, so
  if it's ever lost, next second's check fixes it.
- Comes from the same broker check we're already doing every second, so it
  costs nothing extra.
- Sent straight to the Execution Desk screen, kept separate from the "I'm
  alive" heartbeat file.
