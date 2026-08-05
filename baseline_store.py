"""
Immutable session baseline snapshot for live intraday spike detection.

Load once at session start, freeze, serve lookups only.
Never reloads; never writes the baselines database.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Dict, Literal, Mapping, Optional, Tuple

from spike_types import BaselineSnapshot

ROOT = Path(__file__).resolve().parent
DEFAULT_BASELINES_DB_PATH = ROOT / "data" / "local" / "nifty50_baselines.db"

BaselineLookupStatus = Literal["hit", "miss", "unreliable"]

_LookupKey = Tuple[int, int]


@dataclass(frozen=True)
class BaselineLookupResult:
    status: BaselineLookupStatus
    snapshot: Optional[BaselineSnapshot] = None


def resolve_baseline_as_of_date(
    conn: sqlite3.Connection,
    session_date: str,
) -> Optional[str]:
    """
    Return the latest baseline_as_of_date strictly prior to session_date.

    Enforces the look-ahead contract: as_of D is never used on session D.
    """
    row = conn.execute(
        """
        SELECT MAX(baseline_as_of_date)
        FROM baselines
        WHERE baseline_as_of_date < ?
        """,
        (session_date,),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return str(row[0])


def _row_to_snapshot(row: sqlite3.Row) -> BaselineSnapshot:
    return BaselineSnapshot(
        instrument_token=int(row["instrument_token"]),
        minute_of_day=int(row["minute_of_day"]),
        median_volume=float(row["median_volume"]),
        trimmed_mean_volume=float(row["trimmed_mean_volume"]),
        median_abs_return=float(row["median_abs_return"]),
        valid_session_count=int(row["valid_session_count"]),
        is_reliable=bool(row["is_reliable"]),
        baseline_as_of_date=str(row["baseline_as_of_date"]),
    )


def _load_snapshots(
    conn: sqlite3.Connection,
    baseline_as_of_date: str,
) -> Dict[_LookupKey, BaselineSnapshot]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
            instrument_token,
            minute_of_day,
            median_volume,
            trimmed_mean_volume,
            median_abs_return,
            valid_session_count,
            is_reliable,
            baseline_as_of_date
        FROM baselines
        WHERE baseline_as_of_date = ?
        """,
        (baseline_as_of_date,),
    ).fetchall()
    snapshots: Dict[_LookupKey, BaselineSnapshot] = {}
    for row in rows:
        snapshot = _row_to_snapshot(row)
        snapshots[(snapshot.instrument_token, snapshot.minute_of_day)] = snapshot
    return snapshots


class BaselineStore:
    """
    Frozen in-memory baseline universe for one live session.

    Construct only via BaselineStore.load(...). No reload API.
    """

    def __init__(
        self,
        *,
        session_date: str,
        baseline_as_of_date: Optional[str],
        snapshots: Mapping[_LookupKey, BaselineSnapshot],
    ) -> None:
        self._session_date = session_date
        self._baseline_as_of_date = baseline_as_of_date
        self._snapshots: Mapping[_LookupKey, BaselineSnapshot] = MappingProxyType(
            dict(snapshots)
        )

    @classmethod
    def load(
        cls,
        session_date: str,
        *,
        db_path: Path = DEFAULT_BASELINES_DB_PATH,
    ) -> "BaselineStore":
        """
        Load baselines for the latest as_of strictly prior to session_date.

        If no prior as_of exists, returns an empty frozen store.
        """
        if not db_path.exists():
            return cls(
                session_date=session_date,
                baseline_as_of_date=None,
                snapshots={},
            )

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            as_of = resolve_baseline_as_of_date(conn, session_date)
            if as_of is None:
                return cls(
                    session_date=session_date,
                    baseline_as_of_date=None,
                    snapshots={},
                )
            snapshots = _load_snapshots(conn, as_of)
            return cls(
                session_date=session_date,
                baseline_as_of_date=as_of,
                snapshots=snapshots,
            )
        finally:
            conn.close()

    @property
    def session_date(self) -> str:
        return self._session_date

    @property
    def baseline_as_of_date(self) -> Optional[str]:
        return self._baseline_as_of_date

    @property
    def size(self) -> int:
        return len(self._snapshots)

    def lookup(
        self,
        instrument_token: int,
        minute_of_day: int,
    ) -> BaselineLookupResult:
        snapshot = self._snapshots.get((instrument_token, minute_of_day))
        if snapshot is None:
            return BaselineLookupResult(status="miss")
        if not snapshot.is_reliable:
            return BaselineLookupResult(status="unreliable", snapshot=snapshot)
        return BaselineLookupResult(status="hit", snapshot=snapshot)

    def get(
        self,
        instrument_token: int,
        minute_of_day: int,
        *,
        require_reliable: bool = True,
    ) -> Optional[BaselineSnapshot]:
        """
        Convenience lookup.

        When require_reliable=True (default), only reliable hits are returned.
        Unreliable and missing both yield None — use lookup() to distinguish.
        """
        result = self.lookup(instrument_token, minute_of_day)
        if result.status == "hit":
            return result.snapshot
        if result.status == "unreliable" and not require_reliable:
            return result.snapshot
        return None
