"""Map admin snapshots to VWAP qualifier configuration."""

from __future__ import annotations

from api.admin_config.snapshot import AdminConfigSnapshot
from vwap_qualifier_v2_config import VwapQualifierV2Config


def snapshot_to_vwap_config(snapshot: AdminConfigSnapshot) -> VwapQualifierV2Config:
    return VwapQualifierV2Config(
        accept_gap_exclusive_max=snapshot.vwap_accept_gap_exclusive_max,
        limited_gap_inclusive_max=snapshot.vwap_limited_gap_inclusive_max,
        risk_cap_normal_inr=snapshot.per_trade_risk_cap_inr,
        risk_cap_reduced_inr=snapshot.limited_per_trade_risk_cap_inr,
    )
