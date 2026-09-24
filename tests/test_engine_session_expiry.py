"""A dead Kite access token pauses entries with its real reason.

Before: with no position open the engine made no Kite call, so a stale token
was first met by a trigger. The margin preflight's read failed, the trigger was
recorded as skipped for "insufficient margin" and consumed; a placement refused
for the token was treated as ambiguous and re-asked the book with the same dead
token. Now the token is checked once at start, every broker refusal flips a
sticky flag, and entries pause as kite_session_expired while triggers stay
unconsumed.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock

from kiteconnect.exceptions import NetworkException, TokenException

from engine_entry import ResponseKind, place_entry
from engine_types import ExecutionState
from tests.test_engine_lifecycle import (
    HealthyFeed,
    LifecycleTestCase,
    StaleFeed,
    candidate,
)
from trading_engine_broker import (
    KITE_SESSION_EXPIRED_REASON,
    BrokerSessionExpired,
    KiteBroker,
)
from trading_engine_types import MarginQuote

TOKEN_MESSAGE = "Incorrect `api_key` or `access_token`."


class SwitchableFeed:
    def __init__(self) -> None:
        self.feed = HealthyFeed()

    def check(self, *, now=None):
        return self.feed.check(now=now)


class EngineSessionExpiryTests(LifecycleTestCase):
    def engine_events(self):
        return [r["event_type"] for r in self.store.list_events("__engine__")]

    def assert_held_for_session(self, engine) -> None:
        self.assertTrue(engine.entries_paused)
        self.assertEqual(engine.pause_reason, KITE_SESSION_EXPIRED_REASON)
        self.assertIn(KITE_SESSION_EXPIRED_REASON, engine.failures.escalated)
        self.assertFalse(engine.entries_allowed)

    def test_a_healthy_token_is_checked_once_and_changes_nothing(self) -> None:
        self.candidates = [candidate("s1")]
        engine = self.engine()
        engine.tick()
        engine.tick()
        self.assertEqual(self.engine_events().count("broker_session_checked"), 1)
        checked = [
            json.loads(r["payload_json"])
            for r in self.store.list_events("__engine__")
            if r["event_type"] == "broker_session_checked"
        ]
        self.assertEqual(checked, [{"valid": True}])
        self.assertEqual(self.store.get("s1").state, ExecutionState.PROTECTED)
        self.assertFalse(engine.entries_paused)

    def test_a_dead_token_at_start_holds_the_trigger_unconsumed(self) -> None:
        self.broker.token_dead = True
        self.candidates = [candidate("s1")]
        engine = self.engine()
        engine.tick()
        self.assert_held_for_session(engine)
        self.assertFalse(self.store.exists("s1"))
        self.assertEqual(self.broker.market_place_count, 0)
        # Escalated once, however many ticks pass.
        engine.tick()
        engine.tick()
        self.assertEqual(self.engine_events().count("step_escalated"), 1)
        self.assertFalse(self.store.exists("s1"))

    def test_a_token_dying_while_flat_is_not_a_margin_skip(self) -> None:
        engine = self.engine()
        engine.tick()  # startup check passes
        self.broker.token_dead = True
        self.candidates = [candidate("s1")]
        engine.tick()  # the preflight's margin read is the first call to fail
        self.assert_held_for_session(engine)
        # Not stored as skipped/insufficient_margin_preflight: still routable.
        self.assertFalse(self.store.exists("s1"))
        self.assertEqual(self.broker.market_place_count, 0)

    def test_a_token_dying_under_an_open_position_holds_new_triggers(self) -> None:
        self.candidates = [candidate("s1")]
        engine = self.engine()
        engine.tick()
        self.assertEqual(self.store.get("s1").state, ExecutionState.PROTECTED)

        self.broker.token_dead = True
        self.candidates = [candidate("s2", symbol="BBB")]
        engine.tick()  # reconcile's positions read fails first, same tick
        self.assert_held_for_session(engine)
        self.assertFalse(self.store.exists("s2"))
        # The held position is not touched by the pause.
        self.assertEqual(self.store.get("s1").state, ExecutionState.PROTECTED)

    def test_a_placement_refused_for_the_token_is_rejected_not_ambiguous(self) -> None:
        engine = self.engine()
        engine.tick()
        # Preflight passes (as if margins answered just before the token died).
        self.broker.order_margins = lambda **_: MarginQuote(ok=True, required=1.0)  # type: ignore[method-assign]
        self.broker.token_dead = True
        self.candidates = [candidate("s1")]
        engine.tick()
        stored = self.store.get("s1")
        self.assertEqual(stored.state, ExecutionState.REJECTED)
        self.assertTrue(
            str(stored.extra.get("reject_reason")).startswith(KITE_SESSION_EXPIRED_REASON)
        )
        self.assertNotIn("entry_ambiguous", self.events("s1"))
        # And the next trigger is held, not attempted.
        self.candidates = [candidate("s2", symbol="BBB")]
        engine.tick()
        self.assert_held_for_session(engine)
        self.assertFalse(self.store.exists("s2"))

    def test_the_reason_survives_a_stale_feed_recovering(self) -> None:
        feed = SwitchableFeed()
        self.broker.token_dead = True
        engine = self.engine(feed=feed)
        engine.tick()
        feed.feed = StaleFeed()
        engine.tick()
        feed.feed = HealthyFeed()
        engine.tick()
        self.assert_held_for_session(engine)


class PlaceEntryClassificationTests(unittest.TestCase):
    def test_a_session_refusal_is_a_definite_rejection(self) -> None:
        broker = MagicMock()
        broker.place_market_mis.side_effect = BrokerSessionExpired(TOKEN_MESSAGE)
        response = place_entry(broker, candidate=candidate("s1"), quantity=1, tag="t")
        self.assertIs(response.kind, ResponseKind.REJECTED)
        self.assertTrue(response.reason.startswith(KITE_SESSION_EXPIRED_REASON))


class FakeKite:
    """Mimics the SDK's _request: the hook fires, then TokenException raises."""

    def __init__(self) -> None:
        self.hook = None
        self.token_dead = False
        self.placed = 0
        self.fail_orders_after_place = False

    def set_session_expiry_hook(self, method) -> None:
        self.hook = method

    def _refuse(self) -> None:
        if self.hook:
            self.hook()
        raise TokenException(TOKEN_MESSAGE, code=403)

    def profile(self):
        if self.token_dead:
            self._refuse()
        return {"user_id": "AB1234"}

    def positions(self):
        if self.token_dead:
            self._refuse()
        return {"net": [], "day": []}

    def orders(self):
        if self.token_dead or (self.fail_orders_after_place and self.placed):
            self._refuse()
        return []

    def order_history(self, order_id):
        return [o for o in self.orders() if o.get("order_id") == order_id]

    def place_order(self, **_):
        if self.token_dead:
            self._refuse()
        self.placed += 1
        return "ord1"


class KiteBrokerSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.kite = FakeKite()
        self.broker = KiteBroker(self.kite, live_orders_enabled=True)

    def test_the_hook_is_registered_and_starts_clear(self) -> None:
        self.assertIsNotNone(self.kite.hook)
        self.assertFalse(self.broker.session_expired)

    def test_a_swallowed_read_still_flags_the_session(self) -> None:
        # _positions() swallows the error; the hook is what makes it visible.
        self.kite.token_dead = True
        self.assertIsNone(self.broker.position_quote("AAA"))
        self.assertTrue(self.broker.session_expired)

    def test_check_session(self) -> None:
        self.assertTrue(self.broker.check_session())
        self.kite.token_dead = True
        self.assertFalse(self.broker.check_session())
        self.assertTrue(self.broker.session_expired)

    def test_a_network_error_is_not_a_dead_token(self) -> None:
        self.kite.profile = MagicMock(side_effect=NetworkException("timeout"))  # type: ignore[method-assign]
        self.assertIsNone(self.broker.check_session())
        self.assertFalse(self.broker.session_expired)

    def test_a_refused_placement_raises_session_expired(self) -> None:
        self.kite.token_dead = True
        with self.assertRaises(BrokerSessionExpired):
            self.broker.place_market_mis(
                tradingsymbol="AAA", transaction_type="BUY", quantity=1, tag="t"
            )
        self.assertTrue(self.broker.session_expired)
        self.assertEqual(self.kite.placed, 0)

    def test_a_refusal_after_acceptance_is_not_translated(self) -> None:
        # The order was accepted; only the follow-up poll failed. That is
        # ambiguous (an order may exist), never a definite rejection.
        self.kite.fail_orders_after_place = True
        with self.assertRaises(TokenException):
            self.broker.place_market_mis(
                tradingsymbol="AAA", transaction_type="BUY", quantity=1, tag="t"
            )
        self.assertEqual(self.kite.placed, 1)


if __name__ == "__main__":
    unittest.main()
