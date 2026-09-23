# What Got Added During the Build — in Plain Language

A simpler walkthrough of `execution_engine_rebuild_additions.md` — the
decisions that came up while actually writing the code, which the original
design notes didn't spell out.

---

## Safety holes I had to plug

- **If the broker check itself fails** (network blip, Kite down), we now
  **do nothing** rather than guess. Without this, a broker outage would look
  exactly like "every position just went flat" and the engine would
  panic-close everything.
- **If a stop-loss order disappears from the broker** (got cancelled,
  whatever), the position drops back to "unprotected" instead of quietly
  staying marked "protected." Otherwise the desk would show 0 unprotected
  trades while a real trade sat with no stop.
- **If we're cancelling an order and it fills anyway** in that split second,
  we don't mark it cancelled — we let it be treated as a real live trade.
- **The exit order now has a different ID tag than the entry order.** Sounds
  trivial, but without it, the system could mistake "this trade is closing"
  for "this trade just opened," which would corrupt the numbers.
- **If we can't check margin before sending an order**, we don't block the
  trade — we let the broker's own rejection catch it if there's really a
  problem, rather than losing good trades over a temporary glitch.
- **"Is the engine running" now checks two things**, not one: is the
  heartbeat fresh, *and* is that process actually still alive. Otherwise a
  stuck heartbeat could either block you from restarting, or a dead process
  could look alive.

## A few new position states I had to add

The original design didn't list every possible state change, so I added the
missing ones:

- Entry rejected before it ever filled
- Filled but then closed anyway (before protection even happened)
- Protected trade whose stop vanished → goes back to "needs protection"
- Standing stop fires on its own → goes straight to closed

## When two orders both look like they closed the trade

This happens if we send a "close this" order *and* the stop fires at nearly
the same time. I picked a clear order for deciding which one "really" closed
it:

1. Whichever filled **first** wins.
2. If we don't know the time for one of them, it can't win on timing.
3. A full-size fill beats a partial one.
4. If it's genuinely a tie, an order **we didn't recognize** wins — because
   that's the one a human most needs to know about.

We also now record **both** orders in the trade's diary when this happens,
instead of quietly picking one and hiding the collision.

## Two separate "off" switches

- **"Stopped"** = a human clicked Stop.
- **"Paused"** = the engine itself decided to hold back (bad feed, daily
  loss hit, etc.)

These used to risk being the same flag — I kept them separate because they
mean different things and the desk needs to say the right one.

## Two bugs I actually caught while building

- On this Python version, `str(SomeEnum.STOP)` doesn't give you `"stop"`
  like you'd expect — it gives you `"SomeEnum.STOP"`. This silently broke 23
  tests until I found and fixed it everywhere it mattered.
- If a stop price doesn't land exactly on a valid tick (like ₹107.03 when
  ticks are ₹0.05), we now **refuse to trade** rather than quietly rounding
  it — because a rounded price is one the exchange might just reject anyway.

## Small design choices I made along the way

- Live P&L on screen goes grey and says "stale" if it's more than 5 seconds
  old, instead of showing a possibly-wrong number as if it's current.
- Closes that look suspicious (a human closed it in Kite, or we couldn't
  explain who closed it) get **highlighted in red** on the desk — those are
  the ones worth noticing.
- "Kill It All Now" requires you to actually **type the word KILL** to
  confirm — not just click a button.
- Every command (stop, close, kill-all) can only be **applied once**, even
  if it gets processed twice by accident.

## Cleanup that came with deleting the old engine

- Two small bits of old code (`structural_stop_price`, the trailing-stop
  settings) got pulled out and kept, because the new engine or the settings
  screen still needed them — everything else was deleted.
- The "Diagnostics" screen's pause/resume buttons were **rewired to talk to
  the new engine** instead of removed, so that screen didn't need any
  changes at all.
- Deleted about 60 lines of leftover config that only pointed at other
  leftover config.

## Still left undone (flagged, not fixed)

- The little footer text at the bottom of every screen still says "Trading
  Engine V1" — I didn't touch it because it's shared across every tab,
  including ones you said not to touch.
