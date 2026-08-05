"""Read-only pre-market checklist queries (direct SQL only)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo

from config.nifty100_symbols import NIFTY_100_SYMBOLS
from session_quality import LOOKBACK_COMPLETED_SESSIONS, discover_completed_sessions
from universe_manifest import default_manifest_path, validate_universe_manifest

from api import config
from api.db import open_readonly
from api.queries.radar import fetch_radar_rows, list_sessions
from api.services.local_data_generation import (
    generate_action_for_task,
    get_generate_command,
    prior_trading_session,
)
from api.services.token_check_cache import read_token_check, token_valid_for_today
from login import read_auth_status

EXPECTED_COUNT = len(NIFTY_100_SYMBOLS)
EMA_PERIOD = 20
INSTRUMENTS_STALE_DAYS = 7

_STATUS_RANK = {
    "not_checked": 0,
    "ok": 1,
    "warning": 2,
    "needs_update": 3,
    "failed": 4,
}


def _worst_status(*statuses: str) -> str:
    return max(statuses, key=lambda s: _STATUS_RANK.get(s, 0))


def _parse_tick_size(data_json: Optional[str]) -> Optional[float]:
    if not data_json:
        return None
    try:
        data = json.loads(data_json)
    except json.JSONDecodeError:
        return None
    raw = data.get("tick_size")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _format_datetime_ist(iso_timestamp: Optional[str]) -> str:
    """Format an ISO timestamp for human-readable IST display."""
    if not iso_timestamp:
        return "unknown"
    try:
        normalized = iso_timestamp.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ist = dt.astimezone(ZoneInfo("Asia/Kolkata"))
        return ist.strftime("%d %b %Y, %H:%M IST")
    except ValueError:
        return iso_timestamp


def _is_stale(iso_timestamp: Optional[str], *, days: int = INSTRUMENTS_STALE_DAYS) -> bool:
    if not iso_timestamp:
        return True
    try:
        normalized = iso_timestamp.replace("Z", "+00:00")
        collected = datetime.fromisoformat(normalized)
        if collected.tzinfo is None:
            collected = collected.replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        return collected < cutoff
    except ValueError:
        return True


def _count_symbols_on_date(
    conn: sqlite3.Connection,
    table: str,
    session_date: str,
) -> int:
    try:
        row = conn.execute(
            f"""
            SELECT COUNT(DISTINCT tradingsymbol)
            FROM {table}
            WHERE substr(candle_time, 1, 10) = ?
            """,
            (session_date,),
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except sqlite3.OperationalError:
        return 0


def _latest_session_date(conn: sqlite3.Connection, table: str) -> Optional[str]:
    try:
        row = conn.execute(
            f"""
            SELECT MAX(substr(candle_time, 1, 10))
            FROM {table}
            """
        ).fetchone()
        if row and row[0]:
            return str(row[0])
    except sqlite3.OperationalError:
        pass
    return None


def _symbols_missing_on_date(
    conn: sqlite3.Connection,
    table: str,
    session_date: str,
    *,
    limit: int = 5,
) -> Tuple[int, List[str]]:
    try:
        rows = conn.execute(
            f"""
            SELECT DISTINCT tradingsymbol
            FROM {table}
            WHERE substr(candle_time, 1, 10) = ?
            """,
            (session_date,),
        ).fetchall()
        present = {str(r[0]) for r in rows}
    except sqlite3.OperationalError:
        present = set()
    missing = [s for s in NIFTY_100_SYMBOLS if s not in present]
    return len(missing), missing[:limit]


def _resolve_baseline_as_of(conn: sqlite3.Connection, session_date: str) -> Optional[str]:
    try:
        row = conn.execute(
            """
            SELECT MAX(baseline_as_of_date)
            FROM baselines
            WHERE baseline_as_of_date < ?
            """,
            (session_date,),
        ).fetchone()
        if row and row[0]:
            return str(row[0])
    except sqlite3.OperationalError:
        pass
    return None


def _baseline_token_coverage(conn: sqlite3.Connection, as_of: str) -> Tuple[int, int]:
    try:
        row = conn.execute(
            """
            SELECT
                COUNT(DISTINCT instrument_token),
                COUNT(DISTINCT CASE WHEN is_reliable = 1 THEN instrument_token END)
            FROM baselines
            WHERE baseline_as_of_date = ?
            """,
            (as_of,),
        ).fetchone()
        if row:
            return int(row[0] or 0), int(row[1] or 0)
    except sqlite3.OperationalError:
        pass
    return 0, 0


def _latest_generation_run(conn: sqlite3.Connection) -> Optional[str]:
    try:
        row = conn.execute(
            """
            SELECT generated_at
            FROM baseline_generation_runs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if row and row[0]:
            return str(row[0])
    except sqlite3.OperationalError:
        pass
    return None


def _ema_seed_ready_count(
    conn: sqlite3.Connection,
    tokens: Sequence[int],
    session_date: str,
) -> Tuple[int, int]:
    ready = 0
    for token in tokens:
        prior = conn.execute(
            """
            SELECT DISTINCT substr(candle_time, 1, 10) AS d
            FROM candles_5m
            WHERE instrument_token = ?
              AND substr(candle_time, 1, 10) < ?
            ORDER BY d DESC
            LIMIT 1
            """,
            (token, session_date),
        ).fetchone()
        if prior is None:
            continue
        seed_date = prior[0]
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM candles_5m
            WHERE instrument_token = ?
              AND substr(candle_time, 1, 10) = ?
            """,
            (token, seed_date),
        ).fetchone()
        bar_count = int(row[0]) if row else 0
        if bar_count >= EMA_PERIOD:
            ready += 1
    missing = len(tokens) - ready
    return ready, missing


def _load_instrument_tokens(instruments_db: Path) -> Dict[str, int]:
    if not instruments_db.exists():
        return {}
    try:
        conn = open_readonly(instruments_db)
    except FileNotFoundError:
        return {}
    try:
        rows = conn.execute(
            "SELECT tradingsymbol, instrument_token FROM nifty50_instruments"
        ).fetchall()
        return {str(r["tradingsymbol"]): int(r["instrument_token"]) for r in rows}
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()


# Live DB is created by Start Observation — missing pre-start is a soft warning only.
_OPTIONAL_PRESTART_DATABASES = frozenset({"live"})


def _audit_databases(
    *,
    live_db: Path,
    instruments_db: Path,
    historical_db: Path,
    baselines_db: Path,
) -> Tuple[List[dict], List[str], bool]:
    """Return (database statuses, missing names, all_required_ok).

    Missing ``live`` is reported in ``missing`` but does not set
    ``all_required_ok`` to False (live is created by the observation runner).
    """
    entries = [
        ("live", live_db),
        ("instruments", instruments_db),
        ("historical", historical_db),
        ("baselines", baselines_db),
    ]
    statuses: List[dict] = []
    missing: List[str] = []
    all_ok = True

    for name, path in entries:
        exists = path.exists()
        readable = False
        if exists:
            try:
                conn = open_readonly(path)
                conn.execute("SELECT 1").fetchone()
                conn.close()
                readable = True
            except (FileNotFoundError, sqlite3.Error):
                readable = False
                if name not in _OPTIONAL_PRESTART_DATABASES:
                    all_ok = False
        else:
            missing.append(name)
            if name not in _OPTIONAL_PRESTART_DATABASES:
                all_ok = False

        statuses.append(
            {
                "name": name,
                "path": str(path),
                "exists": exists,
                "readable": readable,
                "scope": "local",
            }
        )

    return statuses, missing, all_ok


def _build_kite_auth() -> dict:
    auth = read_auth_status()
    api_key = auth.get("api_key_configured", False)
    api_secret = auth.get("api_secret_configured", False)
    token_present = auth.get("access_token_present", False)
    cached = read_token_check()
    validated_today = token_valid_for_today()

    if not api_key or not api_secret:
        status = "failed"
        message = "Kite API key or secret missing in backend/.env"
    elif not token_present:
        status = "warning"
        message = "Access token not configured — complete Kite Auth flow"
    elif validated_today is True:
        user_id = cached.get("user_id") if cached else None
        status = "ok"
        message = f"Access token validated today{f' (user: {user_id})' if user_id else ''}"
    elif validated_today is False:
        status = "failed"
        message = "Access token invalid — complete Kite Auth flow or run Check Token"
    else:
        status = "warning"
        message = "Token present — run Check Token to validate"

    return {
        "status": status,
        "message": message,
        "api_key_configured": bool(api_key),
        "api_secret_configured": bool(api_secret),
        "access_token_present": bool(token_present),
        "masked_access_token": auth.get("masked_access_token"),
        "token_validated_today": validated_today is True,
        "token_checked_at": cached.get("checked_at") if cached and validated_today is not None else None,
        "copy_command": "python3 login.py --check-token",
    }


def _build_instruments(instruments_db: Path) -> dict:
    copy_command = get_generate_command("instruments")
    generate = generate_action_for_task(
        "instruments",
        available=True,
        reason="Creates instruments in backend/data/local/",
    )

    if not instruments_db.exists():
        return {
            "status": "failed",
            "message": "Instruments database not found",
            "instruments_count": 0,
            "expected_count": EXPECTED_COUNT,
            "tick_size_count": 0,
            "last_updated": None,
            "missing_symbols": list(NIFTY_100_SYMBOLS[:5]),
            "copy_command": copy_command,
            "generate_action": generate,
        }

    try:
        conn = open_readonly(instruments_db)
    except FileNotFoundError:
        return {
            "status": "failed",
            "message": "Instruments database not readable",
            "instruments_count": 0,
            "expected_count": EXPECTED_COUNT,
            "tick_size_count": 0,
            "last_updated": None,
            "missing_symbols": [],
            "copy_command": copy_command,
            "generate_action": generate,
        }

    try:
        rows = conn.execute(
            """
            SELECT tradingsymbol, instrument_data, collected_at
            FROM nifty50_instruments
            """
        ).fetchall()
        run_row = conn.execute(
            """
            SELECT collected_at
            FROM collection_runs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    except sqlite3.OperationalError:
        rows = []
        run_row = None
    finally:
        conn.close()

    present_symbols: Set[str] = set()
    tick_size_count = 0
    max_collected_at: Optional[str] = None
    for row in rows:
        symbol = str(row["tradingsymbol"])
        present_symbols.add(symbol)
        if _parse_tick_size(row["instrument_data"]) is not None:
            tick_size_count += 1
        collected_at = row["collected_at"]
        if collected_at and (max_collected_at is None or str(collected_at) > max_collected_at):
            max_collected_at = str(collected_at)

    last_updated = max_collected_at
    if run_row and run_row[0]:
        run_at = str(run_row[0])
        if last_updated is None or run_at > last_updated:
            last_updated = run_at

    instruments_count = len(present_symbols)
    missing = [s for s in NIFTY_100_SYMBOLS if s not in present_symbols]

    if instruments_count == 0:
        status = "failed"
        message = "No instruments in database"
    elif instruments_count < EXPECTED_COUNT:
        status = "needs_update"
        message = f"{instruments_count}/{EXPECTED_COUNT} instruments — run collector"
    elif tick_size_count < EXPECTED_COUNT:
        status = "needs_update"
        message = f"Tick-size coverage {tick_size_count}/{EXPECTED_COUNT}"
    elif _is_stale(last_updated):
        status = "warning"
        message = (
            f"Instruments last updated {_format_datetime_ist(last_updated)} "
            f"(older than {INSTRUMENTS_STALE_DAYS} days)"
        )
    elif tick_size_count < instruments_count:
        status = "warning"
        message = f"Tick-size coverage {tick_size_count}/{instruments_count}"
    else:
        status = "ok"
        message = f"{instruments_count}/{EXPECTED_COUNT} instruments with tick-size coverage"

    return {
        "status": status,
        "message": message,
        "instruments_count": instruments_count,
        "expected_count": EXPECTED_COUNT,
        "tick_size_count": tick_size_count,
        "last_updated": last_updated,
        "missing_symbols": missing[:5],
        "copy_command": copy_command,
        "generate_action": generate,
    }


def _build_historical(historical_db: Path) -> dict:
    copy_command = get_generate_command("historical")
    generate = generate_action_for_task(
        "historical",
        available=True,
        reason="Downloads into backend/data/local/ only — does not modify external repos",
    )
    db_path = str(historical_db)

    if not historical_db.exists():
        return {
            "status": "failed",
            "message": (
                "Local historical database not found in backend/data/local/. "
                "External repos are not used for this checklist."
            ),
            "latest_date": None,
            "symbols_covered": 0,
            "expected_count": EXPECTED_COUNT,
            "missing_count": EXPECTED_COUNT,
            "missing_symbols_sample": list(NIFTY_100_SYMBOLS[:5]),
            "copy_command": copy_command,
            "db_path": db_path,
            "generate_action": generate,
        }

    try:
        conn = open_readonly(historical_db)
    except FileNotFoundError:
        return {
            "status": "failed",
            "message": "Local historical database not readable",
            "latest_date": None,
            "symbols_covered": 0,
            "expected_count": EXPECTED_COUNT,
            "missing_count": EXPECTED_COUNT,
            "missing_symbols_sample": [],
            "copy_command": copy_command,
            "db_path": db_path,
            "generate_action": generate,
        }

    try:
        latest_date = _latest_session_date(conn, "candles")
        if latest_date is None:
            return {
                "status": "failed",
                "message": "No historical candle data in local database",
                "latest_date": None,
                "symbols_covered": 0,
                "expected_count": EXPECTED_COUNT,
                "missing_count": EXPECTED_COUNT,
                "missing_symbols_sample": list(NIFTY_100_SYMBOLS[:5]),
                "copy_command": copy_command,
                "db_path": db_path,
                "generate_action": generate,
            }

        symbols_covered = _count_symbols_on_date(conn, "candles", latest_date)
        missing_count, missing_sample = _symbols_missing_on_date(
            conn, "candles", latest_date
        )

        # Per-symbol completed-session quality (≥21 completed sessions required).
        token_rows = conn.execute(
            """
            SELECT DISTINCT instrument_token, tradingsymbol
            FROM candles
            """
        ).fetchall()
        token_by_symbol = {str(r[1]): int(r[0]) for r in token_rows}
        below_threshold: List[str] = []
        for symbol in NIFTY_100_SYMBOLS:
            token = token_by_symbol.get(symbol)
            if token is None:
                below_threshold.append(symbol)
                continue
            completed = len(
                discover_completed_sessions(
                    conn,
                    token,
                    lookback_sessions=LOOKBACK_COMPLETED_SESSIONS,
                    as_of=latest_date,
                )
            )
            if completed < LOOKBACK_COMPLETED_SESSIONS:
                below_threshold.append(symbol)
    finally:
        conn.close()

    if below_threshold:
        status = "needs_update"
        sample = ", ".join(below_threshold[:8])
        more = "" if len(below_threshold) <= 8 else f" (+{len(below_threshold) - 8} more)"
        message = (
            f"{len(below_threshold)}/{EXPECTED_COUNT} symbols below "
            f"{LOOKBACK_COMPLETED_SESSIONS} completed sessions "
            f"(incomplete dates excluded): {sample}{more}"
        )
    elif missing_count == 0 and symbols_covered >= EXPECTED_COUNT:
        status = "ok"
        message = (
            f"Latest session {latest_date} covers {symbols_covered}/{EXPECTED_COUNT} "
            f"symbols with ≥{LOOKBACK_COMPLETED_SESSIONS} completed sessions each"
        )
    elif missing_count > 0:
        status = "needs_update" if missing_count > 2 else "warning"
        message = (
            f"Latest {latest_date}: {symbols_covered}/{EXPECTED_COUNT} symbols "
            f"({missing_count} missing)"
        )
    else:
        status = "warning"
        message = f"Latest session {latest_date}: partial coverage {symbols_covered}/{EXPECTED_COUNT}"

    return {
        "status": status,
        "message": message,
        "latest_date": latest_date,
        "symbols_covered": symbols_covered,
        "expected_count": EXPECTED_COUNT,
        "missing_count": missing_count if not below_threshold else len(below_threshold),
        "missing_symbols_sample": (
            below_threshold[:5] if below_threshold else missing_sample
        ),
        "copy_command": copy_command,
        "db_path": db_path,
        "generate_action": generate,
    }


def _build_baselines(
    baselines_db: Path,
    session_date: str,
    historical_latest: Optional[str],
) -> dict:
    copy_command = get_generate_command("baselines", session_date)
    historical_ready = config.LOCAL_HISTORICAL_DB_PATH.exists()
    generate = generate_action_for_task(
        "baselines",
        available=historical_ready,
        reason=(
            "Requires local historical DB first"
            if not historical_ready
            else "Generates into backend/data/local/ only"
        ),
    )
    db_path = str(baselines_db)

    if not baselines_db.exists():
        return {
            "status": "failed",
            "message": (
                "Local baselines database not found in backend/data/local/. "
                "External baselines in other repos are not used for this checklist."
            ),
            "baseline_as_of": None,
            "expected_as_of": historical_latest,
            "symbols_covered": 0,
            "expected_count": EXPECTED_COUNT,
            "reliable_count": 0,
            "last_generated_at": None,
            "copy_command": copy_command,
            "db_path": db_path,
            "generate_action": generate,
        }

    try:
        conn = open_readonly(baselines_db)
    except FileNotFoundError:
        return {
            "status": "failed",
            "message": "Local baselines database not readable",
            "baseline_as_of": None,
            "expected_as_of": historical_latest,
            "symbols_covered": 0,
            "expected_count": EXPECTED_COUNT,
            "reliable_count": 0,
            "last_generated_at": None,
            "copy_command": copy_command,
            "db_path": db_path,
            "generate_action": generate,
        }

    try:
        baseline_as_of = _resolve_baseline_as_of(conn, session_date)
        last_generated_at = _latest_generation_run(conn)
        symbols_covered = 0
        reliable_count = 0
        if baseline_as_of:
            symbols_covered, reliable_count = _baseline_token_coverage(conn, baseline_as_of)
    finally:
        conn.close()

    expected_as_of = historical_latest
    prior_session = prior_trading_session(
        config.LOCAL_HISTORICAL_DB_PATH, session_date
    )
    if prior_session and (
        expected_as_of is None or expected_as_of >= session_date
    ):
        expected_as_of = prior_session

    if baseline_as_of is None:
        status = "failed"
        message = f"No baseline strictly prior to session {session_date}"
    elif expected_as_of and baseline_as_of < expected_as_of:
        status = "needs_update"
        message = (
            f"Baseline as-of {baseline_as_of} is behind historical latest {expected_as_of}"
        )
    elif symbols_covered < EXPECTED_COUNT:
        status = "needs_update"
        message = f"Baseline covers {symbols_covered}/{EXPECTED_COUNT} symbols"
    elif reliable_count < symbols_covered:
        status = "warning"
        message = (
            f"Baseline {baseline_as_of}: {symbols_covered}/{EXPECTED_COUNT} symbols, "
            f"{reliable_count} reliable"
        )
    else:
        status = "ok"
        message = f"Baseline as-of {baseline_as_of} covers {symbols_covered}/{EXPECTED_COUNT}"

    return {
        "status": status,
        "message": message,
        "baseline_as_of": baseline_as_of,
        "expected_as_of": expected_as_of,
        "symbols_covered": symbols_covered,
        "expected_count": EXPECTED_COUNT,
        "reliable_count": reliable_count,
        "last_generated_at": last_generated_at,
        "copy_command": copy_command,
        "db_path": db_path,
        "generate_action": generate,
    }


def _build_five_minute(
    historical_db: Path,
    instruments_db: Path,
    session_date: str,
    historical_latest: Optional[str],
) -> dict:
    copy_command = get_generate_command("five-minute")
    historical_ready = historical_db.exists()
    generate = generate_action_for_task(
        "five-minute",
        available=historical_ready,
        reason=(
            "Requires local historical DB first"
            if not historical_ready
            else "Generates 5m candles into local historical DB"
        ),
    )

    if not historical_db.exists():
        return {
            "status": "failed",
            "message": (
                "Local historical database not found — cannot read candles_5m. "
                "Generate local historical data first."
            ),
            "latest_date": None,
            "symbols_covered": 0,
            "expected_count": EXPECTED_COUNT,
            "ema_seed_ready": 0,
            "ema_seed_missing": EXPECTED_COUNT,
            "copy_command": copy_command,
            "generate_action": generate,
        }

    token_map = _load_instrument_tokens(instruments_db)
    tokens = list(token_map.values())

    try:
        conn = open_readonly(historical_db)
    except FileNotFoundError:
        return {
            "status": "failed",
            "message": "Local historical database not readable",
            "latest_date": None,
            "symbols_covered": 0,
            "expected_count": EXPECTED_COUNT,
            "ema_seed_ready": 0,
            "ema_seed_missing": len(tokens),
            "copy_command": copy_command,
            "generate_action": generate,
        }

    try:
        latest_date = _latest_session_date(conn, "candles_5m")
        if latest_date is None:
            return {
                "status": "needs_update",
                "message": "No local 5-minute candles — run generator",
                "latest_date": None,
                "symbols_covered": 0,
                "expected_count": EXPECTED_COUNT,
                "ema_seed_ready": 0,
                "ema_seed_missing": len(tokens),
                "copy_command": copy_command,
                "generate_action": generate,
            }

        symbols_covered = _count_symbols_on_date(conn, "candles_5m", latest_date)
        ema_ready, ema_missing = _ema_seed_ready_count(conn, tokens, session_date)
    finally:
        conn.close()

    if historical_latest and latest_date < historical_latest:
        status = "needs_update"
        message = (
            f"5m latest {latest_date} behind 1m historical {historical_latest}"
        )
    elif symbols_covered < EXPECTED_COUNT:
        status = "needs_update" if symbols_covered < EXPECTED_COUNT - 2 else "warning"
        message = f"5m latest {latest_date}: {symbols_covered}/{EXPECTED_COUNT} symbols"
    elif ema_missing > 0:
        status = "warning" if ema_missing <= 2 else "needs_update"
        message = (
            f"5m {latest_date}: EMA seed ready {ema_ready}/{len(tokens)} "
            f"({ema_missing} missing)"
        )
    else:
        status = "ok"
        message = (
            f"5m latest {latest_date}: {symbols_covered}/{EXPECTED_COUNT}, "
            f"EMA seed {ema_ready}/{len(tokens)}"
        )

    return {
        "status": status,
        "message": message,
        "latest_date": latest_date,
        "symbols_covered": symbols_covered,
        "expected_count": EXPECTED_COUNT,
        "ema_seed_ready": ema_ready,
        "ema_seed_missing": ema_missing,
        "copy_command": copy_command,
        "generate_action": generate,
    }


def _build_offline(
    live_db: Path,
    instruments_db: Path,
    historical_db: Path,
    baselines_db: Path,
    session_date: str,
    area_statuses: Dict[str, str],
) -> dict:
    api_health = "ok"
    databases, missing, all_local_ok = _audit_databases(
        live_db=live_db,
        instruments_db=instruments_db,
        historical_db=historical_db,
        baselines_db=baselines_db,
    )
    missing_required = [name for name in missing if name not in _OPTIONAL_PRESTART_DATABASES]
    missing_live = "live" in missing

    radar_count = 0
    if not missing_live:
        try:
            rows = fetch_radar_rows(live_db, instruments_db, session_date)
            radar_count = len(rows)
        except Exception:
            radar_count = 0

    checklist_issues = [
        name
        for name, status in area_statuses.items()
        if status in ("failed", "needs_update")
    ]

    if missing_required:
        status = "failed"
        message = f"Missing databases: {', '.join(missing_required)}"
    elif checklist_issues:
        status = "failed"
        message = f"Checklist issues: {', '.join(checklist_issues)}"
    elif not missing_live and radar_count != EXPECTED_COUNT:
        status = "failed"
        message = f"Radar returned {radar_count}/{EXPECTED_COUNT} rows"
    elif not all_local_ok:
        status = "warning"
        message = "One or more required local databases are unreadable"
    elif missing_live:
        status = "warning"
        message = (
            "Live database not created yet — expected until Start Observation"
        )
    else:
        status = "ok"
        message = "API healthy, all local databases present, radar returns 100 rows"

    return {
        "status": status,
        "message": message,
        "api_health": api_health,
        "database_readable": all_local_ok and not missing_required,
        "databases": databases,
        "missing_databases": missing,
        "radar_row_count": radar_count,
        "copy_command": "python3 -m unittest discover -s tests -v",
        "generate_action": None,
    }


def _build_dashboard(
    *,
    live_db: Path,
    area_statuses: Dict[str, str],
    critical_areas: Sequence[str],
) -> dict:
    sessions = list_sessions(live_db)
    latest_session = sessions[0] if sessions else None

    critical_failed = [a for a in critical_areas if area_statuses.get(a) == "failed"]
    critical_needs = [a for a in critical_areas if area_statuses.get(a) == "needs_update"]
    critical_warn = [a for a in critical_areas if area_statuses.get(a) == "warning"]

    if critical_failed:
        status = "failed"
        message = f"Blockers: {', '.join(critical_failed)}"
        trial_ready = False
        reason = message
    elif critical_needs:
        status = "needs_update"
        message = f"Updates needed: {', '.join(critical_needs)}"
        trial_ready = False
        reason = "Resolve data updates before market-hour trial"
    elif critical_warn:
        status = "warning"
        message = f"Warnings in: {', '.join(critical_warn)}"
        trial_ready = True
        reason = "Trial possible with warnings — validate token and data freshness"
    else:
        status = "ok"
        message = "Dashboard ready for market-hour observation trial"
        trial_ready = True
        reason = "All critical checks passed"

    startup_cmd = (
        "RUNNER_STATUS_FILE=/tmp/runner_status.json "
        "python3 live_observation_runner.py --status-file /tmp/runner_status.json"
    )

    return {
        "status": status,
        "message": message,
        "api_reachable": True,
        "latest_session": latest_session,
        "market_hour_trial_ready": trial_ready,
        "trial_ready_reason": reason,
        "copy_command": startup_cmd,
    }


def fetch_premarket_checklist(
    *,
    live_db: Path,
    instruments_db: Path,
    historical_db: Path,
    baselines_db: Path,
    session_date: str,
) -> dict:
    checked_at = datetime.now(timezone.utc).isoformat()

    kite_auth = _build_kite_auth()
    instruments = _build_instruments(instruments_db)
    historical = _build_historical(historical_db)
    historical_latest = historical.get("latest_date")
    baselines = _build_baselines(baselines_db, session_date, historical_latest)
    five_minute = _build_five_minute(
        historical_db, instruments_db, session_date, historical_latest
    )

    # Universe manifest is a hard blocker (not warning-only).
    manifest = validate_universe_manifest(default_manifest_path(config.LOCAL_DATA_DIR))
    if not manifest.ok:
        # Force instruments area to hard-fail when manifest missing/mismatched,
        # even if leftover instrument rows exist.
        instruments = {
            **instruments,
            "status": "failed" if manifest.status == "failed" else "needs_update",
            "message": manifest.message,
        }
        if instruments["status"] == "needs_update" and manifest.status == "not_initialized":
            instruments["status"] = "needs_update"
            instruments["message"] = manifest.message

    area_statuses = {
        "kite_auth": kite_auth["status"],
        "instruments": instruments["status"],
        "historical_candles": historical["status"],
        "baselines": baselines["status"],
        "five_minute_candles": five_minute["status"],
    }
    offline = _build_offline(
        live_db,
        instruments_db,
        historical_db,
        baselines_db,
        session_date,
        area_statuses,
    )
    area_statuses["offline_checks"] = offline["status"]

    critical = (
        "instruments",
        "historical_candles",
        "baselines",
        "five_minute_candles",
    )
    dashboard = _build_dashboard(
        live_db=live_db,
        area_statuses=area_statuses,
        critical_areas=critical,
    )

    # Soft offline warning (e.g. live DB not created yet) must not block
    # overall_ok / Start Observation — live is an observation output.
    statuses_for_overall: List[str] = []
    for key, status in area_statuses.items():
        if key == "offline_checks" and status == "warning":
            continue
        statuses_for_overall.append(status)
    statuses_for_overall.append(dashboard["status"])
    overall = _worst_status(*statuses_for_overall)
    if not manifest.ok:
        # Hard blocker: overall cannot be ok without a valid NIFTY_100 manifest.
        overall = _worst_status(overall, "failed" if manifest.status == "failed" else "needs_update")

    blockers: List[str] = []
    if not manifest.ok:
        blockers.append(manifest.message)
    if kite_auth["status"] == "failed":
        blockers.append(kite_auth["message"])
    for name, area in (
        ("Instruments", instruments),
        ("Historical candles", historical),
        ("Baselines", baselines),
        ("5-minute candles", five_minute),
        ("Offline checks", offline),
    ):
        if area["status"] in ("failed", "needs_update"):
            blockers.append(f"{name}: {area['message']}")

    if overall == "ok":
        next_step = "Start live observation runner during market hours"
    elif not manifest.ok:
        next_step = manifest.message
    elif kite_auth["status"] in ("warning", "failed"):
        next_step = "Validate Kite token via Check Token"
    elif blockers:
        next_step = blockers[0]
    else:
        next_step = "Review warnings before market open"

    return {
        "session_date": session_date,
        "checked_at": checked_at,
        "overall_status": overall,
        "blockers": blockers[:5],
        "next_step": next_step,
        "local_data_dir": str(config.LOCAL_DATA_DIR),
        "suggested_commands": {
            "runner": (
                "python3 live_observation_runner.py --status-file /tmp/runner_status.json"
            ),
            "instrument_collector": get_generate_command("instruments"),
            "historical_collector": get_generate_command("historical"),
            "baseline_generator": get_generate_command("baselines", session_date),
            "five_minute_generator": get_generate_command("five-minute"),
            "offline_validation": "python3 -m unittest discover -s tests -v",
            "startup": [
                "uvicorn api.main:app --host 127.0.0.1 --port 8000",
                (
                    "RUNNER_STATUS_FILE=/tmp/runner_status.json "
                    "python3 live_observation_runner.py "
                    "--status-file /tmp/runner_status.json"
                ),
            ],
        },
        "areas": {
            "kite_auth": kite_auth,
            "instruments": instruments,
            "historical_candles": historical,
            "baselines": baselines,
            "five_minute_candles": five_minute,
            "offline_checks": offline,
            "dashboard_readiness": dashboard,
        },
    }
