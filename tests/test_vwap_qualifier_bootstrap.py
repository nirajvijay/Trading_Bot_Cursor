"""Bootstrap cutoff filtering: no B0 fragments, no look-ahead bars."""

from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from candle_aggregation import OneMinuteCandle
from tick_event import IST
from vwap_qualifier_bootstrap import (
    filter_one_minute_before_cutoff,
    seed_closed_five_minute_contributions,
)
from vwap_qualifier_state import freeze_cutoff, startup_bucket

_IST = ZoneInfo(IST)
SESSION = "2026-08-21"


def _ist(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 8, 21, hour, minute, second, tzinfo=_IST)


def _1m(hour: int, minute: int, *, volume: int = 100, close: float = 100.0) -> OneMinuteCandle:
    ts = _ist(hour, minute)
    return OneMinuteCandle(
        candle_time=ts.isoformat(timespec="seconds"),
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=volume,
    )


class CutoffFilterTests(unittest.TestCase):
    def test_drops_bars_that_close_after_cutoff(self) -> None:
        cutoff = _ist(10, 2)
        candles = [_1m(10, 0), _1m(10, 1), _1m(10, 2)]
        kept = filter_one_minute_before_cutoff(candles, cutoff)
        times = [c.candle_time for c in kept]
        self.assertIn(_ist(10, 0).isoformat(timespec="seconds"), times)
        self.assertIn(_ist(10, 1).isoformat(timespec="seconds"), times)
        self.assertNotIn(_ist(10, 2).isoformat(timespec="seconds"), times)

    def test_start_1002_seeds_0955_not_1000(self) -> None:
        cutoff = freeze_cutoff(_ist(10, 2, 15))
        self.assertEqual(cutoff, _ist(10, 2))
        b0 = startup_bucket(cutoff, SESSION)
        self.assertEqual(b0, _ist(10, 0))
        candles = [_1m(9, m) for m in range(15, 60)] + [_1m(10, 0), _1m(10, 1)]
        contribs = seed_closed_five_minute_contributions(candles, cutoff=cutoff, b0=b0)
        starts = {c[0] for c in contribs}
        self.assertIn(_ist(9, 55), starts)
        self.assertNotIn(_ist(10, 0), starts)

    def test_on_boundary_cutoff_still_has_b0(self) -> None:
        cutoff = freeze_cutoff(_ist(10, 0, 0))
        b0 = startup_bucket(cutoff, SESSION)
        self.assertEqual(b0, _ist(10, 0))


if __name__ == "__main__":
    unittest.main()
