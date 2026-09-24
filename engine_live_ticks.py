"""The execution engine's own live price feed, for the desk's Open P&L only.

It also carries Kite's order-update pushes, which may only wake the engine's
loop early (see engine_runloop.LoopWake). Nothing from a push -- or a tick --
is ever read by stops, reconciliation, the risk cap or square-off.

Kite's REST positions `pnl` only moves when Kite refreshes it internally, so
the desk could look frozen for 15s+ while Kite's own terminal ticked. This is
one dedicated KiteTicker connection inside the engine process, subscribed only
to the instruments the engine is currently holding.

**Display only.** Nothing here is ever read by stops, reconciliation, the risk
cap or square-off. engine_live_marks turns a tick into a number for the desk;
that is the only consumer.

**Lifecycle follows the engine process.** start() when the engine starts,
stop() when it ends -- including a manual stop and restart mid-day, which is a
new process and so a new connection. KiteTicker runs a Twisted reactor that
cannot be restarted within one process, which is exactly why the feed never
tries to reconnect itself from scratch: it relies on KiteTicker's own
reconnects, and on_connect re-subscribes whatever is currently wanted.

**LIVE only.** PAPER uses NullTickFeed, which has the same interface and never
has a tick, so the engine needs no live/paper branches of its own.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from host_clock import REASON_HOST_NOT_IST, host_is_ist

logger = logging.getLogger(__name__)

# Health reasons, surfaced on the desk's fallback badge.
REASON_PAPER = "paper_mode"
REASON_NOT_STARTED = "ws_not_started"
REASON_START_FAILED = "ws_start_failed"
REASON_CONNECTING = "ws_connecting"
REASON_DISCONNECTED = "ws_disconnected"
REASON_GAVE_UP = "ws_reconnect_exhausted"
REASON_STOPPED = "ws_stopped"


@dataclass(frozen=True)
class Tick:
    last_price: float
    received_at: datetime


@dataclass(frozen=True)
class TickFeedHealth:
    """Whether the feed can currently be trusted for a live mark."""

    live: bool  # False for PAPER: the feed does not exist at all
    connected: bool
    last_tick_at: Optional[datetime] = None
    reason: Optional[str] = None
    # Kite order-update pushes received since start (each one woke the loop).
    order_updates: int = 0


class NullTickFeed:
    """PAPER, and the default for tests: never connected, never a tick."""

    live = False

    def start(self) -> bool:
        return False

    def stop(self) -> None:
        return None

    def sync(self, tokens: Iterable[int]) -> None:
        return None

    def latest(self, token: int) -> Optional[Tick]:
        return None

    def health(self) -> TickFeedHealth:
        return TickFeedHealth(live=False, connected=False, reason=REASON_PAPER)


def _twisted_call_from_thread(fn: Callable[..., Any], *args: Any) -> None:
    """Run fn on the reactor thread, where KiteTicker's socket lives."""
    from twisted.internet import reactor  # imported lazily: LIVE only

    reactor.callFromThread(fn, *args)  # type: ignore[attr-defined]


class LiveTickFeed:
    """One KiteTicker connection, subscribed to the engine's held instruments."""

    live = True

    def __init__(
        self,
        *,
        api_key: str,
        access_token: str,
        ticker_factory: Optional[Callable[[str, str], Any]] = None,
        call_in_io_thread: Optional[Callable[..., None]] = None,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        host_clock_ok: Callable[[], bool] = host_is_ist,
        on_order_update: Optional[Callable[[], None]] = None,
    ) -> None:
        self._api_key = api_key
        self._access_token = access_token
        self._ticker_factory = ticker_factory
        self._call = call_in_io_thread or _twisted_call_from_thread
        self._now = now_fn
        self._host_clock_ok = host_clock_ok
        self._wake = on_order_update
        self._order_updates = 0
        self._lock = threading.Lock()
        self._ticker: Any = None
        self._connected = False
        self._started = False
        self._stopped = False
        self._reason: Optional[str] = REASON_NOT_STARTED
        self._desired: Set[int] = set()
        self._subscribed: Set[int] = set()
        self._latest: Dict[int, Tick] = {}
        self._last_tick_at: Optional[datetime] = None

    # ------------------------------------------------------------------
    # Engine-facing
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Connect once. A failure is recorded, never raised: the engine keeps
        running and every mark falls back to Kite REST."""
        if self._started:
            return True
        if not self._host_clock_ok():
            # Same recorded-not-raised rule as any other start failure: the
            # desk shows why and every mark stays on Kite REST.
            logger.error("Live P&L feed not started: host clock is not IST.")
            self._set_reason(REASON_HOST_NOT_IST)
            return False
        try:
            factory = self._ticker_factory
            if factory is None:
                from kiteconnect import KiteTicker

                factory = KiteTicker
            ticker = factory(self._api_key, self._access_token)
            ticker.on_ticks = self._on_ticks
            ticker.on_connect = self._on_connect
            ticker.on_close = self._on_close
            ticker.on_error = self._on_error
            ticker.on_reconnect = self._on_reconnect
            ticker.on_noreconnect = self._on_noreconnect
            ticker.on_order_update = self._on_order_update
            self._ticker = ticker
            self._started = True
            self._set_reason(REASON_CONNECTING)
            ticker.connect(threaded=True)
            return True
        except Exception as exc:  # noqa: BLE001 - display feed must never end the engine
            logger.exception("Live P&L feed failed to start.")
            self._set_reason(f"{REASON_START_FAILED}: {exc}")
            self._started = False
            return False

    def stop(self) -> None:
        """Idempotent. Called from the engine runner's finally block."""
        if self._stopped:
            return
        self._stopped = True
        with self._lock:
            self._connected = False
            self._reason = REASON_STOPPED
        ticker = self._ticker
        if ticker is None:
            return
        try:
            ticker.close()
        except Exception:  # noqa: BLE001
            logger.exception("Error while closing the live P&L feed.")

    def sync(self, tokens: Iterable[int]) -> None:
        """Subscribe to exactly these instruments: add new ones, drop closed ones.

        The connection stays up while nothing is subscribed. When not connected
        the wish is only remembered; on_connect sends it.
        """
        desired = {int(t) for t in tokens if t}
        with self._lock:
            self._desired = desired
            if not self._connected or self._ticker is None:
                return
            add = sorted(desired - self._subscribed)
            drop = sorted(self._subscribed - desired)
            self._subscribed = set(desired)
            for token in drop:
                self._latest.pop(token, None)
        try:
            if drop:
                self._call(self._ticker.unsubscribe, drop)
            if add:
                self._call(self._ticker.subscribe, add)
                self._call(self._ticker.set_mode, self._ticker.MODE_LTP, add)
        except Exception:  # noqa: BLE001
            logger.exception("Live P&L feed subscription change failed.")

    def latest(self, token: int) -> Optional[Tick]:
        with self._lock:
            return self._latest.get(int(token))

    def health(self) -> TickFeedHealth:
        with self._lock:
            return TickFeedHealth(
                live=True,
                connected=self._connected,
                last_tick_at=self._last_tick_at,
                reason=None if self._connected else self._reason,
                order_updates=self._order_updates,
            )

    # ------------------------------------------------------------------
    # KiteTicker callbacks (reactor thread). Never raise into KiteTicker.
    # ------------------------------------------------------------------

    def _on_connect(self, ws: Any, response: Any = None) -> None:
        with self._lock:
            self._connected = True
            self._reason = None
            wanted = sorted(self._desired)
            self._subscribed = set(wanted)
        logger.info("Live P&L feed connected; subscribing to %d tokens.", len(wanted))
        # On a reconnect KiteTicker re-subscribes its own record of tokens right
        # after this callback (in onOpen). That record still holds anything that
        # was dropped by sync() while disconnected, so trim it to what is wanted
        # now. Safe here: we are on the reactor thread, before the resubscribe.
        record = getattr(ws, "subscribed_tokens", None)
        if isinstance(record, dict):
            keep = set(wanted)
            for token in [t for t in record if t not in keep]:
                del record[token]
        if wanted:
            try:
                # Already on the reactor thread: call directly.
                ws.subscribe(wanted)
                ws.set_mode(ws.MODE_LTP, wanted)
            except Exception:  # noqa: BLE001
                logger.exception("Live P&L feed re-subscribe failed.")

    def _on_ticks(self, ws: Any, ticks: List[Dict[str, Any]]) -> None:
        received_at = self._now()
        with self._lock:
            for raw in ticks or []:
                try:
                    token = int(raw.get("instrument_token"))
                    price = raw.get("last_price")
                    if price is None or float(price) <= 0:
                        continue
                    if token not in self._subscribed:
                        continue
                    self._latest[token] = Tick(float(price), received_at)
                    self._last_tick_at = received_at
                except (TypeError, ValueError):
                    continue

    def _on_order_update(self, ws: Any, data: Any = None) -> None:
        """A wake-up call only. ``data`` is deliberately never read: the
        engine's next tick reads broker truth over REST as always."""
        with self._lock:
            self._order_updates += 1
        wake = self._wake
        if wake is None:
            return
        try:
            wake()
        except Exception:  # noqa: BLE001 - never raise into KiteTicker
            logger.exception("Order-update wake failed.")

    def _on_close(self, ws: Any, code: Any = None, reason: Any = None) -> None:
        logger.warning("Live P&L feed closed (code=%s reason=%s).", code, reason)
        self._mark_down(REASON_STOPPED if self._stopped else REASON_DISCONNECTED)

    def _on_error(self, ws: Any, code: Any = None, reason: Any = None) -> None:
        logger.error("Live P&L feed error (code=%s reason=%s).", code, reason)
        self._mark_down(REASON_DISCONNECTED)

    def _on_reconnect(self, ws: Any, attempts_count: Any = None) -> None:
        logger.warning("Live P&L feed reconnect attempt %s.", attempts_count)
        self._mark_down(REASON_DISCONNECTED)

    def _on_noreconnect(self, ws: Any) -> None:
        logger.critical("Live P&L feed gave up reconnecting; desk falls back to Kite REST.")
        self._mark_down(REASON_GAVE_UP)

    def _mark_down(self, reason: str) -> None:
        with self._lock:
            self._connected = False
            self._reason = reason
            self._subscribed = set()
            self._latest.clear()

    def _set_reason(self, reason: str) -> None:
        with self._lock:
            self._reason = reason
