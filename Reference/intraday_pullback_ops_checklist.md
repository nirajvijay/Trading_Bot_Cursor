# Intraday Pullback — Live Session Ops Checklist

Operational checklist for the Live 5-Minute Pullback Detection Engine
(`intraday_pullback_v1`) composed with spike detection and live 1m/5m candles.

## Before session

1. **Auth / instruments / historical / baselines** — same as spike checklist.
2. **Historical 5m** — ensure `candles_5m` exists for prior session (EMA20 seed).
3. **Process start** — use the live observation runner (preferred):

   ```bash
   python3 live_observation_runner.py --duration-minutes 60
   ```

   Or construct coordinator with:
   - `LiveOneMinuteCandleWriter`
   - `LiveFiveMinuteCandleBuilder` + `LiveFiveMinuteCandleWriter`
   - `IntradaySpikeDetector` (`on_spike` → pullback engine)
   - `IntradayPullbackEngine` as 5m consumer

   Fan-out order: 1m persist → 5m persist → spike → pullback.
4. Load `PullbackEmaSeedStore` for today's `session_date` before accepting spikes.
   Missing seeds are explicit (no fabricated defaults). `EMA_PULLBACK` requires a ready EMA;
   shallow structure pullbacks may still classify without EMA by design.
5. Config frozen: `IntradayPullbackRuleConfig` defaults (30–60% retrace, 2–7 candles).
6. Mid-session starts discard the first incomplete 5m bucket per token (complete five 1m bars required).
7. `restore_session` is restart hardening and may be deferred; a first run may restore 0 setups.

## During session

1. Persist order: 1m candle → 5m builder/write → 1m strategy (spike) → 5m strategy (pullback).
2. New spikes only 09:30–14:00 IST; active setups continue after 14:00.
3. One active setup per stock; one pullback lifecycle per spike.
4. Pullback writer failure → subsystem **degraded** (candles continue; no further pullback transitions).
5. Watch metrics: `setups_created`, `pullback_ready_ema`, `pullback_ready_shallow`,
   `invalidated`, `expired`, `spike_ignored_while_active`, `subsystem_degraded`.

## After session / incident

1. Reconstruct state from `live_pullback_setup_events` (latest event per `setup_id`).
2. Restart replay is idempotent for setups/events with matching payloads.
3. Rule/threshold changes require a new `pullback_rule_version`.
4. Filter all verification SQL by the runner's explicit IST `session_date` (never `date('now')`).

## Later full-session strategy validation

A later full-session (09:15→close) strategy-validation run **must** compare detections
against 1m and 5m market charts (spike candle, impulse high/low, pullback depth/count,
invalidation). Chart review is out of scope for short integration / observation runs.

## Table ownership

| Table | Owner | Writers |
|---|---|---|
| `live_1m_candles` | Market data | `LiveOneMinuteCandleWriter` only |
| `live_5m_candles` | Market data | `LiveFiveMinuteCandleWriter` only |
| `live_intraday_spikes` | Strategy | `IntradaySpikeWriter` only |
| `live_pullback_setups` | Strategy | `IntradayPullbackWriter` only |
| `live_pullback_setup_events` | Strategy | `IntradayPullbackWriter` only |
| `baselines` / historical `candles_5m` | Offline | generators; live read-only for EMA seed |

See also: [`strategy_architecture_principles.md`](strategy_architecture_principles.md),
[`intraday_spike_ops_checklist.md`](intraday_spike_ops_checklist.md).
