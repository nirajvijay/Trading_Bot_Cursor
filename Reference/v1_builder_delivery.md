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

## Stage2 control-plane development checkpoint

Implemented durable idempotent commands and explicit SessionArm, MANUAL preview /
approval through the same execution path as AUTOPILOT, Saved/Effective HTTP fields,
run-bound restart locks, broker-confirmed close/trail outcomes, and Stop Engine
drain semantics. LIVE starts/arming remain locked unless independently authorized
in the server environment; that authorization has not been enabled.

Original setup snapshots are write-once at the database layer. Mode changes to
AUTOPILOT require flat/reconciled state. Safety switch to MANUAL leaves entries
paused. Observation can connect from 09:00 on the current regular trading day;
connection-without-ticks is reported separately from disconnection. Holidays,
session-date mismatch and unconfigured special sessions do not unlock observation.

Local `.venv/bin/python -m unittest discover -s tests -q`: **740 tests in 7.339s,
OK**. Fault-injection tests intentionally print simulated failures. This does not
prove deployed behavior. Stage2 is not yet an overall completion claim: persistent
PAPER runtime, remaining recovery/control integration, frontend binding and
end-to-end sessions still require implementation/verification.

## Durable PAPER runtime checkpoint

The launcher now supplies a separate persistent PAPER account beside the trading
database. Local orders, fills, stop state and write counts survive restart; a
single-writer lease prevents concurrent simulator processes. The quote provider
uses Kite read-only full quotes, while place/modify/cancel stay local. Missing or
stale quotes do not fabricate fills. A stop LIMIT can trigger and remain unfilled
through a gap. Trailing marks refresh from liquidation touch, not the last fill.

The simulator assumes the full executable remainder fills at touch; it does not
model depth, queue priority or guarantee live outcomes. Nine dedicated tests cover
durability, gap behavior, missing quotes, lost-stop acceptance, read-only broker
boundary and real-cycle manual approval / protection / restart / re-arm / close.
Full isolated discovery: **749 tests in 7.248s, OK**. No deployment or real orders.

Launcher failure no longer acknowledges pending Stop Engine commands as drained.
It records error / reconciliation-needed rather than broker-confirmed completion.
Remaining: frontend/sector integration, broader PAPER session/fault report,
release/deployment verification and the separately prohibited real LIVE pilot.

## Stage3 initial website checkpoint (development, not deployed)

Public root is separate from the authenticated owner app. Four-tab navigation,
ordered morning checklist, versioned 17-sector / exact-100 board, and shared
private execution status are implemented locally. Sector-map mismatch blocks
readiness. Desk and Admin redesign remain incomplete; this is not Stage3 acceptance.

Kite callback defaults and legacy root callback settings now return to `/owner`
so the result reaches the embedded Checklist authentication flow rather than the
public homepage. A regression checks legacy success/error and unsafe URL fallback.

After resuming the usage-limited goal: full isolated discovery **752 tests in
7.633s, OK**; `git diff --check` clean. Previous frontend build passed and the
compatible nanoid patch removed the reported dependency vulnerability. Rendered
UI and integrated website sessions still require verification. No remote writes,
deployment, or real broker order operations were performed in this checkpoint.

## Desk command integration checkpoint

The mounted Desk now binds to durable commands for arming, manual approval,
pause/disarm/drain, close position/all, reconciliation and trailing. Existing
password/MFA step-up is reused; retries after step-up retain the command ID.
Manual previews use observed setup identities with quantity reduction / stop
tightening, and display backend blockers. The old capital-on-blur / demo-leverage
panel is no longer mounted. Command acceptance is explicitly not completion.

Corrected the shared strip's API URL to `/trading-engine/control`; stale client
status becomes unknown after five seconds, with bounded read requests. Server
status cannot report armed when stopped, unsynced or recovering. Focused control
API + engine control suite: **18 tests, OK**. Frontend production build passes.

Still incomplete: per-trade immutable audit/detail and precise quantity/P&L
presentation, full Admin redesign, rendered/interface verification, PAPER fault
report and deployment. These UI controls have not been exercised against a real
broker or deployed host. No live orders or deployment.

## Trade audit and expanded Admin development checkpoint

Authenticated per-trade audit returns the durable trade snapshot, immutable
original setup, linked executions and only that trade's events. The Desk exposes
it with intended/filled/exited/pending/remaining/covered quantities, provisional
accounting labels and explicitly gross remaining-position stop estimates. Missing
historical plans are not reconstructed. These are stored records, not fresh
broker confirmations.

Admin now exposes all currently supported backend settings across seven sections,
with draft/save/discard, percentage conversion, Saved vs Effective, explicit
version-conflict blocking, existing step-up and rollback, safety commands, and
recovery history. Polling never replaces an owner draft or silently updates its
base version. Observation section links the workflow conceptually to Checklist
and Radar; no new backend-shell capability was added.

Full isolated suite: **754 tests in 8.649s, OK**. Frontend build passes; lint has
only the pre-existing ChecklistStatusPill Fast Refresh warning. Browser workflow,
full frozen-requirement audit, runtime PAPER report and deployment remain. New
Admin/Desk interfaces are development implementations, not acceptance proof.

## Session report and PAPER lifecycle checkpoint

Desk downloads an authenticated session-cohort JSON report. It separates strategy
outcomes from engineering observations and keeps PAPER / LIVE / unknown provenance
apart. Only price-complete closed records enter outcome P&L; provisional records,
unprotected exposure, reconciliation state and missing plans are explicit. This
is not a broker calendar-day net-charge statement or proof of safety/profitability.

Persistent PAPER lifecycle coverage now runs both MANUAL and AUTOPILOT through
entry, protection, account restart, entry-lock recovery, explicit re-arm, durable
close and report generation. Tests assert flat broker/local state and no pending
orders. Readiness and quotes are injected; these do not replace Checklist/API/UI
end-to-end verification or a live market session.

`tests.test_trading_engine_paper` + `tests.test_trading_engine_stage2_api`: **18
tests in 0.303s, OK**. Frontend build passes. No live broker writes or deployment.

## Frozen-contract audit: auto-trail default

Added missing `auto_trail_default_enabled` Admin key (default 1; validates 0/1),
saved for next explicit arm. Acceptance records the setting through the existing
frozen config and per-trade disable latch. Later saves/arms cannot silently turn
trailing on for that open trade; an explicit per-trade enable remains available.
Regression uses the real cycle and verifies Saved/Effective separation, initial
disabled protection, later config change, and explicit owner enable.

Full isolated suite **757 tests in 32.025s, OK**; frontend build and diff check pass.
Remaining audit items include configurable trailing-profile parameters / execution
preference, per-trade fresh MTM display, richer report costs/R/delay metrics,
browser workflows, operational recovery SOP and deployment verification. Overall
goal remains incomplete. No host writes or live orders.

## Per-trade liquidation-mark checkpoint

Engine loss accounting now publishes per-position mark, quote timestamp, P&L and
the exact quantity/entry-value slice used. Audit API exposes that same mark only
when quote <=2s, feed/sync <5s, session matches and accounting quantities still
match the stored trade. Otherwise P&L is null/stale, never substituted with the
old database open-P&L value. Desk audit ages the received mark locally as well.

Loss + control API focused suite: **20 tests in 1.392s, OK**; build passes. Regression
checks same-slice bid MTM, stale quote, changed remaining quantity and unknown feed.
Still development evidence; interface workflow verification/deployment outstanding.

## Frozen per-trade trailing profiles

Admin now exposes staged-R thresholds/gaps and modification interval/improvement
with the original 1R/2R, 1R/0.5R, 2-second/2-tick defaults unchanged. Validation
requires increasing stages, non-widening later gaps, and retains minimum throttle
bounds. Saved changes wait for explicit arm. Execution reads only the trade's
frozen risk/config snapshot; legacy trades use historical V1 defaults, not current
Admin values. Invalid stored profiles block entries/trailing rather than inventing
a replacement profile. Existing tighten-only and through-price exit checks remain.

Profile + WP13 + Admin store + control suites: **46 tests in 0.662s, OK**. New tests
cover invalid profiles, default compatibility, Saved/Effective and old-trade
isolation, and mirrored long/short custom-gap math. Frontend build passes.

## First-run/calendar and owner recovery checkpoint

Calendar header now reads a private server endpoint using the configured NSE
calendar, not browser weekday-only assumptions. Unconfigured calendar years block
new entries and show UNKNOWN. Thirty observation/calendar/close tests pass; the
frontend builds. Embedded Kite form no longer imposes full-page height inside
Checklist. Token-check errors are surfaced rather than left as unhandled promises.

Added `Reference/v1_owner_recovery_and_pilot.md`: practical pause/reconcile/Kite
inspection steps, command distinctions, unknown-order caution, dashboard-only
alert limitations and a separately authorized supervised minimum-size MANUAL
pilot. No live pilot was run or authorized by this document.

## Lightsail read-only deployment preflight (2026-09-10)

SSH inspection confirms current release remains
`/opt/nifty-radar/releases/e4aa813e08059d3cd17fb785eae0af90eb9c2d29`.
API is active under `nifty-radar-api.service`, working directory `current`, shared
venv Uvicorn on 127.0.0.1:8000. Caddy routes `/api/*` there and serves
`current/frontend/dist` with SPA fallback. No observation/trading runner process
was returned by the process inspection. No services were stopped or restarted.

Remote workspace remains at `26ad912` with Cursor WP17 source/test changes; it
must not be overwritten. A release must be built separately from the local
committed branch, with explicit database backups and LIVE disabled. Both live
authorization flags were absent from the environment file's filtered output.

Read-only trading database inspection: 11 closed, 18 skipped, one protected-open
ADANIPORTS 145 shares from 2026-09-02. This is a persisted row, not evidence of a
current broker position; provenance remains unresolved and must not be auto-adopted
or auto-flattened. Active saved config remains ₹2,995, with entries_paused=0
(no running trading process observed; this is not arming authorization). No host files,
databases, orders, credentials or service configuration were changed.

## Host migration rehearsal — isolated copies only

Source from committed `18b0ae6` was extracted under
`/tmp/nifty-v1-rehearsal.1LtPCV/source` without touching remote workspace/current.
SQLite readonly backups of trading/Admin databases were migrated using the host
Python environment and `tests/rehearse_host_migration.py`. Both integrity checks
passed; all **30** trade identity/quantity/status/session rows were unchanged and
Saved/Effective daily cap remained **₹2,995**. Existing saved keys were preserved.

The old ADANIPORTS 145-share record retained its historical status but has zero
asserted protection and null mode provenance after migration. That is fail-closed
historical accounting, not resolution of the broker position. No broker calls,
production migrations, service restarts or deployment occurred. Temporary copies
remain private (directory 0700, database files 0600) for release verification.

On that isolated source, host Python ran PAPER lifecycle + trailing profile +
control API modules: **22 tests in 3.392s, OK**. These use temporary test databases
and do not exercise the running production API or real broker account.

## Repeatable UI wiring checks

`npm run check:render` server-renders the public route and four owner initial
surfaces. It verifies expected content, private operational labels absent from
the public output, and zero render-time fetch calls. It passes. This is explicitly
not browser-effect, interaction, layout or visual acceptance evidence.

`tests.test_frontend_route_contracts` verifies new service URLs against the actual
FastAPI OpenAPI route registry; one test passes. Local preview on 127.0.0.1:5173
returned HTTP 200; opening it in Codex was queued. No rendered browser inspection
or trading command was performed during this checkpoint.

## Admin execution preference checkpoint

Admin now saves a validated PAPER/LIVE preference, including legacy-default merge,
version diff and reload support. The Desk applies it once on initial load, choosing
PAPER when server LIVE authorization is absent. Polling never overwrites the owner's
selection. Saving this preference neither starts nor arms the engine, and cannot
authorize LIVE orders. Effective promotion remains explicit.

Verification: full `unittest discover -s tests -q` passed **766 tests in 13.182s**;
frontend production build and `npm run check:render` passed; `git diff --check`
clean. New regressions cover invalid enums, restart persistence, preservation of
the 2995 fixture, stopped/disarmed control state and rejected LIVE arming after a
LIVE preference save. This is isolated evidence, not deployment or broker proof.

## Current-run status and PAPER readiness integration

Heartbeat output now carries the engine run ID. Control and trade-audit APIs
discard heartbeat data unless it belongs to the running session/run. A fresh
timestamp from a previous process can no longer imply armed permission, broker
sync or live P&L. Unit and HTTP regressions cover missing/wrong run identity,
stopped process and session mismatch.

The persistent PAPER MANUAL and AUTOPILOT lifecycle tests no longer bypass
`_arming_readiness`. They exercise the actual checklist cache/sector validation
path, prove arm rejection before checklist evidence, then write isolated checklist
evidence and complete entry/protection/restart/rearm/close/report. Market quotes
and checklist results remain fixtures: this is not a real market-data preparation
or browser-session acceptance claim.

Full suite: **768 tests in 12.133s, OK**. Focused PAPER/control/identity set:
**21 tests, OK**. No real orders, production migration or deployment.
