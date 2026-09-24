"""Desk totals and the per-row Stock Day Total."""

from __future__ import annotations

import unittest
from typing import Optional

from api.services.desk_pnl import DeskRow, build_desk_pnl


def row(
    trade_id: str,
    *,
    symbol: str = "RELIANCE",
    state: str = "closed",
    realised: Optional[float] = None,
    at: str = "2026-09-22T10:00:00+00:00",
    kite_day: Optional[float] = None,
    mismatch: bool = False,
    ours: Optional[float] = None,
) -> DeskRow:
    extra = {}
    if kite_day is not None:
        extra["stock_day"] = {
            "kite_pnl": kite_day,
            "ours": ours if ours is not None else kite_day,
            "diff": kite_day - (ours if ours is not None else kite_day),
            "mismatch": mismatch,
        }
    return DeskRow(
        trade_id=trade_id,
        tradingsymbol=symbol,
        state=state,
        realised_pnl=realised,
        created_at=at,
        extra=extra,
    )


class StockDayTotalTests(unittest.TestCase):
    def test_the_reliance_example(self) -> None:
        first = row("r1", realised=500.0, kite_day=500.0, at="2026-09-22T09:00:00+00:00")

        # #2 open at +200: 700, moving live.
        second_open = row("r2", state="protected", at="2026-09-22T10:00:00+00:00")
        desk = build_desk_pnl([first, second_open], {"r2": 200.0})
        self.assertEqual(desk.rows["r1"].stock_day_total, 500.0)
        self.assertEqual(desk.rows["r2"].stock_day_total, 700.0)
        desk = build_desk_pnl([first, second_open], {"r2": 260.0})
        self.assertEqual(desk.rows["r2"].stock_day_total, 760.0)

        # #2 closes at +300: 800, fixed; #1 still shows 500.
        second_closed = row(
            "r2", realised=300.0, kite_day=800.0, at="2026-09-22T10:00:00+00:00"
        )
        desk = build_desk_pnl([first, second_closed], {})
        self.assertEqual(desk.rows["r1"].stock_day_total, 500.0)
        self.assertEqual(desk.rows["r2"].stock_day_total, 800.0)

    def test_rows_are_never_merged_across_the_same_stock(self) -> None:
        desk = build_desk_pnl(
            [
                row("a", realised=100.0, at="2026-09-22T09:00:00+00:00"),
                row("b", realised=-40.0, at="2026-09-22T09:30:00+00:00"),
                row("c", realised=10.0, at="2026-09-22T10:00:00+00:00"),
            ],
            {},
        )
        self.assertEqual(
            [desk.rows[t].stock_day_total for t in ("a", "b", "c")], [100.0, 60.0, 70.0]
        )

    def test_order_is_by_entry_time_not_input_order(self) -> None:
        desk = build_desk_pnl(
            [
                row("late", realised=10.0, at="2026-09-22T11:00:00+00:00"),
                row("early", realised=100.0, at="2026-09-22T09:00:00+00:00"),
            ],
            {},
        )
        self.assertEqual(desk.rows["early"].stock_day_total, 100.0)
        self.assertEqual(desk.rows["late"].stock_day_total, 110.0)

    def test_kites_pinned_figure_is_what_is_shown_and_built_on(self) -> None:
        # Our trades sum to 650, Kite says 800 (e.g. a trade outside the engine).
        desk = build_desk_pnl(
            [
                row("a", realised=650.0, kite_day=800.0, ours=650.0, mismatch=True, at="2026-09-22T09:00:00+00:00"),
                row("b", state="protected", at="2026-09-22T10:00:00+00:00"),
            ],
            {"b": 50.0},
        )
        self.assertEqual(desk.rows["a"].stock_day_total, 800.0)
        self.assertEqual(desk.rows["b"].stock_day_total, 850.0)
        self.assertEqual(
            desk.rows["a"].mismatch, {"kite_pnl": 800.0, "ours": 650.0, "diff": 150.0}
        )
        self.assertIsNone(desk.rows["b"].mismatch)

    def test_a_closed_row_shows_kites_realised(self) -> None:
        # 2026-09-24, as on the desk: DRREDDY booked -23.4 for its own 6
        # shares, but its stops sold 381 more that were bought back by hand.
        desk = build_desk_pnl(
            [
                row("hy", symbol="HYUNDAI", realised=14.0, kite_day=14.0),
                row("ci", symbol="CIPLA", realised=-19.8, kite_day=-19.8),
                row("dm", symbol="DMART", realised=-24.6, kite_day=-24.6),
                row("lo", symbol="LODHA", realised=6.1, kite_day=6.1),
                row("dr", symbol="DRREDDY", realised=-23.4, kite_day=-133.5, ours=-23.4, mismatch=True),
            ],
            {},
        )
        self.assertEqual(desk.rows["dr"].realised, -133.5)
        self.assertEqual(desk.rows["hy"].realised, 14.0)
        rows_total = round(sum(r.realised for r in desk.rows.values()), 2)
        self.assertEqual(rows_total, desk.total_realised)
        self.assertEqual(desk.total_realised, -157.8)

    def test_a_later_trade_shows_only_its_own_share_of_kites_figure(self) -> None:
        # Kite pins 500 after #1, then 800 after #2: #2 made 300.
        desk = build_desk_pnl(
            [
                row("a", realised=500.0, kite_day=500.0, at="2026-09-22T09:00:00+00:00"),
                row("b", realised=280.0, kite_day=800.0, ours=780.0, mismatch=True, at="2026-09-22T10:00:00+00:00"),
            ],
            {},
        )
        self.assertEqual(desk.rows["a"].realised, 500.0)
        self.assertEqual(desk.rows["b"].realised, 300.0)

    def test_without_kites_figure_the_row_keeps_our_number(self) -> None:
        desk = build_desk_pnl([row("a", realised=-12.5)], {})
        self.assertEqual(desk.rows["a"].realised, -12.5)

    def test_other_stocks_are_separate(self) -> None:
        desk = build_desk_pnl(
            [row("r", realised=500.0), row("t", symbol="TCS", realised=-100.0)], {}
        )
        self.assertEqual(desk.rows["r"].stock_day_total, 500.0)
        self.assertEqual(desk.rows["t"].stock_day_total, -100.0)

    def test_an_unattributed_close_is_flagged_not_counted_as_zero_silently(self) -> None:
        desk = build_desk_pnl([row("a", realised=None)], {})
        self.assertTrue(desk.rows["a"].unattributed)
        self.assertIsNone(desk.rows["a"].stock_day_total)

    def test_an_unattributed_close_with_kites_figure_shows_kites_figure(self) -> None:
        desk = build_desk_pnl([row("a", realised=None, kite_day=-240.0, ours=0.0, mismatch=True)], {})
        self.assertTrue(desk.rows["a"].unattributed)
        self.assertEqual(desk.rows["a"].stock_day_total, -240.0)
        self.assertEqual(desk.total_realised, -240.0)

    def test_a_trade_not_yet_filled_has_no_numbers(self) -> None:
        desk = build_desk_pnl([row("a", state="entry_submitted")], {"a": 12.0})
        self.assertIsNone(desk.rows["a"].stock_day_total)
        self.assertEqual(desk.total_ongoing, 0.0)


class TotalsTests(unittest.TestCase):
    def test_day_is_realised_plus_ongoing(self) -> None:
        desk = build_desk_pnl(
            [
                row("r1", realised=500.0, kite_day=500.0, at="2026-09-22T09:00:00+00:00"),
                row("r2", state="protected", at="2026-09-22T10:00:00+00:00"),
                row("t1", symbol="TCS", realised=-120.0),
                row("i1", symbol="INFY", state="trailing"),
            ],
            {"r2": 200.0, "i1": -30.0},
        )
        self.assertEqual(desk.total_realised, 380.0)
        self.assertEqual(desk.total_ongoing, 170.0)
        self.assertEqual(desk.total_day, 550.0)
        self.assertTrue(desk.ongoing_complete)

    def test_realised_uses_kites_figure_for_a_flat_stock(self) -> None:
        desk = build_desk_pnl(
            [
                row("a", realised=300.0, at="2026-09-22T09:00:00+00:00"),
                row("b", realised=350.0, kite_day=800.0, ours=650.0, mismatch=True, at="2026-09-22T10:00:00+00:00"),
            ],
            {},
        )
        self.assertEqual(desk.total_realised, 800.0)

    def test_skipped_and_rejected_rows_count_for_nothing(self) -> None:
        desk = build_desk_pnl(
            [row("s", state="skipped", realised=999.0), row("x", state="rejected")], {}
        )
        self.assertEqual((desk.total_realised, desk.total_ongoing), (0.0, 0.0))
        self.assertEqual(desk.rows, {})

    def test_a_held_trade_without_a_mark_makes_ongoing_incomplete(self) -> None:
        desk = build_desk_pnl([row("a", state="protected")], {})
        self.assertFalse(desk.ongoing_complete)


if __name__ == "__main__":
    unittest.main()
