from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from historical_collector import StockToken
from tick_receiver import QueueOverloadError, TickReceiver

_SAMPLE_STOCKS = [
    StockToken(tradingsymbol="RELIANCE", instrument_token=738561),
    StockToken(tradingsymbol="INFY", instrument_token=408065),
    StockToken(tradingsymbol="TCS", instrument_token=2953217),
]


def _raw_tick(token: int, price: float) -> dict:
    return {
        "instrument_token": token,
        "last_price": price,
        "volume_traded": 100,
        "ohlc": {"open": price, "high": price, "low": price, "close": price},
    }


class ReceiverTestMixin:
    def setUp(self) -> None:
        self.check_patcher = patch(
            "tick_receiver.check_access_token",
            return_value=(True, "ok"),
        )
        self.env_patcher = patch(
            "tick_receiver._require_env",
            return_value={"KITE_API_KEY": "key", "KITE_ACCESS_TOKEN": "token"},
        )
        self.kite_patcher = patch("tick_receiver._get_kite", return_value=MagicMock())
        self.tokens_patcher = patch(
            "tick_receiver.load_nifty50_tokens",
            return_value=list(_SAMPLE_STOCKS),
        )
        self.check_patcher.start()
        self.env_patcher.start()
        self.kite_patcher.start()
        self.tokens_patcher.start()

    def tearDown(self) -> None:
        self.tokens_patcher.stop()
        self.kite_patcher.stop()
        self.env_patcher.stop()
        self.check_patcher.stop()

    def _make_receiver(
        self,
        on_tick: MagicMock,
        *,
        queue_maxsize: int = 100,
        worker_poll_seconds: float = 0.05,
        health_interval: float = 60.0,
        ticker_factory: MagicMock = None,
    ) -> TickReceiver:
        return TickReceiver(
            on_tick=on_tick,
            queue_maxsize=queue_maxsize,
            worker_poll_seconds=worker_poll_seconds,
            health_interval=health_interval,
            ticker_factory=ticker_factory or MagicMock(),
        )


class CallbackForwardingTests(ReceiverTestMixin, unittest.TestCase):
    def test_worker_forwards_normalized_ticks(self) -> None:
        callback = MagicMock()
        receiver = self._make_receiver(callback, queue_maxsize=10)

        receiver._worker_thread = threading.Thread(
            target=receiver._worker_loop,
            name="test-worker",
            daemon=True,
        )
        receiver._worker_thread.start()

        receiver._on_ticks(None, [_raw_tick(738561, 2500.0), _raw_tick(408065, 1800.0)])

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if callback.call_count >= 2:
                break
            time.sleep(0.02)

        receiver.stop()
        receiver._worker_thread.join(timeout=2.0)

        self.assertEqual(callback.call_count, 2)
        sequences = [call.args[0].sequence for call in callback.call_args_list]
        self.assertEqual(sequences, [1, 2])
        tokens = [call.args[0].instrument_token for call in callback.call_args_list]
        self.assertEqual(tokens, [738561, 408065])


class SequenceNumberingTests(ReceiverTestMixin, unittest.TestCase):
    def test_invalid_ticks_do_not_consume_sequence(self) -> None:
        callback = MagicMock()
        receiver = self._make_receiver(callback, queue_maxsize=10)
        receiver._worker_thread = threading.Thread(
            target=receiver._worker_loop,
            daemon=True,
        )
        receiver._worker_thread.start()

        receiver._on_ticks(
            None,
            [
                _raw_tick(738561, 100.0),
                {"last_price": 1.0},
                _raw_tick(408065, 200.0),
                {"instrument_token": 1, "last_price": 0},
                _raw_tick(2953217, 300.0),
            ],
        )

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if callback.call_count >= 3:
                break
            time.sleep(0.02)

        receiver.stop()
        receiver._worker_thread.join(timeout=2.0)

        sequences = [call.args[0].sequence for call in callback.call_args_list]
        self.assertEqual(sequences, [1, 2, 3])
        self.assertEqual(receiver._ticks_invalid, 2)
        self.assertEqual(receiver._next_sequence, 4)


class QueueOverloadTests(ReceiverTestMixin, unittest.TestCase):
    def test_queue_full_sets_fatal_and_halts_acceptance(self) -> None:
        callback = MagicMock()
        receiver = self._make_receiver(callback, queue_maxsize=1, worker_poll_seconds=60.0)

        with self.assertLogs("tick_receiver", level="CRITICAL") as logs:
            receiver._on_ticks(None, [_raw_tick(738561, 100.0)])
            receiver._on_ticks(None, [_raw_tick(408065, 200.0)])

        self.assertFalse(receiver.is_accepting_ticks())
        self.assertIsInstance(receiver.fatal_error, QueueOverloadError)
        self.assertIn("seq=2", str(receiver.fatal_error))
        self.assertIn("408065", str(receiver.fatal_error))
        self.assertTrue(any("Tick queue full" in msg for msg in logs.output))

    def test_subsequent_on_ticks_are_ignored_after_overload(self) -> None:
        callback = MagicMock()
        receiver = self._make_receiver(callback, queue_maxsize=1, worker_poll_seconds=60.0)

        receiver._on_ticks(None, [_raw_tick(738561, 100.0)])
        receiver._on_ticks(None, [_raw_tick(408065, 200.0)])
        before = receiver._next_sequence
        receiver._on_ticks(None, [_raw_tick(2953217, 300.0)])

        self.assertEqual(receiver._next_sequence, before)
        self.assertEqual(receiver._ticks_enqueued, 1)


class FeedStaleTests(ReceiverTestMixin, unittest.TestCase):
    def test_is_feed_stale_true_when_no_ticks(self) -> None:
        receiver = self._make_receiver(MagicMock())
        self.assertTrue(receiver.is_feed_stale())

    def test_is_feed_stale_false_after_recent_tick(self) -> None:
        receiver = self._make_receiver(MagicMock())
        receiver._last_tick_monotonic = time.monotonic()
        self.assertFalse(receiver.is_feed_stale())

    def test_is_feed_stale_true_when_age_exceeded(self) -> None:
        receiver = self._make_receiver(MagicMock(), queue_maxsize=10)
        receiver._last_tick_monotonic = time.monotonic() - 10.0
        self.assertTrue(receiver.is_feed_stale(max_age_seconds=1.0))


class StartFatalPropagationTests(ReceiverTestMixin, unittest.TestCase):
    def test_start_reraises_fatal_error_in_caller_thread(self) -> None:
        callback = MagicMock()
        mock_ticker = MagicMock()

        def connect_side_effect(**kwargs: object) -> None:
            receiver_ref._on_ticks(None, [_raw_tick(738561, 100.0)])
            receiver_ref._on_ticks(None, [_raw_tick(408065, 200.0)])

        mock_ticker.connect.side_effect = connect_side_effect
        receiver_ref = self._make_receiver(
            callback,
            queue_maxsize=1,
            worker_poll_seconds=60.0,
            ticker_factory=MagicMock(return_value=mock_ticker),
        )

        with self.assertRaises(QueueOverloadError):
            receiver_ref.start()

        self.assertIsInstance(receiver_ref.fatal_error, QueueOverloadError)


class WorkerShutdownTests(ReceiverTestMixin, unittest.TestCase):
    def test_worker_exits_when_stop_event_set_and_queue_empty(self) -> None:
        callback = MagicMock()
        receiver = self._make_receiver(callback, queue_maxsize=10, worker_poll_seconds=0.05)
        receiver._worker_thread = threading.Thread(target=receiver._worker_loop, daemon=True)
        receiver._worker_thread.start()

        receiver.stop()
        receiver._worker_thread.join(timeout=2.0)

        self.assertFalse(receiver._worker_thread.is_alive())

    def test_stop_does_not_deadlock_when_queue_full(self) -> None:
        callback = MagicMock()
        receiver = self._make_receiver(callback, queue_maxsize=1, worker_poll_seconds=60.0)

        receiver._on_ticks(None, [_raw_tick(738561, 100.0)])
        self.assertTrue(receiver._queue.full())

        receiver._worker_thread = threading.Thread(target=receiver._worker_loop, daemon=True)
        receiver._worker_thread.start()

        finished = threading.Event()

        def run_stop() -> None:
            receiver.stop()
            finished.set()

        stop_thread = threading.Thread(target=run_stop, daemon=True)
        stop_thread.start()

        self.assertTrue(finished.wait(timeout=2.0))
        receiver._worker_thread.join(timeout=2.0)
        self.assertFalse(stop_thread.is_alive())

    def test_close_ticker_safe_calls_close_without_stop_retry(self) -> None:
        callback = MagicMock()
        receiver = self._make_receiver(callback)
        mock_ticker = MagicMock()
        receiver._ticker = mock_ticker

        receiver._close_ticker_safe()

        mock_ticker.close.assert_called_once_with()


class ReconnectBehaviorTests(ReceiverTestMixin, unittest.TestCase):
    def test_on_connect_subscribes_and_sets_mode(self) -> None:
        receiver = self._make_receiver(MagicMock())
        ws = MagicMock()
        ws.MODE_FULL = "full"

        receiver._on_connect(ws, {})

        ws.subscribe.assert_called_once_with(receiver._instrument_tokens)
        ws.set_mode.assert_called_once_with("full", receiver._instrument_tokens)

    def test_on_reconnect_only_logs_and_updates_state(self) -> None:
        receiver = self._make_receiver(MagicMock())
        ws = MagicMock()
        receiver._connected = False

        with self.assertLogs("tick_receiver", level="WARNING") as logs:
            receiver._on_reconnect(ws, 2)

        self.assertTrue(receiver._connected)
        ws.subscribe.assert_not_called()
        ws.set_mode.assert_not_called()
        ws.resubscribe.assert_not_called()
        self.assertTrue(any("reconnect attempt 2" in msg for msg in logs.output))


if __name__ == "__main__":
    unittest.main()
