"""What ARB needs to know about the market when a funnel trigger fires.

Read-only against the observation DB (the same file the trigger handoff
reads). Two facts:

* **Alignment (D8).** Market and sector direction at the last completed
  minute before the trigger, exactly as the research computed it
  (features.py / features2.py): each stock's first 1-minute open of the day
  against its latest close among bars that started before the trigger's
  minute. Market = mean over every stock with a bar (the traded stock
  included); sector = mean over the stock's sector peers, excluding it.
  Both in percent, signed so positive means moving the trade's way.
* **First trigger today (D7).** Whether this is the stock's first
  continuation trigger of the session, read from the DB rather than from the
  engine's own start time, so a restart cannot forget earlier triggers.
  Triggers are ordered by exchange time; a row with no exchange time sorts
  first, which can only make a trigger "not first" (a skip), never an entry.

``alignment`` is pure; ``LiveArbContext`` only adds the SQLite reads.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Tuple

import engine_clock
from engine_arb import FunnelFeatures
from engine_priority import volume_ratio
from engine_types import TriggerCandidate
from trading_engine_broker import parse_timestamp_text

try:
    from config.nifty100_sector_map import SECTOR_MAP_V1
except Exception:  # pragma: no cover - the map ships with the repo
    SECTOR_MAP_V1 = ()


class ArbContextError(Exception):
    """The observation DB could not answer; the trigger is skipped, not guessed."""


def sector_lookup(sector_map: Iterable[Tuple[str, str]] = SECTOR_MAP_V1) -> Dict[str, str]:
    return {symbol: name for name, symbols in sector_map for symbol in symbols.split()}


def trigger_moment_ist(candidate: TriggerCandidate) -> Optional[datetime]:
    """When the trigger fired, in IST: the exchange time, else created_at."""
    for raw in (candidate.trigger_exchange_ts, candidate.created_at):
        parsed = parse_timestamp_text(raw)
        if parsed is None:
            continue
        if parsed.tzinfo is None:
            # Never guess a zone for a decision input.
            continue
        return engine_clock.to_ist(parsed)
    return None


def alignment(
    *,
    first_open: Mapping[str, float],
    last_close: Mapping[str, float],
    symbol: str,
    direction: str,
    sector_of: Mapping[str, str],
) -> Tuple[Optional[float], Optional[float]]:
    """(market %, sector %) signed to the trade, or None where there is no data."""
    returns: Dict[str, float] = {}
    for sym, close in last_close.items():
        start = first_open.get(sym)
        if start is None or start <= 0 or close is None:
            continue
        returns[sym] = float(close) / float(start) - 1.0
    if not returns:
        return None, None
    sign = 1.0 if str(direction).upper() == "UP" else -1.0
    market = sign * (sum(returns.values()) / len(returns)) * 100.0
    sector = sector_of.get(symbol)
    if sector is None:
        return market, None
    peers = [r for sym, r in returns.items() if sym != symbol and sector_of.get(sym) == sector]
    if not peers:
        return market, None
    return market, sign * (sum(peers) / len(peers)) * 100.0


def stop_pct_of(candidate: TriggerCandidate, stop_price: Optional[float]) -> Optional[float]:
    if stop_price is None:
        return None
    price = float(candidate.trigger_price)
    if price <= 0:
        return None
    return abs(price - float(stop_price)) / price * 100.0


_BARS_SQL = """
SELECT tradingsymbol, open, close
FROM live_1m_candles
WHERE session_date = ? AND candle_time < ?
ORDER BY candle_time ASC
"""

_TRIGGERS_SQL = """
SELECT d.setup_id, d.continuation_rule_version
FROM live_continuation_decisions d
JOIN live_continuation_arms a
  ON a.setup_id = d.setup_id
 AND a.continuation_rule_version = d.continuation_rule_version
WHERE d.decision_type = 'TRIGGERED'
  AND a.session_date = ?
  AND a.tradingsymbol = ?
ORDER BY d.trigger_exchange_ts ASC, d.created_at ASC, d.setup_id ASC
LIMIT 1
"""


class LiveArbContext:
    def __init__(
        self,
        live_db: Path,
        *,
        sector_map: Iterable[Tuple[str, str]] = SECTOR_MAP_V1,
    ) -> None:
        self.live_db = Path(live_db)
        self.sector_of = sector_lookup(sector_map)

    def _connect(self) -> sqlite3.Connection:
        if not self.live_db.exists():
            raise ArbContextError("live db missing")
        return sqlite3.connect(f"file:{self.live_db}?mode=ro", uri=True)

    def features_for(
        self, candidate: TriggerCandidate, stop_price: Optional[float]
    ) -> FunnelFeatures:
        moment = trigger_moment_ist(candidate)
        first_open: Dict[str, float] = {}
        last_close: Dict[str, float] = {}
        is_first = False
        try:
            conn = self._connect()
            try:
                if moment is not None:
                    # Same text form as candle_time (2026-09-25T10:12:00+05:30):
                    # bars that started before the trigger's minute.
                    cutoff = moment.strftime("%Y-%m-%dT%H:%M:00+05:30")
                    for sym, open_, close in conn.execute(
                        _BARS_SQL, (candidate.session_date, cutoff)
                    ):
                        first_open.setdefault(str(sym), float(open_))
                        last_close[str(sym)] = float(close)
                row = conn.execute(
                    _TRIGGERS_SQL, (candidate.session_date, candidate.tradingsymbol)
                ).fetchone()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            raise ArbContextError(f"observation db read failed: {exc}") from exc
        if row is not None:
            is_first = (
                str(row[0]) == str(candidate.setup_id)
                and str(row[1]) == str(candidate.continuation_rule_version)
            )
        market, sector = alignment(
            first_open=first_open,
            last_close=last_close,
            symbol=candidate.tradingsymbol,
            direction=candidate.direction,
            sector_of=self.sector_of,
        )
        return FunnelFeatures(
            trigger_time_ist=None if moment is None else moment.time().replace(tzinfo=None),
            volume_ratio=volume_ratio(candidate),
            stop_pct=stop_pct_of(candidate, stop_price),
            is_first_trigger_today=is_first,
            align_market_pct=market,
            align_sector_pct=sector,
        )
