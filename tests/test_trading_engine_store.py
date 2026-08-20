"""Store restart / events / tag identity."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from trading_engine_store import TradingEngineStore, make_broker_tag, make_trade_id


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "te.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_events_survive_reopen(self) -> None:
        store = TradingEngineStore(self.path)
        run_id = store.start_run(session_date="2026-08-17", live_orders_enabled=False, pid=1)
        trade = store.insert_candidate(
            setup_id="s",
            continuation_rule_version="v1",
            session_date="2026-08-17",
            symbol="AAA",
            instrument_token=1,
            direction="UP",
            entry_estimate=110,
            tick_size=1,
            trigger_time="t",
        )
        assert trade is not None
        store.append_event(trade.trade_id, "candidate", actor="engine")
        store.close()

        store2 = TradingEngineStore(self.path)
        found = store2.find_trade("s", "v1")
        self.assertIsNotNone(found)
        events = store2.list_events(found.trade_id)
        self.assertEqual(events[0]["action"], "candidate")
        self.assertEqual(store2.latest_run()["run_id"], run_id)
        store2.close()

    def test_broker_tag_max_20(self) -> None:
        tag = make_broker_tag(make_trade_id())
        self.assertLessEqual(len(tag), 20)
        self.assertTrue(tag.isalnum())

    def test_commands_roundtrip(self) -> None:
        store = TradingEngineStore(self.path)
        cid = store.enqueue_command("trail_stop", trade_id="t1", payload={"new_stop": 101})
        pending = store.pending_commands()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].command_id, cid)
        store.mark_command_processed(cid)
        self.assertEqual(store.pending_commands(), [])
        store.close()


if __name__ == "__main__":
    unittest.main()
