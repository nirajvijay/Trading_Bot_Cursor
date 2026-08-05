"""Pydantic schemas for pre-market checklist API."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

ChecklistStatus = Literal["not_checked", "ok", "warning", "failed", "needs_update"]


class GenerateAction(BaseModel):
    available: bool
    label: str
    task: Optional[str] = None
    reason: Optional[str] = None


class GenerateResponse(BaseModel):
    success: bool
    message: str
    task: str


class DatabaseStatus(BaseModel):
    name: str
    path: str
    exists: bool
    readable: bool
    scope: str = "local"


class SuggestedCommands(BaseModel):
    runner: str = "python3 live_observation_runner.py --status-file /tmp/runner_status.json"
    instrument_collector: str = "python3 instrument_collector.py"
    historical_collector: str = "python3 historical_collector.py"
    baseline_generator: str = "python3 baseline_generator.py"
    five_minute_generator: str = "python3 five_minute_candle_generator.py"
    offline_validation: str = "python3 -m unittest discover -s tests -v"
    startup: List[str] = Field(
        default_factory=lambda: [
            "uvicorn api.main:app --host 127.0.0.1 --port 8000",
            (
                "RUNNER_STATUS_FILE=/tmp/runner_status.json "
                "python3 live_observation_runner.py --status-file /tmp/runner_status.json"
            ),
        ]
    )


class KiteAuthCheck(BaseModel):
    status: ChecklistStatus
    message: str
    api_key_configured: bool = False
    api_secret_configured: bool = False
    access_token_present: bool = False
    masked_access_token: Optional[str] = None
    token_validated_today: bool = False
    token_checked_at: Optional[str] = None
    copy_command: str = "python3 login.py --check-token"
    generate_action: Optional[GenerateAction] = None


class InstrumentsCheck(BaseModel):
    status: ChecklistStatus
    message: str
    instruments_count: int = 0
    expected_count: int = 50
    tick_size_count: int = 0
    last_updated: Optional[str] = None
    missing_symbols: List[str] = Field(default_factory=list)
    copy_command: str = "python3 instrument_collector.py"
    generate_action: Optional[GenerateAction] = None


class HistoricalCandlesCheck(BaseModel):
    status: ChecklistStatus
    message: str
    latest_date: Optional[str] = None
    symbols_covered: int = 0
    expected_count: int = 50
    missing_count: int = 0
    missing_symbols_sample: List[str] = Field(default_factory=list)
    copy_command: str = "python3 historical_collector.py"
    db_path: Optional[str] = None
    generate_action: Optional[GenerateAction] = None


class BaselinesCheck(BaseModel):
    status: ChecklistStatus
    message: str
    baseline_as_of: Optional[str] = None
    expected_as_of: Optional[str] = None
    symbols_covered: int = 0
    expected_count: int = 50
    reliable_count: int = 0
    last_generated_at: Optional[str] = None
    copy_command: str = "python3 baseline_generator.py"
    db_path: Optional[str] = None
    generate_action: Optional[GenerateAction] = None


class FiveMinuteCandlesCheck(BaseModel):
    status: ChecklistStatus
    message: str
    latest_date: Optional[str] = None
    symbols_covered: int = 0
    expected_count: int = 50
    ema_seed_ready: int = 0
    ema_seed_missing: int = 0
    copy_command: str = "python3 five_minute_candle_generator.py"
    generate_action: Optional[GenerateAction] = None


class OfflineChecksCheck(BaseModel):
    status: ChecklistStatus
    message: str
    api_health: str = "unknown"
    database_readable: bool = False
    databases: List[DatabaseStatus] = Field(default_factory=list)
    missing_databases: List[str] = Field(default_factory=list)
    radar_row_count: int = 0
    copy_command: str = "python3 -m unittest discover -s tests -v"
    generate_action: Optional[GenerateAction] = None


class DashboardReadinessCheck(BaseModel):
    status: ChecklistStatus
    message: str
    api_reachable: bool = False
    latest_session: Optional[str] = None
    market_hour_trial_ready: bool = False
    trial_ready_reason: str = ""
    copy_command: str = ""
    generate_action: Optional[GenerateAction] = None


class ChecklistAreas(BaseModel):
    kite_auth: KiteAuthCheck
    instruments: InstrumentsCheck
    historical_candles: HistoricalCandlesCheck
    baselines: BaselinesCheck
    five_minute_candles: FiveMinuteCandlesCheck
    offline_checks: OfflineChecksCheck
    dashboard_readiness: DashboardReadinessCheck


class PreMarketChecklistResponse(BaseModel):
    session_date: str
    checked_at: str
    overall_status: ChecklistStatus
    blockers: List[str] = Field(default_factory=list)
    next_step: str = ""
    local_data_dir: str = ""
    suggested_commands: SuggestedCommands = Field(default_factory=SuggestedCommands)
    areas: ChecklistAreas
