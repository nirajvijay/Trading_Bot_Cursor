"""API configuration from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PROD_PYTHON = os.environ.get("PROD_PYTHON", "/opt/nifty-radar/venv/bin/python")
PROD_APP_ROOT = Path(
    os.environ.get("PROD_APP_ROOT", "/opt/nifty-radar/current")
).expanduser()


# Host-persistent data root (outside release checkouts). Used for runtime caches.
def data_root() -> Path:
    return Path(
        os.environ.get("NIFTY_RADAR_DATA_ROOT", "/opt/nifty-radar/data")
    ).expanduser()


def local_data_dir() -> Path:
    """Persistent local market-data directory (instruments/historical/baselines/live).

    LOCAL_DATA_DIR env wins. Production (APP_ENV=production) or any host with
    NIFTY_RADAR_DATA_ROOT set uses <data_root>/local so data survives releases.
    Dev/test without those use ROOT/data/local.
    """
    override = os.environ.get("LOCAL_DATA_DIR")
    if override:
        return Path(override).expanduser()
    app_env = os.environ.get("APP_ENV", "").strip().lower()
    if app_env == "production" or os.environ.get("NIFTY_RADAR_DATA_ROOT"):
        return data_root() / "local"
    return ROOT / "data" / "local"


def live_db_path() -> Path:
    """Live 1-minute candle DB path under local_data_dir unless LIVE_DB_PATH set."""
    override = os.environ.get("LIVE_DB_PATH")
    if override:
        return Path(override).expanduser()
    return local_data_dir() / "nifty50_live_1m.db"


def runtime_cache_dir() -> Path:
    """Writable runtime caches (token check, checklist). Never under releases/.

    Production (APP_ENV=production) and any host with NIFTY_RADAR_DATA_ROOT set
    use <data_root>/runtime-cache. Dev/test without those use local_data_dir() so
    unit tests stay writable without touching /opt/nifty-radar/data.
    """
    override = os.environ.get("NIFTY_RADAR_RUNTIME_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    app_env = os.environ.get("APP_ENV", "").strip().lower()
    if app_env == "production" or os.environ.get("NIFTY_RADAR_DATA_ROOT"):
        return data_root() / "runtime-cache"
    return local_data_dir() / "runtime-cache"


# Snapshot defaults for import-time consumers; prefer helpers for dynamic reads.
DATA_ROOT = data_root()
LOCAL_DATA_DIR = local_data_dir()
RUNTIME_CACHE_DIR = runtime_cache_dir()

LIVE_DB_PATH = live_db_path()
INSTRUMENTS_DB_PATH = Path(
    os.environ.get("INSTRUMENTS_DB_PATH", str(LOCAL_DATA_DIR / "nifty50_instruments.db"))
)
BASELINES_DB_PATH = Path(
    os.environ.get("BASELINES_DB_PATH", str(LOCAL_DATA_DIR / "nifty50_baselines.db"))
)
HISTORICAL_DB_PATH = Path(
    os.environ.get("HISTORICAL_DB_PATH", str(LOCAL_DATA_DIR / "nifty50_historical.db"))
)
RUNNER_STATUS_FILE = Path(
    os.environ.get("RUNNER_STATUS_FILE", "/tmp/runner_status.json")
)

# Backward-compatible aliases (same paths as above).
LOCAL_INSTRUMENTS_DB_PATH = INSTRUMENTS_DB_PATH
LOCAL_HISTORICAL_DB_PATH = HISTORICAL_DB_PATH
LOCAL_BASELINES_DB_PATH = BASELINES_DB_PATH
TOKEN_CHECK_CACHE_PATH = RUNTIME_CACHE_DIR / "token_check.json"
