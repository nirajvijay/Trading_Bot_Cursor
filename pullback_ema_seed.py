"""
Read-only prior-session EMA seed loader from historical candles_5m.

Look-ahead safe: only uses candles from sessions strictly before live session_date.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping, Optional, Sequence

from pullback_indicators import Ema20State

ROOT = Path(__file__).resolve().parent
DEFAULT_HISTORICAL_DB = ROOT / "data" / "local" / "nifty50_historical.db"


@dataclass(frozen=True)
class EmaSeedResult:
    instrument_token: int
    seed_session_date: Optional[str]
    closes: tuple[float, ...]
    bar_count: int


def load_prior_session_closes(
    conn: sqlite3.Connection,
    *,
    instrument_token: int,
    session_date: str,
    limit: int = 40,
) -> EmaSeedResult:
    """Load up to `limit` 5m closes from the latest prior trading session."""
    prior = conn.execute(
        """
        SELECT DISTINCT substr(candle_time, 1, 10) AS d
        FROM candles_5m
        WHERE instrument_token = ?
          AND substr(candle_time, 1, 10) < ?
        ORDER BY d DESC
        LIMIT 1
        """,
        (instrument_token, session_date),
    ).fetchone()
    if prior is None:
        return EmaSeedResult(
            instrument_token=instrument_token,
            seed_session_date=None,
            closes=(),
            bar_count=0,
        )
    seed_date = prior[0]
    rows = conn.execute(
        """
        SELECT close
        FROM candles_5m
        WHERE instrument_token = ?
          AND substr(candle_time, 1, 10) = ?
        ORDER BY candle_time ASC
        """,
        (instrument_token, seed_date),
    ).fetchall()
    closes = tuple(float(r[0]) for r in rows[-limit:])
    return EmaSeedResult(
        instrument_token=instrument_token,
        seed_session_date=seed_date,
        closes=closes,
        bar_count=len(closes),
    )


class PullbackEmaSeedStore:
    """Session-frozen prior-session closes for EMA seeding."""

    def __init__(
        self,
        *,
        session_date: str,
        seeds: Mapping[int, EmaSeedResult],
    ) -> None:
        self._session_date = session_date
        self._seeds = dict(seeds)

    @property
    def session_date(self) -> str:
        return self._session_date

    def get(self, instrument_token: int) -> Optional[EmaSeedResult]:
        return self._seeds.get(instrument_token)

    def apply_to(self, instrument_token: int, ema: Ema20State) -> bool:
        seed = self._seeds.get(instrument_token)
        if seed is None or seed.seed_session_date is None or not seed.closes:
            return False
        ema.seed_from_closes(seed.closes, seed_session_date=seed.seed_session_date)
        return ema.available

    @classmethod
    def load(
        cls,
        session_date: str,
        instrument_tokens: Sequence[int],
        db_path: Path = DEFAULT_HISTORICAL_DB,
        period: int = 20,
    ) -> "PullbackEmaSeedStore":
        if not db_path.exists():
            return cls(session_date=session_date, seeds={})
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            seeds = {}
            for token in instrument_tokens:
                seeds[token] = load_prior_session_closes(
                    conn,
                    instrument_token=token,
                    session_date=session_date,
                    limit=max(period * 2, 40),
                )
            return cls(session_date=session_date, seeds=seeds)
        finally:
            conn.close()
