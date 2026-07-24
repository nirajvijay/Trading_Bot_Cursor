"""Pure EMA20 and session VWAP indicators for pullback evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence


@dataclass
class Ema20State:
    period: int = 20
    values: List[float] = field(default_factory=list)
    ema: Optional[float] = None
    seed_session_date: Optional[str] = None
    seeded: bool = False

    @property
    def alpha(self) -> float:
        return 2.0 / (self.period + 1)

    @property
    def available(self) -> bool:
        return self.ema is not None

    def seed_from_closes(
        self,
        closes: Sequence[float],
        *,
        seed_session_date: str,
    ) -> None:
        """Seed from prior-session closes. Uses SMA of first `period` then EMA rest."""
        self.seed_session_date = seed_session_date
        self.values = []
        self.ema = None
        self.seeded = False
        if len(closes) < self.period:
            return
        sma = sum(closes[: self.period]) / self.period
        self.ema = sma
        self.values = list(closes[: self.period])
        for close in closes[self.period :]:
            self.ema = self.alpha * close + (1.0 - self.alpha) * self.ema
            self.values.append(close)
        self.seeded = True

    def update(self, close: float) -> Optional[float]:
        """Update with a completed live 5m close. Returns current EMA or None."""
        if self.ema is None:
            self.values.append(close)
            if len(self.values) < self.period:
                return None
            self.ema = sum(self.values[-self.period :]) / self.period
            self.seeded = True
            return self.ema
        self.ema = self.alpha * close + (1.0 - self.alpha) * self.ema
        self.values.append(close)
        return self.ema


@dataclass
class SessionVwapState:
    cum_pv: float = 0.0
    cum_volume: int = 0

    @property
    def vwap(self) -> Optional[float]:
        if self.cum_volume <= 0:
            return None
        return self.cum_pv / self.cum_volume

    def update(self, high: float, low: float, close: float, volume: int) -> Optional[float]:
        typical = (high + low + close) / 3.0
        self.cum_pv += typical * volume
        self.cum_volume += volume
        return self.vwap

    def reset(self) -> None:
        self.cum_pv = 0.0
        self.cum_volume = 0
