"""Ranking triggers that compete for the same capital and slots.

With ~100 stocks watched, capital and open-position slots are shared. If more
than one stock triggers in the same short window, processing them in whatever
order the database returned (`ORDER BY created_at ASC`) means the better setup
can lose purely by timing accident.

This is a narrow exception, not a general scoring engine: outside genuine
same-window collisions the default is still FIFO, which falls out of rule 3
below. Capital already tied up in earlier open trades is a separate, already
handled concern (capital-based sizing).

Deliberately rejected as a ranking signal: risk-per-share / tightness of stop.
A tighter stop only means more shares fit under the same risk cap — it says
nothing about setup quality, and ranking on it would bias toward stocks with
naturally choppy, tight recent ranges.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from engine_types import TriggerCandidate
from trading_engine_broker import parse_timestamp_text

# The two classifications that clear a candidate to size and enter, mapped to
# which per-trade risk cap the sizing policy should be given. Owned here so the
# cap mapping and the rank ordering below can never disagree about which
# classifications are tradeable.
VWAP_ENTRY_CLASSES = {"ACCEPT": "normal", "LIMITED": "vwap_limited"}

# Rule 1: VWAP tier ranks first, always. An ACCEPT-classified trigger must
# never lose a capital/slot allocation to a LIMITED one that arrived in the
# same window.
TIER_RANK = {"ACCEPT": 0, "LIMITED": 1}
_UNRANKED_TIER = 2


def volume_ratio(candidate: TriggerCandidate) -> Optional[float]:
    """Breakout strength: breakout candle volume against the prior 3m average.

    Signal detection already computed both numbers when the pattern qualified
    as a trigger. None when either is missing or the average is non-positive —
    an unusable ratio must never win by accident, and must never raise.
    """
    volume = candidate.breakout_candle_volume
    average = candidate.avg_prior_3_1m_volume
    if volume is None or average is None:
        return None
    try:
        average = float(average)
        if average <= 0:
            return None
        return float(volume) / average
    except (TypeError, ValueError):
        return None


def _created_at_key(candidate: TriggerCandidate) -> float:
    """Epoch seconds for FIFO ordering; unparseable timestamps sort last."""
    parsed = parse_timestamp_text(candidate.created_at)
    if parsed is None:
        return float("inf")
    return parsed.timestamp() if parsed.tzinfo else parsed.timestamp()


def _sort_key(candidate: TriggerCandidate):
    tier = TIER_RANK.get(str(candidate.vwap_classification or ""), _UNRANKED_TIER)
    ratio = volume_ratio(candidate)
    # Rule 2: within a tier, stronger breakout first. Candidates with no usable
    # ratio sort after every ranked one in their own tier, never ahead of them.
    has_ratio = 0 if ratio is not None else 1
    ratio_key = -ratio if ratio is not None else 0.0
    # Rule 3: FIFO is the final tiebreak, and the only ordering that applies
    # outside a genuine same-window collision.
    return (tier, has_ratio, ratio_key, _created_at_key(candidate))


def rank_candidates(candidates: Sequence[TriggerCandidate]) -> List[TriggerCandidate]:
    """Order candidates for entry. Pure function: no I/O, no mutation.

    Python's sort is stable, so candidates that tie on every rule keep the
    order they arrived in.
    """
    return sorted(candidates, key=_sort_key)
