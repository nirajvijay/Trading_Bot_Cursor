"""Pure gap analytics helpers for pullback setups (v1 analytics only)."""

from __future__ import annotations

from typing import Optional

from pullback_types import GapAnalytics, GapDirection


def compute_gap_analytics(
    previous_session_close: Optional[float],
    session_open: Optional[float],
) -> GapAnalytics:
    if previous_session_close is None or session_open is None:
        return GapAnalytics(
            previous_session_close=previous_session_close,
            session_open=session_open,
            gap_absolute=None,
            gap_percent=None,
            gap_direction=None,
        )
    if previous_session_close <= 0:
        return GapAnalytics(
            previous_session_close=previous_session_close,
            session_open=session_open,
            gap_absolute=None,
            gap_percent=None,
            gap_direction=None,
        )

    gap_absolute = session_open - previous_session_close
    gap_percent = gap_absolute / previous_session_close * 100.0
    direction: GapDirection
    if gap_absolute > 0:
        direction = "GAP_UP"
    elif gap_absolute < 0:
        direction = "GAP_DOWN"
    else:
        direction = "FLAT"
    return GapAnalytics(
        previous_session_close=previous_session_close,
        session_open=session_open,
        gap_absolute=gap_absolute,
        gap_percent=gap_percent,
        gap_direction=direction,
    )
