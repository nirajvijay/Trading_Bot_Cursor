"""The desk's live Open P&L formula and its Kite REST fallback."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from engine_live_marks import (
    FEED_FALLBACK,
    FEED_LIVE,
    FEED_OFF,
    REASON_ENTRY_UNKNOWN,
    REASON_NO_TICK,
    REASON_TICK_STALE,
    SOURCE_KITE_REST,
    SOURCE_WS,
    compute_live_mark,
    feed_state,
)
from engine_live_ticks import REASON_DISCONNECTED, REASON_PAPER, Tick, TickFeedHealth

NOW = datetime(2026, 9, 22, 5, 0, tzinfo=timezone.utc)
UP = TickFeedHealth(live=True, connected=True, last_tick_at=NOW)
DOWN = TickFeedHealth(live=True, connected=False, reason=REASON_DISCONNECTED)
PAPER = TickFeedHealth(live=False, connected=False, reason=REASON_PAPER)


def mark(**overrides):
    args = dict(
        direction="UP",
        entry_avg=110.20,
        qty=300,
        tick=Tick(112.00, NOW - timedelta(seconds=1)),
        feed_health=UP,
        kite_rest_pnl=-999.0,
        now=NOW,
    )
    args.update(overrides)
    return compute_live_mark(**args)


class FormulaTests(unittest.TestCase):
    def test_long(self) -> None:
        result = mark()
        self.assertEqual(result.source, SOURCE_WS)
        self.assertAlmostEqual(result.value, (112.00 - 110.20) * 300, places=2)

    def test_short(self) -> None:
        result = mark(direction="DOWN", entry_avg=110.00, tick=Tick(108.50, NOW))
        self.assertAlmostEqual(result.value, (110.00 - 108.50) * 300, places=2)

    def test_kite_rest_pnl_is_not_used_when_live(self) -> None:
        self.assertNotEqual(mark().value, -999.0)


class FallbackTests(unittest.TestCase):
    def assertFallback(self, result, reason) -> None:
        self.assertEqual(result.source, SOURCE_KITE_REST)
        self.assertEqual(result.value, -999.0)
        self.assertEqual(result.reason, reason)

    def test_feed_down(self) -> None:
        self.assertFallback(mark(feed_health=DOWN), REASON_DISCONNECTED)

    def test_paper_has_no_feed(self) -> None:
        self.assertFallback(mark(feed_health=PAPER), REASON_PAPER)

    def test_no_tick_yet(self) -> None:
        self.assertFallback(mark(tick=None), REASON_NO_TICK)

    def test_exactly_five_seconds_old_is_still_live(self) -> None:
        result = mark(tick=Tick(112.00, NOW - timedelta(seconds=5)))
        self.assertEqual(result.source, SOURCE_WS)

    def test_just_over_five_seconds_old_is_stale(self) -> None:
        self.assertFallback(
            mark(tick=Tick(112.00, NOW - timedelta(seconds=5.01))), REASON_TICK_STALE
        )

    def test_unknown_entry_price(self) -> None:
        self.assertFallback(mark(entry_avg=None), REASON_ENTRY_UNKNOWN)
        self.assertFallback(mark(qty=0), REASON_ENTRY_UNKNOWN)


class FeedStateTests(unittest.TestCase):
    def test_all_ws_is_live(self) -> None:
        self.assertEqual(feed_state(feed_health=UP, sources=[SOURCE_WS, SOURCE_WS]), FEED_LIVE)

    def test_nothing_held_and_connected_is_live(self) -> None:
        self.assertEqual(feed_state(feed_health=UP, sources=[]), FEED_LIVE)

    def test_any_rest_mark_is_fallback(self) -> None:
        self.assertEqual(
            feed_state(feed_health=UP, sources=[SOURCE_WS, SOURCE_KITE_REST]), FEED_FALLBACK
        )

    def test_disconnected_is_fallback(self) -> None:
        self.assertEqual(feed_state(feed_health=DOWN, sources=[]), FEED_FALLBACK)

    def test_paper_is_off(self) -> None:
        self.assertEqual(feed_state(feed_health=PAPER, sources=[SOURCE_KITE_REST]), FEED_OFF)


if __name__ == "__main__":
    unittest.main()
