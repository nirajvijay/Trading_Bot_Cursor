"""Open-trade %-from-open columns on the desk (display only)."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path

from api.services import open_trade_peaks as otp
from api.services.open_trade_peaks import DayBars, UsualPeak
from tests.test_execution_api import BASE, ExecutionApiTestCase, _candidate_row

USUAL = UsualPeak(high_3y=1.25, low_3y=1.47, high_2026=1.42, low_2026=1.38)


def _fields(direction: str, **kw):
    args = dict(
        direction=direction,
        state="protected",
        entry_price=None,
        qty=100,
        live_pnl=None,
        live_pnl_source=None,
        bars=DayBars(day_open=100.0, high=101.0, low=99.0, last_close=100.5),
        usual=USUAL,
    )
    args.update(kw)
    return otp.open_trade_fields(**args)


class PercentMathTests(unittest.TestCase):
    def test_pct_from_open(self) -> None:
        self.assertAlmostEqual(otp.pct_from_open(100.5, 100.0), 0.5)
        self.assertAlmostEqual(otp.pct_from_open(99.0, 100.0), -1.0)
        self.assertIsNone(otp.pct_from_open(100.0, None))
        self.assertIsNone(otp.pct_from_open(None, 100.0))
        self.assertIsNone(otp.pct_from_open(100.0, 0.0))

    def test_buy_gap_with_room_left(self) -> None:
        self.assertAlmostEqual(otp.gap_to_usual("UP", 0.30, 1.25), 0.95)

    def test_buy_gap_negative_once_past_the_usual_peak(self) -> None:
        self.assertAlmostEqual(otp.gap_to_usual("UP", 1.60, 1.42), -0.18)

    def test_sell_gap_with_room_left(self) -> None:
        usual = otp.signed_usual_peak("DOWN", USUAL, period="2026")
        self.assertAlmostEqual(usual, -1.38)
        self.assertAlmostEqual(otp.gap_to_usual("DOWN", -0.50, usual), 0.88)

    def test_sell_gap_negative_once_past_the_usual_low(self) -> None:
        self.assertAlmostEqual(otp.gap_to_usual("DOWN", -1.60, -1.38), -0.22)

    def test_signed_usual_peak_by_side_and_period(self) -> None:
        self.assertEqual(otp.signed_usual_peak("UP", USUAL, period="2026"), 1.42)
        self.assertEqual(otp.signed_usual_peak("UP", USUAL, period="3y"), 1.25)
        self.assertEqual(otp.signed_usual_peak("DOWN", USUAL, period="3y"), -1.47)

    def test_now_price_is_backed_out_of_a_ws_mark_only(self) -> None:
        up = otp.now_price_from_mark(
            direction="UP", entry_price=100.0, qty=50, live_pnl=25.0, source="ws"
        )
        self.assertAlmostEqual(up, 100.5)
        down = otp.now_price_from_mark(
            direction="DOWN", entry_price=100.0, qty=50, live_pnl=25.0, source="ws"
        )
        self.assertAlmostEqual(down, 99.5)
        # Kite REST pnl is the stock's whole-day figure: never a price.
        self.assertIsNone(
            otp.now_price_from_mark(
                direction="UP", entry_price=100.0, qty=50, live_pnl=25.0, source="kite_rest"
            )
        )
        self.assertIsNone(
            otp.now_price_from_mark(
                direction="UP", entry_price=100.0, qty=0, live_pnl=25.0, source="ws"
            )
        )


class OpenTradeFieldsTests(unittest.TestCase):
    def test_buy_row(self) -> None:
        # Entry 100.2, live mark +30 on 100 shares -> now 100.5.
        f = _fields("UP", entry_price=100.2, live_pnl=30.0, live_pnl_source="ws")
        self.assertAlmostEqual(f["entry_pct_from_open"], 0.2)
        self.assertAlmostEqual(f["now_pct_from_open"], 0.5)
        self.assertEqual(f["now_price_source"], "ws")
        self.assertAlmostEqual(f["today_peak_pct"], 1.0)  # the 101.0 bar high
        self.assertEqual(f["usual_peak_2026_pct"], 1.42)
        self.assertEqual(f["usual_peak_3y_pct"], 1.25)
        self.assertAlmostEqual(f["gap_to_usual_2026_pct"], 0.92)
        self.assertAlmostEqual(f["gap_to_usual_3y_pct"], 0.75)

    def test_sell_row(self) -> None:
        # Short at 99.8, live mark +30 on 100 shares -> now 99.5.
        f = _fields("DOWN", entry_price=99.8, live_pnl=30.0, live_pnl_source="ws")
        self.assertAlmostEqual(f["entry_pct_from_open"], -0.2)
        self.assertAlmostEqual(f["now_pct_from_open"], -0.5)
        self.assertAlmostEqual(f["today_peak_pct"], -1.0)  # the 99.0 bar low
        self.assertEqual(f["usual_peak_2026_pct"], -1.38)
        self.assertAlmostEqual(f["gap_to_usual_2026_pct"], 0.88)
        self.assertAlmostEqual(f["gap_to_usual_3y_pct"], 0.97)

    def test_peak_counts_the_live_price_beyond_the_last_bar(self) -> None:
        # Now 101.6 is above every completed bar's high (101.0).
        f = _fields("UP", entry_price=100.0, live_pnl=160.0, live_pnl_source="ws")
        self.assertAlmostEqual(f["today_peak_pct"], 1.6)
        self.assertAlmostEqual(f["gap_to_usual_2026_pct"], -0.18)

    def test_rest_mark_falls_back_to_the_last_1m_close(self) -> None:
        f = _fields("UP", entry_price=100.2, live_pnl=999.0, live_pnl_source="kite_rest")
        self.assertEqual(f["now_price_source"], "1m_close")
        self.assertAlmostEqual(f["now_pct_from_open"], 0.5)

    def test_missing_stock_shows_no_usual_peak_or_gap(self) -> None:
        f = _fields("UP", entry_price=100.2, live_pnl=30.0, live_pnl_source="ws", usual=None)
        self.assertAlmostEqual(f["now_pct_from_open"], 0.5)
        for key in (
            "usual_peak_2026_pct",
            "usual_peak_3y_pct",
            "gap_to_usual_2026_pct",
            "gap_to_usual_3y_pct",
        ):
            self.assertIsNone(f[key], key)

    def test_no_0915_bar_means_no_percent_from_open(self) -> None:
        bars = DayBars(day_open=None, high=101.0, low=99.0, last_close=100.5)
        f = _fields("UP", entry_price=100.2, live_pnl=30.0, live_pnl_source="ws", bars=bars)
        for key in (
            "entry_pct_from_open",
            "now_pct_from_open",
            "now_price_source",
            "today_peak_pct",
            "gap_to_usual_2026_pct",
            "gap_to_usual_3y_pct",
        ):
            self.assertIsNone(f[key], key)
        self.assertEqual(f["usual_peak_2026_pct"], 1.42)  # independent of today

    def test_pending_entry_has_no_entry_percent(self) -> None:
        f = _fields("UP", state="pending_entry", entry_price=100.2)
        self.assertIsNone(f["entry_pct_from_open"])
        self.assertEqual(f["now_price_source"], "1m_close")


class UsualPeakTableTests(unittest.TestCase):
    def test_parses_the_real_header(self) -> None:
        text = (
            "stock,marked,days_all,avg_open_to_high_%,median_open_to_high_%,"
            "avg_open_to_low_%,median_open_to_low_%,days_2026,avg_high_2026_%,"
            "median_high_2026_%,avg_low_2026_%,median_low_2026_%\n"
            "ABB,Y,738,1.25,0.87,1.47,1.15,211,1.42,1.05,1.38,1.15\n"
        )
        table = otp.parse_usual_peaks(io.StringIO(text))
        self.assertEqual(table["ABB"], USUAL)

    def test_shipped_table_loads_and_covers_odd_symbols(self) -> None:
        table = otp.load_usual_peaks()
        self.assertGreaterEqual(len(table), 100)
        for symbol in ("M&M", "BAJAJ-AUTO", "HDFCBANK"):
            self.assertIn(symbol, table)
            self.assertIsNotNone(table[symbol].high_2026)
            self.assertGreater(table[symbol].low_3y, 0)  # stored as a magnitude

    def test_missing_file_is_an_empty_table(self) -> None:
        self.assertEqual(otp.load_usual_peaks(Path("/nonexistent/table.csv")), {})


def _make_live_db(path: Path, bars) -> None:
    from live_one_minute_candle_writer import CREATE_LIVE_1M_CANDLES_TABLE_SQL

    conn = sqlite3.connect(path)
    try:
        conn.execute(CREATE_LIVE_1M_CANDLES_TABLE_SQL)
        for symbol, minute, o, h, l, c in bars:
            conn.execute(
                "INSERT INTO live_1m_candles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    1,
                    symbol,
                    f"2026-09-22T{minute}:00+05:30",
                    "2026-09-22",
                    o,
                    h,
                    l,
                    c,
                    100,
                    5,
                    1,
                    "minute_transition",
                    1,
                    0,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        conn.commit()
    finally:
        conn.close()


class ReadDayBarsTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "live.db"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_open_is_the_0915_bar_and_extremes_span_the_day(self) -> None:
        _make_live_db(
            self.db,
            [
                ("AAA", "09:15", 100.0, 100.4, 99.8, 100.2),
                ("AAA", "09:16", 100.2, 101.0, 100.1, 100.9),
                ("AAA", "09:17", 100.9, 100.95, 99.5, 99.7),
                ("BBB", "09:20", 50.0, 51.0, 49.0, 50.5),  # no 09:15 bar
            ],
        )
        bars = otp.read_day_bars(self.db, "2026-09-22", ["AAA", "BBB", "ZZZ"])
        self.assertEqual(bars["AAA"], DayBars(day_open=100.0, high=101.0, low=99.5, last_close=99.7))
        self.assertIsNone(bars["BBB"].day_open)
        self.assertNotIn("ZZZ", bars)

    def test_missing_db_is_empty(self) -> None:
        self.assertEqual(otp.read_day_bars(self.db, "2026-09-22", ["AAA"]), {})

    def test_db_without_the_table_is_empty(self) -> None:
        sqlite3.connect(self.db).close()
        self.assertEqual(otp.read_day_bars(self.db, "2026-09-22", ["AAA"]), {})


class OpenTradePeakApiTests(ExecutionApiTestCase):
    """The new columns ride on /execution/positions open rows."""

    def setUp(self) -> None:
        super().setUp()
        self.live_db = self.dir / "live_1m.db"
        self._saved_live = os.environ.get("LIVE_DB_PATH")
        os.environ["LIVE_DB_PATH"] = str(self.live_db)

    def tearDown(self) -> None:
        if self._saved_live is None:
            os.environ.pop("LIVE_DB_PATH", None)
        else:
            os.environ["LIVE_DB_PATH"] = self._saved_live
        super().tearDown()

    def _seed(self, symbol: str = "AAA", direction: str = "UP") -> None:
        from engine_store import SqlitePositionStore
        from engine_types import ExecutionState, Position, TriggerCandidate

        cand = _candidate_row("s1", symbol)
        cand["direction"] = direction
        store = SqlitePositionStore(self.db)
        try:
            store.save(
                Position(
                    trade_id="s1",
                    candidate=TriggerCandidate(**cand),
                    state=ExecutionState.PROTECTED,
                    qty=100,
                    entry_price=110.0,
                    stop_price=106.95 if direction == "UP" else 113.05,
                    run_id="run-1",
                )
            )
        finally:
            store.close()

    def _marks(self, pnl: float, source: str = "ws") -> None:
        self.marks_file.write_text(
            json.dumps(
                {
                    "as_of": datetime.now(timezone.utc).isoformat(),
                    "pnl": {"s1": pnl},
                    "total_pnl": pnl,
                    "complete": True,
                    "source": {"s1": source},
                    "reason": {},
                    "feed": {"state": "live"},
                }
            ),
            encoding="utf-8",
        )

    def _open_row(self) -> dict:
        body = self.client.get(f"{BASE}/positions?session_date=2026-09-22").json()
        return body["open"][0]

    def test_buy_row_carries_every_new_column(self) -> None:
        self._seed("ABB", "UP")
        _make_live_db(
            self.live_db,
            [
                ("ABB", "09:15", 100.0, 109.0, 99.5, 108.0),
                ("ABB", "09:16", 108.0, 111.0, 107.5, 110.5),
            ],
        )
        self._marks(50.0)  # (110.5 - 110.0) x 100
        row = self._open_row()
        self.assertEqual(row["day_open_price"], 100.0)
        self.assertAlmostEqual(row["entry_pct_from_open"], 10.0)
        self.assertAlmostEqual(row["now_pct_from_open"], 10.5)
        self.assertEqual(row["now_price_source"], "ws")
        self.assertAlmostEqual(row["today_peak_pct"], 11.0)
        table = otp.load_usual_peaks()["ABB"]
        self.assertAlmostEqual(row["usual_peak_2026_pct"], table.high_2026)
        self.assertAlmostEqual(row["usual_peak_3y_pct"], table.high_3y)
        self.assertAlmostEqual(row["gap_to_usual_2026_pct"], table.high_2026 - 10.5, places=4)
        self.assertAlmostEqual(row["gap_to_usual_3y_pct"], table.high_3y - 10.5, places=4)

    def test_sell_row_measures_downward(self) -> None:
        self._seed("ABB", "DOWN")
        _make_live_db(self.live_db, [("ABB", "09:15", 111.0, 111.5, 109.0, 109.5)])
        self._marks(60.0)  # short at 110.0, now 109.4
        row = self._open_row()
        now = (109.4 / 111.0 - 1) * 100
        self.assertAlmostEqual(row["now_pct_from_open"], now, places=4)
        self.assertAlmostEqual(row["today_peak_pct"], (109.0 / 111.0 - 1) * 100, places=4)
        table = otp.load_usual_peaks()["ABB"]
        self.assertAlmostEqual(row["usual_peak_2026_pct"], -table.low_2026)
        self.assertAlmostEqual(row["gap_to_usual_2026_pct"], now + table.low_2026, places=4)

    def test_stock_missing_from_table_and_no_live_db_give_nulls(self) -> None:
        self._seed("AAA", "UP")  # AAA is not in the table; no live DB file
        self._marks(50.0)
        row = self._open_row()
        for key in (
            "day_open_price",
            "entry_pct_from_open",
            "now_pct_from_open",
            "today_peak_pct",
            "usual_peak_2026_pct",
            "usual_peak_3y_pct",
            "gap_to_usual_2026_pct",
            "gap_to_usual_3y_pct",
        ):
            self.assertIsNone(row[key], key)
        self.assertAlmostEqual(row["live_pnl"], 50.0)  # the rest of the row is untouched

    def test_a_broken_peak_read_never_fails_the_endpoint(self) -> None:
        self._seed("ABB", "UP")
        self.live_db.write_text("not a sqlite database", encoding="utf-8")
        response = self.client.get(f"{BASE}/positions?session_date=2026-09-22")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["open"][0]["now_pct_from_open"])


class HardeningTests(unittest.TestCase):
    """Review fixes: nothing non-finite reaches the JSON, the DB read gives up
    fast, and the held-state set stays the engine's."""

    def test_inf_and_nan_table_cells_are_none(self) -> None:
        text = (
            "stock,avg_open_to_high_%,avg_open_to_low_%,avg_high_2026_%,avg_low_2026_%\n"
            "AAA,inf,-inf,nan,1.0\n"
        )
        usual = otp.parse_usual_peaks(io.StringIO(text))["AAA"]
        self.assertEqual(usual, UsualPeak(high_3y=None, low_3y=None, high_2026=None, low_2026=1.0))

    def test_round_drops_non_finite(self) -> None:
        self.assertIsNone(otp._round(float("inf")))
        self.assertIsNone(otp._round(float("nan")))
        self.assertEqual(otp._round(1.234567), 1.2346)

    def test_non_finite_ws_mark_falls_back_to_the_1m_close(self) -> None:
        out = _fields("UP", entry_price=100.2, live_pnl=float("inf"), live_pnl_source="ws")
        self.assertEqual(out["now_price_source"], "1m_close")
        self.assertAlmostEqual(out["now_pct_from_open"], 0.5)
        for value in out.values():
            if isinstance(value, float):
                self.assertTrue(value == value and abs(value) != float("inf"))

    def test_bars_read_sets_a_short_busy_timeout(self) -> None:
        import tempfile
        from unittest import mock

        seen = []

        class Spy:
            def __init__(self, conn):
                self._conn = conn

            def execute(self, sql, *args):
                seen.append(sql.strip())
                return self._conn.execute(sql, *args)

            def close(self):
                self._conn.close()

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "live.db"
            _make_live_db(db, [("AAA", "09:15", 100.0, 100.4, 99.8, 100.2)])
            real = otp.open_readonly
            with mock.patch.object(otp, "open_readonly", lambda path: Spy(real(path))):
                bars = otp.read_day_bars(db, "2026-09-22", ["AAA"])
        self.assertEqual(bars["AAA"].day_open, 100.0)
        self.assertEqual(seen[0], f"PRAGMA busy_timeout={otp._BUSY_TIMEOUT_MS}")
        self.assertLessEqual(otp._BUSY_TIMEOUT_MS, 500)

    def test_held_states_match_the_engine(self) -> None:
        from engine_reconcile import HOLDING_STATES

        self.assertEqual(set(otp._HELD_STATES), {s.value for s in HOLDING_STATES})


class EndpointHardeningTests(OpenTradePeakApiTests):
    """The endpoint that carries Close stays 200 whatever the columns do."""

    def test_inf_live_mark_still_serialises(self) -> None:
        self._seed("ABB", "UP")
        _make_live_db(self.live_db, [("ABB", "09:15", 100.0, 109.0, 99.5, 108.0)])
        self._marks(float("inf"))
        response = self.client.get(f"{BASE}/positions?session_date=2026-09-22")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["open"][0]["now_price_source"], "1m_close")

    def test_a_raising_helper_leaves_the_row_without_columns(self) -> None:
        from unittest import mock

        self._seed("ABB", "UP")
        self._marks(50.0)
        with mock.patch.object(
            otp, "fields_for_open_rows", side_effect=RuntimeError("boom")
        ):
            response = self.client.get(f"{BASE}/positions?session_date=2026-09-22")
        self.assertEqual(response.status_code, 200)
        row = response.json()["open"][0]
        self.assertIsNone(row["now_pct_from_open"])
        self.assertAlmostEqual(row["live_pnl"], 50.0)


if __name__ == "__main__":
    unittest.main()
