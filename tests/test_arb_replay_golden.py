"""Parity of the live lock rule and the day replay with the ARB backtest.

The fixture (tests/fixtures/lock_golden/lock_golden.json.gz) was written by
Reference/arb_strategy_2026-09/code/export_lock_golden.py from the research
folder. That script re-types the backtest's two exit walks, checks them
against the backtest's stored exits, and then switches on only what a live
engine must do differently (stops on the tick grid, thresholds compared
exactly). It imports nothing from engine_lock, so it is a second opinion.

* Every 2026 candidate (869 funnel triggers, 314 ORB breakouts): the live
  rule, walked over the same 1-minute bars, exits at the same price in the
  same minute.
* Every 2026 day (182): the day replay takes the same trades at the same
  size, and halts in the same minute for the same reason.
"""
from __future__ import annotations

import gzip
import json
import unittest
from collections import defaultdict
from datetime import time
from pathlib import Path

from arb_replay import (
    EXIT_DAY_HALT,
    FREE_BY_CLOSE,
    ReplayBar,
    ReplayCandidate,
    replay_day,
    simulate_exit,
)
from engine_arb import ArbSettings, FunnelFeatures
from engine_lock import LockBar, LockState, advance, fold
from trading_engine_types import STRATEGY_FUNNEL, STRATEGY_ORB

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "lock_golden" / "lock_golden.json.gz"

_DATA = None


def _data():
    global _DATA
    if _DATA is None:
        with gzip.open(FIXTURE, "rt") as fh:
            _DATA = json.load(fh)
    return _DATA


class _Row:
    def __init__(self, row) -> None:
        (self.id, self.src, self.day, self.symbol, self.direction, self.ts, self.entry,
         self.stop, self.tick, self.score, self.volume_ratio, self.stop_pct,
         self.align_market, self.align_sector, self.research_price, self.research_minute,
         self.live_price, self.live_minute, self.live_reason, bars) = row
        self.bars = [ReplayBar(f"{self.day}T{b[0]}", b[1], b[2], b[3], b[4]) for b in bars]
        self.strategy = STRATEGY_ORB if self.src == "ORB" else STRATEGY_FUNNEL
        self.minute = self.ts[:16]

    def exit(self):
        return simulate_exit(
            strategy=self.strategy,
            direction=self.direction,
            entry=self.entry,
            stop=self.stop,
            tick_size=self.tick,
            bars=self.bars,
            entry_minute=self.minute,
        )


def _rows():
    return [_Row(row) for row in _data()["candidates"]]


class LockExitParityTests(unittest.TestCase):
    def test_fixture_is_the_whole_year(self) -> None:
        summary = _data()["summary"]
        self.assertEqual(summary["funnel"], 869)
        self.assertEqual(summary["orb"], 314)
        self.assertEqual(summary["days"], 182)
        self.assertEqual(len(_data()["candidates"]), 1183)

    def test_every_candidate_exits_like_the_backtest(self) -> None:
        mismatches = []
        for row in _rows():
            result = row.exit()
            got = None if result is None else (result.price, result.minute, result.reason)
            want = (row.live_price, row.live_minute, row.live_reason)
            if got != want:
                mismatches.append((row.id, got, want))
        self.assertEqual(mismatches, [])

    def test_where_the_grid_changes_nothing_the_exit_is_the_backtests_own(self) -> None:
        """Most trades are untouched by the grid and the exact thresholds; for
        those the live rule must give the backtest's stored exit itself."""
        same = [
            row for row in _rows()
            if (row.live_price, row.live_minute) == (row.research_price, row.research_minute)
        ]
        self.assertEqual(len(same), _data()["summary"]["live_exit_equals_research"])
        self.assertGreater(len(same), 800)
        for row in same:
            result = row.exit()
            self.assertEqual((result.price, result.minute), (row.research_price, row.research_minute), row.id)

    def test_folding_in_pieces_gives_the_same_stop_as_folding_at_once(self) -> None:
        """What makes a restart safe: the state after N bars does not depend on
        how the bars were fed."""
        checked = 0
        for row in _rows()[::7]:
            bars = [LockBar(b.minute, b.high, b.low, b.close) for b in row.bars]
            kwargs = dict(
                direction=row.direction, entry=row.entry, initial_stop=row.stop,
                tick_size=row.tick, entry_minute=row.minute,
            )
            whole = fold(bars, **kwargs)
            pieces = LockState()
            for start in range(0, len(bars), 3):
                # Overlapping slices: bars already folded must be ignored.
                pieces = fold(bars[max(0, start - 1): start + 3], state=pieces, **kwargs)
            self.assertEqual(pieces, whole, row.id)
            checked += 1
        self.assertGreater(checked, 150)

    def test_a_lock_stop_is_always_on_the_grid_and_beyond_entry_or_capped(self) -> None:
        for row in _rows()[::5]:
            state = LockState()
            for bar in row.bars:
                state = advance(
                    state, LockBar(bar.minute, bar.high, bar.low, bar.close),
                    direction=row.direction, entry=row.entry, initial_stop=row.stop,
                    tick_size=row.tick, entry_bar=bar.minute == row.minute,
                )
                if state.target is None:
                    continue
                ticks = state.target / row.tick
                self.assertAlmostEqual(ticks, round(ticks), places=6, msg=row.id)
                # Never looser than the initial stop.
                if row.direction == "UP":
                    self.assertGreater(state.target, row.stop, row.id)
                else:
                    self.assertLess(state.target, row.stop, row.id)


def _features(row: _Row) -> FunnelFeatures:
    hh, mm = int(row.ts[11:13]), int(row.ts[14:16])
    return FunnelFeatures(
        trigger_time_ist=time(hh, mm),
        volume_ratio=row.volume_ratio,
        stop_pct=row.stop_pct,
        is_first_trigger_today=True,
        align_market_pct=row.align_market,
        align_sector_pct=row.align_sector,
    )


class DayReplayParityTests(unittest.TestCase):
    def test_every_day_takes_the_same_trades_and_halts_the_same(self) -> None:
        by_day = defaultdict(list)
        for row in _rows():
            by_day[row.day].append(row)
        expected_days = _data()["days"]
        self.assertEqual(len(expected_days), 182)
        settings = ArbSettings()
        problems = []
        trades = halts = 0
        for day, rows in sorted(by_day.items()):
            bars = defaultdict(dict)
            candidates = []
            # The backtest's candidate list is ORB first, then funnel, and its
            # sort by time is stable; ties keep that order.
            for row in sorted(rows, key=lambda r: r.strategy != STRATEGY_ORB):
                for bar in row.bars:
                    bars[row.symbol][bar.minute] = bar
                funnel = row.strategy == STRATEGY_FUNNEL
                candidates.append(
                    ReplayCandidate(
                        setup_id=row.id,
                        strategy=row.strategy,
                        symbol=row.symbol,
                        direction=row.direction,
                        minute=row.minute,
                        moment=row.ts,
                        trigger_price=row.entry,
                        stop=row.stop,
                        tick_size=row.tick,
                        features=_features(row) if funnel else None,
                    )
                )
            bars_by_symbol = {s: [m[k] for k in sorted(m)] for s, m in bars.items()}
            replay = replay_day(
                candidates, bars_by_symbol, settings,
                capital_rupees=500_000.0, slip_rate=3e-4, free_rule=FREE_BY_CLOSE,
            )
            want = expected_days.get(day, {"taken": [], "halt": None})
            got = {
                t.candidate.setup_id: (t.qty, t.risk_used, t.exit_price, t.exit_minute, t.net,
                                       t.exit_reason == EXIT_DAY_HALT)
                for t in replay.trades if t.entered
            }
            if set(got) != {w[0] for w in want["taken"]}:
                problems.append((day, "taken set", sorted(set(got) ^ {w[0] for w in want["taken"]})))
                continue
            for cid, qty, risk, price, minute, net, flat in want["taken"]:
                g = got[cid]
                trades += 1
                if (g[0], g[3], g[5]) != (qty, minute, flat) or abs(g[1] - risk) > 1e-6 \
                        or abs(g[2] - price) > 1e-9 or abs(g[4] - net) > 1e-6:
                    problems.append((day, cid, g, (qty, risk, price, minute, net, flat)))
            want_halt = None if want["halt"] is None else tuple(want["halt"])
            got_halt = None if replay.halt_minute is None else (replay.halt_minute, replay.halt_reason)
            # The backtest only records a halt that flattened something.
            if want_halt is not None:
                halts += 1
                if got_halt != want_halt:
                    problems.append((day, "halt", got_halt, want_halt))
        self.assertEqual(problems, [])
        self.assertEqual(trades, _data()["summary"]["trades_after_day_controls"])
        self.assertEqual(halts, _data()["summary"]["halts"])


if __name__ == "__main__":
    unittest.main()
