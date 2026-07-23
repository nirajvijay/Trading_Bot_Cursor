from __future__ import annotations

import threading
import time
import unittest
from datetime import datetime
from typing import List, Optional
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from historical_collector import StockToken
from live_candle_pipeline import LiveCandlePipeline
from live_one_minute_candle_writer import CandleConflictError, LiveOneMinuteCandleWriter
from market_data_coordinator import MarketDataCoordinator
from one_minute_candle_builder import OneMinuteCandleBuilder
from candle_emission import CandleEmissionError
from tick_event import IST, Ohlc, TickEvent
from tick_receiver import FeedContinuityState, QueueOverloadError, TickReceiver

_IST = ZoneInfo(IST)

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


def _raw_tick_with_exchange_ts(
    token: int,
    price: float,
    exchange_ts: datetime,
) -> dict:
    return {
        "instrument_token": token,
        "last_price": price,
        "volume_traded": 100,
        "exchange_timestamp": exchange_ts,
        "ohlc": {"open": price, "high": price, "low": price, "close": price},
    }


def _ist(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    second: int = 0,
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=_IST)


def _set_continuity_state(
    receiver: TickReceiver,
    state: FeedContinuityState,
) -> None:
    with receiver._state_cond:
        receiver._continuity_state = state
        receiver._restoring_from = None


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


class FeedLifecycleCallbackTests(ReceiverTestMixin, unittest.TestCase):
    def _make_receiver_with_lifecycle(
        self,
        on_feed_ready: MagicMock,
        on_feed_interrupted: MagicMock,
        on_tick: Optional[MagicMock] = None,
    ) -> TickReceiver:
        return TickReceiver(
            on_tick=on_tick or MagicMock(),
            on_feed_ready=on_feed_ready,
            on_feed_interrupted=on_feed_interrupted,
            queue_maxsize=10,
            worker_poll_seconds=0.05,
            health_interval=60.0,
            ticker_factory=MagicMock(),
        )

    def test_on_connect_does_not_call_feed_ready(self) -> None:
        on_ready = MagicMock()
        on_interrupted = MagicMock()
        receiver = self._make_receiver_with_lifecycle(on_ready, on_interrupted)
        ws = MagicMock()
        ws.MODE_FULL = "full"

        receiver._on_connect(ws, {})

        on_ready.assert_not_called()
        on_interrupted.assert_not_called()

    def test_on_reconnect_does_not_call_feed_ready(self) -> None:
        on_ready = MagicMock()
        receiver = self._make_receiver_with_lifecycle(on_ready, MagicMock())
        receiver._on_reconnect(MagicMock(), 1)
        on_ready.assert_not_called()

    def test_on_close_from_starting_does_not_interrupt(self) -> None:
        on_interrupted = MagicMock()
        receiver = self._make_receiver_with_lifecycle(MagicMock(), on_interrupted)
        receiver._on_close(MagicMock(), 1000, "closed")
        on_interrupted.assert_not_called()

    def test_on_close_from_healthy_interrupts(self) -> None:
        on_interrupted = MagicMock()
        receiver = self._make_receiver_with_lifecycle(MagicMock(), on_interrupted)
        _set_continuity_state(receiver, FeedContinuityState.HEALTHY)
        receiver._on_close(MagicMock(), 1000, "closed")
        on_interrupted.assert_called_once()

    def test_on_error_from_starting_does_not_interrupt(self) -> None:
        on_interrupted = MagicMock()
        receiver = self._make_receiver_with_lifecycle(MagicMock(), on_interrupted)
        receiver._on_error(MagicMock(), 500, "error")
        on_interrupted.assert_not_called()

    def test_stop_from_healthy_interrupts_once(self) -> None:
        on_interrupted = MagicMock()
        receiver = self._make_receiver_with_lifecycle(MagicMock(), on_interrupted)
        _set_continuity_state(receiver, FeedContinuityState.HEALTHY)
        receiver.stop()
        receiver.stop()
        on_interrupted.assert_called_once()

    def test_stop_from_starting_does_not_interrupt(self) -> None:
        on_interrupted = MagicMock()
        receiver = self._make_receiver_with_lifecycle(MagicMock(), on_interrupted)
        receiver.stop()
        on_interrupted.assert_not_called()

    def test_fatal_from_healthy_interrupts(self) -> None:
        on_interrupted = MagicMock()
        receiver = self._make_receiver_with_lifecycle(MagicMock(), on_interrupted)
        _set_continuity_state(receiver, FeedContinuityState.HEALTHY)
        receiver._set_fatal(RuntimeError("fatal"), accepting=False)
        on_interrupted.assert_called_once()

    def test_optional_callbacks_do_not_raise(self) -> None:
        receiver = self._make_receiver(MagicMock())
        ws = MagicMock()
        ws.MODE_FULL = "full"
        receiver._on_connect(ws, {})
        receiver._on_close(ws, 1000, "closed")
        receiver.stop()


class FeedContinuityTransitionTests(ReceiverTestMixin, unittest.TestCase):
    def _lifecycle_receiver(
        self,
        on_ready: MagicMock,
        on_interrupted: MagicMock,
        *,
        on_tick: Optional[MagicMock] = None,
        queue_maxsize: int = 10,
    ) -> TickReceiver:
        receiver = TickReceiver(
            on_tick=on_tick or MagicMock(),
            on_feed_ready=on_ready,
            on_feed_interrupted=on_interrupted,
            queue_maxsize=queue_maxsize,
            worker_poll_seconds=0.05,
            health_interval=60.0,
            stale_seconds=1.0,
            ticker_factory=MagicMock(),
        )
        return receiver

    def test_healthy_to_stale_fires_interrupt_once(self) -> None:
        on_interrupted = MagicMock()
        receiver = self._lifecycle_receiver(MagicMock(), on_interrupted)
        _set_continuity_state(receiver, FeedContinuityState.HEALTHY)
        receiver._last_tick_monotonic = time.monotonic() - 5.0

        receiver._run_health_check()
        receiver._run_health_check()

        on_interrupted.assert_called_once()
        self.assertEqual(receiver._continuity_state, FeedContinuityState.STALE)

    def test_stale_to_healthy_fires_ready_once(self) -> None:
        on_ready = MagicMock()
        on_tick = MagicMock()
        receiver = self._lifecycle_receiver(on_ready, MagicMock(), on_tick=on_tick)
        receiver._worker_thread = threading.Thread(
            target=receiver._worker_loop,
            daemon=True,
        )
        receiver._worker_thread.start()
        _set_continuity_state(receiver, FeedContinuityState.STALE)

        receiver._on_ticks(None, [_raw_tick(738561, 100.0)])
        receiver._on_ticks(None, [_raw_tick(738561, 101.0)])

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if on_tick.call_count >= 2:
                break
            time.sleep(0.02)

        receiver.stop()
        receiver._worker_thread.join(timeout=2.0)

        on_ready.assert_called_once()
        self.assertEqual(receiver._continuity_state, FeedContinuityState.STOPPED)

    def test_restoration_uses_wall_clock_not_exchange_timestamp(self) -> None:
        on_ready = MagicMock()
        receiver = self._lifecycle_receiver(on_ready, MagicMock())
        _set_continuity_state(receiver, FeedContinuityState.STALE)
        wall = _ist(2026, 7, 22, 10, 32, 15)
        exchange = _ist(2026, 7, 22, 10, 30, 5)

        with patch("tick_receiver.datetime") as mock_dt:
            mock_dt.now.return_value = wall
            receiver._on_ticks(
                None,
                [_raw_tick_with_exchange_ts(738561, 100.0, exchange)],
            )

        on_ready.assert_called_once_with(wall)

    def test_disconnect_while_stale_does_not_duplicate_interrupt(self) -> None:
        on_interrupted = MagicMock()
        receiver = self._lifecycle_receiver(MagicMock(), on_interrupted)
        _set_continuity_state(receiver, FeedContinuityState.HEALTHY)
        receiver._last_tick_monotonic = time.monotonic() - 5.0
        receiver._run_health_check()
        receiver._on_close(MagicMock(), 1000, "closed")
        on_interrupted.assert_called_once()

    def test_reconnect_without_ticks_does_not_restore(self) -> None:
        on_ready = MagicMock()
        receiver = self._lifecycle_receiver(on_ready, MagicMock())
        _set_continuity_state(receiver, FeedContinuityState.STALE)
        receiver._on_reconnect(MagicMock(), 1)
        on_ready.assert_not_called()

    def test_startup_stale_checks_do_not_emit_interrupted(self) -> None:
        on_interrupted = MagicMock()
        receiver = self._lifecycle_receiver(MagicMock(), on_interrupted)
        self.assertEqual(receiver._continuity_state, FeedContinuityState.STARTING)
        receiver._run_health_check()
        receiver._run_health_check()
        on_interrupted.assert_not_called()
        self.assertEqual(receiver._continuity_state, FeedContinuityState.STARTING)

    def test_shutdown_from_healthy_notifies_before_flush(self) -> None:
        events: List[str] = []

        def on_interrupted(at: datetime) -> None:
            events.append("interrupted")

        def on_tick(event: TickEvent) -> None:
            events.append("tick")

        receiver = self._lifecycle_receiver(MagicMock(), MagicMock(side_effect=on_interrupted))
        receiver._on_tick = on_tick
        _set_continuity_state(receiver, FeedContinuityState.HEALTHY)

        receiver.stop()

        def simulate_flush() -> None:
            events.append("flush")

        self.assertEqual(events, ["interrupted"])
        simulate_flush()
        self.assertEqual(events, ["interrupted", "flush"])

    def test_shutdown_while_stale_does_not_duplicate(self) -> None:
        on_interrupted = MagicMock()
        receiver = self._lifecycle_receiver(MagicMock(), on_interrupted)
        _set_continuity_state(receiver, FeedContinuityState.HEALTHY)
        receiver._last_tick_monotonic = time.monotonic() - 5.0
        receiver._run_health_check()
        on_interrupted.reset_mock()
        receiver.stop()
        on_interrupted.assert_not_called()

    def test_ready_completes_before_worker_dequeues_restoration_tick(self) -> None:
        order: List[str] = []
        ready_gate = threading.Event()
        restoring_entered = threading.Event()

        def on_ready(at: datetime) -> None:
            restoring_entered.set()
            order.append("ready_start")
            ready_gate.wait(timeout=2.0)
            order.append("ready_end")

        def on_tick(event: TickEvent) -> None:
            order.append("tick")
            self.assertIn("ready_end", order)

        receiver = self._lifecycle_receiver(
            MagicMock(side_effect=on_ready),
            MagicMock(),
            on_tick=MagicMock(side_effect=on_tick),
        )
        receiver._worker_thread = threading.Thread(
            target=receiver._worker_loop,
            daemon=True,
        )
        receiver._worker_thread.start()
        _set_continuity_state(receiver, FeedContinuityState.STALE)

        def enqueue() -> None:
            receiver._on_ticks(None, [_raw_tick(738561, 100.0)])

        threading.Thread(target=enqueue, daemon=True).start()
        self.assertTrue(restoring_entered.wait(timeout=2.0))
        time.sleep(0.1)
        self.assertNotIn("tick", order)

        ready_gate.set()

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if "tick" in order:
                break
            time.sleep(0.02)

        receiver.stop()
        receiver._worker_thread.join(timeout=2.0)

        self.assertEqual(order, ["ready_start", "ready_end", "tick"])

    def test_health_check_does_not_interrupt_during_restoring(self) -> None:
        ready_gate = threading.Event()
        restoring_entered = threading.Event()
        on_interrupted = MagicMock()

        def on_ready(at: datetime) -> None:
            restoring_entered.set()
            ready_gate.wait(timeout=2.0)

        on_tick = MagicMock()
        receiver = self._lifecycle_receiver(
            MagicMock(side_effect=on_ready),
            on_interrupted,
            on_tick=on_tick,
        )
        receiver._worker_thread = threading.Thread(
            target=receiver._worker_loop,
            daemon=True,
        )
        receiver._worker_thread.start()
        _set_continuity_state(receiver, FeedContinuityState.STALE)
        receiver._last_tick_monotonic = time.monotonic() - 5.0

        def enqueue() -> None:
            receiver._on_ticks(None, [_raw_tick(738561, 100.0)])

        threading.Thread(target=enqueue, daemon=True).start()
        self.assertTrue(restoring_entered.wait(timeout=2.0))
        self.assertEqual(receiver._continuity_state, FeedContinuityState.RESTORING)

        receiver._run_health_check()
        on_interrupted.assert_not_called()
        self.assertEqual(receiver._continuity_state, FeedContinuityState.RESTORING)

        ready_gate.set()

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if on_tick.call_count >= 1:
                break
            time.sleep(0.02)

        receiver.stop()
        receiver._worker_thread.join(timeout=2.0)

    def test_queue_full_does_not_restore_continuity(self) -> None:
        on_ready = MagicMock()
        receiver = self._lifecycle_receiver(
            on_ready,
            MagicMock(),
            queue_maxsize=1,
        )
        _set_continuity_state(receiver, FeedContinuityState.STALE)
        receiver._queue.put_nowait(
            TickEvent(
                sequence=0,
                instrument_token=738561,
                last_price=1.0,
                exchange_timestamp=_ist(2026, 7, 22, 10, 0, 0),
                received_at=_ist(2026, 7, 22, 10, 0, 0),
                volume_traded=1,
                last_traded_quantity=1,
                average_traded_price=1.0,
                ohlc=Ohlc(open=1.0, high=1.0, low=1.0, close=1.0),
            )
        )

        with self.assertLogs("tick_receiver", level="CRITICAL"):
            receiver._on_ticks(None, [_raw_tick(738561, 100.0)])

        on_ready.assert_not_called()
        self.assertNotEqual(receiver._continuity_state, FeedContinuityState.HEALTHY)
        self.assertIsInstance(receiver.fatal_error, QueueOverloadError)

    def test_concurrent_ticks_restore_once(self) -> None:
        on_ready = MagicMock()
        on_tick = MagicMock()
        receiver = self._lifecycle_receiver(
            on_ready,
            MagicMock(),
            on_tick=on_tick,
            queue_maxsize=20,
        )
        receiver._worker_thread = threading.Thread(
            target=receiver._worker_loop,
            daemon=True,
        )
        receiver._worker_thread.start()
        _set_continuity_state(receiver, FeedContinuityState.STALE)

        tokens = [738561, 408065, 2953217]
        errors: List[BaseException] = []
        lock = threading.Lock()

        def enqueue(token: int, price: float) -> None:
            try:
                receiver._on_ticks(None, [_raw_tick(token, price)])
            except BaseException as exc:
                with lock:
                    errors.append(exc)

        threads = [
            threading.Thread(target=enqueue, args=(token, 100.0 + i))
            for i, token in enumerate(tokens)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if on_tick.call_count >= len(tokens):
                break
            time.sleep(0.02)

        receiver.stop()
        receiver._worker_thread.join(timeout=2.0)

        self.assertEqual(errors, [])
        on_ready.assert_called_once()

    def test_lifecycle_callback_exception_is_logged_not_raised(self) -> None:
        receiver = self._lifecycle_receiver(
            MagicMock(side_effect=RuntimeError("ready failed")),
            MagicMock(),
        )
        _set_continuity_state(receiver, FeedContinuityState.STALE)
        with self.assertLogs("tick_receiver", level="ERROR"):
            receiver._on_ticks(None, [_raw_tick(738561, 100.0)])
        self.assertEqual(receiver._continuity_state, FeedContinuityState.STALE)


class FeedContinuityBuilderIntegrationTests(ReceiverTestMixin, unittest.TestCase):
    def test_candle_integration_stale_resume_partial_coverage(self) -> None:
        emitted: List = []
        builder = OneMinuteCandleBuilder(
            on_candle=emitted.append,
            feed_ready_at=_ist(2026, 7, 22, 10, 30, 0),
        )
        on_tick = builder.on_tick
        receiver = TickReceiver(
            on_tick=on_tick,
            on_feed_ready=builder.mark_feed_restored,
            on_feed_interrupted=builder.mark_feed_interrupted,
            queue_maxsize=50,
            worker_poll_seconds=0.05,
            health_interval=60.0,
            stale_seconds=1.0,
            ticker_factory=MagicMock(),
        )
        _set_continuity_state(receiver, FeedContinuityState.HEALTHY)
        receiver._worker_thread = threading.Thread(
            target=receiver._worker_loop,
            daemon=True,
        )
        receiver._worker_thread.start()

        def send(exchange: datetime, volume: int) -> None:
            tick = _raw_tick_with_exchange_ts(738561, 100.0, exchange)
            tick["volume_traded"] = volume
            receiver._on_ticks(None, [tick])

        send(_ist(2026, 7, 22, 10, 30, 10), 100)
        send(_ist(2026, 7, 22, 10, 30, 20), 150)
        receiver._last_tick_monotonic = time.monotonic() - 5.0
        receiver._run_health_check()
        self.assertEqual(receiver._continuity_state, FeedContinuityState.STALE)

        resume_wall = _ist(2026, 7, 22, 10, 32, 15)
        with patch("tick_receiver.datetime") as mock_dt:
            mock_dt.now.return_value = resume_wall
            send(_ist(2026, 7, 22, 10, 30, 25), 200)
            send(_ist(2026, 7, 22, 10, 32, 5), 250)
            send(_ist(2026, 7, 22, 10, 32, 20), 300)
            send(_ist(2026, 7, 22, 10, 33, 1), 350)
            send(_ist(2026, 7, 22, 10, 33, 30), 400)
            send(_ist(2026, 7, 22, 10, 34, 1), 450)

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if len(emitted) >= 3:
                break
            time.sleep(0.02)

        receiver.stop()
        receiver._worker_thread.join(timeout=2.0)

        candle_times = [c.candle_time for c in emitted]
        self.assertIn(_ist(2026, 7, 22, 10, 30, 0), candle_times)
        self.assertNotIn(_ist(2026, 7, 22, 10, 31, 0), candle_times)
        self.assertIn(_ist(2026, 7, 22, 10, 32, 0), candle_times)
        self.assertIn(_ist(2026, 7, 22, 10, 33, 0), candle_times)

        by_time = {c.candle_time: c for c in emitted}
        self.assertTrue(by_time[_ist(2026, 7, 22, 10, 30, 0)].is_partial)
        self.assertFalse(by_time[_ist(2026, 7, 22, 10, 30, 0)].has_full_minute_coverage)
        self.assertTrue(by_time[_ist(2026, 7, 22, 10, 32, 0)].is_partial)
        self.assertFalse(by_time[_ist(2026, 7, 22, 10, 33, 0)].is_partial)
        self.assertTrue(by_time[_ist(2026, 7, 22, 10, 33, 0)].has_full_minute_coverage)


class PersistenceFailureIntegrationTests(ReceiverTestMixin, unittest.TestCase):
    def test_conflict_sets_receiver_fatal_and_stops_acceptance(self) -> None:
        import tempfile
        from pathlib import Path

        from candle_aggregation import CompletedOneMinuteCandle

        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        token = _SAMPLE_STOCKS[0].instrument_token
        token_to_symbol = {stock.instrument_token: stock.tradingsymbol for stock in _SAMPLE_STOCKS}
        writer = LiveOneMinuteCandleWriter(
            db_path=Path(tmpdir.name) / "fatal.db",
            token_to_symbol=token_to_symbol,
        )
        self.addCleanup(writer.close)

        candle_time = _ist(2026, 7, 22, 10, 30, 0)
        writer.on_candle(
            CompletedOneMinuteCandle(
                instrument_token=token,
                candle_time=candle_time,
                open=100.0,
                high=105.0,
                low=99.0,
                close=100.0,
                volume=500,
                tick_count=10,
                volume_reliable=True,
                completion_reason="minute_transition",
                has_full_minute_coverage=True,
                is_partial=False,
            )
        )

        pipeline = LiveCandlePipeline(
            coordinator=MarketDataCoordinator(candle_writer=writer)
        )
        receiver = self._make_receiver(pipeline.builder.on_tick, queue_maxsize=10)
        pipeline.attach_receiver(receiver)

        persist_calls = 0
        original_persist = pipeline.coordinator.on_completed_candle

        def tracked_persist(candle: CompletedOneMinuteCandle) -> None:
            nonlocal persist_calls
            persist_calls += 1
            original_persist(candle)

        pipeline.builder._on_candle = tracked_persist

        receiver._worker_thread = threading.Thread(
            target=receiver._worker_loop,
            name="test-worker",
            daemon=True,
        )
        receiver._worker_thread.start()

        receiver._on_ticks(
            None,
            [
                _raw_tick_with_exchange_ts(token, 101.0, _ist(2026, 7, 22, 10, 30, 5)),
                _raw_tick_with_exchange_ts(token, 102.0, _ist(2026, 7, 22, 10, 31, 5)),
            ],
        )

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if receiver.fatal_error is not None:
                break
            time.sleep(0.02)

        receiver._worker_thread.join(timeout=2.0)

        self.assertIsInstance(receiver.fatal_error, CandleEmissionError)
        assert isinstance(receiver.fatal_error, CandleEmissionError)
        self.assertIsInstance(receiver.fatal_error.cause, CandleConflictError)
        self.assertFalse(receiver.is_accepting_ticks())
        self.assertEqual(persist_calls, 1)
        self.assertEqual(writer.metrics.conflicting_duplicates, 1)
        self.assertIsNotNone(pipeline.builder.failed_emission)


if __name__ == "__main__":
    unittest.main()
