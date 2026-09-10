# V1 PAPER clean-start cutover (await NJ explicit approval)

**Code baseline:** clean-start implementation `c07f08cd146409506950892a6a8b6fc11df823c3` on `codex/v1-acceptance-review`  
(worktree `/opt/nifty-radar/worktrees/v1-acceptance`). Cut over only a tree that contains this commit.  
**Not authorized by this document alone.** Do not deploy, switch production storage,
or start an engine until NJ explicitly approves this exact cutover.

**Safety (unchanged):** no real broker orders; both LIVE gates remain disabled;
preserve owner auth, Kite credentials, market history, Saved/Effective
`daily_loss_cap_inr=2995`, and **both** ledgers (legacy + new PAPER).

## Binding

| Role | Path |
|---|---|
| Active API / PAPER ledger | `/opt/nifty-radar/data/local/trading_engine_v1_paper.db` |
| PAPER broker account | `/opt/nifty-radar/data/local/trading_engine_v1_paper_account.db` |
| PAPER account lock | `/opt/nifty-radar/data/local/trading_engine_v1_paper_account.db.lock` |
| Legacy ledger (untouched) | `/opt/nifty-radar/data/local/trading_engine.db` |
| Legacy archive (SQLite backup API) | `/opt/nifty-radar/data/archives/legacy-unresolved-pre-v1-paper-cleanstart-<UTC>/` |
| Admin / ₹2995 | `/opt/nifty-radar/data/config/admin_config.db` (unchanged) |
| Auth / Kite secrets / market DBs | unchanged |

Default `api.config.trading_engine_db_path()` resolves to the V1 PAPER ledger.
LIVE execution refuses this PAPER-only namespace (path and/or
`engine_account_meta.namespace=paper_v1`) even if LIVE flags are later enabled.

Ownership:
- Archives: root-private (`0700` / `0600`).
- Active ledger, account, `*.db.lock`, WAL/SHM: `nifty-radar:nifty-radar` mode `0644`.

## Preconditions (abort if any fail)

1. Approved release **contains** clean-start commit `c07f08cd146409506950892a6a8b6fc11df823c3`. Do not run bootstrap against a tree that lacks it.
2. Observation/trading runners absent. `pgrep` exit **1** only means clear; any other
   discovery error aborts. Do **not** kill runners automatically — stop and await NJ.
3. Both LIVE gates **explicitly disabled** in discovered service EnvironmentFiles
   (`systemctl show nifty-radar-api -p EnvironmentFiles`). Missing, malformed, or
   unreadable evidence aborts. If the API unit is active, also capture process LIVE
   flags before quiescing.
4. Quiesce API writers and keep them quiesced through archive, init, ownership,
   binding verification, and service-user reopen.
5. Refuse existing PAPER targets by default. Resume only with verified complete
   identity (`namespace=paper_v1`, valid `signal_not_before`,
   `initialization_complete=1`, matching durable `paper_account_id` in both DBs).
6. Do not move/replace/delete `trading_engine.db`. Do not broadly clear `/tmp`.

## Exact cutover steps (when NJ authorizes)

```bash
set -euo pipefail
# Prefer the approved release that contains c07f08cd146409506950892a6a8b6fc11df823c3.
cd /opt/nifty-radar/current

# 0) Confirm tree contains clean-start implementation
git merge-base --is-ancestor c07f08cd146409506950892a6a8b6fc11df823c3 HEAD

# 1) Abort if observation/trading runners are present
if pgrep -af 'live_observation_runner|trading_engine_loop|live_trading_engine' \
  | grep -v 'pgrep -af'; then
  echo "ABORT: runners present — await NJ; do not kill automatically" >&2
  exit 42
fi

# 2) Verify LIVE gates disabled, capture process flags if active, quiesce API
sudo -u root /opt/nifty-radar/venv/bin/python - <<'PY'
from trading_engine_v1_paper_clean_start import (
    assert_live_disabled,
    assert_no_runners,
    quiesce_api_writers,
)
assert_no_runners()
print(assert_live_disabled(capture_running_process=True))
print(quiesce_api_writers())
PY

# 3) Archive legacy via SQLite backup API (original untouched) + init PAPER pair
sudo -u root /opt/nifty-radar/venv/bin/python - <<'PY'
from pathlib import Path
from trading_engine_v1_paper_clean_start import (
    archive_legacy_trading_ledger,
    initialize_v1_paper_ledger,
    assert_api_writers_quiesced,
    assert_live_disabled,
    assert_no_runners,
)

assert_no_runners()
assert_live_disabled(capture_running_process=False)  # already quiesced
assert_api_writers_quiesced()

manifest = archive_legacy_trading_ledger(
    archive_root=Path('/opt/nifty-radar/data/archives'),
)
print(manifest)
init = initialize_v1_paper_ledger(
    signal_not_before=None,  # durable UTC "now" ingest floor
    legacy_archive_label=manifest['label'],
    allow_resume=False,
    apply_ownership=True,
)
print(init)
PY
```

4. **Keep API stopped.** Verify binding targets:
   - Active paths: `trading_engine_v1_paper.db` + `trading_engine_v1_paper_account.db`
   - Meta: `namespace=paper_v1`, `initialization_complete=1`, valid `signal_not_before`
   - Matching `paper_account_id` in ledger meta and account snapshot
   - Ownership `nifty-radar` on ledger, account, and `*.db.lock`
   - Legacy `trading_engine.db` still present and unchanged (ADANIPORTS preserved)
5. **Service-user reopen** (must succeed; proves lock ownership + empty account):
```bash
sudo -u nifty-radar /opt/nifty-radar/venv/bin/python - <<'PY'
from pathlib import Path
from trading_engine_v1_paper_clean_start import (
    open_paper_broker_for_ledger,
    validate_paper_v1_pair,
)
ledger = Path('/opt/nifty-radar/data/local/trading_engine_v1_paper.db')
print(validate_paper_v1_pair(ledger))
broker = open_paper_broker_for_ledger(ledger, quote_provider=lambda _s: None)
assert len(broker.orders) == 0
print({'account_id': broker._account_id, 'orders': 0})
broker.close()
PY
```
6. Bind API/engine consistently to the new PAPER namespace (default path helpers;
   no `TRADING_ENGINE_DB_PATH` override pointing at legacy). Confirm LIVE still refused
   on this namespace.
7. Restart API only after steps 4–6 pass:
   `sudo systemctl start nifty-radar-api.service`
8. Authenticated checks (no engine start, no orders):
   - PAPER / MANUAL / disarmed
   - Empty new PAPER account (no trades/commands/orders)
   - Saved **and** Effective `daily_loss_cap_inr=2995`
   - Both LIVE gates still explicitly disabled
   - Auth boundaries intact; credentials untouched
9. Read-only Kite positions + working orders. Any real exposure or unavailable check
   → **hold** trading acceptance.
10. Only then continue market-data PAPER acceptance (separate gate).

## Failure recovery (safe restore of API availability)

**General rules (all failure points):**
- Do **not** delete partial PAPER files to “clean up”.
- Do **not** recreate accounts, restore databases, merge ledgers, or replay legacy
  commands.
- Preserve legacy `trading_engine.db`, any archive already written, and any partial
  or complete PAPER pair on disk.
- Restore API availability only when writers will not point at an unsafe incomplete
  PAPER binding.

### A. Failure before archive / before any PAPER files created
1. Confirm no new PAPER ledger/account appeared (or only untouched absences).
2. `sudo systemctl start nifty-radar-api.service` to restore prior API availability
   on the **previous** release binding (still legacy ledger if that was active).
3. Report the abort reason; await NJ.

### B. Failure after partial PAPER creation (incomplete pair / missing account /
initialization_complete=0 / ownership or lock failure)
1. Leave partial files in place (evidence). Do not delete, rewrite, or `allow_resume`
   over an incomplete pair.
2. Keep API **stopped** until NJ chooses one of:
   - **Code rollback only** (see below) while leaving data files untouched, or
   - Authorized follow-up that uses a **new** approved procedure (not an automatic
     recreate).
3. Do not start the engine against an incomplete pair (runtime refuses it).

### C. Failure after successful PAPER init but before/during API restart
1. PAPER pair may already be valid. Do not re-run `initialize_v1_paper_ledger`
   (`allow_resume=False` will refuse; do not force recreate).
2. Either finish binding verification + start API on the clean-start release, or
   perform **code rollback** while **keeping** the new PAPER files and legacy ledger.

### Distinguish rollback types

| Type | What it changes | What it must not do |
|---|---|---|
| **Code rollback** | Atomic `current` symlink back to prior release (e.g. pre-clean-start deploy). Restores prior code/API behavior. | Must **not** delete PAPER/legacy DBs, restore DB backups, merge ledgers, or replay legacy commands. |
| **Account-binding rollback** | Separate, explicit owner decision to stop using the V1 PAPER namespace (env/path binding). | Must **not** happen automatically with code rollback. Must **not** invent fills/P&L, delete history, or load legacy rows into PAPER. |

Default on abort: prefer restoring API availability via **code rollback** (or
restarting the prior-bound API) while preserving **all** on-disk ledgers and
archives for inspection.

## Signal boundary

`engine_account_meta.signal_not_before` is the durable clean-start floor.
Ingest/approve/setups/preview parse timezone-aware instants and reject
missing/malformed `created_at` and `trigger_exchange_ts`. An old trigger inserted
after reset cannot bypass the boundary. PAPER start/arm/entry refuse a missing or
invalid namespace/boundary/account pair.
