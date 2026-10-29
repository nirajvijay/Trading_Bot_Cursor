"""The one persisted choice about the execution engine: what autostart launches.

A small JSON file next to the engine DB. Everything else about a session is
chosen at start and fixed for the day (engine_config); this exists because
the 09:15 autostart has nobody at the desk to choose. ORB breakouts start at
09:20, so an ARB day only works if autostart itself launches ARB.

Legacy is the default and what an unreadable or missing file means: a broken
settings file can never switch the strategy.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from api import config
from engine_config import (
    ARB_DEFAULT_TOTAL_CAPITAL_RUPEES,
    ARB_UNLOCKED_SCALE,
    STRATEGY_ARB,
    STRATEGY_LEGACY_VWAP,
    STRATEGY_MODES,
    SessionRiskConfig,
    arb_session_config,
    validate_start,
)
from engine_status import read_json, write_json_atomic


@dataclass(frozen=True)
class ExecutionSettings:
    autostart_strategy: str = STRATEGY_LEGACY_VWAP
    arb_risk_scale: float = ARB_UNLOCKED_SCALE
    arb_total_capital_rupees: float = ARB_DEFAULT_TOTAL_CAPITAL_RUPEES
    # False = ARB trades ORB only. Missing from the file means on.
    arb_funnel_enabled: bool = True
    updated_at: Optional[str] = None
    updated_by: Optional[str] = None


def settings_path() -> Path:
    return config.execution_engine_db_path().with_name("execution_settings.json")


def _flag(value: object) -> bool:
    """A JSON switch; only an explicit off value turns it off."""
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "off", "no")
    return value is not False and value != 0


def load(path: Optional[Path] = None) -> ExecutionSettings:
    data = read_json(path or settings_path()) or {}
    try:
        strategy = str(data.get("autostart_strategy") or STRATEGY_LEGACY_VWAP)
        if strategy not in STRATEGY_MODES:
            return ExecutionSettings()
        settings = ExecutionSettings(
            autostart_strategy=strategy,
            arb_risk_scale=float(data.get("arb_risk_scale") or ARB_UNLOCKED_SCALE),
            arb_total_capital_rupees=float(
                data.get("arb_total_capital_rupees") or ARB_DEFAULT_TOTAL_CAPITAL_RUPEES
            ),
            arb_funnel_enabled=_flag(data.get("arb_funnel_enabled", True)),
            updated_at=data.get("updated_at"),
            updated_by=data.get("updated_by"),
        )
        validate_start(session_config_for(settings))
        return settings
    except (TypeError, ValueError):
        # Includes InvalidSessionConfig: stored numbers that may not be started.
        return ExecutionSettings()


def session_config_for(settings: ExecutionSettings) -> SessionRiskConfig:
    """The session autostart launches with these settings."""
    if settings.autostart_strategy == STRATEGY_ARB:
        return arb_session_config(
            risk_scale=settings.arb_risk_scale,
            total_capital_rupees=settings.arb_total_capital_rupees,
            funnel_enabled=settings.arb_funnel_enabled,
        )
    return SessionRiskConfig()


def save(settings: ExecutionSettings, path: Optional[Path] = None) -> ExecutionSettings:
    """Validate, then write. Raises InvalidSessionConfig (a ValueError) if the
    settings describe a session that could not be started."""
    if settings.autostart_strategy not in STRATEGY_MODES:
        raise ValueError(f"autostart_strategy must be one of {STRATEGY_MODES}")
    validate_start(session_config_for(settings))
    write_json_atomic(path or settings_path(), asdict(settings))
    return settings
