# Intraday Continuation Trigger — Ops Checklist

Observation-only. No orders.

## Preflight

- [ ] Instruments DB present and refreshed (`data/nifty50_instruments.db`)
- [ ] Every subscribed token has positive `tick_size` in `instrument_data` JSON
- [ ] Runner exits non-zero if tick_size preflight fails (no 0.05 fallback)
- [ ] Live DB path writable; continuation tables created on open:
  - `live_continuation_arms`
  - `live_continuation_decisions`

## Wiring order (must match live and replay)

For each tick:

1. `OneMinuteCandleBuilder.on_tick` → coordinator (1m/5m persist → spike → pullback)
2. `ContinuationEngine.on_tick`

Pullback structural terminals on a boundary tick take precedence over continuation evaluation on that same tick.

## Session checks

- [ ] READY freezes `pullback_swing_high` / `pullback_swing_low` (impulse excluded)
- [ ] Arm row appears in `live_continuation_arms` after READY
- [ ] Price reach uses tick-normalized `>=` / `<=` (buffer already beyond swing)
- [ ] Volume confirm: in-progress 1m volume `>` mean of prior 3 eligible completed 1m
- [ ] Exactly one row in `live_continuation_decisions` per `(setup_id, rule_version)`
- [ ] After TRIGGERED or REJECTED: pullback active registry cleared
- [ ] Later spike for same token can create a new setup immediately (no cooldown)

## Failure modes

- [ ] Unreliable / insufficient volume → `CONTINUATION_REJECTED` (not pullback structural INVALIDATED)
- [ ] Continuation decision remains authoritative if pullback audit write fails
- [ ] Continuation writer conflict → subsystem degrade; market data continues

## Offline validation

```bash
python3 -m unittest tests.test_intraday_continuation_core tests.test_import_boundaries -v
python3 smoke_test_intraday_continuation_replay.py
```

## Live observation

```bash
python3 live_observation_runner.py --duration-minutes 60
```

Confirm METRICS lines include `cont_trig` / `cont_rej` and end-of-run continuation DB counts.
