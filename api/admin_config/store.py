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


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_version_id() -> str:
    return uuid.uuid4().hex


def validate_config_values(values: Mapping[str, Any]) -> tuple[dict[str, float], list[str]]:
    """Return normalized values and non-blocking warnings."""
    missing = [k for k in _CONFIG_KEYS if k not in values]
    if missing:
        raise ValueError(f"missing_fields:{','.join(missing)}")

    per_trade = float(values["per_trade_risk_cap_inr"])
    limited = float(values["limited_per_trade_risk_cap_inr"])
    daily = float(values["daily_loss_cap_inr"])
    accept = float(values["vwap_accept_gap_exclusive_max"])
    limited_gap = float(values["vwap_limited_gap_inclusive_max"])

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

    warnings: list[str] = []
    if daily < per_trade:
        warnings.append("daily_cap_below_per_trade_cap")

    normalized = {
        "per_trade_risk_cap_inr": per_trade,
        "limited_per_trade_risk_cap_inr": limited,
        "daily_loss_cap_inr": daily,
        "vwap_accept_gap_exclusive_max": accept,
        "vwap_limited_gap_inclusive_max": limited_gap,
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
            INSERT INTO admin_config_state (id, active_version_id, entries_paused)
            VALUES (1, ?, 0)
            """,
            (version_id,),
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
        version_id = self.active_version_id()
        row = self._conn.execute(
            "SELECT payload_json FROM admin_config_versions WHERE version_id = ?",
            (version_id,),
        ).fetchone()
        if row is None:
            return dict(DEFAULT_ADMIN_CONFIG_VALUES)
        return {k: float(v) for k, v in json.loads(str(row["payload_json"])).items()}

    def capture_snapshot(self, *, risk_cap_used_inr: Optional[float] = None) -> AdminConfigSnapshot:
        version_id = self.active_version_id()
        payload = self.load_active_payload()
        snap = AdminConfigSnapshot.from_payload(
            version_id=version_id,
            payload=payload,
            read_at=_utc_now(),
            risk_cap_used_inr=risk_cap_used_inr,
        )
        return snap

    def get_config_response(self) -> dict[str, Any]:
        payload = self.load_active_payload()
        _, warnings = validate_config_values(payload)
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
        normalized, warnings = validate_config_values(values)
        current_version = self.active_version_id()
        if expected_version_id is not None and expected_version_id != current_version:
            raise VersionConflictError(current_version)
        old_payload = self.load_active_payload()
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
        self.append_control_log(
            actor_username=actor,
            action="config_update",
            version_id=new_version,
            from_version_id=current_version,
            diff_json=diff,
            result="ok",
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
        normalized, _ = validate_config_values(payload)
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
