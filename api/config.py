"""API configuration from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# All databases live under this project (backend/data/). No external symlinks.
LOCAL_DATA_DIR = Path(os.environ.get("LOCAL_DATA_DIR", str(ROOT / "data" / "local")))
LIVE_DB_PATH = Path(os.environ.get("LIVE_DB_PATH", str(ROOT / "data" / "nifty50_live_1m.db")))
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
TOKEN_CHECK_CACHE_PATH = LOCAL_DATA_DIR / "token_check.json"
