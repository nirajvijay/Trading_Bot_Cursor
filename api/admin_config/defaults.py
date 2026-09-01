"""Canonical default admin configuration values (code baseline)."""

from __future__ import annotations

from trading_engine_types import (
    DAILY_LOSS_CAP,
    LIMITED_PER_TRADE_RISK_CAP,
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
}
