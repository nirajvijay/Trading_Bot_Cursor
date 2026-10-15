"""ORB detector, driven tick by tick against a real SQLite file."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import orb_detector
from orb_config import DEFAULT_ORB_CONFIG, OrbConfig
from orb_detector import (
    ARM_WRITE_FAILED,
    REASON_ALL_ARMS_DECIDED,
    REASON_NO_STOCKS_IN_PLAY,
    REASON_NO_VALID_RANGES,
    REASON_RANKED_TOO_LATE,
    REASON_RANKING_WRITE_FAILED,
    REASON_TOO_MANY_FAILURES,
    STATE_COLLECTING,
    STATE_DISABLED,
    STATE_DONE,
    STATE_RANKED,
    OrbDetector,
)
from orb_types import (
    ARM_ARMED,
    DECISION_EXPIRED,
    DECISION_MISSED,
    DECISION_TRIGGERED,
    MISSED_OVERSHOOT,
    RANGE_NO_PREOPEN,
    RANGE_STALE_TICKS,
    OrbBaseline,
)
from orb_writer import OrbWriter, OrbWriterError
from tick_event import Ohlc, TickEvent

IST = ZoneInfo("Asia/Kolkata")
DAY = "2026-09-22"
SYMBOLS = {1: "AAA", 2: "BBB", 3: "CCC", 4: "DDD"}


def ts(hh: int, mm: int, ss: int = 0, *, day: str = DAY) -> datetime:
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, hh, mm, ss, tzinfo=IST)


class Harness:
    """A detector on its own database, fed hand-built ticks."""

    def __init__(self, tmp: str, *, symbols=None, baselines=None, config: OrbConfig = DEFAULT_ORB_CONFIG) -> None:
        self.db = Path(tmp) / "live.db"
        self.symbols: Dict[int, str] = dict(symbols or SYMBOLS)
        self.baselines = baselines or {
            token: OrbBaseline(token, symbol, True, atr14=20.0, avg_or_volume=1000.0)
            for token, symbol in self.symbols.items()
        }
        self.config = config
        self.sequence = 0
        # The wall clock the detector sees. It follows the ticks, so by
        # default every tick is processed the instant it is stamped.
        self.now = ts(8, 0)
        self.ranked_calls: list = []
        self.decisions: list = []
        self.writer: Optional[OrbWriter] = None
        self.detector = self.start()

    def start(self, writer: Optional[OrbWriter] = None) -> OrbDetector:
        self.writer = writer or OrbWriter(self.db)
        self.detector = OrbDetector(
            session_date=DAY,
            token_to_symbol=self.symbols,
            tick_sizes={token: 0.05 for token in self.symbols},
            baselines=self.baselines,
            writer=self.writer,
            config=self.config,
            on_ranked=lambda ranked, arms: self.ranked_calls.append((list(ranked), list(arms))),
            on_decision=lambda arm, decision: self.decisions.append((arm.tradingsymbol, decision)),
            now_fn=lambda: self.now,
        )
        return self.detector

    def restart(self) -> OrbDetector:
        assert self.writer is not None
        self.writer.close()
        return self.start()

    def tick(self, token: int, when: datetime, price: float, *, volume: int = 0,
             day_open: float = 1000.0, high: Optional[float] = None, low: Optional[float] = None,
             lag: float = 0.0) -> None:
        """Feed one tick, processed ``lag`` seconds after its exchange stamp."""
        self.sequence += 1
        self.now = max(self.now, when + timedelta(seconds=lag))
        self.detector.on_tick(
            TickEvent(
                sequence=self.sequence,
                instrument_token=token,
                last_price=price,
                exchange_timestamp=when,
                received_at=when,
                volume_traded=volume,
                last_traded_quantity=1,
                average_traded_price=price,
                ohlc=Ohlc(
                    open=day_open,
                    high=max(price, day_open) if high is None else high,
                    low=min(price, day_open) if low is None else low,
                    close=990.0,
                ),
            )
        )

    def opening(self, token: int, *, rvol: float, close: float = 1010.0, high: float = 1012.0,
                low: float = 998.0, preopen: Optional[int] = 500, last_at: Optional[datetime] = None) -> None:
        """Pre-open tick, then the stock's last tick inside the range."""
        if preopen is not None:
            self.tick(token, ts(9, 8, 5), 1000.0, volume=preopen)
        self.tick(
            token,
            last_at or ts(9, 19, 59),
            close,
            volume=(preopen or 0) + int(rvol * 1000),
            high=high,
            low=low,
        )

    def standard_open(self) -> None:
        """AAA 5x up, BBB 4x down, CCC 3.5x up; DDD at 2x is not in play."""
        self.opening(1, rvol=5.0)
        self.opening(2, rvol=4.0, close=999.0)
        self.opening(3, rvol=3.5)
        self.opening(4, rvol=2.0)

    def cross_boundary(self, price_by_token: Optional[Dict[int, float]] = None) -> None:
        """Every stock ticks at 09:20:00 inside its range, which starts the ranking."""
        prices = {1: 1010.0, 2: 999.0, 3: 1010.0, 4: 1010.0}
        prices.update(price_by_token or {})
        for token in self.symbols:
            self.tick(token, ts(9, 20, 0), prices.get(token, 1010.0), volume=10**6, high=1012.0, low=998.0)

    def rows(self, table: str) -> List[sqlite3.Row]:
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(f"SELECT * FROM {table}").fetchall()
        finally:
            conn.close()

    def decision_rows(self) -> Dict[str, sqlite3.Row]:
        arms = {row["setup_id"]: row["tradingsymbol"] for row in self.rows("live_orb_arms")}
        return {arms[row["setup_id"]]: row for row in self.rows("live_orb_decisions")}


class DetectorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.h = Harness(self._tmp.name)
        self.addCleanup(lambda: self.h.writer and self.h.writer.close())


class RankingTests(DetectorTestCase):
    def test_ranks_when_every_stock_has_crossed_0920_and_arms_the_top_three(self) -> None:
        h = self.h
        h.standard_open()
        self.assertEqual(h.detector.state, STATE_COLLECTING)
        h.cross_boundary()
        self.assertEqual(h.detector.state, STATE_RANKED)
        arms = h.detector.arms()
        self.assertEqual([(a.tradingsymbol, a.direction, a.level, a.rank) for a in arms],
                         [("AAA", "UP", 1012.0, 1), ("BBB", "DOWN", 998.0, 2), ("CCC", "UP", 1012.0, 3)])
        self.assertEqual(len(h.rows("live_orb_ranges")), 4)
        self.assertEqual(len(h.rows("live_orb_arms")), 3)
        self.assertEqual(len(h.ranked_calls), 1)
        self.assertEqual(h.rows("live_orb_status")[0]["state"], STATE_RANKED)

    def test_auction_volume_is_not_opening_volume(self) -> None:
        h = self.h
        # 2,600 shares by 09:20, but 2,000 of them traded in the pre-open
        # auction: the opening range saw 600, which is 0.6x, not 2.6x.
        h.opening(1, rvol=0.6, preopen=2000)
        h.opening(2, rvol=3.0, close=999.0)
        h.cross_boundary()
        ranges = {row["tradingsymbol"]: row for row in h.rows("live_orb_ranges")}
        self.assertEqual(ranges["AAA"]["or_volume"], 600)
        self.assertEqual(ranges["AAA"]["preopen_volume"], 2000)
        self.assertAlmostEqual(ranges["AAA"]["rvol"], 0.6)
        self.assertEqual([a.tradingsymbol for a in h.detector.arms()], ["BBB"])

    def test_a_silent_stock_does_not_hold_the_ranking_past_one_second(self) -> None:
        h = self.h
        h.standard_open()
        for token in (1, 2, 3):                     # DDD never ticks again
            h.tick(token, ts(9, 20, 0), 1010.0 if token != 2 else 999.0, volume=10**6, high=1012.0, low=998.0)
        self.assertEqual(h.detector.state, STATE_COLLECTING)
        h.tick(1, ts(9, 20, 1), 1010.0, volume=10**6, high=1012.0, low=998.0)
        self.assertEqual(h.detector.state, STATE_RANKED)
        self.assertEqual(len(h.detector.arms()), 3)

    def test_a_late_tick_from_inside_the_range_still_counts_before_the_ranking(self) -> None:
        h = self.h
        h.standard_open()
        h.tick(2, ts(9, 20, 0), 999.0, volume=10**6, high=1012.0, low=998.0)
        # AAA's last range tick arrives after BBB's first post-range tick.
        h.tick(1, ts(9, 19, 59), 1011.0, volume=500 + 6000, high=1015.0, low=998.0)
        h.cross_boundary()
        arm = h.detector.arms()[0]
        self.assertEqual((arm.tradingsymbol, arm.level, arm.rvol), ("AAA", 1015.0, 6.0))

    def test_the_first_tick_after_the_range_never_moves_the_range(self) -> None:
        h = self.h
        h.standard_open()
        # AAA makes a new high in the first second after 09:20.
        h.tick(1, ts(9, 20, 0), 1013.0, volume=10**6, high=1013.0, low=998.0)
        h.cross_boundary({1: 1013.0})
        self.assertEqual(h.detector.arms()[0].level, 1012.0)

    def test_no_pre_open_tick_means_the_stock_is_not_ranked(self) -> None:
        h = self.h
        h.opening(1, rvol=9.0, preopen=None)        # observation connected after 09:15
        h.opening(2, rvol=4.0, close=999.0)
        h.cross_boundary()
        ranges = {row["tradingsymbol"]: row for row in h.rows("live_orb_ranges")}
        self.assertEqual((ranges["AAA"]["valid"], ranges["AAA"]["reason"]), (0, RANGE_NO_PREOPEN))
        self.assertEqual([a.tradingsymbol for a in h.detector.arms()], ["BBB"])

    def test_a_stock_quiet_for_the_last_half_minute_is_left_out(self) -> None:
        h = self.h
        h.opening(1, rvol=9.0, last_at=ts(9, 19, 25))
        h.opening(2, rvol=4.0, close=999.0)
        h.cross_boundary()
        ranges = {row["tradingsymbol"]: row for row in h.rows("live_orb_ranges")}
        self.assertEqual(ranges["AAA"]["reason"], RANGE_STALE_TICKS)

    def test_yesterdays_snapshot_on_connect_is_ignored(self) -> None:
        h = self.h
        h.tick(1, ts(15, 29, 59, day="2026-09-21"), 1000.0, volume=9_999_999)
        h.standard_open()
        h.cross_boundary()
        ranges = {row["tradingsymbol"]: row for row in h.rows("live_orb_ranges")}
        self.assertEqual(ranges["AAA"]["preopen_volume"], 500)

    def test_nothing_in_play_ends_the_day_quietly(self) -> None:
        h = self.h
        for token in h.symbols:
            h.opening(token, rvol=1.5)
        h.cross_boundary()
        self.assertEqual((h.detector.state, h.detector.reason), (STATE_DONE, REASON_NO_STOCKS_IN_PLAY))
        self.assertEqual(h.rows("live_orb_arms"), [])
        self.assertEqual(len(h.rows("live_orb_ranges")), 4)

    def test_started_after_the_open_there_is_nothing_to_rank(self) -> None:
        h = self.h
        h.tick(1, ts(9, 17, 0), 1005.0, volume=4000, high=1012.0, low=998.0)   # no pre-open ticks
        h.cross_boundary()
        self.assertEqual((h.detector.state, h.detector.reason), (STATE_DONE, REASON_NO_VALID_RANGES))

    def test_a_ranking_that_can_only_run_late_arms_nothing(self) -> None:
        h = self.h
        h.standard_open()
        h.tick(1, ts(9, 27, 0), 1030.0, volume=10**6, high=1030.0, low=998.0)  # feed back after 7 minutes
        self.assertEqual((h.detector.state, h.detector.reason), (STATE_DONE, REASON_RANKED_TOO_LATE))
        self.assertEqual(h.rows("live_orb_arms"), [])
        late = [row for row in h.rows("live_orb_ranges") if row["reason"] == REASON_RANKED_TOO_LATE]
        self.assertEqual(len(late), 3)
        self.assertTrue(all(row["selected"] == 0 for row in h.rows("live_orb_ranges")))

    def test_baseline_not_ready_keeps_a_stock_out(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        baselines = {
            token: OrbBaseline(token, symbol, token != 1, reason=None if token != 1 else "stale_history",
                               atr14=20.0 if token != 1 else None,
                               avg_or_volume=1000.0 if token != 1 else None)
            for token, symbol in SYMBOLS.items()
        }
        h = Harness(tmp.name, baselines=baselines)
        self.addCleanup(lambda: h.writer.close())
        h.standard_open()
        h.cross_boundary()
        self.assertEqual([a.tradingsymbol for a in h.detector.arms()], ["BBB", "CCC"])


class BreakoutTests(DetectorTestCase):
    def armed(self) -> Harness:
        self.h.standard_open()
        self.h.cross_boundary()
        return self.h

    def test_first_tick_at_the_level_triggers_with_a_stop_035_atr_away(self) -> None:
        h = self.armed()
        h.tick(1, ts(9, 23, 10), 1011.95, volume=10**6)
        self.assertEqual(h.decisions, [])
        h.tick(1, ts(9, 23, 11), 1012.0, volume=10**6)
        self.assertEqual(len(h.decisions), 1)
        symbol, decision = h.decisions[0]
        self.assertEqual((symbol, decision.decision_type), ("AAA", DECISION_TRIGGERED))
        self.assertEqual((decision.trigger_price, decision.planned_stop), (1012.0, 1005.0))
        row = h.decision_rows()["AAA"]
        self.assertEqual(row["decision_type"], DECISION_TRIGGERED)
        self.assertEqual(row["trigger_exchange_ts"], "2026-09-22T09:23:11+05:30")
        self.assertEqual((row["trigger_price"], row["planned_stop"], row["overshoot"]), (1012.0, 1005.0, 0.0))
        self.assertEqual(h.detector.arm_state(1), DECISION_TRIGGERED)

    def test_a_short_triggers_at_the_low_with_the_stop_above(self) -> None:
        h = self.armed()
        h.tick(2, ts(9, 31, 0), 997.5, volume=10**6)
        row = h.decision_rows()["BBB"]
        self.assertEqual((row["decision_type"], row["trigger_price"], row["planned_stop"]),
                         (DECISION_TRIGGERED, 997.5, 1004.5))
        self.assertAlmostEqual(row["overshoot"], 0.5)

    def test_one_trigger_per_stock_per_day(self) -> None:
        h = self.armed()
        h.tick(1, ts(9, 23, 11), 1012.0, volume=10**6)
        h.tick(1, ts(9, 23, 12), 1011.0, volume=10**6)
        h.tick(1, ts(9, 23, 13), 1013.0, volume=10**6)
        self.assertEqual(len(h.decisions), 1)
        self.assertEqual(len(h.rows("live_orb_decisions")), 1)

    def test_an_unarmed_stock_never_triggers(self) -> None:
        h = self.armed()
        h.tick(4, ts(9, 23, 11), 1050.0, volume=10**6)
        self.assertEqual(h.decisions, [])

    def test_a_first_tick_far_beyond_the_level_is_missed_not_chased(self) -> None:
        h = self.armed()
        h.tick(1, ts(9, 23, 11), 1014.05, volume=10**6)     # 2.05 beyond; the limit is 2.00
        symbol, decision = h.decisions[0]
        self.assertEqual((decision.decision_type, decision.reason), (DECISION_MISSED, MISSED_OVERSHOOT))
        self.assertIsNone(decision.planned_stop)
        h.tick(1, ts(9, 23, 12), 1012.0, volume=10**6)      # back at the level: still done
        self.assertEqual(len(h.decisions), 1)
        self.assertEqual(h.decision_rows()["AAA"]["decision_type"], DECISION_MISSED)

    def test_a_breakout_during_the_grace_second_is_seen_at_arm_time(self) -> None:
        h = self.h
        h.standard_open()
        h.cross_boundary({1: 1012.5})                      # AAA is already through its 1012 level
        self.assertEqual([(s, d.decision_type, d.trigger_price) for s, d in h.decisions],
                         [("AAA", DECISION_TRIGGERED, 1012.5)])

    def test_last_second_before_the_cutoff_triggers_and_the_cutoff_expires_the_rest(self) -> None:
        h = self.armed()
        h.tick(1, ts(11, 0, 59), 1012.0, volume=10**6)
        self.assertEqual(h.detector.arm_state(1), DECISION_TRIGGERED)
        h.tick(4, ts(11, 1, 0), 1010.0, volume=10**6)       # any stock's tick moves the clock
        rows = h.decision_rows()
        self.assertEqual((rows["BBB"]["decision_type"], rows["CCC"]["decision_type"]),
                         (DECISION_EXPIRED, DECISION_EXPIRED))
        self.assertEqual((h.detector.state, h.detector.reason), (STATE_DONE, REASON_ALL_ARMS_DECIDED))
        h.tick(3, ts(11, 5, 0), 1020.0, volume=10**6)
        self.assertEqual(len(h.rows("live_orb_decisions")), 3)

    def test_a_tick_at_the_cutoff_cannot_trigger(self) -> None:
        h = self.armed()
        h.tick(1, ts(11, 1, 0), 1012.0, volume=10**6)
        self.assertEqual(h.decision_rows()["AAA"]["decision_type"], DECISION_EXPIRED)

    def test_a_price_off_the_tick_grid_is_ignored(self) -> None:
        h = self.armed()
        h.tick(1, ts(9, 23, 11), 1012.03, volume=10**6)
        self.assertEqual(h.decisions, [])
        self.assertEqual(h.detector.metrics.off_grid_ticks, 1)


class FreshnessTests(DetectorTestCase):
    def armed(self) -> Harness:
        self.h.standard_open()
        self.h.cross_boundary()
        return self.h

    def test_a_tick_that_arrives_too_old_is_not_judged(self) -> None:
        h = self.armed()
        h.tick(1, ts(9, 23, 0), 1012.0, volume=10**6, lag=11)     # processed at 09:23:11
        self.assertEqual(h.decisions, [])
        self.assertEqual(h.detector.arm_state(1), ARM_ARMED)
        self.assertEqual(h.detector.metrics.stale_ticks, 1)
        h.tick(1, ts(9, 23, 12), 1012.5, volume=10**6)            # the next fresh tick decides
        self.assertEqual([(s, d.decision_type, d.trigger_price) for s, d in h.decisions],
                         [("AAA", DECISION_TRIGGERED, 1012.5)])

    def test_ten_seconds_old_is_still_fresh(self) -> None:
        h = self.armed()
        h.tick(1, ts(9, 23, 0), 1012.0, volume=10**6, lag=10)
        self.assertEqual(h.detector.arm_state(1), DECISION_TRIGGERED)

    def test_after_a_backlog_a_price_long_gone_is_missed_not_entered(self) -> None:
        h = self.armed()
        for second in range(0, 30, 5):                            # a 20-second backlog drains
            h.tick(1, ts(9, 23, second), 1012.0 + second, volume=10**6, lag=20)
        self.assertEqual(h.decisions, [])
        h.tick(1, ts(9, 23, 50), 1040.0, volume=10**6)            # caught up: far through
        self.assertEqual(h.decisions[0][1].decision_type, DECISION_MISSED)

    def test_a_breakout_seen_only_in_stale_ticks_at_arm_time_is_not_triggered(self) -> None:
        h = self.h
        h.standard_open()
        for token in (2, 3, 4):
            h.tick(token, ts(9, 20, 0), 999.0 if token == 2 else 1010.0, volume=10**6,
                   high=1012.0, low=998.0, lag=15)
        h.tick(1, ts(9, 20, 0), 1012.5, volume=10**6, high=1012.5, low=998.0, lag=15)
        self.assertEqual(h.detector.state, STATE_RANKED)          # ranked, 15 seconds late
        self.assertEqual(h.decisions, [])
        self.assertEqual(h.detector.metrics.stale_ticks, 3)       # one held tick per armed stock

    def test_a_tick_stamped_in_the_future_cannot_rank_early(self) -> None:
        h = self.h
        h.opening(1, rvol=5.0, last_at=ts(9, 17, 0))               # the range is still forming
        h.now = ts(9, 17, 0)
        h.sequence += 1
        h.detector.on_tick(TickEvent(h.sequence, 2, 999.0, ts(9, 25, 0), ts(9, 17, 0), 10**6, 1, 999.0,
                                     Ohlc(1000.0, 1012.0, 998.0, 990.0)))
        self.assertEqual(h.detector.state, STATE_COLLECTING)
        self.assertEqual(h.detector.metrics.future_ticks, 1)
        self.assertEqual(h.rows("live_orb_ranges"), [])

    def test_a_tick_stamped_in_the_future_cannot_expire_the_arms(self) -> None:
        h = self.armed()
        h.sequence += 1
        h.detector.on_tick(TickEvent(h.sequence, 4, 1010.0, ts(11, 30, 0), ts(9, 21, 0), 10**6, 1, 1010.0,
                                     Ohlc(1000.0, 1012.0, 998.0, 990.0)))
        self.assertEqual(h.detector.state, STATE_RANKED)
        self.assertEqual(h.rows("live_orb_decisions"), [])

    def test_a_few_seconds_of_clock_skew_is_tolerated(self) -> None:
        h = self.armed()
        h.sequence += 1
        h.now = ts(9, 23, 0)
        h.detector.on_tick(TickEvent(h.sequence, 1, 1012.0, ts(9, 23, 20), ts(9, 23, 0), 10**6, 1, 1012.0,
                                     Ohlc(1000.0, 1012.0, 998.0, 990.0)))
        self.assertEqual(h.detector.arm_state(1), DECISION_TRIGGERED)


class RestartTests(DetectorTestCase):
    def test_restart_resumes_the_arms_and_does_not_rank_again(self) -> None:
        h = self.h
        h.standard_open()
        h.cross_boundary()
        h.tick(1, ts(9, 23, 11), 1012.0, volume=10**6)      # AAA triggers before the restart
        detector = h.restart()
        self.assertTrue(detector.restored)
        self.assertEqual(detector.state, STATE_RANKED)
        self.assertEqual({a.tradingsymbol: detector.arm_state(a.instrument_token) for a in detector.arms()},
                         {"AAA": DECISION_TRIGGERED, "BBB": ARM_ARMED, "CCC": ARM_ARMED})
        h.tick(1, ts(9, 40, 0), 1012.5, volume=10**6)       # must not trigger a second time
        h.tick(3, ts(9, 40, 1), 1012.0, volume=10**6)       # CCC still live
        self.assertEqual(len(h.rows("live_orb_ranges")), 4)
        rows = h.decision_rows()
        self.assertEqual(rows["CCC"]["decision_type"], DECISION_TRIGGERED)
        self.assertEqual(len(rows), 2)

    def test_a_breakout_that_ran_while_down_is_missed_after_the_restart(self) -> None:
        h = self.h
        h.standard_open()
        h.cross_boundary()
        h.restart()
        h.tick(3, ts(10, 5, 0), 1030.0, volume=10**6)       # 18 rupees through the level
        self.assertEqual(h.decision_rows()["CCC"]["decision_type"], DECISION_MISSED)

    def test_restart_after_everything_is_decided_stays_done(self) -> None:
        h = self.h
        h.standard_open()
        h.cross_boundary()
        h.tick(4, ts(11, 1, 0), 1010.0, volume=10**6)
        detector = h.restart()
        self.assertEqual((detector.state, detector.reason), (STATE_DONE, REASON_ALL_ARMS_DECIDED))

    def test_restart_on_a_day_with_no_arms_does_not_rank_again(self) -> None:
        h = self.h
        for token in h.symbols:
            h.opening(token, rvol=1.5)
        h.cross_boundary()
        detector = h.restart()
        self.assertEqual((detector.state, detector.reason), (STATE_DONE, REASON_NO_STOCKS_IN_PLAY))
        h.opening(1, rvol=9.0)
        h.cross_boundary()
        self.assertEqual(h.rows("live_orb_arms"), [])

    def test_a_second_detector_on_the_same_day_adopts_the_first_ranking(self) -> None:
        h = self.h
        h.standard_open()
        other = OrbDetector(
            session_date=DAY, token_to_symbol=h.symbols, tick_sizes={t: 0.05 for t in h.symbols},
            baselines=h.baselines, writer=OrbWriter(h.db), now_fn=lambda: ts(9, 20, 1),
        )
        h.cross_boundary()                                  # the first one ranks
        self.assertEqual(other.state, STATE_COLLECTING)
        # The second saw a different open; its own ranking would arm DDD.
        seq = 10_000
        for token, price in ((4, 1010.0), (1, 1010.0)):
            for when, volume in ((ts(9, 8, 5), 500), (ts(9, 19, 59), 500 + (9000 if token == 4 else 100))):
                seq += 1
                other.on_tick(TickEvent(seq, token, price, when, when, volume, 1, price,
                                        Ohlc(1000.0, 1012.0, 998.0, 990.0)))
        seq += 1
        other.on_tick(TickEvent(seq, 1, 1010.0, ts(9, 20, 1), ts(9, 20, 1), 10**6, 1, 1010.0,
                                Ohlc(1000.0, 1012.0, 998.0, 990.0)))
        self.assertEqual(other.state, STATE_RANKED)
        self.assertEqual([a.tradingsymbol for a in other.arms()], ["AAA", "BBB", "CCC"])
        self.assertEqual(len(h.rows("live_orb_arms")), 3)


class FailureTests(DetectorTestCase):
    def test_no_arms_when_the_ranking_cannot_be_written(self) -> None:
        h = self.h
        h.standard_open()

        def boom(*args, **kwargs):
            raise OrbWriterError("disk full")

        h.writer.save_ranking = boom  # type: ignore[assignment]
        h.cross_boundary()
        self.assertEqual((h.detector.state, h.detector.reason), (STATE_DISABLED, REASON_RANKING_WRITE_FAILED))
        self.assertEqual(h.detector.arms(), [])
        h.tick(1, ts(9, 23, 11), 1012.0, volume=10**6)
        self.assertEqual(h.decisions, [])

    def test_a_trigger_that_cannot_be_written_is_never_announced(self) -> None:
        h = self.h
        h.standard_open()
        h.cross_boundary()
        real = h.writer.insert_decision
        calls = []

        def boom(decision, **kwargs):
            calls.append(decision.setup_id)
            raise OrbWriterError("locked")

        h.writer.insert_decision = boom  # type: ignore[assignment]
        h.tick(1, ts(9, 23, 11), 1012.0, volume=10**6)
        h.tick(1, ts(9, 23, 12), 1012.0, volume=10**6)      # no retry on the next tick
        self.assertEqual(len(calls), 1)
        self.assertEqual(h.decisions, [])
        self.assertEqual(h.detector.arm_state(1), ARM_WRITE_FAILED)
        h.writer.insert_decision = real  # type: ignore[assignment]
        h.tick(3, ts(9, 24, 0), 1012.0, volume=10**6)       # other arms carry on
        self.assertEqual([s for s, _ in h.decisions], ["CCC"])

    def test_a_failing_callback_does_not_lose_the_trigger(self) -> None:
        h = self.h
        h.standard_open()
        h.cross_boundary()
        h.detector._on_decision = lambda arm, decision: 1 / 0  # noqa: SLF001
        h.tick(1, ts(9, 23, 11), 1012.0, volume=10**6)
        self.assertEqual(h.decision_rows()["AAA"]["decision_type"], DECISION_TRIGGERED)
        self.assertEqual(h.detector.metrics.callback_failures, 1)

    def test_a_detector_that_keeps_failing_switches_itself_off(self) -> None:
        h = self.h
        h.detector._handle_tick = lambda tick: 1 / 0  # type: ignore[assignment]  # noqa: SLF001
        with self.assertLogs(orb_detector.logger, level="ERROR"):
            for i in range(orb_detector.MAX_TICK_FAILURES + 5):
                h.tick(1, ts(9, 16, 0) + timedelta(seconds=i), 1000.0, volume=100)
        self.assertEqual((h.detector.state, h.detector.reason), (STATE_DISABLED, REASON_TOO_MANY_FAILURES))
        self.assertEqual(h.detector.metrics.tick_failures, orb_detector.MAX_TICK_FAILURES)

    def test_status_reports_the_day(self) -> None:
        h = self.h
        h.standard_open()
        h.cross_boundary()
        h.tick(1, ts(9, 23, 11), 1012.0, volume=10**6)
        status = h.detector.status()
        self.assertEqual((status["arms"], status["armed"], status["triggered"]), (3, 2, 1))
        self.assertEqual(status["ranking"]["in_play"], 3)
        self.assertEqual(status["ranking"]["valid"], 4)
        self.assertEqual(status["baselines_ready"], 4)


if __name__ == "__main__":
    unittest.main()
