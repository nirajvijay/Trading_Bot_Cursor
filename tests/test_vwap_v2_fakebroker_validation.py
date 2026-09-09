"""Phase 5: FakeBroker validation — LIMITED places with ₹450 cap sizing."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from trading_engine_broker import FakeBroker
from trading_engine_cycle import TradingEngineCycle
from tests.engine_lifecycle_fixture import TradingEngineCycle
from trading_engine_store import TradingEngineStore
from tests.test_trading_engine_cycle import SCHEMA, _seed_live, _seed_vwap, _session_clock, _fresh_feed_age


class FakeBrokerLimitedValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.live = Path(self.tmp.name) / "live.db"
        self.te = Path(self.tmp.name) / "te.db"
        conn = sqlite3.connect(self.live)
        conn.executescript(SCHEMA)
        conn.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_limited_sized_qty_below_accept(self) -> None:
        _seed_live(self.live, "lim", "2026-08-17T04:40:00+00:00", symbol="LIM", vwap="LIMITED")
        _seed_live(self.live, "acc", "2026-08-17T04:41:00+00:00", symbol="ACC", vwap="ACCEPT")
        broker = FakeBroker(last_prices={"LIM": 110, "ACC": 110})
        store = TradingEngineStore(self.te)
        run_id = store.start_run(session_date="2026-08-17", live_orders_enabled=False, pid=1)
        cycle = TradingEngineCycle(
            store,
            broker,
            live_db=self.live,
            session_date="2026-08-17",
            started_at="2026-08-17T04:30:00+00:00",
            run_id=run_id,
            live_orders_enabled=False,
            clock_fn=_session_clock(),
            feed_age_seconds_fn=_fresh_feed_age,
        )
        cycle.tick()
        cycle.tick()
        trades = {t.setup_id: t for t in store.list_trades("2026-08-17")}
        self.assertEqual(trades["lim"].status, "protected_open")
        self.assertEqual(trades["acc"].status, "protected_open")
        self.assertLess(trades["lim"].qty, trades["acc"].qty)
        lim_risk = trades["lim"].qty * abs(trades["lim"].entry_estimate - trades["lim"].current_stop)
        self.assertLessEqual(lim_risk, 450.0 + 1e-9)
        store.close()


if __name__ == "__main__":
    unittest.main()
