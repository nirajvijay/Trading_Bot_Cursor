# Strategy Architecture Principles

Binding constitution for NIFTY RADAR strategy code. Market-data ingestion may evolve independently; strategy stages (spike, pullback, continuation, risk, execution) must obey these rules to prevent architectural drift.

## Principles

1. **Market-data modules must never import strategy modules.**  
   Tick receiver, normalizers, candle builder, candle writer, aggregators, and historical/baseline generators must not depend on spike, pullback, risk, or execution packages. Only a thin top-level entrypoint or coordinator may compose both sides.

2. **Strategy modules must never modify market-data tables.**  
   Strategy may read `live_1m_candles` and baseline tables. It may write only strategy-owned tables (e.g. `live_intraday_spikes`). Never `UPDATE`/`DELETE` candle or baseline market-data rows from strategy code.

3. **Rule engines must be pure and deterministic.**  
   Feature calculators and `evaluate(features, config) -> decision` have no I/O, no wall-clock in decision math, no hidden globals, and no mutable shared strategy state. Same inputs always yield the same decision.

4. **Strategy configuration must be immutable for the entire session.**  
   Rule config and the loaded baseline snapshot are fixed at process/session start. No mid-session threshold hot-reload and no baseline refresh.

5. **Every strategy decision must be reproducible from persisted data.**  
   Offline replay from the candle row, baseline identity (`baseline_as_of_date` and inputs), persisted features, configuration, and `rule_version` must reproduce accept/reject. Persist features and baseline context, not only the boolean outcome.

6. **Strategy stages must communicate through immutable events, not shared mutable state.**  
   Stages exchange frozen events (`IntradaySpikeEvent`, later pullback/setup events) or read durable tables. No cross-stage mutable registries, in-place flags on candles, or hidden side channels.

7. **Historical strategy rows must never be updated in place.**  
   Changed logic or thresholds require a new `rule_version` and new result rows. Prior rows remain queryable for audit, tuning, and comparison.

## Lightweight enforcement

| Mechanism | Expectation |
|---|---|
| Import-direction checks | Tests or packaging boundaries: market-data modules fail CI if they import strategy modules. |
| Code-review checklist | Reviewers verify all seven principles on every strategy PR. |
| Explicit table ownership | Document which module owns each table; market-data vs strategy ownership is non-overlapping. |
| Strategy writers scoped | Strategy writers insert only into strategy tables; never into `live_1m_candles` or baseline tables. |
| Rule purity tests | Unit tests call rule engines twice with identical inputs and assert identical outputs; no DB/network in those tests. |
| Config freeze | After initialization, config/baseline store expose no reload or mutate API for the session. |
| Persistence conflicts | Use insert-or-ignore (or equivalent) plus conflict detection on divergent payloads; never overwrite historical strategy rows. |

## Composition note

`MarketDataCoordinator` (or equivalent entrypoint) is the allowed composition seam: it may import market-data and strategy consumers. Individual market-data libraries must remain strategy-free.
