"""08:40–09:15 IST checklist supervisor. No observation or trading starts."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime

from api import config
from api.services.checklist_activity import (ChecklistBusy, STAGES, read_activity, save_activity,
                                             workflow_lock, workflow_busy)
from nse_trading_calendar import IST, NSE_CALENDAR_VERSION, entry_calendar_block_reason, prior_nse_trading_session

# Kite access tokens expire at 06:00 IST and Kite recommends fetching the daily
# instrument dump around 08:30, so start at 08:40 and finish by the 09:15 open.
WINDOW_START_MINUTE = 8 * 60 + 40
DEADLINE_MINUTE = 9 * 60 + 15
OUTSIDE_WINDOW = "outside_0840_0915_window"
DEADLINE_MESSAGE = "09:15 preparation deadline reached; retry the incomplete stage manually."
# Outlasts brief shared locks from page reads; a manual stage still wins.
LOCK_WAIT_SECONDS = 60


def eligibility(now: datetime) -> str | None:
    now = now.astimezone(IST)
    day = now.date().isoformat()
    reason = entry_calendar_block_reason(day, now)
    if reason:
        return reason
    if prior_nse_trading_session(day) is None:
        return "calendar_prior_session_unconfigured"
    if not WINDOW_START_MINUTE <= now.hour * 60 + now.minute < DEADLINE_MINUTE:
        return OUTSIDE_WINDOW
    return None


def checklist(day: str) -> dict:
    from api.queries.checklist import fetch_premarket_checklist
    return fetch_premarket_checklist(live_db=config.LIVE_DB_PATH, instruments_db=config.INSTRUMENTS_DB_PATH,
                                    historical_db=config.HISTORICAL_DB_PATH, baselines_db=config.BASELINES_DB_PATH,
                                    session_date=day)


def validate_kite() -> None:
    from api.auth import settings
    from api.services.kite_auto_login import attempt_kite_auto_login, auto_login_credentials_configured
    from api.services.kite_auto_login_rate import check_account_budget, record_account_attempt
    from api.services.token_check_cache import write_token_check
    from api.services.token_generation_cache import write_token_generated
    from login import check_access_token_details
    valid, _, user = check_access_token_details()
    if not valid:
        if not settings.KITE_AUTO_LOGIN_ENABLED or not auto_login_credentials_configured():
            raise ValueError("Configure Kite automatic login or complete Kite login manually.")
        check_account_budget()
        record_account_attempt()
        result = attempt_kite_auto_login()
        if not result.success:
            if result.failure_reason == "connect_login_failed":
                from requests import ConnectionError
                raise ConnectionError("Kite connection failed")
            # Broker payloads may contain sensitive values; expose only typed reason.
            raise ValueError("Kite login blocked: " + str(result.failure_reason) + "; complete Kite login manually.")
        write_token_generated()
        valid, _, user = check_access_token_details()
    valid = valid and (not settings.KITE_EXPECTED_USER_ID or user == settings.KITE_EXPECTED_USER_ID)
    write_token_check(valid=bool(valid), user_id=user)
    if not valid:
        raise ValueError("Kite token invalid or account mismatch; complete Kite login manually.")


def worker(stage: str, day: str, lock_fd: int) -> dict:
    from api.services.morning_data import repair_history, repair_five_minute, repair_plan
    if stage == "kite":
        validate_kite()
    elif stage in ("instruments", "historical", "baselines", "five-minute"):
        from api.services.generation_lock import acquire_generation_lock, release_generation_lock
        lock = acquire_generation_lock(stage, local_data_dir=config.LOCAL_DATA_DIR)
        try:
            if stage == "instruments":
                from instrument_collector import collect_nifty50
                collect_nifty50(config.INSTRUMENTS_DB_PATH, fetch_live_quotes=False)
            elif stage == "baselines":
                from baseline_generator import generate_baselines
                # Generator progress goes to stderr, leaving stdout as a JSON protocol.
                with contextlib.redirect_stdout(sys.stderr):
                    summary = generate_baselines(historical_db=config.HISTORICAL_DB_PATH,
                                                 baselines_db=config.BASELINES_DB_PATH,
                                                 as_of=prior_nse_trading_session(day))
                if summary.get("errors"):
                    raise ValueError("Baseline generation incomplete; inspect historical session quality.")
            else:
                (repair_history if stage == "historical" else repair_five_minute)(day)
        finally:
            release_generation_lock(lock)
    data = checklist(day)
    if stage in STAGES and data["areas"][STAGES[stage]]["status"] != "ok":
        raise ValueError(f"{stage} validation failed: " + data["areas"][STAGES[stage]]["message"])
    try:
        pending = bool(repair_plan(day))
    except Exception:
        # Report through the history stage, after instruments have a chance to refresh.
        pending = True
    return {"ok": True, "checklist": data, "history_needed": pending}


def stop_child(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=5)
    for stream in (proc.stdout, proc.stderr):
        if stream is not None:
            stream.close()


def run_child(stage: str, day: str, fd: int, deadline: datetime) -> dict:
    remaining = (deadline - datetime.now(IST)).total_seconds()
    if remaining <= 0:
        raise TimeoutError(DEADLINE_MESSAGE)
    proc = subprocess.Popen([sys.executable, str(config.ROOT / "morning_checklist.py"),
                             "--stage", stage, "--session-date", day, "--lock-fd", str(fd)],
                            cwd=config.ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, start_new_session=True, pass_fds=(fd,))
    try:
        output, _ = proc.communicate(timeout=remaining)
        if proc.returncode != 0:
            raise ValueError(f"{stage} process failed; inspect server configuration.")
        return json.loads(output)
    except subprocess.TimeoutExpired:
        raise TimeoutError(DEADLINE_MESSAGE) from None
    finally:
        stop_child(proc)


def ensure_idle() -> None:
    from api.services.checklist_activity import ensure_market_data_idle
    ensure_market_data_idle()


def run() -> int:
    if os.environ.get("MORNING_CHECKLIST_ENABLED", "false").lower() not in ("true", "1", "yes"):
        return 0
    try:
        with workflow_lock(wait_seconds=LOCK_WAIT_SECONDS) as fd:
            # Evaluate the clock after any lock wait.
            now = datetime.now(IST)
            day = now.date().isoformat()
            reason = eligibility(now)
            previous = read_activity(day) or {}
            if previous.get("source") == "automatic" and previous.get("status") == "completed" and not previous.get("dirty"):
                return 0
            if reason:
                if reason == OUTSIDE_WINDOW and previous:
                    return 0
                save_activity({"session_date": day, "source": "automatic", "stage": "kite",
                               "status": "skipped" if reason == "nse_holiday_or_weekend" else "blocked",
                               "message": reason})
                return 0 if reason == "nse_holiday_or_weekend" else 1
            from api.services.checklist_cache import invalidate_checklist_cache, write_checklist_cache
            state = {"session_date": day, "source": "automatic", "status": "running", "stage": "kite",
                     "started_at": now.isoformat(), "release": os.environ.get("RELEASE_SHA", config.ROOT.name),
                     "release_sha": os.environ.get("RELEASE_SHA"), "calendar_version": NSE_CALENDAR_VERSION,
                     "message": "Validating Kite token", "dirty": previous.get("dirty", []),
                     "attempts": previous.get("attempts", {}), "stages": previous.get("stages", {})}
            state = save_activity(state)
            invalidate_checklist_cache()
            deadline = now.replace(hour=DEADLINE_MINUTE // 60, minute=DEADLINE_MINUTE % 60, second=0, microsecond=0)
            try:
                ensure_idle()
                result = run_child("inspect", day, fd, deadline)
                if not result.get("ok"):
                    raise ValueError(result.get("message", "Checklist inspection failed"))
                dirty = set(state.get("dirty", []))
                for stage, area in STAGES.items():
                    ensure_idle()
                    needed = (stage == "kite" or stage in dirty or
                              result["checklist"]["areas"][area]["status"] != "ok" or
                              (stage == "historical" and result["history_needed"]))
                    if not needed:
                        state["stages"][stage] = {"status": "valid", "checked_at": datetime.now(IST).isoformat()}
                        state = save_activity(state)
                        continue
                    if stage in ("instruments", "historical"):
                        dirty.update(["baselines", "five-minute"])
                    state["stages"][stage] = {"status": "running", "started_at": datetime.now(IST).isoformat()}
                    state = save_activity({**state, "stage": stage, "dirty": sorted(dirty),
                                           "message": f"Preparing {stage}"})
                    start_attempt = state["attempts"].get(stage, 0)
                    if start_attempt >= 3:
                        raise ValueError(f"{stage} exhausted its daily retry budget; recover this stage manually.")
                    for attempt in range(start_attempt, 3):
                        state["attempts"][stage] = attempt + 1
                        state = save_activity(state)
                        result = run_child(stage, day, fd, deadline)
                        if result.get("ok"):
                            break
                        if not result.get("retryable") or attempt == 2:
                            raise ValueError(result.get("message", "Preparation failed"))
                        remaining = (deadline - datetime.now(IST)).total_seconds()
                        if remaining <= 0:
                            raise TimeoutError(DEADLINE_MESSAGE)
                        time.sleep(min(2 ** attempt, remaining))
                    dirty.discard(stage)
                    state["stages"][stage].update(status="valid", finished_at=datetime.now(IST).isoformat())
                    state = save_activity({**state, "dirty": sorted(dirty), "message": f"{stage} validated"})
                state["stages"]["validation"] = {"status": "running", "started_at": datetime.now(IST).isoformat()}
                state = save_activity({**state, "stage": "validation", "message": "Validating checklist readiness"})
                result = run_child("inspect", day, fd, deadline)
                data = result.get("checklist", {})
                if not result.get("ok") or data.get("overall_status") != "ok" or dirty:
                    raise ValueError(data.get("next_step") or "Final checklist validation failed")
                # Publish while exclusive ownership still prevents competing starts.
                write_checklist_cache(data)
                state["stages"]["validation"].update(status="valid", finished_at=datetime.now(IST).isoformat())
                save_activity({**state, "status": "completed", "message": "", "finished_at": datetime.now(IST).isoformat()})
                return 0
            except (Exception, KeyboardInterrupt) as exc:
                invalidate_checklist_cache()
                message = str(exc) if isinstance(exc, (ValueError, TimeoutError)) else "Preparation interrupted; inspect server logs and retry."
                state["stages"].setdefault(state["stage"], {}).update(status="blocked", message=message)
                save_activity({**state, "status": "blocked", "message": message, "finished_at": datetime.now(IST).isoformat()})
                return 1
    except ChecklistBusy:
        # A manual operation owns the workflow, so someone is at the controls.
        # Skip without touching its activity record; the journal keeps the reason.
        print("Morning checklist skipped: another checklist operation held the workflow lock.",
              file=sys.stderr)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stage", choices=["inspect", *STAGES])
    parser.add_argument("--session-date")
    parser.add_argument("--lock-fd", type=int)
    args = parser.parse_args()
    if args.dry_run:
        day = datetime.now(IST).date().isoformat()
        data = checklist(day)
        required = [stage for stage, area in STAGES.items() if data.get("areas", {}).get(area, {}).get("status") != "ok"]
        from api.services.morning_data import repair_plan
        try:
            pending = repair_plan(day)
            history_note = f"{len(pending)} symbol/session ranges require repair"
            if pending and "historical" not in required:
                required.append("historical")
        except Exception as exc:
            history_note = str(exc) if isinstance(exc, ValueError) else "Historical database unavailable for incremental planning"
        print(json.dumps({"session_date": day, "eligibility": eligibility(datetime.now(IST)),
                          "required_stages": required, "history": history_note, "checklist": data}, default=str))
        return 0
    if args.stage:
        if args.lock_fd is None or not args.session_date:
            parser.error("stage execution requires an inherited workflow lock and session date")
        try:
            inherited = os.fstat(args.lock_fd)
            expected = (config.runtime_cache_dir() / "checklist-workflow.lock").stat()
            if (inherited.st_dev, inherited.st_ino) != (expected.st_dev, expected.st_ino) or not workflow_busy():
                raise ValueError("Worker requires the supervisor's active workflow lock")
            if args.session_date != datetime.now(IST).date().isoformat() or eligibility(datetime.now(IST)):
                raise ValueError("Outside today's permitted morning preparation window")
            result = worker(args.stage, args.session_date, args.lock_fd)
        except Exception as exc:
            import requests
            from kiteconnect.exceptions import NetworkException
            retryable = isinstance(exc, (requests.ConnectionError, requests.Timeout, NetworkException))
            code = getattr(exc, "code", None)
            retryable = retryable or (isinstance(code, int) and (code == 429 or 500 <= code < 600))
            result = {"ok": False, "retryable": retryable,
                      "message": str(exc) if isinstance(exc, ValueError) else f"{args.stage} failed ({type(exc).__name__}); inspect server configuration."}
        print(json.dumps(result, default=str))
        return 0
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
