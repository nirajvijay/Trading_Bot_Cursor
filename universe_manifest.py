"""Universe identity manifest for local data (despite nifty50_*.db filenames)."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple

from config.nifty100_symbols import NIFTY_100_NSE_SOURCE_DATE, NIFTY_100_SYMBOLS

UNIVERSE_NAME = "NIFTY_100"
UNIVERSE_SIZE = 100
MANIFEST_FILENAME = "universe_manifest.json"


def symbol_list_checksum(symbols: tuple[str, ...] = NIFTY_100_SYMBOLS) -> str:
    payload = "\n".join(symbols).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def default_manifest_path(local_data_dir: Path) -> Path:
    return local_data_dir / MANIFEST_FILENAME


def build_manifest_dict(*, created_at: Optional[str] = None) -> dict[str, Any]:
    ts = created_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "universe": UNIVERSE_NAME,
        "universe_size": UNIVERSE_SIZE,
        "nse_source_date": NIFTY_100_NSE_SOURCE_DATE,
        "created_at": ts,
        "cutover_at": ts,
        "symbol_list_checksum": symbol_list_checksum(),
    }


def write_universe_manifest_atomic(path: Path, *, created_at: Optional[str] = None) -> None:
    """Write manifest via temp file + rename. Raises on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_manifest_dict(created_at=created_at)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    # Manifest identity is part of checklist cache validation; drop stale cache.
    try:
        from api.services.checklist_cache import invalidate_checklist_cache

        invalidate_checklist_cache(local_data_dir=path.parent)
    except Exception:
        pass


@dataclass(frozen=True)
class ManifestValidation:
    ok: bool
    status: str  # ok | not_initialized | failed
    message: str
    data: Optional[dict[str, Any]] = None


def validate_universe_manifest(path: Path) -> ManifestValidation:
    """Hard validation against current NIFTY_100_SYMBOLS. Not warning-only."""
    if not path.exists():
        return ManifestValidation(
            ok=False,
            status="not_initialized",
            message="Universe manifest not initialized — run Instruments collect",
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ManifestValidation(
            ok=False,
            status="failed",
            message=f"Universe manifest invalid: {exc}",
        )
    if not isinstance(data, dict):
        return ManifestValidation(
            ok=False,
            status="failed",
            message="Universe manifest invalid: expected JSON object",
        )

    expected_checksum = symbol_list_checksum()
    errors: list[str] = []
    if data.get("universe") != UNIVERSE_NAME:
        errors.append(f"universe={data.get('universe')!r} (expected {UNIVERSE_NAME!r})")
    if data.get("universe_size") != UNIVERSE_SIZE:
        errors.append(f"universe_size={data.get('universe_size')!r} (expected {UNIVERSE_SIZE})")
    if data.get("symbol_list_checksum") != expected_checksum:
        errors.append("symbol_list_checksum does not match NIFTY_100_SYMBOLS")
    if errors:
        return ManifestValidation(
            ok=False,
            status="failed",
            message="Universe manifest mismatch: " + "; ".join(errors),
            data=data,
        )
    return ManifestValidation(
        ok=True,
        status="ok",
        message=f"Universe {UNIVERSE_NAME} ({UNIVERSE_SIZE}) initialized",
        data=data,
    )
