"""Charges and net P&L per closed trade, for the Charges tab. Display only.

Nothing here feeds the engine: the daily loss cap keeps using Kite's gross day
P&L. The engine database is opened read-only; what this module saves goes in
its own file (api.config.trade_charges_db_path).

How Kite prices charges (verified read-only on live Kite, 2026-09-25):

* ``get_virtual_contract_note`` is a pure calculator. It ignores order_id and
  prices exactly the rows it is sent.
* It nets the rows within ONE call. A balanced BUY+SELL pair is priced as
  intraday; a lone leg is priced as delivery (0.1% STT, stamp duty, no
  brokerage) -- about 3x too much for an MIS trade.

So a trade's entry and all of its exits always go into the same call, with
bought quantity equal to sold quantity. Exits beyond the entry quantity are
capped, the same rule the engine's own realised P&L uses. Anything that cannot
be paired exactly is reported unavailable, never guessed, and never saved.

Kite's order book only holds today's orders and our store does not keep each
exit order's own fill price, so charges can only be worked out on the day.
Once worked out they are saved and never recomputed.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

try:  # guarded: the tests run without the SDK installed in some envs
    from kiteconnect.exceptions import TokenException as _KiteTokenException
except Exception:  # noqa: BLE001
    class _KiteTokenException(Exception):  # type: ignore[no-redef]
        pass


# Order statuses after which Kite will not change the fill.
FINAL_STATUSES = frozenset({"COMPLETE", "CANCELLED", "REJECTED"})

# Kite has not documented a row limit for this endpoint; a whole day (21 rows)
# was verified in one call. Chunk well above any realistic day, never
# splitting a trade across calls.
MAX_LEGS_PER_CALL = 50

STATUS_OK = "ok"
STATUS_PAPER = "paper"
STATUS_UNAVAILABLE = "unavailable"

# Reasons shown on an unavailable row.
REASON_NOT_CAPTURED = "not_captured"  # a past day that was never priced
REASON_ENTRY_MISSING = "entry_order_not_in_book"
REASON_EXIT_MISSING = "exit_order_not_in_book"
REASON_NO_EXITS = "no_exit_orders"
REASON_NOT_FINAL = "order_not_final"
REASON_FILL_UNKNOWN = "fill_unknown"
REASON_LEG_MISMATCH = "exit_does_not_match_entry"
REASON_UNCOVERED = "exits_do_not_cover_entry"
REASON_SESSION_EXPIRED = "kite_session_expired"
REASON_KITE_UNAVAILABLE = "kite_unavailable"
REASON_BAD_RESPONSE = "kite_response_mismatch"

# Charge components, in display order: our key -> Kite's key in `charges`.
COMPONENTS: Tuple[Tuple[str, str], ...] = (
    ("brokerage", "brokerage"),
    ("stt", "transaction_tax"),
    ("exchange", "exchange_turnover_charge"),
    ("sebi", "sebi_turnover_charge"),
    ("stamp_duty", "stamp_duty"),
    ("gst", "gst"),  # a dict; its "total" is used
)


# ----------------------------------------------------------------------
# Legs: which Kite orders make up one trade, and in what quantities
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class Leg:
    order_id: str
    tradingsymbol: str
    exchange: str
    transaction_type: str
    variety: str
    product: str
    order_type: str
    quantity: int
    average_price: float

    def as_param(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "exchange": self.exchange,
            "tradingsymbol": self.tradingsymbol,
            "transaction_type": self.transaction_type,
            "variety": self.variety,
            "product": self.product,
            "order_type": self.order_type,
            "quantity": self.quantity,
            "average_price": self.average_price,
        }


@dataclass(frozen=True)
class LegPlan:
    """Either a balanced set of legs, or the reason there is none."""

    legs: Tuple[Leg, ...] = ()
    reason: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.reason is None and bool(self.legs)


def _filled(order: Mapping[str, Any]) -> int:
    try:
        return int(order.get("filled_quantity") or 0)
    except (TypeError, ValueError):
        return 0


def _price(order: Mapping[str, Any]) -> Optional[float]:
    try:
        value = float(order.get("average_price") or 0.0)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def _leg(order: Mapping[str, Any], quantity: int) -> Leg:
    return Leg(
        order_id=str(order.get("order_id")),
        tradingsymbol=str(order.get("tradingsymbol")),
        exchange=str(order.get("exchange") or "NSE"),
        transaction_type=str(order.get("transaction_type")),
        variety=str(order.get("variety") or "regular"),
        product=str(order.get("product") or "MIS"),
        # Whatever Kite booked (a protected MARKET order reads as LIMIT);
        # verified to price identically either way.
        order_type=str(order.get("order_type") or "MARKET"),
        quantity=int(quantity),
        average_price=float(_price(order) or 0.0),
    )


def build_legs(
    entry_order_id: Optional[str],
    exit_order_ids: Sequence[str],
    book: Mapping[str, Mapping[str, Any]],
) -> LegPlan:
    """The trade's entry plus its exits, capped so bought == sold.

    ``book`` is Kite's order book for the day, keyed by order_id. Exits are
    taken oldest first, as the engine's realised P&L does.
    """
    entry = book.get(str(entry_order_id)) if entry_order_id else None
    if entry is None:
        return LegPlan(reason=REASON_ENTRY_MISSING)
    if not exit_order_ids:
        return LegPlan(reason=REASON_NO_EXITS)

    exits: List[Mapping[str, Any]] = []
    for oid in dict.fromkeys(str(o) for o in exit_order_ids):  # de-duplicated, in order
        order = book.get(oid)
        if order is None:
            return LegPlan(reason=REASON_EXIT_MISSING)
        exits.append(order)

    for order in (entry, *exits):
        if str(order.get("status")) not in FINAL_STATUSES:
            return LegPlan(reason=REASON_NOT_FINAL)

    entry_qty = _filled(entry)
    if entry_qty <= 0 or _price(entry) is None:
        return LegPlan(reason=REASON_FILL_UNKNOWN)

    side = str(entry.get("transaction_type"))
    symbol = str(entry.get("tradingsymbol"))
    for order in exits:
        if (
            str(order.get("tradingsymbol")) != symbol
            or str(order.get("transaction_type")) == side
            or str(order.get("product")) != str(entry.get("product"))
        ):
            return LegPlan(reason=REASON_LEG_MISMATCH)

    def placed(indexed: Tuple[int, Mapping[str, Any]]) -> Tuple[str, int]:
        index, order = indexed
        return (str(order.get("order_timestamp") or ""), index)

    remaining = entry_qty
    exit_legs: List[Leg] = []
    for _, order in sorted(enumerate(exits), key=placed):
        if remaining <= 0:
            break  # the rest is over-exit: not part of this trade's round trip
        qty = _filled(order)
        if qty <= 0:
            continue
        if _price(order) is None:
            return LegPlan(reason=REASON_FILL_UNKNOWN)
        take = min(qty, remaining)
        exit_legs.append(_leg(order, take))
        remaining -= take

    if remaining != 0 or not exit_legs:
        # Pricing an unmatched remainder would bill it as delivery.
        return LegPlan(reason=REASON_UNCOVERED)
    return LegPlan(legs=(_leg(entry, entry_qty), *exit_legs))


# ----------------------------------------------------------------------
# Pricing: one or more contract-note calls, each holding whole trades
# ----------------------------------------------------------------------


def _number(value: Any) -> Optional[float]:
    if isinstance(value, dict):
        value = value.get("total")
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def breakdown_of(rows: Iterable[Mapping[str, Any]]) -> Optional[Dict[str, float]]:
    """Sum each charge component across a trade's legs. None if any is unreadable."""
    totals = {key: 0.0 for key, _ in COMPONENTS}
    totals["total"] = 0.0
    for row in rows:
        charges = row.get("charges")
        if not isinstance(charges, Mapping):
            return None
        for key, kite_key in (*COMPONENTS, ("total", "total")):
            number = _number(charges.get(kite_key, 0.0))
            if number is None:
                return None
            totals[key] += number
    if totals["total"] < 0:
        return None
    return totals


def _chunks(plans: Sequence[Tuple[str, LegPlan]]) -> List[List[Tuple[str, LegPlan]]]:
    out: List[List[Tuple[str, LegPlan]]] = []
    current: List[Tuple[str, LegPlan]] = []
    size = 0
    for trade_id, plan in plans:
        n = len(plan.legs)
        if current and size + n > MAX_LEGS_PER_CALL:
            out.append(current)
            current, size = [], 0
        current.append((trade_id, plan))
        size += n
    if current:
        out.append(current)
    return out


def _row_matches(row: Any, leg: Leg) -> bool:
    if not isinstance(row, Mapping):
        return False
    try:
        return (
            str(row.get("tradingsymbol")) == leg.tradingsymbol
            and str(row.get("transaction_type")) == leg.transaction_type
            and int(row.get("quantity")) == leg.quantity
        )
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class Priced:
    charges: Optional[Dict[str, float]] = None
    reason: Optional[str] = None


def price_plans(
    kite: Any, plans: Sequence[Tuple[str, LegPlan]]
) -> Dict[str, Priced]:
    """Price balanced plans. A TokenException propagates; any other failure
    marks only its own chunk unavailable."""
    out: Dict[str, Priced] = {}
    for chunk in _chunks(plans):
        legs = [leg for _, plan in chunk for leg in plan.legs]
        try:
            rows = kite.get_virtual_contract_note([leg.as_param() for leg in legs])
        except _KiteTokenException:
            raise
        except Exception as exc:  # noqa: BLE001 - one bad call must not blank the tab
            logger.warning("Contract note call failed: %s", exc)
            for trade_id, _ in chunk:
                out[trade_id] = Priced(reason=REASON_KITE_UNAVAILABLE)
            continue
        # Rows come back one per leg, in the order sent. Checked, not assumed:
        # a shifted row would charge one trade for another's order.
        if (
            not isinstance(rows, list)
            or len(rows) != len(legs)
            or not all(_row_matches(row, leg) for row, leg in zip(rows, legs))
        ):
            for trade_id, _ in chunk:
                out[trade_id] = Priced(reason=REASON_BAD_RESPONSE)
            continue
        at = 0
        for trade_id, plan in chunk:
            mine = rows[at : at + len(plan.legs)]
            at += len(plan.legs)
            charges = breakdown_of(mine)
            out[trade_id] = (
                Priced(charges=charges) if charges is not None else Priced(reason=REASON_BAD_RESPONSE)
            )
    return out


# ----------------------------------------------------------------------
# Cache: write once per trade, in the tab's own database
# ----------------------------------------------------------------------

CREATE_CACHE_SQL = """
CREATE TABLE IF NOT EXISTS trade_charges (
    trade_id      TEXT PRIMARY KEY,
    session_date  TEXT NOT NULL,
    legs_json     TEXT NOT NULL,
    charges_json  TEXT NOT NULL,
    total         REAL NOT NULL,
    fetched_at    TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class CachedCharges:
    legs: Tuple[Dict[str, Any], ...]
    charges: Dict[str, float]
    fetched_at: str


class ChargesCache:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        with self._conn:
            self._conn.execute(CREATE_CACHE_SQL)

    def close(self) -> None:
        self._conn.close()

    def get_many(self, trade_ids: Sequence[str]) -> Dict[str, CachedCharges]:
        if not trade_ids:
            return {}
        marks = ",".join("?" for _ in trade_ids)
        rows = self._conn.execute(
            f"SELECT trade_id, legs_json, charges_json, fetched_at FROM trade_charges "
            f"WHERE trade_id IN ({marks})",
            tuple(trade_ids),
        )
        out: Dict[str, CachedCharges] = {}
        for trade_id, legs_json, charges_json, fetched_at in rows:
            try:
                out[str(trade_id)] = CachedCharges(
                    legs=tuple(json.loads(legs_json)),
                    charges={k: float(v) for k, v in json.loads(charges_json).items()},
                    fetched_at=str(fetched_at),
                )
            except (TypeError, ValueError):
                continue  # unreadable row: treated as not saved
        return out

    def put(self, trade_id: str, session_date: str, legs: Sequence[Leg], charges: Dict[str, float]) -> None:
        """Write once. A row already there is never overwritten."""
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO trade_charges "
                "(trade_id, session_date, legs_json, charges_json, total, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    trade_id,
                    session_date,
                    json.dumps([leg.as_param() for leg in legs]),
                    json.dumps(charges),
                    float(charges["total"]),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )


# ----------------------------------------------------------------------
# Reading the engine's closed trades, read-only
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class ClosedTrade:
    trade_id: str
    tradingsymbol: str
    direction: str
    qty: int
    realised_pnl: Optional[float]
    is_live: bool
    entry_order_id: Optional[str]
    exit_order_ids: Tuple[str, ...]
    close_reason: Optional[str]
    closed_at: Optional[str]


def _exit_ids(extra: Mapping[str, Any], exit_order_id: Optional[str]) -> Tuple[str, ...]:
    """What the engine recorded as this trade's exits: every order it booked
    P&L against, else the one closing order."""
    ids = extra.get("exit_order_ids")
    if isinstance(ids, list) and ids:
        return tuple(str(i) for i in ids if i)
    closing = extra.get("closing_order_id") or exit_order_id
    return (str(closing),) if closing else ()


def read_closed_trades(engine_db: Path, session_date: str) -> List[ClosedTrade]:
    if not engine_db.exists():
        return []
    # mode=ro: this tab can never write to the engine's database.
    conn = sqlite3.connect(f"{engine_db.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        # closed_at is the engine's own "closed" event, not updated_at: a
        # closed row can still be saved again afterwards (e.g. the Kite day
        # figure pinned on it).
        rows = conn.execute(
            "SELECT p.trade_id, p.tradingsymbol, p.direction, p.qty, p.realised_pnl, "
            "p.is_live, p.entry_order_id, p.exit_order_id, p.extra_json, "
            "(SELECT MAX(e.at) FROM position_events e "
            " WHERE e.trade_id = p.trade_id AND e.event_type = 'closed') AS closed_at "
            "FROM positions p WHERE p.session_date = ? AND p.state = 'closed' "
            "ORDER BY p.created_at ASC",
            (session_date,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []  # no positions table yet
    finally:
        conn.close()
    out: List[ClosedTrade] = []
    for row in rows:
        try:
            extra = json.loads(str(row["extra_json"] or "{}"))
        except (TypeError, ValueError):
            extra = {}
        if not isinstance(extra, dict):
            extra = {}
        out.append(
            ClosedTrade(
                trade_id=str(row["trade_id"]),
                tradingsymbol=str(row["tradingsymbol"]),
                direction=str(row["direction"]),
                qty=int(row["qty"] or 0),
                realised_pnl=None if row["realised_pnl"] is None else float(row["realised_pnl"]),
                is_live=bool(row["is_live"]),
                entry_order_id=str(row["entry_order_id"]) if row["entry_order_id"] else None,
                exit_order_ids=_exit_ids(extra, row["exit_order_id"]),
                close_reason=extra.get("close_reason"),
                closed_at=str(row["closed_at"]) if row["closed_at"] else None,
            )
        )
    return out


# ----------------------------------------------------------------------
# The day
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class TradeCharges:
    trade: ClosedTrade
    status: str
    reason: Optional[str] = None
    charges: Optional[Dict[str, float]] = None
    legs: Tuple[Dict[str, Any], ...] = ()

    @property
    def total_charges(self) -> Optional[float]:
        return None if self.charges is None else self.charges["total"]

    @property
    def net_pnl(self) -> Optional[float]:
        if self.charges is None or self.trade.realised_pnl is None:
            return None
        return self.trade.realised_pnl - self.charges["total"]

    def _avg(self, entry: bool) -> Optional[float]:
        if not self.legs:
            return None
        side = self.legs[0].get("transaction_type")
        mine = [leg for leg in self.legs if (leg.get("transaction_type") == side) == entry]
        qty = sum(int(leg.get("quantity") or 0) for leg in mine)
        if qty <= 0:
            return None
        return sum(int(leg["quantity"]) * float(leg["average_price"]) for leg in mine) / qty

    @property
    def entry_avg(self) -> Optional[float]:
        return self._avg(True)

    @property
    def exit_avg(self) -> Optional[float]:
        return self._avg(False)


@dataclass(frozen=True)
class DayCharges:
    session_date: str
    trades: List[TradeCharges] = field(default_factory=list)
    # Set when this request asked Kite and it failed as a whole.
    kite_error: Optional[str] = None


# One Kite pricing pass at a time: two open browser tabs must not both call.
_PRICING_LOCK = threading.Lock()


def _default_kite() -> Any:
    from login import _get_kite

    return _get_kite(timeout=5.0)


def compute_day(
    session_date: str,
    *,
    today: str,
    engine_db: Path,
    cache_db: Path,
    kite_factory: Optional[Callable[[], Any]] = None,
) -> DayCharges:
    trades = read_closed_trades(engine_db, session_date)
    cache = ChargesCache(cache_db)
    try:
        live_ids = [t.trade_id for t in trades if t.is_live]
        cached = cache.get_many(live_ids)
        missing = [t for t in trades if t.is_live and t.trade_id not in cached]
        fresh: Dict[str, TradeCharges] = {}
        kite_error: Optional[str] = None

        if missing and session_date == today:
            with _PRICING_LOCK:
                # Another request may have priced them while this one waited.
                cached.update(cache.get_many([t.trade_id for t in missing]))
                missing = [t for t in missing if t.trade_id not in cached]
                if missing:
                    fresh, kite_error = _price_today(
                        missing, session_date, cache, kite_factory or _default_kite
                    )
                    cached.update(cache.get_many([t.trade_id for t in missing]))

        out: List[TradeCharges] = []
        for trade in trades:
            if not trade.is_live:
                out.append(TradeCharges(trade, STATUS_PAPER))
            elif trade.trade_id in cached:
                hit = cached[trade.trade_id]
                out.append(TradeCharges(trade, STATUS_OK, charges=hit.charges, legs=hit.legs))
            elif trade.trade_id in fresh:
                out.append(fresh[trade.trade_id])
            elif session_date != today:
                out.append(TradeCharges(trade, STATUS_UNAVAILABLE, REASON_NOT_CAPTURED))
            else:
                out.append(TradeCharges(trade, STATUS_UNAVAILABLE, kite_error or REASON_KITE_UNAVAILABLE))
        return DayCharges(session_date=session_date, trades=out, kite_error=kite_error)
    finally:
        cache.close()


def _price_today(
    missing: Sequence[ClosedTrade],
    session_date: str,
    cache: ChargesCache,
    kite_factory: Callable[[], Any],
) -> Tuple[Dict[str, TradeCharges], Optional[str]]:
    """Price today's unsaved trades. Saves only complete, verified results."""
    try:
        kite = kite_factory()
        orders = kite.orders()
    except _KiteTokenException:
        return {}, REASON_SESSION_EXPIRED
    except Exception as exc:  # noqa: BLE001
        logger.warning("Kite order book unavailable for charges: %s", exc)
        return {}, REASON_KITE_UNAVAILABLE
    book = {
        str(o.get("order_id")): o
        for o in (orders if isinstance(orders, list) else [])
        if isinstance(o, Mapping) and o.get("order_id")
    }

    results: Dict[str, TradeCharges] = {}
    plans: List[Tuple[str, LegPlan]] = []
    by_id = {t.trade_id: t for t in missing}
    for trade in missing:
        plan = build_legs(trade.entry_order_id, trade.exit_order_ids, book)
        if plan.ok:
            plans.append((trade.trade_id, plan))
        else:
            results[trade.trade_id] = TradeCharges(trade, STATUS_UNAVAILABLE, plan.reason)

    if not plans:
        return results, None
    try:
        priced = price_plans(kite, plans)
    except _KiteTokenException:
        return results, REASON_SESSION_EXPIRED

    for trade_id, plan in plans:
        result = priced.get(trade_id, Priced(reason=REASON_KITE_UNAVAILABLE))
        trade = by_id[trade_id]
        if result.charges is None:
            results[trade_id] = TradeCharges(trade, STATUS_UNAVAILABLE, result.reason)
            continue
        cache.put(trade_id, session_date, plan.legs, result.charges)
        results[trade_id] = TradeCharges(
            trade,
            STATUS_OK,
            charges=result.charges,
            legs=tuple(leg.as_param() for leg in plan.legs),
        )
    return results, None
