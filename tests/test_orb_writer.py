"""ORB persistence: one ranking a day, one outcome per arm."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from orb_rules import make_arm, rank_ranges
from orb_types import (
    DECISION_EXPIRED,
    DECISION_TRIGGERED,
    OpeningRange,
    OrbBaseline,
    OrbDecision,
)
from orb_writer import OrbWriter, OrbWriterError

IST = ZoneInfo("Asia/Kolkata")
DAY = "2026-09-22"
RULE = "orb_v1"
RANKED_AT = datetime(2026, 9, 22, 9, 20, 1, tzinfo=IST)


def a_range(symbol: str, token: int, rvol: float, *, valid: bool = True) -> OpeningRange:
    return OpeningRange(
        instrument_token=token, tradingsymbol=symbol, valid=valid,
        reason=None if valid else "no_direction", or_open=100.0, or_high=102.0, or_low=99.0,
        or_close=101.0, or_volume=int(rvol * 1000), preopen_volume=50, direction="UP",
        level=102.0, atr14=4.0, avg_or_volume=1000.0, rvol=rvol,
        snapshot_at=datetime(2026, 9, 22, 9, 19, 59, tzinfo=IST),
        preopen_at=datetime(2026, 9, 22, 9, 8, 2, tzinfo=IST),
    )


def ranking():
    ranked = rank_ranges([a_range("AAA", 1, 5.0), a_range("BBB", 2, 4.0),
                          a_range("CCC", 3, 2.0), a_range("DDD", 4, 9.0, valid=False)])
    arms = [make_arm(e, session_date=DAY, tick_size=0.05) for e in ranked if e.selected]
    return ranked, arms


class OrbWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "live.db"
        self.writer = OrbWriter(self.db)
        self.addCleanup(self.writer.close)

    def query(self, sql: str, params=()):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def test_creates_only_its_own_tables(self) -> None:
        names = {r["name"] for r in self.query("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(names, {"live_orb_baselines", "live_orb_ranges", "live_orb_arms",
                                 "live_orb_decisions", "live_orb_status"})

    def test_baselines_are_replaced_on_a_rerun(self) -> None:
        ready = OrbBaseline(1, "AAA", True, atr14=4.0, avg_or_volume=1000.0,
                            history_as_of="2026-09-21", sessions=("2026-09-18", "2026-09-21"), min_or_bars=5)
        not_ready = OrbBaseline(2, "BBB", False, reason="stale_history", detail="latest 2026-09-17")
        self.assertEqual(self.writer.save_baselines(DAY, RULE, [ready, not_ready]), 2)
        self.writer.save_baselines(DAY, RULE, [OrbBaseline(1, "AAA", True, atr14=5.0, avg_or_volume=1200.0)])
        rows = {r["tradingsymbol"]: r for r in self.query("SELECT * FROM live_orb_baselines")}
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows["AAA"]["ready"], rows["AAA"]["atr14"]), (1, 5.0))
        self.assertEqual((rows["BBB"]["ready"], rows["BBB"]["reason"]), (0, "stale_history"))
        self.assertEqual(json.loads(rows["BBB"]["sessions_json"]), [])

    def test_ranking_and_arms_are_written_together_once(self) -> None:
        ranked, arms = ranking()
        self.assertTrue(self.writer.save_ranking(DAY, RULE, ranked, arms, ranked_at=RANKED_AT))
        ranges = {r["tradingsymbol"]: r for r in self.query("SELECT * FROM live_orb_ranges")}
        self.assertEqual({s: (r["selected"], r["rank"], r["reason"]) for s, r in ranges.items()}, {
            "AAA": (1, 1, None), "BBB": (1, 2, None),
            "CCC": (0, None, "rvol_below_min"), "DDD": (0, None, "no_direction"),
        })
        self.assertEqual(ranges["AAA"]["ranked_at"], "2026-09-22T09:20:01+05:30")
        self.assertEqual(ranges["AAA"]["snapshot_at"], "2026-09-22T09:19:59+05:30")
        self.assertEqual([r["tradingsymbol"] for r in self.query("SELECT * FROM live_orb_arms ORDER BY rank")],
                         ["AAA", "BBB"])
        # A second ranking for the same day changes nothing.
        other = rank_ranges([a_range("ZZZ", 9, 7.0)])
        other_arms = [make_arm(e, session_date=DAY, tick_size=0.05) for e in other if e.selected]
        self.assertFalse(self.writer.save_ranking(DAY, RULE, other, other_arms, ranked_at=RANKED_AT))
        self.assertEqual(len(self.query("SELECT * FROM live_orb_ranges")), 4)
        self.assertEqual(len(self.query("SELECT * FROM live_orb_arms")), 2)

    def test_a_failed_ranking_leaves_nothing_behind(self) -> None:
        ranked, arms = ranking()
        # An arm row that already exists makes the arm insert fail after the
        # ranges were inserted: the whole ranking must roll back.
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO live_orb_arms VALUES (?, ?, ?, 1, 'AAA', 'UP', 1, 1, 0.05, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 'x', 'x')",
            (arms[0].setup_id, RULE, DAY),
        )
        conn.commit()
        conn.close()
        self.assertFalse(self.writer.save_ranking(DAY, RULE, ranked, arms, ranked_at=RANKED_AT))
        self.assertEqual(self.query("SELECT * FROM live_orb_ranges"), [])

    def test_another_day_is_ranked_separately(self) -> None:
        ranked, arms = ranking()
        self.assertTrue(self.writer.save_ranking(DAY, RULE, ranked, arms, ranked_at=RANKED_AT))
        next_day = [make_arm(e, session_date="2026-09-23", tick_size=0.05) for e in ranked if e.selected]
        self.assertTrue(self.writer.save_ranking("2026-09-23", RULE, ranked, next_day, ranked_at=RANKED_AT))
        self.assertEqual(len(self.writer.load_day("2026-09-23", RULE).arms), 2)

    def test_one_decision_per_arm(self) -> None:
        ranked, arms = ranking()
        self.writer.save_ranking(DAY, RULE, ranked, arms, ranked_at=RANKED_AT)
        first = OrbDecision(arms[0].setup_id, RULE, DECISION_TRIGGERED, tick_sequence=7,
                            exchange_ts=datetime(2026, 9, 22, 9, 23, 11, tzinfo=IST),
                            trigger_price=102.0, planned_stop=100.6, overshoot=0.0, overshoot_atr=0.0)
        self.assertTrue(self.writer.insert_decision(first, payload={"level": 102.0}))
        late = OrbDecision(arms[0].setup_id, RULE, DECISION_EXPIRED, reason="not_triggered_by_cutoff")
        self.assertFalse(self.writer.insert_decision(late))
        rows = self.query("SELECT * FROM live_orb_decisions")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["decision_type"], rows[0]["trigger_exchange_ts"], rows[0]["planned_stop"]),
                         (DECISION_TRIGGERED, "2026-09-22T09:23:11+05:30", 100.6))
        self.assertEqual(json.loads(rows[0]["payload_json"]), {"level": 102.0})
        self.assertTrue(rows[0]["created_at"].endswith("+00:00"))

    def test_unknown_decision_type_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.writer.insert_decision(OrbDecision("x", RULE, "ENTERED"))

    def test_load_day_returns_arms_with_their_outcomes(self) -> None:
        self.assertFalse(self.writer.load_day(DAY, RULE).ranked)
        ranked, arms = ranking()
        self.writer.save_ranking(DAY, RULE, ranked, arms, ranked_at=RANKED_AT)
        self.writer.insert_decision(OrbDecision(arms[1].setup_id, RULE, DECISION_EXPIRED))
        record = self.writer.load_day(DAY, RULE)
        self.assertTrue(record.ranked)
        self.assertEqual(record.arms, arms)
        self.assertEqual(record.decisions, {arms[1].setup_id: DECISION_EXPIRED})
        self.assertFalse(self.writer.load_day("2026-09-23", RULE).ranked)

    def test_a_day_ranked_with_no_arms_still_counts_as_ranked(self) -> None:
        ranked = rank_ranges([a_range("CCC", 3, 2.0)])
        self.assertTrue(self.writer.save_ranking(DAY, RULE, ranked, [], ranked_at=RANKED_AT))
        record = self.writer.load_day(DAY, RULE)
        self.assertTrue(record.ranked)
        self.assertEqual(record.arms, [])

    def test_status_is_one_row_per_day(self) -> None:
        self.writer.save_status(DAY, RULE, "collecting", detail={"tokens": 100})
        self.writer.save_status(DAY, RULE, "ranked", reason=None, detail={"arms": 3})
        rows = self.query("SELECT * FROM live_orb_status")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["state"], json.loads(rows[0]["detail_json"])), ("ranked", {"arms": 3}))

    def test_closed_writer_refuses_writes(self) -> None:
        self.writer.close()
        with self.assertRaises(OrbWriterError):
            self.writer.save_status(DAY, RULE, "collecting")


if __name__ == "__main__":
    unittest.main()
