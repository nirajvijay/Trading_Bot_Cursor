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
#
# NOTE: the age filter compares via julianday(), not string comparison.
# created_at is written as ISO-8601 with a 'T' and a '+00:00' offset
# (intraday_continuation_writer._utc_now_iso), which sorts ABOVE SQLite's own
# space-separated datetime('now') output for the same day -- a plain string
# compare silently matches nothing and the check never alarms.
GRACE_SECONDS = 120


def _today_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


@dataclass(frozen=True)
class VwapHealth:
    # "ok"   - triggers are being classified and persisted
    # "alarm"- something is being dropped on the floor
    # "idle" - no observation session for today yet; nothing to judge. This is
    #          deliberately NOT "ok": green before the runner starts would mean
    #          "nothing happened", which trains you to ignore green.
    status: str
    session_live: bool
    session_date: str
    triggered_count: int
    qualified_count: int
    stuck_count: int
    callback_failures: int
    persist_failures: int
    reason: Optional[str]
    checked_at: str


@dataclass(frozen=True)
class StatusCounters:
    session_live: bool
    callback_failures: int
    persist_failures: int


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
          AND julianday(d.created_at) <= julianday('now', ?)
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


def _read_status_counters(status_file: Path, session_date: str) -> StatusCounters:
    """Read failure counters, but ONLY from a status file describing today.

    runner_status.json is never deleted when the observation runner stops, so
    yesterday's file -- and yesterday's failure counts -- sits on disk until the
    next start. Reading it blind would re-date an old failure to today and raise
    an alarm before the runner has even been started. The file carries its own
    session_date; anything that isn't today's is treated as "no live session"
    rather than as a run with zero failures.
    """
    if not status_file.exists():
        return StatusCounters(False, 0, 0)
    try:
        data = json.loads(status_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return StatusCounters(False, 0, 0)
    if not isinstance(data, dict) or data.get("session_date") != session_date:
        return StatusCounters(False, 0, 0)
    vwap = data.get("vwap_qualifier")
    if not isinstance(vwap, dict):
        return StatusCounters(True, 0, 0)
    return StatusCounters(
        True,
        int(vwap.get("callback_failures", 0) or 0),
        int(vwap.get("persist_failures", 0) or 0),
    )


def _classify(
    *,
    session_live: bool,
    triggered: int,
    stuck: int,
    callback_failures: int,
) -> tuple[str, Optional[str]]:
    """Turn the raw counts into a verdict and a human-readable reason.

    Returns (status, reason) where status is one of:
      "idle"  - no observation session for today; nothing to judge yet
      "alarm" - triggers are being dropped, or classify crashed
      "ok"    - triggers are being classified and persisted

    Useful phrasings for `reason` (return None when there is nothing wrong):
      f"{callback_failures} VWAP classify call(s) crashed before persisting a verdict"
      f"{stuck} trigger(s) older than {GRACE_SECONDS}s have no VWAP verdict on file"
    """
    # Idle requires BOTH no live session and no triggers on record. Checking
    # session_live alone would let a runner that ran and died this morning look
    # idle while its stuck triggers went unreported.
    if not session_live and triggered == 0:
        return "idle", None

    # A crash is the more specific, more actionable cause; stuck triggers are
    # usually just its symptom, so report the crash when both are present.
    if callback_failures > 0:
        return (
            "alarm",
            f"{callback_failures} VWAP classify call(s) crashed before persisting a verdict",
        )

    # Stuck triggers come from the database, so this stands on its own evidence
    # and alarms even when no live status file backs it up.
    if stuck > 0:
        return (
            "alarm",
            f"{stuck} trigger(s) older than {GRACE_SECONDS}s have no VWAP verdict on file",
        )

    return "ok", None


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

    counters = _read_status_counters(status_path, date)

    stuck = max(0, triggered - qualified)
    status, reason = _classify(
        session_live=counters.session_live,
        triggered=triggered,
        stuck=stuck,
        callback_failures=counters.callback_failures,
    )
    callback_failures = counters.callback_failures
    persist_failures = counters.persist_failures

    return VwapHealth(
        status=status,
        session_live=counters.session_live,
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
    status = result.status.upper()
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
    # "idle" is not a failure: exit 0 so systemd does not flag every pre-open
    # run as an alarm. Only a real "alarm" exits 1.
    return 1 if result.status == "alarm" else 0


if __name__ == "__main__":
    raise SystemExit(main())
