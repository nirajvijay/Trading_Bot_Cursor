"""Run data collectors/generators into this project's local data directory only."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from api import config
from api.db import open_readonly
from nse_trading_calendar import prior_nse_trading_session

ROOT = config.ROOT
GENERATION_TIMEOUT_SECONDS = 3600

TASK_NAMES = frozenset({"instruments", "historical", "five-minute", "baselines"})


def _today_ist() -> str:
    return datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")


def prior_trading_session(
    historical_db: Path,
    session_date: Optional[str] = None,
) -> Optional[str]:
    """Latest completed session in historical DB strictly before session_date."""
    if not historical_db.exists():
        return None
    date = session_date or _today_ist()
    try:
        conn = open_readonly(historical_db)
    except FileNotFoundError:
        return None
    try:
        row = conn.execute(
            """
            SELECT MAX(substr(candle_time, 1, 10))
            FROM candles
            WHERE substr(candle_time, 1, 10) < ?
            """,
            (date,),
        ).fetchone()
        if row and row[0]:
            return str(row[0])
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    return None


def _instruments_source_db() -> Path:
    return config.INSTRUMENTS_DB_PATH


def _ensure_local_dir() -> None:
    config.LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)


def _abs_db(path: Path | str) -> str:
    """Return an absolute, resolved DB path string for CLI / copy commands."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    return str(candidate.resolve())


def _copy_command(script: str, *argv: str) -> str:
    """Fully-qualified SSH-safe copy command (any cwd).

    Uses prod venv python, cd into PROD_APP_ROOT, absolute script + argv paths.
    """
    app_root = str(config.PROD_APP_ROOT)
    script_path = str(Path(app_root) / script)
    parts = [
        f"cd {app_root}",
        f"&& {config.PROD_PYTHON}",
        script_path,
        *argv,
    ]
    return " ".join(parts)


def _path_allowed_for_generation(path: Path | str) -> bool:
    """Reject release trees and anything outside the configured local data dir."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    resolved = candidate.resolve()
    normalized = str(resolved).replace("\\", "/")
    if "/releases/" in normalized:
        return False
    local_resolved = config.LOCAL_DATA_DIR.resolve()
    try:
        resolved.relative_to(local_resolved)
    except ValueError:
        return False
    return True


def get_generate_command(task: str, session_date: Optional[str] = None) -> str:
    """Return the CLI command string for display/copy.

    Does not create directories — checklist/read paths must stay safe under
    ProtectSystem=strict immutable releases.
    """
    if task == "instruments":
        return _copy_command(
            "instrument_collector.py",
            "--db",
            _abs_db(config.LOCAL_INSTRUMENTS_DB_PATH),
        )
    if task == "historical":
        return _copy_command(
            "historical_collector.py",
            "--db",
            _abs_db(config.LOCAL_HISTORICAL_DB_PATH),
            "--instruments-db",
            _abs_db(_instruments_source_db()),
        )
    if task == "five-minute":
        return _copy_command(
            "five_minute_candle_generator.py",
            "--db",
            _abs_db(config.LOCAL_HISTORICAL_DB_PATH),
        )
    if task == "baselines":
        as_of = prior_nse_trading_session(session_date or _today_ist())
        argv = [
            "--historical-db",
            _abs_db(config.LOCAL_HISTORICAL_DB_PATH),
            "--baselines-db",
            _abs_db(config.LOCAL_BASELINES_DB_PATH),
        ]
        if as_of:
            argv.extend(["--as-of", as_of])
        return _copy_command("baseline_generator.py", *argv)
    raise ValueError(f"Unknown task: {task}")


def _build_command(task: str, session_date: Optional[str] = None) -> List[str]:
    _ensure_local_dir()
    if task == "instruments":
        command = [
            sys.executable,
            "instrument_collector.py",
            "--db",
            _abs_db(config.LOCAL_INSTRUMENTS_DB_PATH),
        ]
    elif task == "historical":
        command = [
            sys.executable,
            "historical_collector.py",
            "--db",
            _abs_db(config.LOCAL_HISTORICAL_DB_PATH),
            "--instruments-db",
            _abs_db(_instruments_source_db()),
        ]
    elif task == "five-minute":
        if not config.LOCAL_HISTORICAL_DB_PATH.exists():
            raise ValueError("Local historical database not found — generate historical candles first")
        command = [
            sys.executable,
            "five_minute_candle_generator.py",
            "--db",
            _abs_db(config.LOCAL_HISTORICAL_DB_PATH),
        ]
    elif task == "baselines":
        if not config.LOCAL_HISTORICAL_DB_PATH.exists():
            raise ValueError("Local historical database not found — generate historical candles first")
        as_of = prior_nse_trading_session(session_date or _today_ist())
        if as_of is None:
            raise ValueError(
                f"No prior NSE trading session found before {session_date or _today_ist()}"
            )
        command = [
            sys.executable,
            "baseline_generator.py",
            "--historical-db",
            _abs_db(config.LOCAL_HISTORICAL_DB_PATH),
            "--baselines-db",
            _abs_db(config.LOCAL_BASELINES_DB_PATH),
            "--as-of",
            as_of,
        ]
    else:
        raise ValueError(f"Unknown task: {task}")

    for arg in command:
        if arg.endswith(".db") and not _path_allowed_for_generation(arg):
            raise ValueError(f"Refusing to write outside local data dir: {arg}")
    return command


def run_local_generation(task: str, session_date: Optional[str] = None) -> Tuple[bool, str]:
    """Run a collector/generator writing only to the configured local data dir."""
    if task not in TASK_NAMES:
        return False, f"Unknown generation task: {task}"

    from api.services.generation_lock import (
        GenerationLockBusy,
        acquire_generation_lock,
        release_generation_lock,
    )

    try:
        command = _build_command(task, session_date=session_date)
    except ValueError as exc:
        return False, str(exc)

    # Safety: all output paths must be under LOCAL_DATA_DIR and never under releases/
    for arg in command:
        if arg.endswith(".db") and not _path_allowed_for_generation(arg):
            return False, f"Refusing to write outside local data dir: {Path(arg).resolve()}"

    local_dir = config.LOCAL_DATA_DIR
    try:
        lock_file = acquire_generation_lock(task, local_data_dir=local_dir)
    except GenerationLockBusy as exc:
        return False, str(exc)

    try:
        try:
            result = subprocess.run(
                command,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=GENERATION_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return False, f"Generation timed out after {GENERATION_TIMEOUT_SECONDS}s"
        except OSError as exc:
            return False, f"Failed to start generation: {exc}"

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "Unknown error").strip()
            tail = "\n".join(detail.splitlines()[-8:])
            return False, f"Generation failed (exit {result.returncode}):\n{tail}"

        from api.services.checklist_cache import invalidate_checklist_cache

        # Clear persistent runtime-cache (production) and any LOCAL_DATA_DIR
        # colocated cache used by tests / older layouts.
        invalidate_checklist_cache()
        invalidate_checklist_cache(local_data_dir=local_dir)
        summary = (result.stdout or "").strip()
        tail = "\n".join(summary.splitlines()[-5:]) if summary else "Completed successfully"
        return True, tail
    finally:
        release_generation_lock(lock_file, local_data_dir=local_dir)


def generate_action_for_task(task: str, *, available: bool, reason: str = "") -> Dict[str, object]:
    label_map = {
        "instruments": "Generate Instruments",
        "historical": "Generate Historical DB",
        "five-minute": "Generate 5-Minute Candles",
        "baselines": "Generate Baselines",
    }
    return {
        "available": available,
        "label": label_map.get(task, "Generate"),
        "task": task,
        "reason": reason or None,
    }
