"""SQLite store for Admin Console V1 configuration."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from api.admin_config.defaults import DEFAULT_ADMIN_CONFIG_VALUES
from api.admin_config.migrations import run_migrations
from api.admin_config.snapshot import AdminConfigSnapshot

_CONFIG_KEYS = tuple(DEFAULT_ADMIN_CONFIG_VALUES.keys())

# Frozen apply policy (§3.7): risk-budget reductions apply immediately for new entries;
# capital / concurrency / fill limits / VWAP (and risk increases) wait for next arm.
_IMMEDIATE_REDUCTION_KEYS = frozenset(
    {
        "daily_loss_cap_inr",
        "per_trade_risk_cap_inr",
        "limited_per_trade_risk_cap_inr",
    }
)


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_version_id() -> str:
    return uuid.uuid4().hex


def merge_payload_with_defaults(stored: Mapping[str, Any]) -> dict[str, float]:
    """Backward-compatible load merge: code defaults under saved values (never clobber).

    Legacy ``round_trip_cost_slippage_bps`` maps to charges when the split keys are
    absent (slippage defaults to 0 so we do not invent a split).
    """
    merged = dict(DEFAULT_ADMIN_CONFIG_VALUES)
    for key, raw in stored.items():
        if key in merged:
            merged[key] = float(raw)
    if (
        "round_trip_charge_bps" not in stored
        and "round_trip_cost_slippage_bps" in stored
    ):
        merged["round_trip_charge_bps"] = float(stored["round_trip_cost_slippage_bps"])
        if "estimated_slippage_bps" not in stored:
            merged["estimated_slippage_bps"] = 0.0
    return merged


def apply_saved_to_effective(
    effective: Mapping[str, float],
    saved: Mapping[str, float],
) -> dict[str, float]:
    """Merge Saved into Effective under the frozen apply policy (no full promote).

    Risk-budget reductions take effect immediately. Risk increases, capital,
    concurrency/fill limits, and VWAP wait for ``arm_effective_config``.
    Charge/slippage *increases* (tightening) apply immediately.
    """
    out = {k: float(effective.get(k, DEFAULT_ADMIN_CONFIG_VALUES[k])) for k in _CONFIG_KEYS}
    for key in _IMMEDIATE_REDUCTION_KEYS:
        new_v = float(saved[key])
        old_v = float(out[key])
        if new_v < old_v - 1e-12:
            out[key] = new_v
    for key in ("round_trip_charge_bps", "estimated_slippage_bps"):
        new_v = float(saved[key])
        old_v = float(out[key])
        if new_v > old_v + 1e-12:
            out[key] = new_v
    # Shorter safety timers (tightening) apply immediately for new decisions.
    for key in ("protection_confirm_deadline_seconds", "entry_remainder_cancel_seconds"):
        new_v = float(saved[key])
        old_v = float(out[key])
        if new_v < old_v - 1e-12:
            out[key] = new_v
    return out


def validate_config_values(
    values: Mapping[str, Any],
    *,
    base: Optional[Mapping[str, Any]] = None,
    fill_defaults: bool = False,
) -> tuple[dict[str, float], list[str]]:
    """Return normalized values and non-blocking warnings.

    Save path: pass ``base`` = current saved/effective payload so omitted keys keep
    their prior values (protects ₹2,995 etc.). Do **not** fill omitted keys from
    code defaults on save.

    Load/bootstrap path: ``fill_defaults=True`` merges missing keys from code
    defaults (new WP keys only when absent).

    Strict replacement: neither ``base`` nor ``fill_defaults`` → require every key.
    """
    if base is not None:
        merged = merge_payload_with_defaults(base)
        for key, raw in values.items():
            if key in merged:
                merged[key] = float(raw)
            elif key == "round_trip_cost_slippage_bps":
                # Legacy write path: treat as charge-only update.
                merged["round_trip_charge_bps"] = float(raw)
    elif fill_defaults:
        merged = merge_payload_with_defaults(values)
    else:
        # Allow legacy combined key as a stand-in for the split pair.
        provided = dict(values)
        if (
            "round_trip_charge_bps" not in provided
            and "round_trip_cost_slippage_bps" in provided
        ):
            provided["round_trip_charge_bps"] = float(provided["round_trip_cost_slippage_bps"])
            provided.setdefault("estimated_slippage_bps", 0.0)
        missing = [k for k in _CONFIG_KEYS if k not in provided]
        if missing:
            raise ValueError(f"incomplete_config:{','.join(missing)}")
        merged = {k: float(provided[k]) for k in _CONFIG_KEYS}

    per_trade = float(merged["per_trade_risk_cap_inr"])
    limited = float(merged["limited_per_trade_risk_cap_inr"])
    daily = float(merged["daily_loss_cap_inr"])
    accept = float(merged["vwap_accept_gap_exclusive_max"])
    limited_gap = float(merged["vwap_limited_gap_inclusive_max"])
    allocated = float(merged["allocated_capital_inr"])
    max_concurrent = float(merged["max_concurrent_positions"])
    max_filled = float(merged["max_filled_setups_per_day"])
    one_per_symbol = float(merged["one_position_or_unresolved_entry_per_symbol"])
    notional_cap_flag = float(merged["aggregate_notional_cap_equals_allocated_capital"])
    charge_bps = float(merged["round_trip_charge_bps"])
    slippage_bps = float(merged["estimated_slippage_bps"])
    protection_deadline = float(merged["protection_confirm_deadline_seconds"])
    remainder_cancel = float(merged["entry_remainder_cancel_seconds"])

    if per_trade <= 0 or per_trade > 2000:
        raise ValueError("per_trade_risk_cap_inr out of range")
    if limited <= 0 or limited > per_trade:
        raise ValueError("limited_per_trade_risk_cap_inr out of range")
    if daily <= 0 or daily > 50000:
        raise ValueError("daily_loss_cap_inr out of range")
    if accept < 0:
        raise ValueError("vwap_accept_gap_exclusive_max out of range")
    if limited_gap <= 0 or limited_gap > 0.01:
        raise ValueError("vwap_limited_gap_inclusive_max out of range")
    if limited_gap <= accept:
        raise ValueError("vwap_limited_gap_inclusive_max must exceed accept threshold")
    if allocated <= 0:
        raise ValueError("allocated_capital_inr out of range")
    if max_concurrent < 1 or max_concurrent > 20:
        raise ValueError("max_concurrent_positions out of range")
    if max_filled < 1 or max_filled > 50:
        raise ValueError("max_filled_setups_per_day out of range")
    if one_per_symbol not in (0.0, 1.0):
        raise ValueError("one_position_or_unresolved_entry_per_symbol must be 0 or 1")
    if notional_cap_flag not in (0.0, 1.0):
        raise ValueError("aggregate_notional_cap_equals_allocated_capital must be 0 or 1")
    if charge_bps < 0 or charge_bps > 100:
        raise ValueError("round_trip_charge_bps out of range")
    if slippage_bps < 0 or slippage_bps > 100:
        raise ValueError("estimated_slippage_bps out of range")
    if protection_deadline < 1 or protection_deadline > 120:
        raise ValueError("protection_confirm_deadline_seconds out of range")
    if remainder_cancel < 1 or remainder_cancel > 120:
        raise ValueError("entry_remainder_cancel_seconds out of range")

    warnings: list[str] = []
    if daily < per_trade:
        warnings.append("daily_cap_below_per_trade_cap")

    normalized = {
        "per_trade_risk_cap_inr": per_trade,
        "limited_per_trade_risk_cap_inr": limited,
        "daily_loss_cap_inr": daily,
        "vwap_accept_gap_exclusive_max": accept,
        "vwap_limited_gap_inclusive_max": limited_gap,
        "allocated_capital_inr": allocated,
        "max_concurrent_positions": max_concurrent,
        "max_filled_setups_per_day": max_filled,
        "one_position_or_unresolved_entry_per_symbol": one_per_symbol,
        "aggregate_notional_cap_equals_allocated_capital": notional_cap_flag,
        "round_trip_charge_bps": charge_bps,
        "estimated_slippage_bps": slippage_bps,
        "protection_confirm_deadline_seconds": protection_deadline,
        "entry_remainder_cancel_seconds": remainder_cancel,
    }
    return normalized, warnings


def _diff_payload(old: Mapping[str, float], new: Mapping[str, float]) -> dict[str, dict[str, float]]:
    diff: dict[str, dict[str, float]] = {}
    for key in _CONFIG_KEYS:
        o = float(old[key])
        n = float(new[key])
        if abs(o - n) > 1e-12:
            diff[key] = {"old": o, "new": n}
    return diff


class AdminConfigStore:
    def __init__(self, db_path: Path, *, read_only: bool = False) -> None:
        self.db_path = Path(db_path)
        self.read_only = bool(read_only)
        if not self.read_only:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self.db_path.parent, 0o700)
            except OSError:
                pass
        elif not _path_exists(self.db_path):
            # Fresh hosts/tests: bootstrap once so read-only consumers can start.
            bootstrap = AdminConfigStore(self.db_path, read_only=False)
            bootstrap.close()
        uri = f"file:{self.db_path}?mode=ro" if self.read_only else str(self.db_path)
        self._conn = sqlite3.connect(uri, uri=self.read_only, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        if not self.read_only:
            run_migrations(self._conn)
            self._bootstrap_if_empty()
            self._set_db_permissions()

    def _set_db_permissions(self) -> None:
        try:
            os.chmod(self.db_path, 0o660)
        except OSError:
            pass

    def close(self) -> None:
        self._conn.close()

    def _bootstrap_if_empty(self) -> None:
        row = self._conn.execute("SELECT id FROM admin_config_state WHERE id = 1").fetchone()
        if row is not None:
            return
        version_id = _new_version_id()
        now = _utc_now()
        payload = dict(DEFAULT_ADMIN_CONFIG_VALUES)
        self._conn.execute(
            """
            INSERT INTO admin_config_versions (
                version_id, created_at, created_by, payload_json, parent_version_id, comment
            ) VALUES (?, ?, ?, ?, NULL, ?)
            """,
            (version_id, now, "bootstrap", json.dumps(payload, sort_keys=True), "initial"),
        )
        self._conn.execute(
            """
            INSERT INTO admin_config_state (
                id, active_version_id, entries_paused,
                effective_version_id, effective_payload_json, effective_armed_at
            )
            VALUES (1, ?, 0, ?, ?, ?)
            """,
            (version_id, version_id, json.dumps(payload, sort_keys=True), now),
        )
        self._conn.commit()

    def read_entries_paused(self) -> bool:
        row = self._conn.execute(
            "SELECT entries_paused FROM admin_config_state WHERE id = 1"
        ).fetchone()
        if row is None:
            return False
        return bool(int(row["entries_paused"]))

    def set_entries_paused(self, paused: bool) -> None:
        if self.read_only:
            raise RuntimeError("admin config store is read-only")
        self._conn.execute(
            "UPDATE admin_config_state SET entries_paused = ? WHERE id = 1",
            (1 if paused else 0,),
        )
        self._conn.commit()

    def active_version_id(self) -> str:
        row = self._conn.execute(
            "SELECT active_version_id FROM admin_config_state WHERE id = 1"
        ).fetchone()
        if row is None:
            return "bootstrap"
        return str(row["active_version_id"])

    def load_active_payload(self) -> dict[str, float]:
        """Return Saved config: saved values overlay code defaults (preserve 2995 etc.)."""
        version_id = self.active_version_id()
        row = self._conn.execute(
            "SELECT payload_json FROM admin_config_versions WHERE version_id = ?",
            (version_id,),
        ).fetchone()
        if row is None:
            return dict(DEFAULT_ADMIN_CONFIG_VALUES)
        stored = {k: float(v) for k, v in json.loads(str(row["payload_json"])).items()}
        return merge_payload_with_defaults(stored)

    def effective_version_id(self) -> str:
        row = self._conn.execute(
            "SELECT effective_version_id, active_version_id FROM admin_config_state WHERE id = 1"
        ).fetchone()
        if row is None:
            return "bootstrap"
        return str(row["effective_version_id"] or row["active_version_id"] or "bootstrap")

    def load_effective_payload(self) -> dict[str, float]:
        """Return Effective config used for new entry decisions (after apply rules)."""
        row = self._conn.execute(
            "SELECT effective_payload_json FROM admin_config_state WHERE id = 1"
        ).fetchone()
        if row is None or row["effective_payload_json"] is None:
            return self.load_active_payload()
        stored = {
            k: float(v) for k, v in json.loads(str(row["effective_payload_json"])).items()
        }
        return merge_payload_with_defaults(stored)

    def arm_effective_config(self, *, actor: str = "engine_arm") -> str:
        """Promote Saved → Effective at an explicit validated arm boundary only."""
        if self.read_only:
            raise RuntimeError("admin config store is read-only")
        saved = self.load_active_payload()
        validate_config_values(saved, fill_defaults=True)
        version_id = self.active_version_id()
        now = _utc_now()
        old = self.load_effective_payload()
        old_eff_version = self.effective_version_id()
        self._conn.execute(
            """
            UPDATE admin_config_state
            SET effective_version_id = ?,
                effective_payload_json = ?,
                effective_armed_at = ?
            WHERE id = 1
            """,
            (version_id, json.dumps(saved, sort_keys=True), now),
        )
        diff = _diff_payload(old, saved)
        self._conn.commit()
        self.append_control_log(
            actor_username=actor,
            action="arm_effective_config",
            version_id=version_id,
            from_version_id=old_eff_version,
            diff_json=diff,
            result="ok",
            detail="explicit_arm_boundary",
        )
        return version_id

    def capture_snapshot(self, *, risk_cap_used_inr: Optional[float] = None) -> AdminConfigSnapshot:
        """Snapshot Effective config for new decisions / trade provenance."""
        version_id = self.effective_version_id()
        payload = self.load_effective_payload()
        snap = AdminConfigSnapshot.from_payload(
            version_id=version_id,
            payload=payload,
            read_at=_utc_now(),
            risk_cap_used_inr=risk_cap_used_inr,
        )
        return snap

    def get_config_response(self) -> dict[str, Any]:
        payload = self.load_active_payload()
        _, warnings = validate_config_values(payload, fill_defaults=True)
        accept = payload["vwap_accept_gap_exclusive_max"]
        limited = payload["vwap_limited_gap_inclusive_max"]
        return {
            "version_id": self.active_version_id(),
            "entries_paused": self.read_entries_paused(),
            "values": payload,
            "vwap_accept_gap_percent": accept * 100.0,
            "vwap_limited_gap_percent": limited * 100.0,
            "warnings": warnings,
        }

    def update_config(
        self,
        values: Mapping[str, Any],
        *,
        actor: str,
        expected_version_id: Optional[str] = None,
        comment: Optional[str] = None,
    ) -> tuple[str, list[str]]:
        if self.read_only:
            raise RuntimeError("admin config store is read-only")
        current_version = self.active_version_id()
        if expected_version_id is not None and expected_version_id != current_version:
            raise VersionConflictError(current_version)
        old_payload = self.load_active_payload()
        # Merge explicitly against saved/effective — never reset omitted keys to code defaults.
        normalized, warnings = validate_config_values(values, base=old_payload)
        diff = _diff_payload(old_payload, normalized)
        if not diff:
            return current_version, warnings
        new_version = _new_version_id()
        now = _utc_now()
        self._conn.execute(
            """
            INSERT INTO admin_config_versions (
                version_id, created_at, created_by, payload_json, parent_version_id, comment
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_version,
                now,
                actor,
                json.dumps(normalized, sort_keys=True),
                current_version,
                comment,
            ),
        )
        self._conn.execute(
            "UPDATE admin_config_state SET active_version_id = ? WHERE id = 1",
            (new_version,),
        )
        # Apply policy: reductions immediate; increases/capital/limits/VWAP wait for arm.
        old_effective = self.load_effective_payload()
        new_effective = apply_saved_to_effective(old_effective, normalized)
        eff_diff = _diff_payload(old_effective, new_effective)
        if eff_diff:
            self._conn.execute(
                """
                UPDATE admin_config_state
                SET effective_payload_json = ?,
                    effective_version_id = ?
                WHERE id = 1
                """,
                (json.dumps(new_effective, sort_keys=True), new_version),
            )
        self.append_control_log(
            actor_username=actor,
            action="config_update",
            version_id=new_version,
            from_version_id=current_version,
            diff_json=diff,
            result="ok",
        )
        if eff_diff:
            self.append_control_log(
                actor_username=actor,
                action="effective_immediate_apply",
                version_id=new_version,
                from_version_id=current_version,
                diff_json=eff_diff,
                result="ok",
                detail="risk_budget_reductions_only",
            )
        self._conn.commit()
        return new_version, warnings

    def rollback_config(
        self,
        target_version_id: str,
        *,
        actor: str,
    ) -> str:
        if self.read_only:
            raise RuntimeError("admin config store is read-only")
        row = self._conn.execute(
            "SELECT payload_json FROM admin_config_versions WHERE version_id = ?",
            (target_version_id,),
        ).fetchone()
        if row is None:
            raise KeyError("version_not_found")
        current_version = self.active_version_id()
        if current_version == target_version_id:
            return current_version
        payload = json.loads(str(row["payload_json"]))
        # Historical payloads may omit newer keys — fill those only on load semantics.
        normalized, _ = validate_config_values(payload, fill_defaults=True)
        old_payload = self.load_active_payload()
        diff = _diff_payload(old_payload, normalized)
        self._conn.execute(
            "UPDATE admin_config_state SET active_version_id = ? WHERE id = 1",
            (target_version_id,),
        )
        self.append_control_log(
            actor_username=actor,
            action="rollback",
            version_id=target_version_id,
            from_version_id=current_version,
            diff_json=diff,
            result="ok",
        )
        self._conn.commit()
        return target_version_id

    def append_control_log(
        self,
        *,
        actor_username: str,
        action: str,
        result: str,
        diff_json: Optional[Mapping[str, Any]] = None,
        version_id: Optional[str] = None,
        from_version_id: Optional[str] = None,
        detail: Optional[str] = None,
        step_up_verified: bool = True,
    ) -> None:
        if self.read_only:
            raise RuntimeError("admin config store is read-only")
        self._conn.execute(
            """
            INSERT INTO admin_control_log (
                at, actor_username, action, version_id, from_version_id,
                diff_json, result, detail, step_up_verified
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _utc_now(),
                actor_username,
                action,
                version_id,
                from_version_id,
                json.dumps(diff_json or {}, sort_keys=True),
                result,
                detail,
                1 if step_up_verified else 0,
            ),
        )
        self._conn.commit()

    def list_audit(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT * FROM admin_control_log
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]


class VersionConflictError(Exception):
    def __init__(self, current_version_id: str) -> None:
        super().__init__(current_version_id)
        self.current_version_id = current_version_id
