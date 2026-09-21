"""V1 PAPER clean-start helpers: archive legacy ledger, init PAPER-only namespace.

Does not switch production storage by itself. Cutover must be explicitly approved
and must abort if observation/trading runners are present or API writers are active.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from api import config
from trading_engine_broker import parse_timestamp_text
from trading_engine_store import TradingEngineStore

PAPER_NAMESPACE = "paper_v1"
_SERVICE_LIVE_KEYS = (
    "TRADING_ENGINE_LIVE_ORDERS",
    "NIFTY_RADAR_LIVE_WRITES_AUTHORIZED",
)
_SERVICE_LIVE_KEYSET = frozenset(_SERVICE_LIVE_KEYS)
_DEFAULT_API_UNIT = "nifty-radar-api.service"
_ACTIVE_DB_OWNER = "nifty-radar"
_ACTIVE_DB_GROUP = "nifty-radar"
_DISABLED_LIVE_ORDERS = frozenset({"0", "false", "no", "off"})
_DISABLED_LIVE_WRITES = frozenset({"0", "false", "no", "off"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_required_aware_instant(raw: object, *, field: str) -> datetime:
    """Parse a timezone-aware instant; reject missing/naive/malformed values."""
    parsed = parse_timestamp_text(raw)
    if parsed is None or parsed.tzinfo is None:
        raise ValueError(f"{field}_missing_or_malformed")
    return parsed.astimezone(timezone.utc)


def normalize_aware_instant(raw: object, *, field: str) -> str:
    return parse_required_aware_instant(raw, field=field).isoformat(timespec="seconds")


def max_aware_instant_iso(*raws: object, field: str = "instant") -> str:
    instants = [parse_required_aware_instant(raw, field=field) for raw in raws if raw]
    if not instants:
        raise ValueError(f"{field}_missing_or_malformed")
    return max(instants).isoformat(timespec="seconds")


def candidate_blocked_by_clean_start(
    candidate: Any, boundary_raw: Optional[str]
) -> bool:
    """True when candidate must not enter PAPER after clean-start.

    Missing or malformed created_at / trigger_exchange_ts are rejected (blocked).
    An old trigger inserted after reset is blocked via trigger_exchange_ts even if
    created_at is post-boundary.
    """
    if not boundary_raw:
        return False
    boundary = parse_required_aware_instant(boundary_raw, field="signal_not_before")
    created = parse_timestamp_text(getattr(candidate, "created_at", None))
    trigger = parse_timestamp_text(getattr(candidate, "trigger_exchange_ts", None))
    if created is None or created.tzinfo is None:
        return True
    if trigger is None or trigger.tzinfo is None:
        return True
    created_utc = created.astimezone(timezone.utc)
    trigger_utc = trigger.astimezone(timezone.utc)
    return created_utc < boundary or trigger_utc < boundary


def runners_present() -> list[str]:
    """Return matching runner command lines.

    Only ``pgrep`` exit status 1 means no matches. Any other discovery failure
    raises — never treat errors as a clear runway.
    """
    try:
        proc = subprocess.run(
            [
                "pgrep",
                "-af",
                "live_observation_runner|trading_engine_loop|live_trading_engine",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"runner_discovery_failed:{exc}") from exc
    if proc.returncode == 1:
        return []
    if proc.returncode != 0:
        raise RuntimeError(
            f"runner_discovery_failed:exit={proc.returncode}:{(proc.stderr or '').strip()}"
        )
    lines = []
    for line in (proc.stdout or "").splitlines():
        if "pgrep -af" in line:
            continue
        line = line.strip()
        if line:
            lines.append(line)
    return lines


def assert_no_runners() -> None:
    present = runners_present()
    if present:
        raise RuntimeError("cutover_aborted_runners_present:" + ";".join(present))


def _parse_env_file_keys(path: Path, keys: frozenset[str]) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"service_env_missing:{path}") from exc
    except PermissionError as exc:
        raise RuntimeError(f"service_env_unreadable:{path}") from exc
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in keys:
            out[key] = value.strip().strip('"').strip("'")
    return out


def discover_service_environment_files(
    *, unit: str = _DEFAULT_API_UNIT
) -> list[Path]:
    """Read EnvironmentFiles= from the actual systemd unit (not a hard-coded list)."""
    try:
        proc = subprocess.run(
            ["systemctl", "show", unit, "-p", "EnvironmentFiles", "--no-pager"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"service_env_discovery_failed:{exc}") from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"service_env_discovery_failed:exit={proc.returncode}:{(proc.stderr or '').strip()}"
        )
    files: list[Path] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.startswith("EnvironmentFiles="):
            raise RuntimeError(f"service_env_discovery_malformed:{line[:120]}")
        payload = line.split("=", 1)[1].strip()
        if not payload:
            continue
        # Entries look like: `/path/file (ignore_errors=no)`
        for token in payload.split():
            if token.startswith("(") or token.endswith(")"):
                continue
            files.append(Path(token))
    if not files:
        raise RuntimeError("service_environment_files_missing")
    return files


def _parse_explicit_disabled_flag(raw: str, *, key: str) -> bool:
    """Return True when the gate is explicitly disabled. Unknown values abort."""
    text = str(raw).strip().lower()
    if not text:
        raise RuntimeError(f"live_flag_empty:{key}")
    if key == "TRADING_ENGINE_LIVE_ORDERS":
        if text in _DISABLED_LIVE_ORDERS:
            return True
        if text in {"1", "true", "yes", "on"}:
            return False
        raise RuntimeError(f"live_flag_malformed:{key}:{text}")
    if key == "NIFTY_RADAR_LIVE_WRITES_AUTHORIZED":
        if text in _DISABLED_LIVE_WRITES:
            return True
        if text in {"1", "true", "yes", "on"}:
            return False
        raise RuntimeError(f"live_flag_malformed:{key}:{text}")
    raise RuntimeError(f"live_flag_unknown_key:{key}")


def resolve_live_flags(
    *,
    env_files: Optional[Sequence[Path]] = None,
    process_env: Optional[Mapping[str, str]] = None,
    unit: str = _DEFAULT_API_UNIT,
) -> dict[str, Any]:
    """Resolve LIVE gates from verified service EnvironmentFiles.

    Missing, unreadable, or malformed evidence aborts. Disabled is never assumed
    from absent keys. ``env_files`` may be passed explicitly by tests; otherwise
    files are discovered from the live systemd unit.
    """
    del process_env  # Invoking shell defaults are not authoritative for cutover.
    files = (
        discover_service_environment_files(unit=unit)
        if env_files is None
        else [Path(p) for p in env_files]
    )
    if not files:
        raise RuntimeError("service_environment_files_missing")
    merged: dict[str, str] = {}
    for path in files:
        merged.update(_parse_env_file_keys(path, _SERVICE_LIVE_KEYSET))
    missing = [k for k in _SERVICE_LIVE_KEYS if k not in merged]
    if missing:
        raise RuntimeError("live_flag_missing:" + ",".join(missing))
    live_orders_disabled = _parse_explicit_disabled_flag(
        merged["TRADING_ENGINE_LIVE_ORDERS"], key="TRADING_ENGINE_LIVE_ORDERS"
    )
    live_writes_disabled = _parse_explicit_disabled_flag(
        merged["NIFTY_RADAR_LIVE_WRITES_AUTHORIZED"],
        key="NIFTY_RADAR_LIVE_WRITES_AUTHORIZED",
    )
    return {
        "live_orders_enabled": not live_orders_disabled,
        "live_writes_authorized": not live_writes_disabled,
        "env_files": [str(p) for p in files],
        "raw": {k: merged[k] for k in _SERVICE_LIVE_KEYS},
    }


def capture_unit_process_live_flags(
    *, unit: str = _DEFAULT_API_UNIT
) -> Optional[dict[str, Any]]:
    """Capture LIVE flags from the running unit process when available."""
    info = inspect_api_service(unit=unit)
    if info["ActiveState"] != "active":
        return None
    pid = int(info.get("MainPID") or 0)
    if pid <= 0:
        raise RuntimeError(f"service_mainpid_missing:{unit}")
    environ_path = Path(f"/proc/{pid}/environ")
    try:
        raw = environ_path.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"service_process_env_unreadable:{pid}:{exc}") from exc
    env: dict[str, str] = {}
    for item in raw.split(b"\0"):
        if not item or b"=" not in item:
            continue
        key_b, val_b = item.split(b"=", 1)
        try:
            key = key_b.decode("utf-8")
            val = val_b.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if key in _SERVICE_LIVE_KEYSET:
            env[key] = val
    missing = [k for k in _SERVICE_LIVE_KEYS if k not in env]
    if missing:
        raise RuntimeError("process_live_flag_missing:" + ",".join(missing))
    return {
        "pid": pid,
        "live_orders_enabled": not _parse_explicit_disabled_flag(
            env["TRADING_ENGINE_LIVE_ORDERS"], key="TRADING_ENGINE_LIVE_ORDERS"
        ),
        "live_writes_authorized": not _parse_explicit_disabled_flag(
            env["NIFTY_RADAR_LIVE_WRITES_AUTHORIZED"],
            key="NIFTY_RADAR_LIVE_WRITES_AUTHORIZED",
        ),
        "raw": env,
    }


def assert_live_disabled(
    *,
    env_files: Optional[Sequence[Path]] = None,
    process_env: Optional[Mapping[str, str]] = None,
    unit: str = _DEFAULT_API_UNIT,
    capture_running_process: bool = True,
) -> dict[str, Any]:
    """Abort unless BOTH LIVE gates are explicitly disabled in service config."""
    del process_env
    flags = resolve_live_flags(env_files=env_files, unit=unit)
    if flags["live_orders_enabled"]:
        raise RuntimeError("cutover_aborted_live_orders_enabled")
    if flags["live_writes_authorized"]:
        raise RuntimeError("cutover_aborted_live_writes_authorized")
    process_flags = None
    if capture_running_process:
        process_flags = capture_unit_process_live_flags(unit=unit)
        if process_flags is not None:
            if process_flags["live_orders_enabled"]:
                raise RuntimeError("cutover_aborted_process_live_orders_enabled")
            if process_flags["live_writes_authorized"]:
                raise RuntimeError("cutover_aborted_process_live_writes_authorized")
    return {"service": flags, "process": process_flags}


def inspect_api_service(*, unit: str = _DEFAULT_API_UNIT) -> dict[str, str]:
    """Return LoadState/ActiveState/SubState/MainPID or abort on inspection failure."""
    try:
        proc = subprocess.run(
            [
                "systemctl",
                "show",
                unit,
                "-p",
                "LoadState",
                "-p",
                "ActiveState",
                "-p",
                "SubState",
                "-p",
                "MainPID",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"api_service_inspection_failed:{exc}") from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"api_service_inspection_failed:exit={proc.returncode}:{(proc.stderr or '').strip()}"
        )
    props: dict[str, str] = {}
    for line in (proc.stdout or "").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        props[key.strip()] = value.strip()
    for required in ("LoadState", "ActiveState", "SubState", "MainPID"):
        if required not in props or props[required] == "":
            raise RuntimeError(f"api_service_property_missing:{required}")
    if props["LoadState"] != "loaded":
        raise RuntimeError(f"api_service_not_loaded:{props['LoadState']}")
    return props


def api_service_is_active(*, unit: str = _DEFAULT_API_UNIT) -> bool:
    """True only when ActiveState is confirmed active. Unknown states raise."""
    state = inspect_api_service(unit=unit)["ActiveState"]
    if state == "active":
        return True
    if state == "inactive":
        return False
    raise RuntimeError(f"api_service_state_unknown:{state}")


def assert_api_writers_quiesced(*, unit: str = _DEFAULT_API_UNIT) -> None:
    state = inspect_api_service(unit=unit)["ActiveState"]
    if state == "inactive":
        return
    if state == "active":
        raise RuntimeError(f"cutover_aborted_api_writers_active:{unit}")
    raise RuntimeError(f"cutover_aborted_api_writers_state_unknown:{unit}:{state}")


def quiesce_api_writers(*, unit: str = _DEFAULT_API_UNIT) -> dict[str, Any]:
    """Capture process LIVE flags when running, stop API writers, require inactive."""
    pre = assert_live_disabled(unit=unit, capture_running_process=True)
    try:
        subprocess.run(
            ["systemctl", "stop", unit], check=True, capture_output=True, text=True
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"api_quiesce_failed:{exc}") from exc
    assert_api_writers_quiesced(unit=unit)
    return pre


def paper_account_lock_path(account: Path) -> Path:
    account = Path(account)
    return account.with_suffix(account.suffix + ".lock")


def apply_active_db_ownership(
    paths: Sequence[Path],
    *,
    user: str = _ACTIVE_DB_OWNER,
    group: str = _ACTIVE_DB_GROUP,
) -> None:
    """Assign nifty-radar ownership to active ledger/account/lock/WAL/SHM files.

    Archives remain root-private and must not be passed here.
    """
    import grp
    import pwd

    try:
        uid = pwd.getpwnam(user).pw_uid
        gid = grp.getgrnam(group).gr_gid
    except KeyError as exc:
        raise RuntimeError(f"active_db_owner_missing:{user}:{group}") from exc
    seen: set[Path] = set()
    for path in paths:
        target = Path(path)
        candidates = [target, paper_account_lock_path(target)]
        for suffix in ("-wal", "-shm"):
            candidates.append(Path(str(target) + suffix))
        for item in candidates:
            if item in seen or not item.exists():
                continue
            seen.add(item)
            os.chown(item, uid, gid)
            if item.suffix == ".lock" or str(item).endswith(".lock"):
                # Lock must remain service-writable; do not weaken below 0644.
                os.chmod(item, 0o644)
            else:
                os.chmod(item, 0o644)


def _inspect_paper_account(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"paper_account_regular_file_required:{path}")
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            try:
                row = db.execute(
                    "SELECT snapshot FROM paper_account WHERE id = 1"
                ).fetchone()
            except sqlite3.Error as exc:
                raise RuntimeError(f"paper_account_unreadable:{path}:{exc}") from exc
    except sqlite3.Error as exc:
        raise RuntimeError(f"paper_account_open_failed:{path}:{exc}") from exc
    if row is None:
        raise RuntimeError(f"paper_account_uninitialized:{path}")
    try:
        state = json.loads(row[0])
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"paper_account_snapshot_invalid:{path}") from exc
    if state.get("version") != 1:
        raise RuntimeError(f"paper_account_version_unknown:{path}")
    account_id = str(state.get("account_id") or "").strip() or None
    if not account_id:
        raise RuntimeError(f"paper_account_id_missing:{path}")
    orders = state.get("orders") or []
    if not isinstance(orders, list):
        raise RuntimeError(f"paper_account_orders_invalid:{path}")
    return {
        "path": str(path.resolve()),
        "version": 1,
        "account_id": account_id,
        "order_count": len(orders),
        "empty": len(orders) == 0,
    }


def _table_counts(db: sqlite3.Connection, table: str) -> int:
    try:
        row = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    except sqlite3.Error as exc:
        raise RuntimeError(f"ledger_inventory_failed:{table}:{exc}") from exc
    return int(row[0])


def _inspect_ledger_identity(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"ledger_regular_file_required:{path}")
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            try:
                meta = db.execute(
                    "SELECT namespace, signal_not_before, legacy_archive_label, "
                    "paper_account_id, initialization_complete, updated_at "
                    "FROM engine_account_meta WHERE id = 1"
                ).fetchone()
            except sqlite3.Error as exc:
                raise RuntimeError(f"ledger_meta_unreadable:{path}:{exc}") from exc
            trades = _table_counts(db, "trades")
            commands = _table_counts(db, "engine_commands")
    except sqlite3.Error as exc:
        raise RuntimeError(f"ledger_open_failed:{path}:{exc}") from exc
    if meta is None:
        raise RuntimeError(f"paper_ledger_unstamped:{path}")
    namespace = str(meta["namespace"] or "")
    if namespace != PAPER_NAMESPACE:
        raise RuntimeError(f"paper_ledger_namespace_mismatch:{namespace}")
    boundary = meta["signal_not_before"]
    parse_required_aware_instant(boundary, field="signal_not_before")
    keys = set(meta.keys())
    account_id = None
    if "paper_account_id" in keys and meta["paper_account_id"] is not None:
        account_id = str(meta["paper_account_id"]).strip() or None
    complete = False
    if "initialization_complete" in keys and meta["initialization_complete"] is not None:
        complete = bool(int(meta["initialization_complete"]))
    return {
        "path": str(path.resolve()),
        "namespace": namespace,
        "signal_not_before": str(boundary),
        "legacy_archive_label": (
            None
            if meta["legacy_archive_label"] is None
            else str(meta["legacy_archive_label"])
        ),
        "paper_account_id": account_id,
        "initialization_complete": complete,
        "trade_count": trades,
        "command_count": commands,
        "updated_at": str(meta["updated_at"]),
    }


def validate_paper_v1_pair(
    ledger: Path, account: Optional[Path] = None
) -> dict[str, Any]:
    """Validate completed ledger/account identity. Never creates files."""
    ledger = Path(ledger)
    account = Path(account or config.trading_engine_paper_account_db_path(ledger))
    if not ledger.exists():
        raise RuntimeError("paper_v1_ledger_not_initialized")
    if not account.exists():
        raise RuntimeError("paper_account_missing")
    identity = _inspect_ledger_identity(ledger)
    if not identity.get("initialization_complete"):
        raise RuntimeError("paper_v1_initialization_incomplete")
    account_id = identity.get("paper_account_id")
    if not account_id:
        raise RuntimeError("paper_v1_account_id_missing")
    account_info = _inspect_paper_account(account)
    if account_info["account_id"] != account_id:
        raise RuntimeError(
            f"paper_account_id_mismatch:ledger={account_id}:account={account_info['account_id']}"
        )
    return {"ledger": identity, "account": account_info}


def require_paper_v1_ready(store: TradingEngineStore, path: Optional[Path] = None) -> None:
    """Fail closed unless PAPER-only path/namespace has a completed matching pair."""
    db_path = Path(path or store.db_path)
    applies = config.is_paper_only_ledger_path(db_path) or store.is_paper_v1_namespace()
    if not applies:
        return
    if not store.is_paper_v1_namespace():
        raise RuntimeError("paper_v1_namespace_missing_or_invalid")
    validate_paper_v1_pair(db_path)


def open_paper_broker_for_ledger(
    ledger: Path,
    *,
    quote_provider,
    total_capital: float = 300000.0,
):
    """Open the durable PAPER account for a verified ledger without creating it."""
    from trading_engine_paper import PaperBroker

    ledger = Path(ledger)
    pair = validate_paper_v1_pair(ledger)
    account = Path(pair["account"]["path"])
    return PaperBroker(
        account,
        quote_provider=quote_provider,
        total_capital=total_capital,
        allow_create=False,
        account_id=pair["ledger"]["paper_account_id"],
    )


def _validate_init_targets(
    ledger: Path,
    account: Path,
    *,
    allow_resume: bool,
) -> Optional[dict[str, Any]]:
    """Validate both targets before writing either. Never assume empty on error."""
    ledger_exists = ledger.exists()
    account_exists = account.exists()
    if ledger_exists ^ account_exists:
        raise FileExistsError(
            f"paper_targets_partially_initialized:ledger={ledger_exists}:account={account_exists}"
        )
    if not ledger_exists and not account_exists:
        return None
    if not allow_resume:
        raise FileExistsError(f"paper_targets_already_exist:{ledger}:{account}")
    return validate_paper_v1_pair(ledger, account)


def archive_legacy_trading_ledger(
    *,
    archive_root: Path,
    legacy_db: Optional[Path] = None,
    label: str = "legacy-unresolved-pre-v1-paper-cleanstart",
) -> dict[str, Any]:
    """SQLite backup-API copy of legacy ledger. Never moves/deletes/replaces source."""
    assert_no_runners()
    assert_live_disabled(capture_running_process=True)
    assert_api_writers_quiesced()
    source = Path(legacy_db or config.trading_engine_legacy_db_path())
    if not source.is_file() or source.is_symlink():
        raise ValueError("legacy_ledger_regular_file_required")
    if config.is_paper_only_ledger_path(source):
        raise ValueError("refusing_to_archive_paper_only_ledger_as_legacy")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest_dir = Path(archive_root) / f"{label}-{stamp}"
    dest_dir.mkdir(parents=True, mode=0o700)
    dest = dest_dir / "trading_engine.db"
    if dest.exists():
        raise FileExistsError(str(dest))

    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as original:
        with sqlite3.connect(dest) as copied:
            original.backup(copied)
    os.chmod(dest, 0o600)

    with sqlite3.connect(f"file:{dest}?mode=ro", uri=True) as db:
        trade_rows = db.execute(
            "SELECT trade_id, symbol, status, qty, session_date, run_id, "
            "entry_live_orders_enabled FROM trades ORDER BY created_at"
        ).fetchall()
        cmd_n = db.execute("SELECT COUNT(*) FROM engine_commands").fetchone()[0]
        open_like = [
            {
                "trade_id": r[0],
                "symbol": r[1],
                "status": r[2],
                "qty": r[3],
                "session_date": r[4],
                "run_id": r[5],
                "entry_live_orders_enabled": r[6],
            }
            for r in trade_rows
            if str(r[2]) not in {"closed", "skipped", "rejected"}
        ]

    manifest = {
        "label": label,
        "classification": "legacy/unresolved",
        "created_at": _utc_now(),
        "source_path": str(source.resolve()),
        "archive_db": str(dest.resolve()),
        "note": (
            "Preserved accounting history only. Not part of the active V1 PAPER "
            "ledger. Do not invent fills/P&L or delete. Do not load into PAPER "
            "positions, risk totals, or session reports."
        ),
        "trade_count": len(trade_rows),
        "command_count": int(cmd_n),
        "unresolved_or_open_rows": open_like,
        "rollback_policy": (
            "Preserve both legacy archive and any new PAPER ledger activity. "
            "No automatic DB restore, merge, or replay of legacy commands."
        ),
        "ownership": "root-private archive (mode 0700/0600); original untouched",
    }
    man_path = dest_dir / "MANIFEST.json"
    man_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(man_path, 0o600)
    readme = dest_dir / "README.md"
    readme.write_text(
        "# Legacy trading ledger archive\n\n"
        "Classification: **legacy/unresolved**.\n\n"
        "This archive is a read-only SQLite backup of the pre-clean-start "
        "`trading_engine.db`. The original host file remains untouched.\n\n"
        "The active V1 PAPER ledger is `trading_engine_v1_paper.db` and must not "
        "import these rows into positions, risk, recovery, or session reports.\n",
        encoding="utf-8",
    )
    os.chmod(readme, 0o600)
    os.chmod(dest_dir, 0o700)
    return manifest


def initialize_v1_paper_ledger(
    *,
    paper_db: Optional[Path] = None,
    signal_not_before: Optional[str] = None,
    legacy_archive_label: Optional[str] = None,
    create_empty_paper_account: bool = True,
    allow_resume: bool = False,
    owner_user: str = _ACTIVE_DB_OWNER,
    owner_group: str = _ACTIVE_DB_GROUP,
    apply_ownership: bool = True,
    env_files: Optional[Sequence[Path]] = None,
) -> dict[str, Any]:
    """Create a genuinely empty PAPER-only ledger + account, or resume a verified pair.

    Refuses existing targets by default. Validates both targets before writing either.
    Never treats schema errors as empty. Preserves all existing files.
    """
    assert_no_runners()
    assert_live_disabled(env_files=env_files, capture_running_process=env_files is None)
    assert_api_writers_quiesced()
    path = Path(paper_db or config.trading_engine_v1_paper_db_path())
    if not config.is_paper_only_ledger_path(path):
        raise ValueError("paper_db_must_be_paper_only_namespace_path")
    account = config.trading_engine_paper_account_db_path(path)

    resumed = _validate_init_targets(path, account, allow_resume=allow_resume)
    if resumed is not None:
        store = TradingEngineStore(path)
        try:
            require_paper_v1_ready(store, path)
            meta = store.account_meta()
        finally:
            store.close()
        assert meta is not None
        return {
            "paper_ledger": str(path.resolve()),
            "paper_account": str(account.resolve()),
            "account_meta": meta,
            "resumed": True,
            "active_api_path_helper": str(config.trading_engine_db_path()),
            "legacy_path_untouched": str(config.trading_engine_legacy_db_path()),
            "resume_inventory": resumed,
        }

    if not create_empty_paper_account:
        raise ValueError("create_empty_paper_account_required_for_bootstrap")

    boundary = normalize_aware_instant(
        signal_not_before or _utc_now(), field="signal_not_before"
    )
    account_id = str(uuid.uuid4())
    store = TradingEngineStore(path, allow_create=True)
    try:
        store.ensure_paper_v1_namespace(
            signal_not_before=boundary,
            legacy_archive_label=legacy_archive_label,
            paper_account_id=account_id,
            initialization_complete=False,
            actor="clean_start",
        )
        # Incomplete ledger must not be treated as ready.
        try:
            require_paper_v1_ready(store, path)
            raise RuntimeError("incomplete_ledger_incorrectly_ready")
        except RuntimeError as exc:
            if "incomplete" not in str(exc) and "missing" not in str(exc):
                raise

        if account.exists():
            raise FileExistsError(f"paper_account_exists:{account}")
        from trading_engine_paper import PaperBroker

        def _no_quote(_symbol: str):
            return None

        broker = PaperBroker(
            account,
            quote_provider=_no_quote,
            total_capital=300000.0,
            allow_create=True,
            account_id=account_id,
        )
        broker.close()
        account_info = _inspect_paper_account(account)
        if not account_info["empty"]:
            raise RuntimeError(f"paper_account_not_empty_after_init:{account}")
        if account_info["account_id"] != account_id:
            raise RuntimeError("paper_account_id_mismatch_after_init")

        meta = store.finalize_paper_v1_initialization(
            paper_account_id=account_id, actor="clean_start"
        )
        require_paper_v1_ready(store, path)
    finally:
        store.close()

    if apply_ownership:
        apply_active_db_ownership(
            [path, account], user=owner_user, group=owner_group
        )

    return {
        "paper_ledger": str(path.resolve()),
        "paper_account": str(account.resolve()),
        "account_meta": meta,
        "resumed": False,
        "active_api_path_helper": str(config.trading_engine_db_path()),
        "legacy_path_untouched": str(config.trading_engine_legacy_db_path()),
    }


def refuse_live_on_paper_ledger(trading_db: Path) -> None:
    """Shared LIVE isolation check used by engine entrypoints and tests."""
    path = Path(trading_db)
    if config.is_paper_only_ledger_path(path):
        raise RuntimeError(f"LIVE execution refused on PAPER-only ledger namespace ({path})")
    if path.exists():
        store = TradingEngineStore(path)
        try:
            if store.is_paper_v1_namespace():
                raise RuntimeError(
                    f"LIVE execution refused on PAPER-only ledger namespace ({path})"
                )
        finally:
            store.close()
