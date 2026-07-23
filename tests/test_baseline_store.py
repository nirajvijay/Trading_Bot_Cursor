"""Unit tests for immutable BaselineStore (Phase 2)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from baseline_generator import init_baselines_db
from baseline_store import (
    BaselineStore,
    resolve_baseline_as_of_date,
)


def _insert_baseline(
    conn,
    *,
    token: int = 738561,
    symbol: str = "RELIANCE",
    minute_of_day: int = 630,
    as_of: str = "2026-07-22",
    median_volume: float = 5000.0,
    trimmed_mean_volume: float = 4800.0,
    median_abs_return: float = 0.0005,
    valid_session_count: int = 21,
    is_reliable: int = 1,
) -> None:
    conn.execute(
        """
        INSERT INTO baselines (
            instrument_token, tradingsymbol, minute_of_day,
            median_volume, trimmed_mean_volume, median_abs_return,
            valid_session_count, is_reliable, baseline_as_of_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            token,
            symbol,
            minute_of_day,
            median_volume,
            trimmed_mean_volume,
            median_abs_return,
            valid_session_count,
            is_reliable,
            as_of,
        ),
    )
    conn.commit()


class BaselineStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "baselines.db"
        self.conn = init_baselines_db(self.db_path)

    def tearDown(self) -> None:
        self.conn.close()
        self._tmpdir.cleanup()

    def test_resolve_as_of_strictly_prior(self) -> None:
        _insert_baseline(self.conn, as_of="2026-07-21")
        _insert_baseline(self.conn, as_of="2026-07-22", minute_of_day=631)
        self.assertEqual(
            resolve_baseline_as_of_date(self.conn, "2026-07-23"),
            "2026-07-22",
        )
        # Never use as_of D on session D.
        self.assertEqual(
            resolve_baseline_as_of_date(self.conn, "2026-07-22"),
            "2026-07-21",
        )
        self.assertIsNone(resolve_baseline_as_of_date(self.conn, "2026-07-21"))

    def test_load_selects_strictly_prior_snapshot(self) -> None:
        _insert_baseline(
            self.conn,
            as_of="2026-07-21",
            median_volume=1000.0,
            minute_of_day=630,
        )
        _insert_baseline(
            self.conn,
            as_of="2026-07-22",
            median_volume=5000.0,
            minute_of_day=630,
        )
        store = BaselineStore.load("2026-07-23", db_path=self.db_path)
        self.assertEqual(store.baseline_as_of_date, "2026-07-22")
        self.assertEqual(store.session_date, "2026-07-23")
        hit = store.lookup(738561, 630)
        self.assertEqual(hit.status, "hit")
        assert hit.snapshot is not None
        self.assertEqual(hit.snapshot.median_volume, 5000.0)
        self.assertEqual(hit.snapshot.baseline_as_of_date, "2026-07-22")

    def test_load_on_as_of_date_uses_prior_only(self) -> None:
        _insert_baseline(self.conn, as_of="2026-07-21", median_volume=111.0)
        _insert_baseline(
            self.conn, as_of="2026-07-22", median_volume=222.0, minute_of_day=631
        )
        store = BaselineStore.load("2026-07-22", db_path=self.db_path)
        self.assertEqual(store.baseline_as_of_date, "2026-07-21")
        snap = store.get(738561, 630)
        assert snap is not None
        self.assertEqual(snap.median_volume, 111.0)

    def test_miss_vs_unreliable(self) -> None:
        _insert_baseline(
            self.conn,
            as_of="2026-07-22",
            minute_of_day=630,
            is_reliable=1,
        )
        _insert_baseline(
            self.conn,
            as_of="2026-07-22",
            minute_of_day=631,
            is_reliable=0,
            valid_session_count=10,
        )
        store = BaselineStore.load("2026-07-23", db_path=self.db_path)

        hit = store.lookup(738561, 630)
        self.assertEqual(hit.status, "hit")
        self.assertIsNotNone(store.get(738561, 630))

        unreliable = store.lookup(738561, 631)
        self.assertEqual(unreliable.status, "unreliable")
        self.assertIsNone(store.get(738561, 631, require_reliable=True))
        self.assertIsNotNone(store.get(738561, 631, require_reliable=False))

        miss = store.lookup(738561, 999)
        self.assertEqual(miss.status, "miss")
        self.assertIsNone(miss.snapshot)
        self.assertIsNone(store.get(738561, 999))

    def test_empty_when_no_prior_as_of(self) -> None:
        _insert_baseline(self.conn, as_of="2026-07-23")
        store = BaselineStore.load("2026-07-23", db_path=self.db_path)
        self.assertIsNone(store.baseline_as_of_date)
        self.assertEqual(store.size, 0)
        self.assertEqual(store.lookup(738561, 630).status, "miss")

    def test_missing_db_file_yields_empty_store(self) -> None:
        missing = Path(self._tmpdir.name) / "does_not_exist.db"
        store = BaselineStore.load("2026-07-23", db_path=missing)
        self.assertIsNone(store.baseline_as_of_date)
        self.assertEqual(store.size, 0)

    def test_snapshots_are_frozen_mapping(self) -> None:
        _insert_baseline(self.conn, as_of="2026-07-22")
        store = BaselineStore.load("2026-07-23", db_path=self.db_path)
        with self.assertRaises(TypeError):
            store._snapshots[(738561, 630)] = store._snapshots[(738561, 630)]  # type: ignore[index]

    def test_no_reload_api(self) -> None:
        store = BaselineStore.load("2026-07-23", db_path=self.db_path)
        self.assertFalse(hasattr(store, "reload"))
        self.assertFalse(hasattr(BaselineStore, "reload"))

    def test_load_reads_both_volume_baselines(self) -> None:
        _insert_baseline(
            self.conn,
            as_of="2026-07-22",
            median_volume=5000.0,
            trimmed_mean_volume=4200.0,
            median_abs_return=0.001,
        )
        store = BaselineStore.load("2026-07-23", db_path=self.db_path)
        snap = store.get(738561, 630)
        assert snap is not None
        self.assertEqual(snap.median_volume, 5000.0)
        self.assertEqual(snap.trimmed_mean_volume, 4200.0)
        self.assertEqual(snap.median_abs_return, 0.001)
        self.assertTrue(snap.is_reliable)


if __name__ == "__main__":
    unittest.main()
