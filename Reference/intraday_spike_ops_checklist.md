# Intraday Spike — Live Session Ops Checklist

Operational checklist before/during/after a live session that runs the
1-minute candle pipeline with intraday spike detection (`intraday_spike_v1`).

## Before session

1. **Auth** — valid Kite access token (`python3 login.py --check-token`).
2. **Instruments** — Nifty 50 tokens present (`python3 instrument_collector.py` if needed).
3. **Historical candles** — up to date through the last completed session.
4. **Baselines** — regenerate from historical DB for the latest completed session:
   ```bash
   python3 baseline_generator.py
   ```
   Confirm `data/nifty50_baselines.db` has `baseline_as_of_date` **strictly prior**
   to today’s session date (look-ahead contract).
5. **Reliability** — prefer rows with `valid_session_count >= 18` / `is_reliable = 1`
   (generator default lookback: 21 sessions).
6. **Process start** — start (or restart) the live process **after** baselines are
   ready. `BaselineStore` loads once and freezes; mid-session baseline regeneration
   is ignored until the next process start.
7. **Config** — `IntradaySpikeRuleConfig` defaults (`intraday_spike_v1`, window
   09:30–14:00 IST inclusive). Do not hot-reload thresholds mid-session.

## During session

1. Candle writer remains the source of truth for `live_1m_candles`.
2. Spike detector is a non-fatal coordinator consumer; writer failures must not
   halt market-data ingestion.
3. Watch metrics: `candles_seen`, `eligible_candles`, `partial_skipped`,
   `baseline_miss`, `baseline_unreliable`, `accepted_spikes`, `rejected_spikes`,
   `writer_failures`.
4. Spikes land in the **same** DB: `data/nifty50_live_1m.db` → `live_intraday_spikes`.

## After session / incident

1. Join check:
   ```sql
   SELECT s.*, c.is_partial
   FROM live_intraday_spikes s
   JOIN live_1m_candles c
     ON c.instrument_token = s.instrument_token
    AND c.candle_time = s.candle_time;
   ```
2. Restart replay is safe: duplicate candle/spike PKs are ignored when payloads match.
3. Rule or threshold changes require a new `rule_version` (never overwrite old spike rows).
4. New baselines apply only after process restart.

## Table ownership

| Table | Owner | Writers |
|---|---|---|
| `live_1m_candles` | Market data | `LiveOneMinuteCandleWriter` only |
| `live_intraday_spikes` | Strategy | `IntradaySpikeWriter` only |
| `baselines` | Offline analytics | `baseline_generator.py` only (live is read-only) |

See also: [`strategy_architecture_principles.md`](strategy_architecture_principles.md).
