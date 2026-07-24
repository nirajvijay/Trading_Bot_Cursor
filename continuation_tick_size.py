"""
Load and validate per-instrument tick_size from the instruments database.

Fail closed: missing/invalid/non-positive tick_size raises — no 0.05 fallback.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence

from historical_collector import DEFAULT_INSTRUMENTS_DB_PATH


class TickSizePreflightError(Exception):
    """Raised when tick_size cannot be resolved for required instruments."""


@dataclass(frozen=True)
class TickSizeMap:
    by_token: Mapping[int, float]
    by_symbol: Mapping[str, float]

    def get(self, instrument_token: int) -> float:
        try:
            return self.by_token[instrument_token]
        except KeyError as exc:
            raise TickSizePreflightError(
                "tick_size missing for instrument_token=%s" % instrument_token
            ) from exc


def _parse_tick_size(raw: object, *, label: str) -> float:
    if raw is None:
        raise TickSizePreflightError("tick_size missing for %s" % label)
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise TickSizePreflightError(
            "tick_size invalid for %s: %r" % (label, raw)
        ) from exc
    if value <= 0:
        raise TickSizePreflightError(
            "tick_size non-positive for %s: %s" % (label, value)
        )
    return value


def load_tick_size_map(
    instruments_db: Path = DEFAULT_INSTRUMENTS_DB_PATH,
    *,
    required_tokens: Optional[Sequence[int]] = None,
) -> TickSizeMap:
    """
    Load tick_size from nifty50_instruments.instrument_data JSON.

    If required_tokens is provided, every token must resolve or preflight fails.
    """
    if not instruments_db.exists():
        raise TickSizePreflightError(
            "instruments DB not found: %s" % instruments_db
        )

    conn = sqlite3.connect(str(instruments_db))
    try:
        rows = conn.execute(
            "SELECT instrument_token, tradingsymbol, instrument_data "
            "FROM nifty50_instruments"
        ).fetchall()
    finally:
        conn.close()

    by_token: Dict[int, float] = {}
    by_symbol: Dict[str, float] = {}
    for token, symbol, data_json in rows:
        label = "%s(%s)" % (symbol, token)
        try:
            data = json.loads(data_json) if data_json else {}
        except json.JSONDecodeError as exc:
            raise TickSizePreflightError(
                "instrument_data JSON invalid for %s" % label
            ) from exc
        tick_size = _parse_tick_size(data.get("tick_size"), label=label)
        by_token[int(token)] = tick_size
        by_symbol[str(symbol)] = tick_size

    if required_tokens is not None:
        missing = [t for t in required_tokens if t not in by_token]
        if missing:
            raise TickSizePreflightError(
                "tick_size missing for required tokens: %s" % missing
            )

    return TickSizeMap(by_token=by_token, by_symbol=by_symbol)


def preflight_tick_sizes(
    instruments_db: Path,
    required_tokens: Sequence[int],
) -> TickSizeMap:
    """Session-start preflight: fail process if any subscribed token is invalid."""
    if not required_tokens:
        raise TickSizePreflightError("required_tokens is empty")
    return load_tick_size_map(
        instruments_db, required_tokens=list(required_tokens)
    )
