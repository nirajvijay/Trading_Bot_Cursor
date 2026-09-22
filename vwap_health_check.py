"""
Standalone VWAP pipeline health check.

Runs outside the observation runner / execution engine (own process, own
schedule) so it can never affect trigger-handling latency. Compares today's
TRIGGERED continuation decisions against live_vwap_qualifications rows: if
triggers exist but nothing was ever classified, that is exactly the failure
mode that went unnoticed for three weeks (Sep 2 - Sep 22, 2026). Also reads
the observation runner's status file for classify/persist failure counters.

Writes its verdict to runtime-cache/vwap_health.json for the API to serve.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from api import config

IST = ZoneInfo("Asia/Kolkata")

# Real triggers younger than this may not have a verdict yet purely due to
# normal classify latency; only count a gap once a candidate has had time
# to be classified and persisted.
GRACE_SECONDS = 120


def _today_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


@dataclass(frozen=True)
class VwapHealth:
    ok: bool
    session_date: str
    triggered_count: int
    qualified_count: int
    stuck_count: int
    callback_failures: int
    persist_failures: int
    reason: Optional[str]
    checked_at: str


def _count_triggered_past_grace(conn: sqlite3.Connection, session_date: str) -> int:
    row = conn.execute(
        """
        SELECT count(*)
        FROM live_continuation_decisions d
        JOIN live_continuation_arms a
          ON a.setup_id = d.setup_id
         AND a.continuation_rule_version = d.continuation_rule_version
        WHERE d.decision_type = 'TRIGGERED'
          AND a.session_date = ?
          AND d.created_at <= datetime('now', ?)
        """,
        (session_date, f"-{GRACE_SECONDS} seconds"),
    ).fetchone()
    return int(row[0]) if row else 0


def _count_qualified(conn: sqlite3.Connection, session_date: str) -> int:
    row = conn.execute(
        "SELECT count(*) FROM live_vwap_qualifications WHERE session_date = ?",
        (session_date,),
    ).fetchone()
    return int(row[0]) if row else 0


def _read_status_counters(status_file: Path) -> tuple[int, int]:
    if not status_file.exists():
        return 0, 0
    try:
        data = json.loads(status_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0, 0
    vwap = data.get("vwap_qualifier")
    if not isinstance(vwap, dict):
        return 0, 0
    return (
        int(vwap.get("callback_failures", 0) or 0),
        int(vwap.get("persist_failures", 0) or 0),
    )


def check(
    *,
    live_db: Optional[Path] = None,
    status_file: Optional[Path] = None,
    session_date: Optional[str] = None,
) -> VwapHealth:
    date = session_date or _today_ist()
    db_path = live_db or config.LIVE_DB_PATH
    status_path = status_file or config.RUNNER_STATUS_FILE

    triggered = 0
    qualified = 0
    if db_path.exists():
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            triggered = _count_triggered_past_grace(conn, date)
            qualified = _count_qualified(conn, date)
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

    callback_failures, persist_failures = _read_status_counters(status_path)

    stuck = max(0, triggered - qualified)
    ok = stuck == 0 and callback_failures == 0
    reason = None
    if callback_failures > 0:
        reason = f"{callback_failures} VWAP classify call(s) crashed before persisting a verdict"
    elif stuck > 0:
        reason = f"{stuck} trigger(s) older than {GRACE_SECONDS}s have no VWAP verdict on file"

    return VwapHealth(
        ok=ok,
        session_date=date,
        triggered_count=triggered,
        qualified_count=qualified,
        stuck_count=stuck,
        callback_failures=callback_failures,
        persist_failures=persist_failures,
        reason=reason,
        checked_at=datetime.now(IST).isoformat(timespec="seconds"),
    )


def write_health(result: VwapHealth, *, out_path: Optional[Path] = None) -> Path:
    path = out_path or config.VWAP_HEALTH_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
    tmp_path.replace(path)
    return path


def main() -> int:
    result = check()
    path = write_health(result)
    status = "OK" if result.ok else "ALARM"
    print(
        "VWAP_HEALTH %s session=%s triggered=%d qualified=%d stuck=%d "
        "callback_failures=%d persist_failures=%d reason=%s -> %s"
        % (
            status,
            result.session_date,
            result.triggered_count,
            result.qualified_count,
            result.stuck_count,
            result.callback_failures,
            result.persist_failures,
            result.reason or "-",
            path,
        )
    )
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
