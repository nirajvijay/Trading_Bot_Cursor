"""Is this machine's local clock on IST?

KiteTicker builds every tick timestamp with ``datetime.fromtimestamp(epoch)``:
the host's *local* time, returned without a timezone. kite_tick_normalizer then
reads any naive timestamp as IST. The two only agree when the host itself runs
on Asia/Kolkata -- true on Lightsail today, but nothing enforced it. A UTC
container or a rebuilt VM would silently shift every candle by 5h30m.

This check asks the exact question the SDK depends on: does ``fromtimestamp``
on this host produce IST wall-clock time? IST has no daylight saving, so one
instant answers it for the whole day.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from tick_event import IST

REASON_HOST_NOT_IST = "host_not_ist"

_IST = ZoneInfo(IST)


def host_is_ist(
    epoch: Optional[float] = None,
    *,
    local_fromtimestamp: Callable[[float], datetime] = datetime.fromtimestamp,
) -> bool:
    """True when naive local time from ``fromtimestamp`` equals IST wall time."""
    instant = float(time.time() if epoch is None else epoch)
    local_naive = local_fromtimestamp(instant)
    ist_naive = datetime.fromtimestamp(instant, _IST).replace(tzinfo=None)
    return local_naive == ist_naive


def host_not_ist_message() -> str:
    return (
        "Host clock is not IST (Asia/Kolkata). KiteTicker stamps ticks in host "
        "local time and the tick normalizer reads them as IST, so candles would "
        "be shifted. Set the machine timezone to Asia/Kolkata."
    )
