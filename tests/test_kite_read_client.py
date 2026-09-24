"""KiteBroker reads go through a short-timeout client; writes keep the default."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from trading_engine_broker import KiteBroker

ORDER_ROW = {
    "order_id": "o1",
    "status": "COMPLETE",
    "tradingsymbol": "AAA",
    "transaction_type": "BUY",
    "order_type": "MARKET",
    "quantity": 1,
    "filled_quantity": 1,
    "average_price": 100.0,
    "product": "MIS",
    "tag": "t",
}


class SplitClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.write = MagicMock(name="write")
        self.read = MagicMock(name="read")
        self.read.orders.return_value = [dict(ORDER_ROW)]
        self.read.positions.return_value = {"net": [], "day": []}
        self.read.margins.return_value = {"equity": {"net": 1.0}}
        self.read.order_margins.return_value = [{"total": 1.0}]
        self.read.ltp.return_value = {"NSE:AAA": {"last_price": 1.0}}
        self.read.quote.return_value = {}
        self.write.place_order.return_value = "o2"
        self.broker = KiteBroker(self.write, live_orders_enabled=True, read_kite=self.read)

    def assert_write_did_not_read(self) -> None:
        for name in ("orders", "positions", "margins", "order_margins", "ltp", "quote", "profile"):
            getattr(self.write, name).assert_not_called()

    def test_every_read_uses_the_read_client(self) -> None:
        self.broker.list_orders()
        self.broker.orders_by_tag("t")
        self.broker.orders_for_symbol("AAA")
        self.broker.poll_order("o1")
        self.broker.list_net_positions()
        self.broker.clear_quote_cache()
        self.broker.position_quote("AAA")
        self.broker.order_margins(tradingsymbol="AAA", transaction_type="BUY", quantity=1)
        self.broker.available_margins()
        self.broker.ltp("AAA")
        self.broker.touch_quote("AAA")
        self.broker.check_session()
        self.read.profile.assert_called_once()
        self.read.order_history.assert_called_once_with("o1")
        self.write.order_history.assert_not_called()
        self.assert_write_did_not_read()

    def test_writes_use_the_write_client(self) -> None:
        self.broker.place_market_mis(
            tradingsymbol="AAA", transaction_type="BUY", quantity=1, tag="new"
        )
        self.broker.cancel_order("o1")
        self.write.place_order.assert_called_once()
        self.write.cancel_order.assert_called_once()
        self.read.place_order.assert_not_called()
        self.read.cancel_order.assert_not_called()
        self.assert_write_did_not_read()

    def test_the_session_hook_is_registered_on_both_clients(self) -> None:
        self.write.set_session_expiry_hook.assert_called_once()
        self.read.set_session_expiry_hook.assert_called_once()
        self.read.set_session_expiry_hook.call_args.args[0]()
        self.assertTrue(self.broker.session_expired)

    def test_one_client_serves_both_roles_by_default(self) -> None:
        kite = MagicMock()
        kite.orders.return_value = []
        broker = KiteBroker(kite, live_orders_enabled=True)
        broker.list_orders()
        kite.orders.assert_called_once()
        kite.set_session_expiry_hook.assert_called_once()


class BuildBrokerTimeoutTests(unittest.TestCase):
    def test_live_broker_gets_a_short_read_timeout_and_default_writes(self) -> None:
        import run_execution_engine

        clients = []

        def fake_get_kite(*_args, timeout=None, **_kwargs):
            client = MagicMock()
            client.timeout = timeout
            clients.append(client)
            return client

        with patch("login._get_kite", side_effect=fake_get_kite):
            broker = run_execution_engine.build_broker(live_orders=True)

        self.assertIsNone(broker._kite.timeout)  # SDK default (7s) for writes
        self.assertEqual(broker._read.timeout, run_execution_engine.KITE_READ_TIMEOUT_SECONDS)
        self.assertEqual(run_execution_engine.KITE_READ_TIMEOUT_SECONDS, 3.0)
        self.assertIsNot(broker._kite, broker._read)


class GetKiteTimeoutTests(unittest.TestCase):
    def test_timeout_reaches_the_sdk_client(self) -> None:
        import login

        with patch.object(login, "_read_env_merged", return_value={"KITE_API_KEY": "k"}):
            self.assertEqual(login._get_kite(timeout=3.0).timeout, 3.0)
            self.assertEqual(login._get_kite().timeout, 7)  # SDK default


if __name__ == "__main__":
    unittest.main()
