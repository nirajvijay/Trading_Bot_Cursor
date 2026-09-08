"""Engine cycle: ingest, state machine, trail, snapshot buckets, no double MARKET."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from api.admin_config.store import AdminConfigStore
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


def _seed_vwap(
    path: Path,
    setup_id: str,
    *,
    classification: str = "ACCEPT",
    session_date: str = "2026-08-17",
    continuation_rule_version: str = "v1",
    vwap_rule_version: str = "vwap_qualifier_v2",
) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS live_vwap_qualifications (
            setup_id TEXT NOT NULL,
            continuation_rule_version TEXT NOT NULL,
            vwap_rule_version TEXT NOT NULL,
            instrument_token INTEGER NOT NULL,
            tradingsymbol TEXT NOT NULL,
            session_date TEXT NOT NULL,
            direction TEXT NOT NULL,
            trigger_price REAL NOT NULL,
            last_price REAL NOT NULL,
            trigger_tick_sequence INTEGER NOT NULL,
            trigger_exchange_ts TEXT NOT NULL,
            vwap REAL,
            gap REAL,
            classification TEXT NOT NULL,
            quality_ok INTEGER NOT NULL,
            quality_reason TEXT,
            vwap_provenance TEXT,
            bootstrap_cutoff_exchange_ts TEXT,
            completed_5m_count INTEGER NOT NULL,
            in_progress_bucket_start TEXT,
            in_progress_volume INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (session_date, setup_id, continuation_rule_version, vwap_rule_version)
        )
        """
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO live_vwap_qualifications (
            setup_id, continuation_rule_version, vwap_rule_version,
            instrument_token, tradingsymbol, session_date, direction,
            trigger_price, last_price, trigger_tick_sequence, trigger_exchange_ts,
            vwap, gap, classification, quality_ok, quality_reason, vwap_provenance,
            bootstrap_cutoff_exchange_ts, completed_5m_count, in_progress_bucket_start,
            in_progress_volume, payload_json, created_at
        ) VALUES (?, ?, ?, 1, 'AAA', ?, 'UP', 110, 110, 1, 't',
                  110, 0.001, ?, 1, NULL, 'live', NULL, 1, NULL, 0, '{}', 'c')
        """,
        (setup_id, continuation_rule_version, vwap_rule_version, session_date, classification),
    )
    conn.commit()
    conn.close()


def _ensure_vwap_table(path: Path) -> None:
    _seed_vwap(path, "__ensure__", session_date="1999-01-01")
    conn = sqlite3.connect(path)
    conn.execute(
        "DELETE FROM live_vwap_qualifications WHERE setup_id='__ensure__'"
    )
    conn.commit()
    conn.close()


def _seed_live(
    path: Path,
    setup_id: str,
    created_at: str,
    *,
    swing_low: float | None = 100.0,
    symbol: str = "AAA",
    vwap: str | None = "ACCEPT",
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
    if vwap is not None:
        _seed_vwap(path, setup_id, classification=vwap)


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

    def _cycle(
        self,
        broker: FakeBroker,
        started_at: str = "2026-08-17T04:30:00+00:00",
        *,
        require_vwap_accept: bool = True,
        monotonic_fn=None,
        admin_config_db: Path | None = None,
    ):
        store = TradingEngineStore(self.te)
        run_id = store.start_run(
            session_date="2026-08-17",
            live_orders_enabled=False,
            pid=1,
            require_vwap_accept=require_vwap_accept,
        )
        kwargs = {}
        if monotonic_fn is not None:
            kwargs["monotonic_fn"] = monotonic_fn
        if admin_config_db is not None:
            kwargs["admin_config_db"] = admin_config_db
        cycle = TradingEngineCycle(
            store,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at=started_at,
            run_id=run_id,
            live_orders_enabled=False,
            require_vwap_accept=require_vwap_accept,
            **kwargs,
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
        self.assertEqual(statuses["a"], "protection_pending")
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
        self.assertTrue(any(r["status"] == "protection_pending" for r in snap["active"]))
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
        from trading_engine_risk import estimated_cost_per_share, trade_remaining_risk

        # Price downside is zero once stop is in profit, but costs remain reserved.
        costs = estimated_cost_per_share(
            float(updated.entry_fill or updated.entry_estimate)
        ) * float(updated.remaining_position_qty or updated.qty)
        self.assertAlmostEqual(trade_remaining_risk(updated), costs, places=6)
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

    def test_stale_stop_engine_does_not_pause_new_run(self) -> None:
        _seed_live(self.live, "ok1", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        store._conn.execute(
            """
            INSERT INTO engine_commands (kind, payload_json, created_at)
            VALUES ('stop_engine', '{}', '2026-08-17T04:00:00+00:00')
            """
        )
        store._conn.commit()
        cycle.tick()
        self.assertTrue(cycle.consume_new_triggers)
        self.assertEqual(len(store.list_trades("2026-08-17")), 1)
        self.assertEqual(store.pending_commands(), [])
        store.close()

    def test_external_flatten_closes_active_trade(self) -> None:
        _seed_live(self.live, "ok1", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        self.assertEqual(trade.status, "protected_open")
        store.update_trade(trade.trade_id, entry_time="2026-08-17T04:40:00+00:00")
        broker.simulate_external_flatten(
            "AAA",
            112.0,
            order_timestamp="2026-08-17T04:55:00+00:00",
        )
        cycle.tick()
        closed = store.get_trade(trade.trade_id)
        assert closed is not None
        self.assertEqual(closed.status, "closed")
        self.assertEqual(closed.close_reason, "external_exit")
        self.assertGreater(closed.realised_pnl, 0)
        sl = broker.poll_order(trade.sl_order_id or "")
        self.assertIsNotNone(sl)
        assert sl is not None
        self.assertEqual(str(sl.status).upper(), "CANCELLED")
        store.close()

    def test_cancelled_sl_becomes_unprotected(self) -> None:
        _seed_live(self.live, "ok1", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        first_sl = trade.sl_order_id
        places = broker.slm_place_count
        broker.cancel_order(trade.sl_order_id or "")
        cycle.tick()
        updated = store.get_trade(trade.trade_id)
        assert updated is not None
        # Residual exposure must be re-protected (new working stop), not left stranded.
        self.assertGreater(broker.slm_place_count, places)
        self.assertNotEqual(updated.sl_order_id, first_sl)
        self.assertIn(updated.status, {"protected_open", "protection_pending", "stop_pending"})
        self.assertGreater(int(updated.remaining_position_qty or 0), 0)
        store.close()

    def test_auto_trail_tightens_with_price(self) -> None:
        """Staged-R: +1R..+2R tightens stop (1R behind extreme); not tick-gap."""
        _seed_live(self.live, "ok1", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        # R = |110-99| = 11; mark +1.5R → 126.5 rounded via LTP 127.
        if not trade.auto_trail_enabled:
            cycle.set_auto_trail(trade.trade_id, enabled=True)
        broker.last_prices["AAA"] = 127
        store.update_trade(trade.trade_id, last_trail_modify_at=None)
        cycle._reset_quote_cache()
        cycle.apply_auto_trails()
        updated = store.get_trade(trade.trade_id)
        assert updated is not None
        self.assertTrue(updated.auto_trail_enabled)
        self.assertGreater(updated.current_stop or 0, trade.current_stop or 0)
        store.close()

    def test_auto_trail_staged_r_below_one_r_keeps_structural(self) -> None:
        """Below +1R favorable excursion, staged-R leaves structural stop alone."""
        _seed_live(self.live, "ok1", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        self.assertEqual(trade.current_stop, 99.0)
        if not trade.auto_trail_enabled:
            cycle.set_auto_trail(trade.trade_id, enabled=True)
        # +1 tick only (~0.09R) — must not trail.
        broker.last_prices["AAA"] = 111
        store.update_trade(trade.trade_id, last_trail_modify_at=None)
        cycle._reset_quote_cache()
        cycle.apply_auto_trails()
        moved = store.get_trade(trade.trade_id)
        assert moved is not None
        self.assertEqual(moved.current_stop, 99.0)
        # After +1.5R, stop tightens; subsequent adverse tick must not widen.
        broker.last_prices["AAA"] = 127
        store.update_trade(trade.trade_id, last_trail_modify_at=None)
        cycle._reset_quote_cache()
        cycle.apply_auto_trails()
        tightened = store.get_trade(trade.trade_id)
        assert tightened is not None
        self.assertGreater(float(tightened.current_stop or 0), 99.0)
        prior = float(tightened.current_stop or 0)
        broker.last_prices["AAA"] = 120
        store.update_trade(trade.trade_id, last_trail_modify_at=None)
        cycle._reset_quote_cache()
        cycle.apply_auto_trails()
        chopped = store.get_trade(trade.trade_id)
        assert chopped is not None
        self.assertEqual(float(chopped.current_stop or 0), prior)
        store.close()

    def test_live_open_pnl_uses_kite_position_pnl(self) -> None:
        from trading_engine_cycle import snapshot_dict
        from trading_engine_types import PositionQuote

        _seed_live(self.live, "ok1", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 200})
        store, cycle = self._cycle(broker)
        cycle.live_orders_enabled = True
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        broker.position_quotes["AAA"] = PositionQuote(
            quantity=trade.qty,
            average_price=111.0,
            last_price=115.0,
            pnl=55.5,
            unrealised=55.5,
        )
        cycle._reset_quote_cache()
        cycle.mark_to_market()
        updated = store.get_trade(trade.trade_id)
        assert updated is not None
        self.assertAlmostEqual(updated.open_pnl, 55.5)
        self.assertAlmostEqual(updated.entry_fill or 0, 111.0)
        snap = snapshot_dict(
            store,
            session_date="2026-08-17",
            total_capital=300000,
            leverage_factor=5,
            live_orders_enabled=True,
            running=True,
        )
        self.assertAlmostEqual(snap["live_pnl"], 55.5)
        self.assertAlmostEqual(snap["active"][0]["open_pnl"], 55.5)
        store.close()

    def test_missing_ltp_does_not_zero_open_pnl(self) -> None:
        _seed_live(self.live, "ok1", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        store.update_trade(trade.trade_id, open_pnl=12.0)
        broker.last_prices.pop("AAA")
        cycle._reset_quote_cache()
        cycle.mark_to_market()
        updated = store.get_trade(trade.trade_id)
        assert updated is not None
        self.assertAlmostEqual(updated.open_pnl, 12.0)
        store.close()

    def test_positions_failure_does_not_zero_open_pnl(self) -> None:
        _seed_live(self.live, "ok1", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.live_orders_enabled = True
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        store.update_trade(trade.trade_id, open_pnl=12.0)
        broker.positions_error = True
        cycle._reset_quote_cache()
        cycle.mark_to_market()
        updated = store.get_trade(trade.trade_id)
        assert updated is not None
        self.assertAlmostEqual(updated.open_pnl, 12.0)
        store.close()

    def test_auto_trail_modify_failure_still_marks_to_market(self) -> None:
        from trading_engine_types import PositionQuote

        _seed_live(self.live, "ok1", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.live_orders_enabled = True
        cycle.tick()
        trade = store.list_trades("2026-08-17")[0]
        cycle.set_auto_trail(trade.trade_id, enabled=True)
        broker.last_prices["AAA"] = 120
        broker.modify_error = "kite_modify_rejected"
        broker.position_quotes["AAA"] = PositionQuote(
            quantity=trade.qty,
            average_price=110.0,
            last_price=120.0,
            unrealised=80.0,
            pnl=80.0,
        )
        cycle.tick()
        updated = store.get_trade(trade.trade_id)
        assert updated is not None
        self.assertAlmostEqual(updated.open_pnl, 80.0)
        store.close()


class _Clock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class VwapGateCycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.live = Path(self.tmp.name) / "live.db"
        self.te = Path(self.tmp.name) / "te.db"
        conn = sqlite3.connect(self.live)
        conn.executescript(SCHEMA)
        conn.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _cycle(self, broker: FakeBroker, *, require_vwap_accept: bool = True, monotonic_fn=None, admin_config_db: Path | None = None):
        store = TradingEngineStore(self.te)
        run_id = store.start_run(
            session_date="2026-08-17",
            live_orders_enabled=False,
            pid=1,
            require_vwap_accept=require_vwap_accept,
        )
        kwargs = {}
        if monotonic_fn is not None:
            kwargs["monotonic_fn"] = monotonic_fn
        if admin_config_db is not None:
            kwargs["admin_config_db"] = admin_config_db
        cycle = TradingEngineCycle(
            store,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:30:00+00:00",
            run_id=run_id,
            live_orders_enabled=False,
            require_vwap_accept=require_vwap_accept,
            **kwargs,
        )
        return store, cycle

    def test_accept_at_first_ingest_places(self) -> None:
        _seed_live(self.live, "ok1", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.tick()
        self.assertEqual(store.list_trades("2026-08-17")[0].status, "protected_open")
        self.assertFalse(cycle.has_pending_vwap())
        self.assertEqual(broker.market_place_count, 1)
        store.close()

    def test_accept_arrives_at_250ms(self) -> None:
        _seed_live(self.live, "ok1", "2026-08-17T04:40:00+00:00", vwap=None)
        _ensure_vwap_table(self.live)
        clock = _Clock(0.0)
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker, monotonic_fn=clock)
        cycle.tick()
        self.assertEqual(store.list_trades("2026-08-17"), [])
        self.assertTrue(cycle.has_pending_vwap())
        _seed_vwap(self.live, "ok1")
        clock.t = 0.25
        cycle.poll_pending_vwap()
        self.assertEqual(len(store.list_trades("2026-08-17")), 1)
        self.assertEqual(store.list_trades("2026-08-17")[0].status, "protected_open")
        self.assertFalse(cycle.has_pending_vwap())
        store.close()

    def test_accept_arrives_at_1_5s(self) -> None:
        _seed_live(self.live, "ok1", "2026-08-17T04:40:00+00:00", vwap=None)
        _ensure_vwap_table(self.live)
        clock = _Clock(0.0)
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker, monotonic_fn=clock)
        cycle.tick()
        _seed_vwap(self.live, "ok1")
        clock.t = 1.5
        cycle.poll_pending_vwap()
        self.assertEqual(store.list_trades("2026-08-17")[0].status, "protected_open")
        store.close()

    def test_timeout_at_2s_skips_unavailable(self) -> None:
        _seed_live(self.live, "ok1", "2026-08-17T04:40:00+00:00", vwap=None)
        _ensure_vwap_table(self.live)
        clock = _Clock(0.0)
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker, monotonic_fn=clock)
        cycle.tick()
        clock.t = 2.0
        cycle.poll_pending_vwap()
        trades = store.list_trades("2026-08-17")
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].status, "skipped")
        self.assertEqual(trades[0].skip_reason, "vwap_unavailable")
        self.assertFalse(cycle.has_pending_vwap())
        self.assertEqual(broker.market_place_count, 0)
        store.close()

    def test_no_trade_row_while_pending(self) -> None:
        _seed_live(self.live, "ok1", "2026-08-17T04:40:00+00:00", vwap=None)
        _ensure_vwap_table(self.live)
        clock = _Clock(0.0)
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker, monotonic_fn=clock)
        cycle.tick()
        cycle.tick()
        clock.t = 0.5
        cycle.poll_pending_vwap()
        self.assertEqual(store.list_trades("2026-08-17"), [])
        self.assertTrue(cycle.has_pending_vwap())
        store.close()

    def test_no_late_order_after_timeout(self) -> None:
        _seed_live(self.live, "ok1", "2026-08-17T04:40:00+00:00", vwap=None)
        _ensure_vwap_table(self.live)
        clock = _Clock(0.0)
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker, monotonic_fn=clock)
        cycle.tick()
        clock.t = 2.0
        cycle.poll_pending_vwap()
        _seed_vwap(self.live, "ok1")
        clock.t = 2.5
        cycle.tick()
        cycle.poll_pending_vwap()
        trades = store.list_trades("2026-08-17")
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].skip_reason, "vwap_unavailable")
        self.assertEqual(broker.market_place_count, 0)
        store.close()

    def test_limited_places_with_reduced_cap(self) -> None:
        _seed_live(self.live, "lim", "2026-08-17T04:40:00+00:00", vwap="LIMITED")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.tick()
        trades = store.list_trades("2026-08-17")
        self.assertEqual(trades[0].status, "protected_open")
        self.assertEqual(broker.market_place_count, 1)
        store.close()

    def test_reject_unavailable_skip(self) -> None:
        for setup_id, klass, reason in (
            ("rej", "REJECT", "vwap_reject"),
            ("unav", "UNAVAILABLE", "vwap_unavailable"),
        ):
            with self.subTest(klass=klass):
                live = Path(self.tmp.name) / f"{setup_id}.db"
                te = Path(self.tmp.name) / f"{setup_id}-te.db"
                conn = sqlite3.connect(live)
                conn.executescript(SCHEMA)
                conn.close()
                _seed_live(live, setup_id, "2026-08-17T04:40:00+00:00", vwap=klass)
                broker = FakeBroker(last_prices={"AAA": 110})
                store = TradingEngineStore(te)
                run_id = store.start_run(
                    session_date="2026-08-17", live_orders_enabled=False, pid=1
                )
                cycle = TradingEngineCycle(
                    store,
                    broker,
                    live_db=live,
                    session_date="2026-08-17",
                    started_at="2026-08-17T04:30:00+00:00",
                    run_id=run_id,
                    live_orders_enabled=False,
                )
                cycle.tick()
                trades = store.list_trades("2026-08-17")
                self.assertEqual(trades[0].status, "skipped")
                self.assertEqual(trades[0].skip_reason, reason)
                self.assertEqual(broker.market_place_count, 0)
                store.close()

    def test_malformed_and_missing_table_unavailable(self) -> None:
        _seed_live(self.live, "bad", "2026-08-17T04:40:00+00:00", vwap="NOT_A_CLASS")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker)
        cycle.tick()
        self.assertEqual(store.list_trades("2026-08-17")[0].skip_reason, "vwap_unavailable")
        store.close()

        live2 = Path(self.tmp.name) / "notable.db"
        te2 = Path(self.tmp.name) / "notable-te.db"
        conn = sqlite3.connect(live2)
        conn.executescript(SCHEMA)
        conn.close()
        _seed_live(live2, "miss", "2026-08-17T04:40:00+00:00", vwap=None)
        broker = FakeBroker(last_prices={"AAA": 110})
        store = TradingEngineStore(te2)
        run_id = store.start_run(session_date="2026-08-17", live_orders_enabled=False, pid=1)
        cycle = TradingEngineCycle(
            store,
            broker,
            live_db=live2,
            session_date="2026-08-17",
            started_at="2026-08-17T04:30:00+00:00",
            run_id=run_id,
            live_orders_enabled=False,
        )
        cycle.tick()
        self.assertEqual(store.list_trades("2026-08-17")[0].skip_reason, "vwap_unavailable")
        store.close()

    def test_wrong_identity_cannot_place(self) -> None:
        _seed_live(self.live, "ok1", "2026-08-17T04:40:00+00:00", vwap=None)
        _seed_vwap(self.live, "ok1", session_date="2026-08-16")
        clock = _Clock(0.0)
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker, monotonic_fn=clock)
        cycle.tick()
        self.assertEqual(store.list_trades("2026-08-17"), [])
        clock.t = 2.0
        cycle.poll_pending_vwap()
        self.assertEqual(store.list_trades("2026-08-17")[0].skip_reason, "vwap_unavailable")
        store.close()

        live = Path(self.tmp.name) / "ver.db"
        conn = sqlite3.connect(live)
        conn.executescript(SCHEMA)
        conn.close()
        _seed_live(live, "ok2", "2026-08-17T04:40:00+00:00", vwap=None)
        _seed_vwap(live, "ok2", continuation_rule_version="other")
        _seed_vwap(live, "ok2", vwap_rule_version="vwap_qualifier_v1")
        store = TradingEngineStore(Path(self.tmp.name) / "ver-te.db")
        run_id = store.start_run(session_date="2026-08-17", live_orders_enabled=False, pid=1)
        broker = FakeBroker(last_prices={"AAA": 110})
        clock = _Clock(0.0)
        cycle = TradingEngineCycle(
            store,
            broker,
            live_db=live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:30:00+00:00",
            run_id=run_id,
            live_orders_enabled=False,
            monotonic_fn=clock,
        )
        cycle.tick()
        clock.t = 2.0
        cycle.poll_pending_vwap()
        self.assertEqual(store.list_trades("2026-08-17")[0].skip_reason, "vwap_unavailable")
        store.close()

    def test_flag_off_places_without_vwap(self) -> None:
        _seed_live(self.live, "ok1", "2026-08-17T04:40:00+00:00", vwap=None)
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker, require_vwap_accept=False)
        cycle.tick()
        self.assertEqual(store.list_trades("2026-08-17")[0].status, "protected_open")
        snap = snapshot_dict(
            store,
            session_date="2026-08-17",
            total_capital=300000,
            leverage_factor=5,
            live_orders_enabled=False,
            running=True,
            require_vwap_accept=False,
        )
        self.assertFalse(snap["require_vwap_accept"])
        store.close()

    def test_admin_pause_blocks_new_entries(self) -> None:
        admin_db = Path(self.tmp.name) / "admin_config.db"
        admin_store = AdminConfigStore(admin_db)
        admin_store.set_entries_paused(True)
        admin_store.close()

        _seed_live(self.live, "ok1", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker, admin_config_db=admin_db)
        cycle.tick()
        trades = store.list_trades("2026-08-17")
        self.assertEqual(len(trades), 0)
        store.close()

    def test_placement_stamps_admin_provenance(self) -> None:
        admin_db = Path(self.tmp.name) / "admin_config.db"
        admin_store = AdminConfigStore(admin_db)
        version_id = admin_store.active_version_id()
        admin_store.close()

        _seed_live(self.live, "ok1", "2026-08-17T04:40:00+00:00")
        broker = FakeBroker(last_prices={"AAA": 110})
        store, cycle = self._cycle(broker, admin_config_db=admin_db)
        cycle.tick()
        trade_id = store.list_trades("2026-08-17")[0].trade_id
        conn = sqlite3.connect(self.te)
        row = conn.execute(
            "SELECT admin_config_version_id, daily_loss_cap_inr FROM trades WHERE trade_id = ?",
            (trade_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], version_id)
        self.assertIsNotNone(row[1])
        store.close()


if __name__ == "__main__":
    unittest.main()
