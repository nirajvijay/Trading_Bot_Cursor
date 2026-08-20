"""Engine cycle: ingest, state machine, trail, snapshot buckets, no double MARKET."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from trading_engine_broker import FakeBroker
from trading_engine_cycle import TradingEngineCycle, snapshot_dict
from trading_engine_store import TradingEngineStore

SCHEMA = """
CREATE TABLE live_continuation_arms (
    setup_id TEXT NOT NULL,
    continuation_rule_version TEXT NOT NULL,
    instrument_token INTEGER NOT NULL,
    tradingsymbol TEXT NOT NULL,
    session_date TEXT NOT NULL,
    direction TEXT NOT NULL,
    pullback_swing_high REAL,
    pullback_swing_low REAL,
    tick_size REAL NOT NULL,
    buffer_ticks INTEGER NOT NULL,
    trigger_price REAL NOT NULL,
    trigger_price_ticks INTEGER NOT NULL,
    pullback_type TEXT,
    ready_5m_candle_time TEXT,
    armed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (setup_id, continuation_rule_version)
);
CREATE TABLE live_continuation_decisions (
    setup_id TEXT NOT NULL,
    continuation_rule_version TEXT NOT NULL,
    decision_type TEXT NOT NULL,
    reason TEXT,
    trigger_tick_sequence INTEGER,
    trigger_exchange_ts TEXT,
    last_price REAL,
    last_price_ticks INTEGER,
    breakout_candle_time TEXT,
    breakout_candle_volume INTEGER,
    avg_prior_3_1m_volume REAL,
    volume_ok INTEGER,
    volume_reliable INTEGER,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (setup_id, continuation_rule_version)
);
"""


def _seed_live(
    path: Path,
    setup_id: str,
    created_at: str,
    *,
    swing_low: float | None = 100.0,
    symbol: str = "AAA",
) -> None:
    conn = sqlite3.connect(path)
    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='live_continuation_arms'"
    ).fetchone():
        conn.executescript(SCHEMA)
    conn.execute(
        """
        INSERT INTO live_continuation_arms (
            setup_id, continuation_rule_version, instrument_token, tradingsymbol,
            session_date, direction, pullback_swing_high, pullback_swing_low,
            tick_size, buffer_ticks, trigger_price, trigger_price_ticks,
            pullback_type, ready_5m_candle_time, armed_at, payload_json
        ) VALUES (?, 'v1', 1, ?, '2026-08-17', 'UP', 109, ?, 1, 1, 110, 110,
                  NULL, NULL, '2026-08-17T10:00:00', '{}')
        """,
        (setup_id, symbol, swing_low),
    )
    conn.execute(
        """
        INSERT INTO live_continuation_decisions (
            setup_id, continuation_rule_version, decision_type, reason,
            trigger_tick_sequence, trigger_exchange_ts, last_price, last_price_ticks,
            breakout_candle_time, breakout_candle_volume, avg_prior_3_1m_volume,
            volume_ok, volume_reliable, payload_json, created_at
        ) VALUES (?, 'v1', 'TRIGGERED', NULL, 1, '2026-08-17T10:01:16', 110.2, 110,
                  '2026-08-17T10:01:00', 100, 80, 1, 1, '{}', ?)
        """,
        (setup_id, created_at),
    )
    conn.commit()
    conn.close()


class CycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.live = Path(self.tmp.name) / "live.db"
        self.te = Path(self.tmp.name) / "te.db"
        sqlite3.connect(self.live).close()
        conn = sqlite3.connect(self.live)
        conn.executescript(SCHEMA)
        conn.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _cycle(self, broker: FakeBroker, started_at: str = "2026-08-17T04:30:00+00:00"):
        store = TradingEngineStore(self.te)
        run_id = store.start_run(
            session_date="2026-08-17", live_orders_enabled=False, pid=1
        )
        cycle = TradingEngineCycle(
            store,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at=started_at,
            run_id=run_id,
            live_orders_enabled=False,
        )
        return store, cycle

    def test_live_margin_reject_skips_market(self) -> None:
        _seed_live(self.live, "ok1", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110}, remaining_capital=0)
        store = TradingEngineStore(self.te)
        run_id = store.start_run(
            session_date="2026-08-17", live_orders_enabled=True, pid=1
        )
        cycle = TradingEngineCycle(
            store,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:30:00+00:00",
            run_id=run_id,
            live_orders_enabled=True,
        )
        cycle.tick()
        trades = store.list_trades("2026-08-17")
        self.assertEqual(trades[0].status, "rejected")
        self.assertEqual(trades[0].reject_reason, "insufficient_margin")
        self.assertEqual(broker.market_place_count, 0)
        store.close()

    def test_stale_trigger_skipped_from_poll(self) -> None:
        _seed_live(self.live, "old", "2026-08-17T03:00:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.tick()
        self.assertEqual(store.list_trades("2026-08-17"), [])
        self.assertEqual(broker.market_place_count, 0)
        store.close()

    def test_missing_stop_skipped(self) -> None:
        _seed_live(self.live, "nostop", "2026-08-17T04:40:00+00:00", swing_low=None)
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.tick()
        trades = store.list_trades("2026-08-17")
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].status, "skipped")
        self.assertEqual(trades[0].skip_reason, "missing_stop")
        self.assertEqual(broker.market_place_count, 0)
        store.close()

    def test_happy_path_protected_open_and_duplicate(self) -> None:
        _seed_live(self.live, "ok1", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.tick()
        trades = store.list_trades("2026-08-17")
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].status, "protected_open")
        count = broker.market_place_count
        cycle.tick()
        self.assertEqual(len(store.list_trades("2026-08-17")), 1)
        self.assertEqual(broker.market_place_count, count)
        store.close()

    def test_restart_reconcile_no_second_market(self) -> None:
        _seed_live(self.live, "ok1", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        tag = trade.broker_tag
        first_count = broker.market_place_count
        store.close()

        store2 = TradingEngineStore(self.te)
        run = store2.latest_run()
        cycle2 = TradingEngineCycle(
            store2,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:30:00+00:00",
            run_id=str(run["run_id"]),
            live_orders_enabled=False,
        )
        cycle2.tick()
        self.assertEqual(broker.market_place_count, first_count)
        self.assertEqual(len(broker.orders_by_tag(tag)), 2)  # market + slm
        store2.close()

    def test_unprotected_blocks_second_and_snapshot_critical(self) -> None:
        _seed_live(self.live, "a", "2026-08-17T04:40:00+00:00", symbol="AAA")
        _seed_live(self.live, "b", "2026-08-17T04:41:00+00:00", symbol="BBB")
        broker = FakeBroker(
            last_prices={"AAA": 110, "BBB": 110},
            auto_fill_entry=True,
            auto_confirm_sl=False,
        )
        store, cycle = self._cycle(broker)
        cycle.tick()
        trades = store.list_trades("2026-08-17")
        statuses = {t.setup_id: t.status for t in trades}
        self.assertEqual(statuses["a"], "stop_pending")
        self.assertEqual(statuses["b"], "rejected")
        self.assertEqual(trades[1].reject_reason if trades[0].setup_id == "a" else trades[0].reject_reason, "unprotected_lockout")
        snap = snapshot_dict(
            store,
            session_date="2026-08-17",
            total_capital=300000,
            leverage_factor=5,
            live_orders_enabled=False,
            running=True,
        )
        self.assertEqual(snap["state"], "critical")
        self.assertEqual(len(snap["active"]), 1)
        self.assertEqual(len(snap["skipped"]), 1)
        self.assertTrue(any(r["status"] == "stop_pending" for r in snap["active"]))
        store.close()

    def test_trail_into_profit_zero_remaining(self) -> None:
        _seed_live(self.live, "ok1", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        self.assertEqual(trade.status, "protected_open")
        updated = cycle.apply_trail(trade.trade_id, 111.0, last_price=112.0)
        self.assertEqual(updated.current_stop, 111.0)
        from trading_engine_risk import trade_remaining_risk

        self.assertEqual(trade_remaining_risk(updated), 0.0)
        events = store.list_events(trade.trade_id)
        actions = [e["action"] for e in events]
        self.assertIn("sl_modified", actions)
        with self.assertRaises(ValueError):
            cycle.apply_trail(trade.trade_id, 90.0, last_price=112.0)
        store.close()

    def test_snapshot_three_buckets(self) -> None:
        _seed_live(self.live, "ok1", "2026-08-17T04:40:00+00:00")
        _seed_live(self.live, "miss", "2026-08-17T04:41:00+00:00", swing_low=None, symbol="ZZZ")
        broker = FakeBroker(last_prices={"AAA": 110, "ZZZ": 110})
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = [t for t in store.list_trades("2026-08-17") if t.status == "protected_open"][0]
        broker.fill_sl(trade.sl_order_id, 99.0)
        cycle.tick()
        snap = snapshot_dict(
            store,
            session_date="2026-08-17",
            total_capital=300000,
            leverage_factor=5,
            live_orders_enabled=False,
            running=True,
        )
        self.assertEqual(len(snap["closed"]), 1)
        self.assertEqual(len(snap["skipped"]), 1)
        self.assertEqual(snap["closed"][0]["close_reason"], "sl_hit")
        self.assertLess(snap["closed"][0]["realised_pnl"], 0)
        store.close()


if __name__ == "__main__":
    unittest.main()
