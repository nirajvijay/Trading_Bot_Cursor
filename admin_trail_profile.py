"""Trailing-profile admin config values — vestigial, kept for the config store.

Trailing was deliberately removed from the execution engine and will be
redesigned from scratch; nothing in the engine reads these numbers any more.
They remain here only because the Admin config store persists and validates
them, and the Diagnostics tab displays them. Dropping them would change saved
admin payloads and that tab's contents, which is a separate decision from the
engine rebuild.

Moved out of the deleted ``trading_engine_trail_profile`` unchanged, minus the
``trade_trail_profile`` helper, which read the old engine's TradeRecord and had
no other caller.
"""
from __future__ import annotations

import math
from typing import Dict

DEFAULT_TRAIL_PROFILE: Dict[str, float] = {
    "trail_stage_one_r": 1.0,
    "trail_stage_two_r": 2.0,
    "trail_stage_one_gap_r": 1.0,
    "trail_stage_two_gap_r": 0.5,
    "trail_modify_interval_seconds": 2.0,
    "trail_min_improvement_ticks": 2.0,
}


def validate_trail_profile(values) -> Dict[str, float]:
    p = {k: float(values.get(k, v)) for k, v in DEFAULT_TRAIL_PROFILE.items()}
    if any(not math.isfinite(v) or v <= 0 for v in p.values()):
        raise ValueError("invalid_trailing_profile")
    if not p["trail_stage_one_r"] < p["trail_stage_two_r"] <= 10:
        raise ValueError("trailing_stages_must_increase_up_to_10R")
    if not p["trail_stage_two_gap_r"] <= p["trail_stage_one_gap_r"] <= 10:
        raise ValueError("later_trailing_gap_must_not_widen")
    if not 2 <= p["trail_modify_interval_seconds"] <= 60:
        raise ValueError("trailing_interval_must_be_2_to_60_seconds")
    ticks = p["trail_min_improvement_ticks"]
    if not ticks.is_integer() or not 2 <= ticks <= 100:
        raise ValueError("trailing_improvement_must_be_2_to_100_whole_ticks")
    return p
