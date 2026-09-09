# V1 builder delivery ledger

Owner authorization: primary-builder takeover, repository work, GitHub and deployment.
**No real broker orders.** No live execution, cancellation or modification may be
used to test this delivery. Supervised LIVE trading remains an unexecuted owner gate.

## Development checkpoints

- WP1.6 inherited commit: `26ad912`.
- WP1.7 reviewed and corrected locally: `67f98ed`. Added fail-closed discovery
  response validation and symbol/mode-scoped ownership. 251 isolated tests passed.
  Further WP1.8 review corrected an attempt-map call signature.
- WP1.8: bounded LIMIT entries, original signal expiry, timestamped bid/ask,
  drift/tick bounds, final-price quantity reduction, durable accepted-order ID on
  poll failure. 15 dedicated strict tests passed; preceding combined run 264 passed
  before the last two dedicated tests were added.

Historical WP1.1–17 lifecycle tests deliberately isolate entry admission using
`tests/engine_lifecycle_fixture.py`; they are not evidence of strict entry gates.
`tests/test_trading_engine_wp18_entry.py` uses the real cycle without overrides.
All test databases are temporary/local. Host history and saved ₹2,995 were not changed.

## Not yet complete

- Integrated runtime verification of WP1.7–1.10 (development implementations exist).
- Stage2 control plane, arming, approvals, truthful command outcomes, recovery UI APIs.
- Stage3 four-tab frontend, full Admin, private status, sector map, public landing.
- Stage4 integrated PAPER and fault sessions with report.
- Deployment and rendered/private UI verification.
- Real supervised LIVE pilot prohibited by current authorization.

## WP1.9 checkpoint

Post-fill cap validation now uses frozen acceptance limits. Confirmed breaches
pause entries and share the durable serialized exit path; unresolved prices pause
without inventing a breach. Rechecks run on fills, before admission, and after
remainder cancellation. Restart/hidden-stop/competing-close regressions pass.
Combined WP1.1–19/admin/calendar suite: 280 tests, 3.224s, OK locally.
Historical lifecycle fixture isolates post-fill policy for intentionally injected
overfills; WP19 tests exercise the real production cycle.

## WP1.10 checkpoint

Daily-loss halt now sums confirmed realised slices (including partial exits) and
fresh liquidation-side open MTM. Unknown inputs block admission and return null
total P&L, preserving prior confirmed realised values. Realised charges use the
stamped estimate once; no added slippage on confirmed fills. Admission reservations
are excluded from the halt calculation. The halt is durable per session/mode;
resume/restart cannot clear it. Exits use the shared ownership/unknown-stop rules.

Combined engine suite including strict WP18/19/110 tests: **291 tests in 3.747s,
OK locally**. Full command: `python3 -m unittest` followed by
`tests.test_trading_engine_risk tests.test_trading_engine_wp12_risk
tests.test_trading_engine_wp11_lifecycle tests.test_trading_engine_wp13_trail
tests.test_trading_engine_wp14_close tests.test_trading_engine_wp15_reconcile
tests.test_trading_engine_wp16_protection tests.test_trading_engine_wp17_recovery
tests.test_trading_engine_wp18_entry tests.test_trading_engine_wp19_postfill
tests.test_trading_engine_wp110_loss tests.test_trading_engine_cycle
tests.test_trading_engine_store tests.test_trading_engine_broker
tests.test_admin_config_store tests.test_trading_engine_loop
tests.test_trading_engine_handoff tests.test_nse_trading_calendar -q`.

This is isolated evidence, not deployed-runtime or real-broker proof. Next: control
plane, fresh PAPER quote wiring/persistence, truthful commands, session arming,
observation readiness, frontend integration, PAPER sessions and deployment checks.

## External interface reference

Kite full quotes supply `timestamp` (quote packet exchange time) and buy/sell depth;
LTP/last-trade time is not the quote freshness source:
https://kite.trade/docs/connect/v3/market-quotes/
LIMIT order payload contract: https://kite.trade/docs/connect/v3/orders/
