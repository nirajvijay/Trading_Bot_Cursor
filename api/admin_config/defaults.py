"""Canonical default admin configuration values (code baseline)."""

from __future__ import annotations

from trading_engine_types import (
    DAILY_LOSS_CAP,
    DEFAULT_ENTRY_REMAINDER_CANCEL_SECONDS,
    DEFAULT_ESTIMATED_SLIPPAGE_BPS,
    DEFAULT_PROTECTION_CONFIRM_DEADLINE_SECONDS,
    DEFAULT_ROUND_TRIP_CHARGE_BPS,
    DEFAULT_TOTAL_CAPITAL,
    LIMITED_PER_TRADE_RISK_CAP,
    MAX_CONCURRENT_POSITIONS,
    MAX_FILLED_SETUPS_PER_DAY,
    PER_TRADE_RISK_CAP,
)
from vwap_qualifier_v2_config import VwapQualifierV2Config

_VWAP_DEFAULTS = VwapQualifierV2Config()

DEFAULT_ADMIN_CONFIG_VALUES: dict[str, float] = {
    "per_trade_risk_cap_inr": float(PER_TRADE_RISK_CAP),
    "limited_per_trade_risk_cap_inr": float(LIMITED_PER_TRADE_RISK_CAP),
    "daily_loss_cap_inr": float(DAILY_LOSS_CAP),
    "vwap_accept_gap_exclusive_max": float(_VWAP_DEFAULTS.accept_gap_exclusive_max),
    "vwap_limited_gap_inclusive_max": float(_VWAP_DEFAULTS.limited_gap_inclusive_max),
    # WP-1.2 — merge-only defaults (never clobber saved payloads on load).
    "allocated_capital_inr": float(DEFAULT_TOTAL_CAPITAL),
    "max_concurrent_positions": float(MAX_CONCURRENT_POSITIONS),
    "max_filled_setups_per_day": float(MAX_FILLED_SETUPS_PER_DAY),
    "one_position_or_unresolved_entry_per_symbol": 1.0,
    "aggregate_notional_cap_equals_allocated_capital": 1.0,
    # Separated cost components (charges ≠ slippage).
    "round_trip_charge_bps": float(DEFAULT_ROUND_TRIP_CHARGE_BPS),
    "estimated_slippage_bps": float(DEFAULT_ESTIMATED_SLIPPAGE_BPS),
    # WP-1.3 timers (§3.8).
    "protection_confirm_deadline_seconds": float(DEFAULT_PROTECTION_CONFIRM_DEADLINE_SECONDS),
    "entry_remainder_cancel_seconds": float(DEFAULT_ENTRY_REMAINDER_CANCEL_SECONDS),
}
