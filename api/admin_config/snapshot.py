"""Immutable admin configuration snapshot for placement and provenance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Optional

from api.admin_config.defaults import DEFAULT_ADMIN_CONFIG_VALUES


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class AdminConfigSnapshot:
    admin_config_version_id: str
    per_trade_risk_cap_inr: float
    limited_per_trade_risk_cap_inr: float
    daily_loss_cap_inr: float
    vwap_accept_gap_exclusive_max: float
    vwap_limited_gap_inclusive_max: float
    admin_config_read_at: str
    risk_cap_used_inr: Optional[float] = None

    @classmethod
    def from_payload(
        cls,
        *,
        version_id: str,
        payload: Mapping[str, float],
        read_at: Optional[str] = None,
        risk_cap_used_inr: Optional[float] = None,
    ) -> "AdminConfigSnapshot":
        return cls(
            admin_config_version_id=str(version_id),
            per_trade_risk_cap_inr=float(payload["per_trade_risk_cap_inr"]),
            limited_per_trade_risk_cap_inr=float(payload["limited_per_trade_risk_cap_inr"]),
            daily_loss_cap_inr=float(payload["daily_loss_cap_inr"]),
            vwap_accept_gap_exclusive_max=float(payload["vwap_accept_gap_exclusive_max"]),
            vwap_limited_gap_inclusive_max=float(payload["vwap_limited_gap_inclusive_max"]),
            admin_config_read_at=read_at or _utc_now_iso(),
            risk_cap_used_inr=risk_cap_used_inr,
        )

    @classmethod
    def from_defaults(cls, *, version_id: str = "bootstrap") -> "AdminConfigSnapshot":
        return cls.from_payload(version_id=version_id, payload=DEFAULT_ADMIN_CONFIG_VALUES)

    def with_risk_cap_used(self, cap_inr: float) -> "AdminConfigSnapshot":
        return AdminConfigSnapshot(
            admin_config_version_id=self.admin_config_version_id,
            per_trade_risk_cap_inr=self.per_trade_risk_cap_inr,
            limited_per_trade_risk_cap_inr=self.limited_per_trade_risk_cap_inr,
            daily_loss_cap_inr=self.daily_loss_cap_inr,
            vwap_accept_gap_exclusive_max=self.vwap_accept_gap_exclusive_max,
            vwap_limited_gap_inclusive_max=self.vwap_limited_gap_inclusive_max,
            admin_config_read_at=self.admin_config_read_at,
            risk_cap_used_inr=float(cap_inr),
        )

    def provenance_fields(self) -> dict[str, object]:
        fields: dict[str, object] = {
            "admin_config_version_id": self.admin_config_version_id,
            "daily_loss_cap_inr": self.daily_loss_cap_inr,
            "vwap_accept_gap_exclusive_max": self.vwap_accept_gap_exclusive_max,
            "vwap_limited_gap_inclusive_max": self.vwap_limited_gap_inclusive_max,
            "admin_config_read_at": self.admin_config_read_at,
        }
        if self.risk_cap_used_inr is not None:
            fields["risk_cap_used_inr"] = self.risk_cap_used_inr
        return fields
