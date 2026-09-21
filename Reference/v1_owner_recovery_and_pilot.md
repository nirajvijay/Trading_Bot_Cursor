# Owner recovery and supervised-pilot checklist

## Current authorization boundary

Development, simulated PAPER tests and deployment are authorized. Real broker
orders are not. Keep LIVE execution authorization disabled. This document is a
future operating checklist, not authorization to run the pilot or evidence that
the pilot has passed.

## If the Desk reports unknown, stale or unprotected exposure

1. Request Pause entries. A stale browser is not evidence that the command reached
   the server; inspect its command state when connectivity returns.
2. Open Kite independently and inspect current positions, pending orders and
   executions. Compare broker order IDs with the trade audit. Do not infer order
   absence from an empty or unavailable API response.
3. Use Reconcile now when the engine is available. Unknown submissions remain
   unresolved until identified; repeatedly clicking close or trail is not recovery.
4. If broker-side intervention is needed, NJ must perform it with awareness of
   pending entries and protective/exit orders. An additional exit alongside an
   existing working exit can reverse exposure. This system does not authorize an
   assistant to place, modify or cancel real orders.
5. Confirm broker quantities and working orders, then re-check the Desk. A local
   closed row alone is not proof that the brokerage account is flat.
6. Keep entries paused until ownership, quantities, protection and accounting are
   reconciled. Do not delete history or adopt unrelated orders to clear an alert.

Dashboard-only alerts cannot reach you when you are away, disconnected, or the
host has failed. Do not treat V1 as an unattended-LIVE safety guarantee.

## Control meanings

- Pause entries: block new entries; existing fills remain managed.
- Disarm: remove session entry permission; does not immediately flatten.
- Close all & pause: request serialized exits of positively owned engine trades.
  Unknown orders/ownership can prevent completion; inspect the command state.
- Stop engine / drain: disarm and manage existing exposure until flat; not the
  same action as immediate Close all.

## Supervised LIVE pilot — separate later owner gate

Prerequisites: deployment verified; PAPER lifecycle and failure gates passed;
broker/account readiness verified; no unresolved provenance or exposure; explicit
new authorization to use LIVE. The historical ADANIPORTS row is not a current
position assertion and must be reconciled without inventing ownership.

NJ stays at the desk. Start with MANUAL and minimum valid quantity within configured
risk limits. Verify preview/approval, actual entry fills, confirmed protection,
one trail, manual close and broker/local reconciliation. Record the order IDs,
timestamps, quantities and command outcomes. Stop the pilot on any unresolved
critical discrepancy. Do not enable away-from-desk AUTOPILOT as part of this gate.

No profits, technical-error-free operation or live execution equivalence are
guaranteed by PAPER tests or a successful supervised pilot.
