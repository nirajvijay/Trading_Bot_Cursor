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

ROOT = config.ROOT
LOCAL_DATA_DIR = config.LOCAL_DATA_DIR
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
    LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def get_generate_command(task: str, session_date: Optional[str] = None) -> str:
    """Return the CLI command string for display/copy."""
    _ensure_local_dir()
    if task == "instruments":
        return (
            f"python3 instrument_collector.py --db {_relative(config.LOCAL_INSTRUMENTS_DB_PATH)}"
        )
    if task == "historical":
        return (
            f"python3 historical_collector.py --db {_relative(config.LOCAL_HISTORICAL_DB_PATH)} "
            f"--instruments-db {_relative(_instruments_source_db())}"
        )
    if task == "five-minute":
        return (
            f"python3 five_minute_candle_generator.py --db {_relative(config.LOCAL_HISTORICAL_DB_PATH)}"
        )
    if task == "baselines":
        as_of = prior_trading_session(config.LOCAL_HISTORICAL_DB_PATH, session_date)
        as_of_flag = f" --as-of {as_of}" if as_of else ""
        return (
            f"python3 baseline_generator.py "
            f"--historical-db {_relative(config.LOCAL_HISTORICAL_DB_PATH)} "
            f"--baselines-db {_relative(config.LOCAL_BASELINES_DB_PATH)}"
            f"{as_of_flag}"
        )
    raise ValueError(f"Unknown task: {task}")


def _build_command(task: str, session_date: Optional[str] = None) -> List[str]:
    _ensure_local_dir()
    if task == "instruments":
        return [
            sys.executable,
            "instrument_collector.py",
            "--db",
            str(config.LOCAL_INSTRUMENTS_DB_PATH),
        ]
    if task == "historical":
        return [
            sys.executable,
            "historical_collector.py",
            "--db",
            str(config.LOCAL_HISTORICAL_DB_PATH),
            "--instruments-db",
            str(_instruments_source_db()),
        ]
    if task == "five-minute":
        if not config.LOCAL_HISTORICAL_DB_PATH.exists():
            raise ValueError("Local historical database not found — generate historical candles first")
        return [
            sys.executable,
            "five_minute_candle_generator.py",
            "--db",
            str(config.LOCAL_HISTORICAL_DB_PATH),
        ]
    if task == "baselines":
        if not config.LOCAL_HISTORICAL_DB_PATH.exists():
            raise ValueError("Local historical database not found — generate historical candles first")
        as_of = prior_trading_session(config.LOCAL_HISTORICAL_DB_PATH, session_date)
        if as_of is None:
            raise ValueError(
                "No completed trading session found before "
                f"{session_date or _today_ist()} in local historical DB"
            )
        return [
            sys.executable,
            "baseline_generator.py",
            "--historical-db",
            str(config.LOCAL_HISTORICAL_DB_PATH),
            "--baselines-db",
            str(config.LOCAL_BASELINES_DB_PATH),
            "--as-of",
            as_of,
        ]
    raise ValueError(f"Unknown task: {task}")


def run_local_generation(task: str, session_date: Optional[str] = None) -> Tuple[bool, str]:
    """Run a collector/generator writing only to backend/data/local/."""
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

    # Safety: all output paths must be under LOCAL_DATA_DIR
    for arg in command:
        if arg.endswith(".db"):
            path = Path(arg)
            if not path.is_absolute():
                path = ROOT / path
            resolved = path.resolve()
            local_resolved = LOCAL_DATA_DIR.resolve()
            if not str(resolved).startswith(str(local_resolved)):
                return False, f"Refusing to write outside local data dir: {resolved}"

    try:
        lock_file = acquire_generation_lock(task, local_data_dir=LOCAL_DATA_DIR)
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

        summary = (result.stdout or "").strip()
        tail = "\n".join(summary.splitlines()[-5:]) if summary else "Completed successfully"
        return True, tail
    finally:
        release_generation_lock(lock_file, local_data_dir=LOCAL_DATA_DIR)


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
