"""
Ref-counted arm registry for armed-symbol-only VWAP cache lifecycle.

Cold → Warming → Ready; backoff → Degraded.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Set

from vwap_qualifier_v2_types import CacheState

WarmupCallback = Callable[[int], None]
DisarmCallback = Callable[[int], None]


@dataclass
class _TokenArmState:
    ref_count: int = 0
    setup_ids: Set[str] = field(default_factory=set)
    cache_state: CacheState = "cold"


class VwapArmRegistry:
    def __init__(
        self,
        *,
        on_warmup: Optional[WarmupCallback] = None,
        on_cold: Optional[DisarmCallback] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._tokens: Dict[int, _TokenArmState] = {}
        self._on_warmup = on_warmup
        self._on_cold = on_cold

    def arm(self, setup_id: str, instrument_token: int) -> CacheState:
        with self._lock:
            state = self._tokens.setdefault(instrument_token, _TokenArmState())
            was_cold = state.ref_count == 0
            state.setup_ids.add(setup_id)
            state.ref_count += 1
            if was_cold:
                state.cache_state = "warming"
                warmup = self._on_warmup
            else:
                warmup = None
            cache_state = state.cache_state
        if warmup is not None:
            warmup(instrument_token)
        return cache_state

    def disarm(self, setup_id: str, instrument_token: int) -> None:
        cold_callback: Optional[DisarmCallback] = None
        with self._lock:
            state = self._tokens.get(instrument_token)
            if state is None:
                return
            if setup_id not in state.setup_ids:
                return
            state.setup_ids.discard(setup_id)
            state.ref_count -= 1
            if state.ref_count > 0:
                return
            self._tokens.pop(instrument_token, None)
            cold_callback = self._on_cold
        if cold_callback is not None:
            cold_callback(instrument_token)

    def cache_state(self, instrument_token: int) -> CacheState:
        with self._lock:
            state = self._tokens.get(instrument_token)
            if state is None:
                return "cold"
            return state.cache_state

    def set_cache_state(self, instrument_token: int, cache_state: CacheState) -> None:
        with self._lock:
            state = self._tokens.get(instrument_token)
            if state is None:
                return
            state.cache_state = cache_state

    def armed_tokens(self) -> Set[int]:
        with self._lock:
            return {t for t, s in self._tokens.items() if s.ref_count > 0}

    def is_armed(self, instrument_token: int) -> bool:
        with self._lock:
            state = self._tokens.get(instrument_token)
            return state is not None and state.ref_count > 0
