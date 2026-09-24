"""The one way this project builds a KiteTicker.

Reconnects: KiteTicker gives up after 50 attempts by default (~50 minutes at
its 60s maximum backoff), and for the observation feed giving up is fatal. 300
is the SDK's own ceiling -- about 5 hours at 60s, which covers a full session.

Connection budget: Kite allows 3 WebSocket connections per API key. The
observation receiver and the execution engine's live feed use 2, so only one
more (a manual tick_receiver.py run, a smoke test) fits during market hours.
"""
from __future__ import annotations

from typing import Any

KITE_RECONNECT_MAX_TRIES = 300  # the SDK's maximum (_maximum_reconnect_max_tries)


def make_kite_ticker(api_key: str, access_token: str) -> Any:
    from kiteconnect import KiteTicker  # lazy: PAPER and tests never need it

    return KiteTicker(
        api_key,
        access_token,
        reconnect_max_tries=KITE_RECONNECT_MAX_TRIES,
    )
