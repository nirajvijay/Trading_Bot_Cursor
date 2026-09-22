# Automated morning checklist

The server prepares the existing five checklist stages from 08:40 to 09:15 IST on
configured regular NSE sessions. The existing buttons reflect server activity;
no additional page, panel, notifications, observation start, or trading actions
are introduced. A hidden or closed browser has no effect on the job.

## Activation (separate from merging this PR)

1. Inspect the deployed release, service user, data paths, Kite settings and live
   service state. Use the normal production release workflow. Never run the job
   against active observation/execution.
2. Complete a supervised initial historical backfill and verify all five stages.
   Daily repair allows at most three deficient sessions per configured symbol
   within the 21 required prior regular sessions; larger gaps require manual
   backfill. Existing full generation commands remain available on the page.
3. Confirm the `nifty-radar` service user can read `api.env` and read/write the
   existing Kite secrets store. Token refresh uses its existing atomic writer.
   Check that process environment does not override the persisted access token
   with an old token. Do not print credentials to logs or copy them into Git.
4. Run `morning_checklist.py --dry-run` with the production venv, service account,
   and matching environment. This reads current readiness without writes or
   broker login. Confirm configured paths point outside immutable releases.
5. Install `ops/morning-checklist.service` and `.timer` in systemd. Validate using
   `systemd-analyze verify` and `systemd-analyze calendar '*-*-* 08:40:00 Asia/Kolkata'`.
   Units are supplied only; installation/activation is not performed by the app.
6. Configure `/opt/nifty-radar/secrets/morning-checklist.env` with
   `MORNING_CHECKLIST_ENABLED=true`. The flag defaults to false. Existing
   `KITE_AUTO_LOGIN_ENABLED` and configured owner credentials are also required
   when the daily token is not already valid. Match `KITE_EXPECTED_USER_ID`.
7. Reload systemd, enable/start the timer, and supervise one regular morning.
   Verify each existing button, saved checklist readiness, normal completion,
   and the 09:15 stop behavior before relying on it unattended. Benchmark the
   real database so preparation reliably completes before 09:15.

Systemd uses the current release and existing venv. Write access includes the
secrets directory because token persistence uses atomic rename and a lock file.
Keep its existing restrictive ownership. The timer runs daily; holidays only
record a skip. Persistent scheduling recovers a missed timer within the window.
Kite access tokens expire at 06:00 IST and Kite recommends fetching the daily
instrument dump around 08:30, so the run starts at 08:40 and stops at the 09:15
open. It runs once per day; after a block, recovery is manual from the page.
An abnormal process death is restarted by systemd; stage attempts survive it.

## State and concurrency

`runtime-cache/checklist-runs.db` stores one current run record per IST date,
including source, stage, revisions, attempts, timestamps and dirty dependencies.
The stable `checklist-workflow.lock` inode is never removed. Subprocesses inherit
ownership, so an orphaned collector still blocks another writer. Existing
generation locks remain in force. Manual website operations share this state.
At start the job waits up to 60 seconds for brief page reads to release the
lock. If a manual operation still holds it, the job logs the skip to the journal
and leaves that operation's record untouched.

Each stage is checked before and after work. Kite is checked first; an invalid
token is regenerated and checked again, and a later stage starts only after the
previous one validates. A manual Kite login or token generation is not complete
until a token check passes.

The existing authenticated checklist endpoint includes optional `activity`.
`?activity_only=true` returns only session date and activity without scanning
market databases. The visible checklist polls it every five seconds and refreshes
the full checklist on revision changes and visibility/focus recovery. Calendar
date changes are handled without reloading the app. Observation readiness refreshes
after activity transitions.

Readiness is invalid during work, after interruption, or while derived stages
are dirty. Full checklist reads cannot publish results spanning a mutation.
Actual validation, not the process exit code, controls stage readiness.

## Failure and recovery

- CAPTCHA, rejected credentials/TOTP, account mismatch, or missing configuration:
  use the existing Kite login/recovery controls, then retry affected stages.
- Network failures: at most three attempts per stage/day and three automatic
  login attempts per ten-minute account window, shared with browser login.
- At 09:15 the child process group is terminated, with five seconds of graceful
  cleanup before forced termination. Final state records the blocked stage.
- A crashed run becomes interrupted when its lock is released. Revalidate before
  restarting inside the permitted window; outside it use existing manual controls.
- Source repair keeps successful transactions, rolls back incomplete replacements,
  invalidates affected five-minute rows, and keeps derived stages dirty until
  regenerated. Do not delete state or locks to make a checklist appear ready.
- Inspect persisted stage state and `journalctl -u morning-checklist.service`.
  The job does not send failure alerts. Open the existing Checklist page to see
  its current result.

## Calendar maintenance

`nse_trading_calendar.py` references NSE CMTR/71775 and its CMTR/72260 amendment
for 2026. Review official Capital Market trading circulars (not settlement
holidays), including mid-year changes, and update supported years and tests
through a reviewed PR. Review next year's publication each December.
Unknown years and prior-session lookbacks crossing unsupported years block
preparation. Special sessions (for example muhurat) are skipped when
resolving prior sessions; preparation on a special-session day itself requires a
separate reviewed schedule change.

## Rollback

Disable/stop the new timer and service first, confirm its entire process group
has exited, then use the existing release rollback procedure. Preserve market
databases, Kite secrets and run records. The previous frontend tolerates the
additional API field; the new frontend tolerates older responses without it.

## Verification

Run the morning-runner/data tests and existing calendar, checklist, cache,
remote-generation, broker-authentication, observation, and execution API tests.
Run `npm run build` in frontend. `frontend/verify-morning-checklist.cjs` exercises
the built frontend with all API traffic mocked; `PARITY_RUNTIME` points to a
Node runtime containing Playwright. No production data or broker calls are used.
