# Continuation Trigger Engine — Live Validation Report

**Report generated:** 2026-07-24 (IST)
**Validator role:** Senior QA / production-readiness review
**Scope:** Observation only — no orders, no execution engine, no strategy rule changes

---

## 1. Overall Verdict

### **PASS**

The 60-minute live observation run completed successfully with healthy infrastructure, zero writer/degraded/audit failures, schema integrity confirmed, and natural live continuation events covering **TRIGGERED**, **REJECTED**, and **DISARMED** outcomes. Trigger-price math, tick-normalized comparisons, volume gating, pullback active-release, and boundary-tick ordering all validated.

**Recommendation:** Ready to mark Continuation Trigger Engine **live-validated** for observation pipeline integration. Execution wiring remains a separate phase.

---

## 2. Test Timing

| Field | Value |
|-------|-------|
| Start (IST) | 2026-07-24 13:29:35 |
| End (IST) | 2026-07-24 14:29:36 (`duration_elapsed`) |
| Logged shutdown | 2026-07-24 14:30:31 |
| Actual duration | ~60 minutes |
| Market status | **Open** (NSE, Friday, 13:29–14:30 IST window) |
| Exit code | **0** (clean shutdown; integrity OK; spike-once check OK) |

---

## 3. Git State (pre-test)

| Field | Value |
|-------|-------|
| Branch | `main` |
| Commit | `0580fa2847f830bbc635e0fcd747379f07d9ef2d` |
| Uncommitted changes | Yes — continuation engine files and pullback integration modifications present (untracked + modified) |

---

## 4. Commands Executed

```bash
# Credential check (masked output)
python3 -c "from login import check_access_token; ..."

# Pre-run tests
python3 -m unittest tests.test_intraday_continuation_core tests.test_import_boundaries -v
python3 -m unittest tests.test_intraday_pullback_core tests.test_market_data_coordinator tests.test_live_candle_pipeline -v
python3 smoke_test_intraday_continuation_replay.py

# Database baseline
python3 logs/continuation_live_validation/validation_queries.py baseline 2026-07-24

# Live observation (60 minutes)
python3 live_observation_runner.py --duration-minutes 60

# Post-run validation
python3 logs/continuation_live_validation/validation_queries.py integrity 2026-07-24

# Final regression
python3 -m unittest discover -s tests -v
python3 smoke_test_intraday_continuation_replay.py
```

**Note:** No project virtual environment was found (`venv`/`.venv` absent). Used system `python3` (3.9). All tests passed.

**Live DB path:** `data/nifty50_live_1m.db`
**Instruments DB:** `data/nifty50_instruments.db`

---

## 5. Pre-Run Test Results

| Suite | Result |
|-------|--------|
| `tests.test_intraday_continuation_core` | **15/15 OK** |
| `tests.test_import_boundaries` | included above |
| `tests.test_intraday_pullback_core` | **OK** |
| `tests.test_market_data_coordinator` | **OK** |
| `tests.test_live_candle_pipeline` | **OK** |
| `smoke_test_intraday_continuation_replay.py` | **10/10 OK** (pre-run) |

Access token check: **OK** (user ID masked: `LS****`)

---

## 6. Live Startup Checks

| Check | Result |
|-------|--------|
| Authentication | PASS — access token valid |
| Tick subscription | PASS — KiteTicker connected, 50 tokens |
| Tick-size preflight | PASS — `tick_size preflight OK for 50 tokens` |
| No 0.05 fallback | PASS — preflight loads per-instrument tick_size from instruments DB |
| Continuation writer init | PASS — tables created on open |
| Pullback/continuation callbacks wired | PASS — `on_pullback_ready`, `on_setup_terminal`, `continuation.on_tick` |
| 1m/5m builders init | PASS |
| No execution/order module | PASS — observation-only runner |
| Degraded mode at start | PASS — `degraded=0` |
| Ticks received | PASS — 882 enqueued within first 10s; feed healthy |

**Subscribed instruments:** 50 (Nifty 50)
**Tick-size validated:** 50/50

**Pre-existing warnings (non-blocking):**
- EMA seed coverage: 1/50 ready (`warmup_unavailable=49`) — affects pullback EMA-ready path only; shallow pullbacks still functioned
- Baseline coverage: 50/50 (baseline_as_of: 2026-07-21)

---

## 7. Runtime Metrics Snapshots

| Time (approx) | 1m tokens | 5m tokens | Spikes acc | Setups | cont_trig | cont_rej | writer_fail | degraded |
|---------------|-----------|-----------|------------|--------|-----------|----------|-------------|----------|
| 13:29 (start) | 0→50 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ~13:40 | 50 | 50 | 3 | 3 | 0 | 0 | 0 | 0 |
| ~13:55 | 50 | 50 | 7 | 6 | 0 | 0 | 0 | 0 |
| ~14:00 | 50 | 50 | 14 | 9 | 0 | 1 | 0 | 0 |
| ~14:10 | 50 | 50 | 17 | 10 | 0 | 2 | 0 | 0 |
| 14:29 (end) | 50 | 50 | 17 | 10 | **1** | **3** | 0 | 0 |

**Final tick count (receiver health):** 145,762 ticks enqueued
**Final 1m candles logged this run:** 3,050 new (session total 6,100)
**Final 5m candles logged this run:** 600 new (session total 1,150)

**End-of-run continuation metrics:**
- arms=8, triggered=1, rejected_vol=3, rejected_hist=0, rejected_unrel=0
- disarmed=3, audit_sync_fail=0, degraded=0

---

## 8. Errors, Warnings, Reconnects

| Category | Count | Notes |
|----------|-------|-------|
| Process crash | 0 | Clean `duration_elapsed` shutdown |
| Exceptions | 0 | No ERROR/Exception lines in log |
| Writer failures | 0 | All subsystems |
| Callback failures | 0 | |
| Audit-sync failures | 0 | |
| Degraded continuation | 0 | |
| Feed reconnects | 0 | `connected=True` throughout |
| Invalid ticks | 0 | `invalid=0` on all health lines |
| Feed stale | 0 | |

**Non-failure warnings:**
- urllib3 LibreSSL warning (environment; pre-existing)
- EMA seed missing for 49/50 tokens (pre-existing data gap)
- 50 incomplete 5m buckets discarded at shutdown (expected end-of-session behavior)

---

## 9. Database Counts

### Before (baseline at 13:28 IST)

| Table | Count |
|-------|-------|
| live_1m_candles | 3,050 |
| live_5m_candles | 550 |
| live_intraday_spikes | 54 |
| live_pullback_setups | 36 |
| live_pullback_setup_events | 147 |
| live_continuation_arms | 0 |
| live_continuation_decisions | 0 |
| Active CONTINUATION_MONITORING | 0 |

### After (14:29 IST)

| Table | Count |
|-------|-------|
| live_1m_candles | 6,100 |
| live_5m_candles | 1,150 |
| live_intraday_spikes | 71 |
| live_pullback_setups | 46 |
| live_pullback_setup_events | 203 |
| live_continuation_arms | 8 |
| live_continuation_decisions | 7 |
| Active CONTINUATION_MONITORING | 0 |

### Delta (this run)

| Table | Δ |
|-------|---|
| 1m candles | +3,050 |
| 5m candles | +600 |
| spikes | +17 |
| pullback setups | +10 |
| pullback events | +56 |
| continuation arms | +8 |
| continuation decisions | +7 |

**Rule version:** `intraday_continuation_v1`
**Max 1m timestamp after run:** `2026-07-24T14:29:00+05:30`
**Max 5m timestamp after run:** `2026-07-24T14:25:00+05:30`

---

## 10. Continuation Decisions by Type (this run)

| Type | Count |
|------|-------|
| TRIGGERED | 1 |
| REJECTED | 3 |
| DISARMED | 3 |
| Still ARMED at shutdown | 1 (HINDUNILVR — no trigger reach before timer) |

---

## 11. Duplicate / Orphan / Schema Integrity

| Check | Result |
|-------|--------|
| Duplicate arms `(setup_id, rule_version)` | **0** |
| Duplicate decisions `(setup_id, rule_version)` | **0** |
| Orphan decisions (no matching arm) | **0** |
| Invalid tick_size (≤0 or null) | **0** |
| Null trigger levels | **0** |
| Invalid decision types | **0** |
| Runner integrity check | **PASS** (no duplicate PKs, no partial 5m rows) |

All 8 arms have valid setup_id, instrument_token, direction, tick_size, buffer_ticks, trigger_price, trigger_price_ticks, rule_version.

---

## 12. Trigger-Price Verification

All 8 arms created during this run: **trigger math PASS** (0 issues).

Formula verified per arm:
- **UP:** `trigger_price_ticks = round(pullback_swing_high / tick_size) + buffer_ticks`
- **DOWN:** `trigger_price_ticks = round(pullback_swing_low / tick_size) - buffer_ticks`
- **Display price:** `trigger_price ≈ trigger_price_ticks × tick_size`

**Notable edge cases observed live:**
- **TMPV (TRIGGERED):** swing_high=325.65, tick_size=0.05 → trigger at 6514 ticks (₹325.70). `last_price_ticks=6514` — inclusive `>=` reach confirmed (not two-tick buffer).
- **BAJFINANCE (REJECTED):** `last_price_ticks=10168` exactly equals trigger — price reached but volume failed. Correct rejection.

---

## 13. Volume Verification

| Symbol | Decision | Price reached | vol_reliable | avg3 available | breakout > avg3 | Consistent |
|--------|----------|---------------|--------------|----------------|-----------------|------------|
| TATACONSUM | REJECTED | Yes (10964 ≥ 10958) | 1 | Yes (6179) | No (5254) | Yes — `failed_breakout_volume_confirmation` |
| BAJAJFINSV | REJECTED | Yes (18742 ≤ 18744) | 1 | Yes (3374) | No (1567) | Yes |
| BAJFINANCE | REJECTED | Yes (10168 ≥ 10168) | 1 | Yes (53555) | No (9645) | Yes |
| TMPV | TRIGGERED | Yes (6514 ≥ 6514) | 1 | Yes (7260) | Yes (11254) | Yes |
| ULTRACEMCO | DISARMED | N/A | N/A | N/A | N/A | Yes — structural before breakout |
| SHRIRAMFIN | DISARMED | N/A | N/A | N/A | N/A | Yes — `excessive_retracement` |
| ETERNAL | DISARMED | N/A | N/A | N/A | N/A | Yes — `excessive_retracement` |
| HINDUNILVR | ARMED | No reach | — | — | — | N/A at shutdown |

No TRIGGERED with unreliable volume. No TRIGGERED with insufficient history. No look-ahead flags detected.

---

## 14. Pullback Active-Release Verification

| Outcome | Symbol | Continuation persisted first | Pullback audit event | Terminal state | Active cleared |
|---------|--------|-------------------------------|----------------------|----------------|--------------|
| REJECTED | TATACONSUM | Yes | `CONTINUATION_REJECTED` | `CONTINUATION_REJECTED` | Yes |
| REJECTED | BAJAJFINSV | Yes | `CONTINUATION_REJECTED` | `CONTINUATION_REJECTED` | Yes |
| REJECTED | BAJFINANCE | Yes | `CONTINUATION_REJECTED` | `CONTINUATION_REJECTED` | Yes |
| TRIGGERED | TMPV | Yes | `CONTINUATION_TRIGGERED` | `CONTINUATION_TRIGGERED` | Yes |
| DISARMED | ULTRACEMCO/SHRIRAMFIN/ETERNAL | Yes | N/A (structural INVALIDATED) | `INVALIDATED` | Yes |

**Post-run active CONTINUATION_MONITORING count:** 0 (HINDUNILVR was armed in-memory at shutdown but no blocking active registry issue — run ended cleanly).

**Re-spike after continuation terminal:**
- **Live-observed:** MAXHEALTH received a new spike at 13:47 after prior setup invalidated at 13:45 (demonstrates active registry release).
- **Test-verified:** Immediate re-spike after `CONTINUATION_REJECTED` covered by `test_reject_clears_active_and_allows_new_spike` and replay smoke (TATACONSUM had only one spike this session — no natural post-reject re-spike).

**Audit-sync failures:** 0

---

## 15. Boundary-Tick Ordering Verification

### Code-based (authoritative)

`live_candle_pipeline.py` enforces:
1. `OneMinuteCandleBuilder.on_tick(tick)`
2. Coordinator path (1m persist → strategy → 5m → pullback)
3. `ContinuationEngine.on_tick(tick)` via `tick_consumers`

Documented in module docstring and verified by `test_builder_before_tick_consumer`.

### Runtime evidence

- ULTRACEMCO: `PULLBACK_READY` + `CONTINUATION_ATTEMPT` at 13:55:02, then `INVALIDATED` at 14:00:01 — structural invalidation on 5m boundary preceded continuation disarm; no spurious trigger after invalidation.
- TMPV: `CONTINUATION_TRIGGERED` at 14:16:52 after tick-level price/volume evaluation — consistent with tick consumer running after candle path.

**Live/replay ordering:** Identical pipeline wiring; replay smoke 10/10 post-run.

---

## 16. Manual Event Samples

### TRIGGERED (1/1)

**TMPV** — UP setup
- Impulse excluded; READY candle at 14:05 included in swing freeze
- `pullback_swing_high=325.65`, `tick_size=0.05`, `buffer=1`
- Trigger: 6514 ticks (₹325.70)
- Breakout volume: 11,254 > avg3: 7,260.3
- Pullback closed: `CONTINUATION_TRIGGERED`

### REJECTED (3/3)

1. **TATACONSUM** — price 10964 ≥ trigger 10958; vol 5254 < avg3 6179
2. **BAJAJFINSV** — price 18742 ≤ trigger 18744; vol 1567 < avg3 3374
3. **BAJFINANCE** — price exactly at trigger; vol 9645 < avg3 53555

### DISARMED (3/3)

1. **ULTRACEMCO** — `impulse_extreme_close_break` (INVALIDATED before breakout)
2. **SHRIRAMFIN** — `excessive_retracement`
3. **ETERNAL** — `excessive_retracement`

---

## 17. Final Regression Test Results

| Suite | Result |
|-------|--------|
| Full test suite (`unittest discover -s tests`) | **210/210 OK** |
| Continuation replay smoke | **10/10 OK** (post-run) |
| Live DB consistency | **PASS** |
| Historical/baseline DBs | **Unmodified** (append-only live writes) |

---

## 18. Replay Result

```
Result: OK (10/10)
```

Post-live replay confirms deterministic behavior unchanged.

---

## 19. Deviations from Approved Architecture

| Item | Status |
|------|--------|
| Tick-normalized `>=` / `<=` comparisons | Implemented as designed |
| UNIQUE terminal decision PK | Confirmed |
| Continuation authoritative storage | Confirmed |
| Pullback audit derived only | Confirmed |
| Boundary-tick precedence | Confirmed in code + runtime |
| Active registry clear after TRIGGERED/REJECTED | Confirmed live |
| No execution wiring | Confirmed |

**Minor operational notes (not architecture deviations):**
- No project venv used (system Python)
- EMA seed gap (49/50) limits EMA-ready pullbacks but does not affect continuation engine correctness

---

## 20. Remaining Risks / Limitations

1. **EMA seed coverage (1/50):** Most instruments cannot reach EMA-ready pullback path until historical seed data is backfilled.
2. **Chart reconciliation:** Runner explicitly notes chart review is out of scope; manual chart validation against broker charts not performed in this run.
3. **HINDUNILVR arm still open at shutdown:** Expected — run timer expired while armed; no integrity issue.
4. **Post-reject re-spike not naturally observed** for a continuation-rejected symbol this hour (covered by automated tests).
5. **LibreSSL/urllib3 warning** in Python 3.9 environment — monitor for TLS issues in production.

---

## 21. Final Recommendation

**Ready to mark Continuation Trigger Engine live-validated** for the observation pipeline.

Next phase (separate): wire execution consumer on `ContinuationTriggeredEvent` with risk management — explicitly out of scope for this test.

---

## Appendix: Raw Log

**Path:** `logs/continuation_live_validation/live_run_20260724_132900.log`
