"""engine_arb_context: alignment and first-trigger reads from the observation DB."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from engine_arb_context import (
    ArbContextError,
    LiveArbContext,
    alignment,
    stop_pct_of,
    trigger_moment_ist,
)
from engine_types import TriggerCandidate

SECTORS = (("Banks", "HDFCBANK ICICIBANK SBIN"), ("IT", "INFY TCS"))
DAY = "2026-09-22"


def candidate(
    setup_id: str = "s1",
    *,
    symbol: str = "HDFCBANK",
    direction: str = "UP",
    trigger_ts: str = "2026-09-22T10:12:34+05:30",
    version: str = "v1",
    created_at: str = "2026-09-22T04:42:34+00:00",
) -> TriggerCandidate:
    return TriggerCandidate(
        setup_id=setup_id,
        continuation_rule_version=version,
        session_date=DAY,
        tradingsymbol=symbol,
        instrument_token=1,
        direction=direction,
        trigger_price=100.0,
        pullback_swing_high=None,
        pullback_swing_low=None,
        tick_size=0.05,
        buffer_ticks=1,
        trigger_exchange_ts=trigger_ts,
        created_at=created_at,
        breakout_candle_volume=3000,
        avg_prior_3_1m_volume=1000.0,
    )


class LiveDb:
    """A minimal observation DB: 1m bars plus continuation triggers."""

    def __init__(self, root: Path) -> None:
        self.path = root / "live.db"
        conn = sqlite3.connect(self.path)
        conn.execute(
            "CREATE TABLE live_1m_candles (tradingsymbol TEXT, candle_time TEXT, "
            "session_date TEXT, open REAL, close REAL)"
        )
        conn.execute(
            "CREATE TABLE live_continuation_arms (setup_id TEXT, continuation_rule_version TEXT, "
            "session_date TEXT, tradingsymbol TEXT)"
        )
        conn.execute(
            "CREATE TABLE live_continuation_decisions (setup_id TEXT, continuation_rule_version TEXT, "
            "decision_type TEXT, trigger_exchange_ts TEXT, created_at TEXT)"
        )
        conn.commit()
        conn.close()

    def bar(self, symbol: str, hhmm: str, open_: float, close: float, day: str = DAY) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT INTO live_1m_candles VALUES (?, ?, ?, ?, ?)",
                (symbol, f"{day}T{hhmm}:00+05:30", day, open_, close),
            )

    def trigger(self, setup_id: str, symbol: str, ts: str, *, version: str = "v1",
                decision: str = "TRIGGERED", day: str = DAY) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT INTO live_continuation_arms VALUES (?, ?, ?, ?)",
                (setup_id, version, day, symbol),
            )
            conn.execute(
                "INSERT INTO live_continuation_decisions VALUES (?, ?, ?, ?, ?)",
                (setup_id, version, decision, ts, ts),
            )


class AlignmentTests(unittest.TestCase):
    def test_market_is_the_mean_move_signed_to_the_trade(self) -> None:
        first = {"A": 100.0, "B": 200.0}
        last = {"A": 101.0, "B": 198.0}  # +1% and -1%
        up, _ = alignment(first_open=first, last_close=last, symbol="A", direction="UP", sector_of={})
        self.assertAlmostEqual(up, 0.0)
        last = {"A": 102.0, "B": 200.0}  # +2% and 0%
        up, _ = alignment(first_open=first, last_close=last, symbol="A", direction="UP", sector_of={})
        down, _ = alignment(first_open=first, last_close=last, symbol="A", direction="DOWN", sector_of={})
        self.assertAlmostEqual(up, 1.0)
        self.assertAlmostEqual(down, -1.0)

    def test_sector_excludes_the_stock_itself(self) -> None:
        first = {"A": 100.0, "B": 100.0, "C": 100.0}
        last = {"A": 110.0, "B": 99.0, "C": 101.0}
        sector_of = {"A": "S", "B": "S", "C": "T"}
        _, sector = alignment(first_open=first, last_close=last, symbol="A", direction="UP", sector_of=sector_of)
        self.assertAlmostEqual(sector, -1.0)

    def test_no_peers_or_no_data_gives_none(self) -> None:
        _, sector = alignment(
            first_open={"A": 100.0}, last_close={"A": 101.0}, symbol="A", direction="UP", sector_of={"A": "S"}
        )
        self.assertIsNone(sector)
        self.assertEqual(
            alignment(first_open={}, last_close={}, symbol="A", direction="UP", sector_of={}), (None, None)
        )


class HelperTests(unittest.TestCase):
    def test_trigger_time_is_the_exchange_time_in_ist(self) -> None:
        moment = trigger_moment_ist(candidate(trigger_ts="2026-09-22T10:12:34+05:30"))
        self.assertEqual(moment.strftime("%H:%M:%S"), "10:12:34")

    def test_a_naive_exchange_time_is_not_guessed(self) -> None:
        # Falls back to created_at (UTC), shown here as IST.
        moment = trigger_moment_ist(candidate(trigger_ts="2026-09-22T10:12:34"))
        self.assertEqual(moment.strftime("%H:%M:%S"), "10:12:34")
        self.assertIsNone(trigger_moment_ist(candidate(trigger_ts=None, created_at="garbage")))

    def test_stop_pct(self) -> None:
        self.assertAlmostEqual(stop_pct_of(candidate(), 99.5), 0.5)
        self.assertIsNone(stop_pct_of(candidate(), None))


class LiveContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = LiveDb(Path(self._tmp.name))
        self.context = LiveArbContext(self.db.path, sector_map=SECTORS)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_only_bars_that_started_before_the_trigger_minute_count(self) -> None:
        self.db.trigger("s1", "HDFCBANK", "2026-09-22T10:12:34+05:30")
        self.db.bar("HDFCBANK", "09:15", 100.0, 100.5)
        self.db.bar("ICICIBANK", "09:15", 100.0, 99.0)
        self.db.bar("HDFCBANK", "10:11", 100.8, 101.0)   # latest close before 10:12
        self.db.bar("ICICIBANK", "10:11", 99.2, 99.0)
        self.db.bar("HDFCBANK", "10:12", 101.0, 150.0)   # the trigger's own minute: ignored
        self.db.bar("ICICIBANK", "10:12", 99.0, 10.0)
        f = self.context.features_for(candidate(), 99.5)
        self.assertAlmostEqual(f.align_market_pct, (1.0 + -1.0) / 2)
        self.assertAlmostEqual(f.align_sector_pct, -1.0)
        self.assertEqual(f.trigger_time_ist.strftime("%H:%M:%S"), "10:12:34")
        self.assertAlmostEqual(f.volume_ratio, 3.0)
        self.assertAlmostEqual(f.stop_pct, 0.5)
        self.assertTrue(f.is_first_trigger_today)

    def test_other_days_are_ignored(self) -> None:
        self.db.trigger("s1", "HDFCBANK", "2026-09-22T10:12:34+05:30")
        self.db.bar("HDFCBANK", "09:15", 100.0, 200.0, day="2026-09-21")
        f = self.context.features_for(candidate(), 99.5)
        self.assertIsNone(f.align_market_pct)
        self.assertIsNone(f.align_sector_pct)

    def test_an_earlier_trigger_in_the_same_stock_makes_it_not_first(self) -> None:
        self.db.trigger("s0", "HDFCBANK", "2026-09-22T09:40:00+05:30")
        self.db.trigger("s1", "HDFCBANK", "2026-09-22T10:12:34+05:30")
        self.assertFalse(self.context.features_for(candidate("s1"), 99.5).is_first_trigger_today)

    def test_earlier_triggers_elsewhere_do_not_count(self) -> None:
        self.db.trigger("x0", "INFY", "2026-09-22T09:40:00+05:30")
        self.db.trigger("y0", "HDFCBANK", "2026-09-21T09:40:00+05:30", day="2026-09-21")
        self.db.trigger("z0", "HDFCBANK", "2026-09-22T09:41:00+05:30", decision="NOT_TRIGGERED")
        self.db.trigger("s1", "HDFCBANK", "2026-09-22T10:12:34+05:30")
        self.assertTrue(self.context.features_for(candidate("s1"), 99.5).is_first_trigger_today)

    def test_the_rule_version_is_part_of_the_identity(self) -> None:
        self.db.trigger("s1", "HDFCBANK", "2026-09-22T10:12:34+05:30", version="v2")
        self.assertFalse(self.context.features_for(candidate("s1", version="v1"), 99.5).is_first_trigger_today)

    def test_a_trigger_missing_from_the_db_is_not_first(self) -> None:
        self.assertFalse(self.context.features_for(candidate("s1"), 99.5).is_first_trigger_today)

    def test_a_missing_db_or_table_raises(self) -> None:
        with self.assertRaises(ArbContextError):
            LiveArbContext(Path(self._tmp.name) / "absent.db").features_for(candidate(), 99.5)
        empty = Path(self._tmp.name) / "empty.db"
        sqlite3.connect(empty).close()
        with self.assertRaises(ArbContextError):
            LiveArbContext(empty).features_for(candidate(), 99.5)


if __name__ == "__main__":
    unittest.main()
