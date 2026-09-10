"""Validated staged-R profile; defaults retain the frozen V1 policy."""
import json
import math

DEFAULT_TRAIL_PROFILE = {
    "trail_stage_one_r": 1.0,
    "trail_stage_two_r": 2.0,
    "trail_stage_one_gap_r": 1.0,
    "trail_stage_two_gap_r": 0.5,
    "trail_modify_interval_seconds": 2.0,
    "trail_min_improvement_ticks": 2.0,
}


def validate_trail_profile(values):
    p = {k:float(values.get(k,v)) for k,v in DEFAULT_TRAIL_PROFILE.items()}
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


def trade_trail_profile(trade):
    # Legacy rows use the historical V1 defaults, never today's Admin settings.
    return validate_trail_profile(json.loads(trade.risk_limits_json or "{}"))
