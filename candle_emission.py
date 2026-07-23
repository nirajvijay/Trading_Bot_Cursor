"""
Storage-agnostic fatal candle emission errors for the live pipeline.

Persistence-specific classification lives in the writer and pipeline callback
boundary. The builder only reacts to CandleEmissionError.
"""

from __future__ import annotations

from dataclasses import dataclass

from candle_aggregation import CompletedOneMinuteCandle


@dataclass
class CandleEmissionError(Exception):
    """Fatal candle emission failure raised by the persistence callback boundary."""

    candle: CompletedOneMinuteCandle
    cause: BaseException

    def __str__(self) -> str:
        return (
            "fatal candle emission for token=%d time=%s: %s"
            % (
                self.candle.instrument_token,
                self.candle.candle_time,
                self.cause,
            )
        )


@dataclass(frozen=True)
class FailedEmission:
    candle: CompletedOneMinuteCandle
    cause: BaseException
