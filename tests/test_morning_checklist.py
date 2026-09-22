from __future__ import annotations

import copy
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import morning_checklist as morning
from api import config
from api.services import checklist_activity as activity
from api.services.checklist_cache import read_checklist_cache, write_checklist_cache
from nse_trading_calendar import IST, is_nse_trading_day, prior_nse_trading_session


def good_checklist(day):
    return {"session_date": day, "overall_status": "ok", "blockers": [], "next_step": "",
            "areas": {key: {"status": "ok", "message": ""} for key in [*activity.STAGES.values(), "dashboard_readiness"]}}


class MorningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"NIFTY_RADAR_RUNTIME_CACHE_DIR": str(root / "cache"),
                                           "MORNING_CHECKLIST_ENABLED": "true"})
        self.env.start()
        self.local = patch.object(config, "LOCAL_DATA_DIR", root / "local")
        self.local.start()

    def tearDown(self):
        self.local.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_calendar_and_window(self):
        for stamp, reason in [("2026-09-23T08:40:00", None), ("2026-09-23T09:14:59", None),
                              ("2026-09-23T09:15:00", "outside_0840_0915_window"),
                              ("2026-09-23T08:39:59", "outside_0840_0915_window"),
                              ("2026-09-26T09:00:00", "nse_holiday_or_weekend"),
                              ("2026-10-02T09:00:00", "nse_holiday_or_weekend"),
                              ("2026-11-08T09:00:00", "special_session_unconfigured"),
                              ("2027-01-04T09:00:00", "calendar_year_unconfigured")]:
            self.assertEqual(morning.eligibility(datetime.fromisoformat(stamp).replace(tzinfo=IST)), reason)
        self.assertFalse(is_nse_trading_day(date(2027, 1, 4)))
        self.assertIsNone(prior_nse_trading_session("2027-01-04"))

    def test_special_session_is_skipped_in_lookbacks(self):
        from api.services.morning_data import required_sessions
        # Muhurat on Sunday 2026-11-08 must not hide the prior regular session.
        self.assertEqual(prior_nse_trading_session("2026-11-09"), "2026-11-06")
        self.assertIsNone(morning.eligibility(datetime(2026, 11, 9, 8, 40, tzinfo=IST)))
        sessions = required_sessions("2026-11-20")
        self.assertIn("2026-11-09", sessions)
        self.assertIn("2026-11-06", sessions)
        self.assertNotIn("2026-11-08", sessions)

    def test_activity_recovers_interrupted_and_previous_day(self):
        with activity.workflow_lock():
            state = activity.save_activity({"session_date": activity.today(), "status": "running", "stage": "historical"})
            self.assertEqual(activity.read_activity()["status"], "running")
            with self.assertRaises(activity.ChecklistBusy):
                with activity.workflow_lock():
                    pass
        recovered = activity.read_activity()
        self.assertEqual(recovered["status"], "blocked")
        self.assertNotEqual(recovered["revision"], state["revision"])
        with activity.workflow_lock():
            activity.save_activity({"session_date": "2025-01-01", "status": "running", "stage": "kite"})
            self.assertEqual(activity.read_activity("2025-01-01")["status"], "blocked")

    def test_child_keeps_workflow_lock_after_parent_closes(self):
        with activity.workflow_lock() as fd:
            proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"],
                                    stdin=subprocess.PIPE, pass_fds=(fd,))
        try:
            self.assertTrue(activity.workflow_busy())
        finally:
            proc.communicate(timeout=2)
        self.assertFalse(activity.workflow_busy())

    def test_readiness_cannot_use_cached_success_during_mutation(self):
        day = activity.today()
        write_checklist_cache(good_checklist(day))
        self.assertIsNotNone(read_checklist_cache(day))
        with activity.workflow_lock():
            self.assertIsNone(read_checklist_cache(day))
        activity.save_activity({"session_date": day, "status": "blocked", "stage": "baselines"})
        self.assertIsNone(read_checklist_cache(day))

    def test_manual_history_marks_dependents_dirty(self):
        with patch("api.services.observation_runner.is_runner_running", return_value=False), \
             patch("api.services.execution_engine_runner.engine_is_running", return_value=False):
            with activity.manual_operation("historical"):
                self.assertEqual(activity.read_activity()["stage"], "historical")
        self.assertEqual(set(activity.read_activity()["dirty"]), {"baselines", "five-minute"})
        with activity.manual_operation("baselines"):
            pass
        self.assertEqual(activity.read_activity()["dirty"], ["five-minute"])

    def test_supervisor_runs_order_and_publishes_only_final_success(self):
        now = datetime(2026, 9, 23, 9, 0, tzinfo=IST)
        data = good_checklist(now.date().isoformat())
        for value in data["areas"].values():
            value["status"] = "needs_update"
        stages = []
        def child(stage, day, fd, deadline):
            self.assertTrue(activity.workflow_busy())
            if stage != "inspect":
                stages.append(stage)
                data["areas"][activity.STAGES[stage]]["status"] = "ok"
            return {"ok": True, "checklist": copy.deepcopy(data), "history_needed": data["areas"]["historical_candles"]["status"] != "ok"}
        with patch.object(morning, "datetime") as clock, patch.object(morning, "ensure_idle"), \
             patch.object(morning, "run_child", side_effect=child), patch.object(activity, "today", return_value=data["session_date"]):
            clock.now.return_value = now
            self.assertEqual(morning.run(), 0)
        self.assertEqual(stages, list(activity.STAGES))
        self.assertEqual(activity.read_activity(data["session_date"])["status"], "completed")
        self.assertIsNotNone(read_checklist_cache(data["session_date"]))

    def test_deadline_failure_never_publishes_ready(self):
        now = datetime(2026, 9, 23, 9, 0, tzinfo=IST)
        with patch.object(morning, "datetime") as clock, patch.object(morning, "ensure_idle"), \
             patch.object(morning, "run_child", side_effect=TimeoutError(morning.DEADLINE_MESSAGE)):
            clock.now.return_value = now
            self.assertEqual(morning.run(), 1)
        state = activity.read_activity(now.date().isoformat())
        self.assertEqual(state["status"], "blocked")
        self.assertIn("09:15", state["message"])
        self.assertIsNone(read_checklist_cache(now.date().isoformat()))

    def test_busy_runner_does_not_overwrite_existing_state(self):
        with activity.workflow_lock(), patch.object(morning, "LOCK_WAIT_SECONDS", 0):
            saved = activity.save_activity({"session_date": activity.today(), "status": "running", "stage": "historical"})
            self.assertEqual(morning.run(), 0)
            self.assertEqual(activity.read_activity()["revision"], saved["revision"])

    def test_writer_waits_out_a_brief_reader_lock(self):
        import threading
        held, release = threading.Event(), threading.Event()
        def reader():
            with activity.workflow_lock(shared=True):
                held.set()
                release.wait(5)
        thread = threading.Thread(target=reader)
        thread.start()
        held.wait(5)
        threading.Timer(0.6, release.set).start()
        try:
            with activity.workflow_lock(wait_seconds=5):
                self.assertTrue(release.is_set())
        finally:
            release.set()
            thread.join(5)
        held.clear(); release.clear()
        thread = threading.Thread(target=reader)
        thread.start()
        held.wait(5)
        try:
            with self.assertRaises(activity.ChecklistBusy):
                with activity.workflow_lock(wait_seconds=0.2):
                    pass
        finally:
            release.set()
            thread.join(5)

    def test_manual_kite_completes_only_after_passing_check(self):
        from api.services.token_check_cache import write_token_check
        with patch("api.services.token_check_cache.token_identity", return_value="token"):
            # Login/redirect without a check is not completion.
            with activity.manual_operation("kite"):
                pass
            state = activity.read_activity()
            self.assertEqual(state["status"], "blocked")
            self.assertIn("not validated", state["message"])
            # An earlier passing check does not count for a new operation.
            write_token_check(valid=True, user_id="OWNER")
            with activity.manual_operation("kite"):
                pass
            self.assertEqual(activity.read_activity()["status"], "blocked")
            # A failed check during the operation stays blocked.
            with activity.manual_operation("kite"):
                write_token_check(valid=False, user_id="OWNER")
            self.assertEqual(activity.read_activity()["status"], "blocked")
            with activity.manual_operation("kite"):
                write_token_check(valid=True, user_id="OWNER")
            self.assertEqual(activity.read_activity()["status"], "completed")

    def test_stopping_child_reaps_process(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        morning.stop_child(proc)
        self.assertIsNotNone(proc.poll())

    def test_retry_budget_is_bounded_and_persistent(self):
        now = datetime(2026, 9, 23, 9, 0, tzinfo=IST)
        initial = {"ok": True, "checklist": good_checklist(now.date().isoformat()), "history_needed": False}
        failure = {"ok": False, "retryable": True, "message": "Network unavailable"}
        with patch.object(morning, "datetime") as clock, patch.object(morning, "ensure_idle"), \
             patch.object(morning.time, "sleep"), patch.object(morning, "run_child", side_effect=[initial, failure, failure, failure]) as child:
            clock.now.return_value = now
            self.assertEqual(morning.run(), 1)
            self.assertEqual(child.call_count, 4)
        self.assertEqual(activity.read_activity(now.date().isoformat())["attempts"]["kite"], 3)
        with patch.object(morning, "datetime") as clock, patch.object(morning, "ensure_idle"), \
             patch.object(morning, "run_child", return_value=initial) as child:
            clock.now.return_value = now
            self.assertEqual(morning.run(), 1)
            self.assertEqual(child.call_count, 1, 'restart must not reset attempt budget')

    def test_wrong_kite_account_is_never_cached_valid(self):
        with patch("login.check_access_token_details", return_value=(True, "ok", "OTHER")), \
             patch("api.auth.settings.KITE_EXPECTED_USER_ID", "OWNER"), \
             patch("api.services.token_check_cache.write_token_check") as write:
            with self.assertRaisesRegex(ValueError, "mismatch"):
                morning.validate_kite()
            write.assert_called_once_with(valid=False, user_id="OTHER")

    def test_valid_kite_token_is_reused(self):
        with patch("login.check_access_token_details", return_value=(True, "ok", "OWNER")), \
             patch("api.auth.settings.KITE_EXPECTED_USER_ID", "OWNER"), \
             patch("api.services.token_check_cache.write_token_check") as write, \
             patch("api.services.kite_auto_login.attempt_kite_auto_login") as login:
            morning.validate_kite()
            write.assert_called_once_with(valid=True, user_id="OWNER")
            login.assert_not_called()

    def test_generator_success_does_not_override_failed_validation(self):
        data = good_checklist(activity.today())
        data['areas']['instruments'].update(status='failed', message='Missing symbols')
        with patch('instrument_collector.collect_nifty50', return_value={}), patch.object(morning, 'checklist', return_value=data):
            with self.assertRaisesRegex(ValueError, 'validation failed'):
                morning.worker('instruments', activity.today(), -1)

    def test_account_login_budget_survives_calls(self):
        from api.services.kite_auto_login_rate import check_account_budget, record_account_attempt
        with activity.workflow_lock():
            for _ in range(3):
                check_account_budget()
                record_account_attempt()
            with self.assertRaisesRegex(ValueError, 'limit'):
                check_account_budget()

    def test_dry_run_has_no_mutations(self):
        with patch.object(sys, "argv", ["morning_checklist.py", "--dry-run"]), \
             patch.object(morning, "checklist", return_value={}), patch("builtins.print"), \
             patch.object(morning, "save_activity") as save, patch.object(morning, "validate_kite") as login:
            self.assertEqual(morning.main(), 0)
            save.assert_not_called()
            login.assert_not_called()
        self.assertFalse((config.runtime_cache_dir() / "checklist-runs.db").exists())

    def test_scan_crossing_mutation_cannot_publish_a_stale_success(self):
        from api.routers.checklist import premarket_checklist
        day = activity.today()
        data = good_checklist(day)
        def scan(**kwargs):
            activity.save_activity({"session_date": day, "status": "completed", "stage": "kite", "source": "manual"})
            return data
        with patch('api.routers.checklist.fetch_premarket_checklist', side_effect=scan), \
             patch('api.routers.checklist.PreMarketChecklistResponse') as schema, \
             patch('api.routers.checklist.write_checklist_cache') as publish:
            schema.return_value.model_dump.return_value = data
            response = premarket_checklist(day, activity_only=False)
        publish.assert_not_called()
        self.assertTrue(response['activity']['revision'].endswith('-refresh'))

    def test_late_restart_does_not_mutate_or_regenerate(self):
        day = '2026-09-23'
        activity.save_activity({'session_date': day, 'status': 'blocked', 'stage': 'historical', 'source': 'automatic'})
        before = activity.read_activity(day)
        with patch.object(morning, 'datetime') as clock, patch.object(morning, 'run_child') as child:
            clock.now.return_value = datetime(2026, 9, 23, 10, 0, tzinfo=IST)
            self.assertEqual(morning.run(), 0)
        child.assert_not_called()
        self.assertEqual(activity.read_activity(day), before)

    def test_stale_but_live_engine_prevents_regeneration(self):
        with patch('api.services.observation_runner.is_runner_running', return_value=False), \
             patch('api.services.observation_runner._current_runner_pid', return_value=None), \
             patch('api.services.execution_engine_runner.engine_is_running', return_value=False), \
             patch('api.services.execution_engine_runner.heartbeat', return_value={'pid': os.getpid()}):
            with self.assertRaisesRegex(ValueError, 'active'):
                activity.ensure_market_data_idle()

    def test_child_deadline_terminates_and_reaps(self):
        # Exercise communicate timeout with a real process, without broker access.
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            with activity.workflow_lock() as fd, patch.object(morning.subprocess, "Popen", return_value=proc):
                with self.assertRaises(TimeoutError):
                    morning.run_child("historical", activity.today(), fd, datetime.now(IST) + timedelta(milliseconds=30))
            self.assertIsNotNone(proc.poll())
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


class MorningApiTests(unittest.TestCase):
    def test_activity_poll_is_authenticated_and_does_not_scan_market_data(self):
        from tests.auth_test_helpers import AuthTestHarness
        with AuthTestHarness() as h:
            url = '/api/v1/premarket-checklist?activity_only=true'
            self.assertEqual(h.client.get(url).status_code, 401)
            h.login()
            with patch('api.routers.checklist.fetch_premarket_checklist') as scan:
                response = h.client.get(url)
            self.assertEqual(response.status_code, 200)
            scan.assert_not_called()

    def test_manual_generation_conflicts_with_automatic_run(self):
        from tests.auth_test_helpers import AuthTestHarness
        with AuthTestHarness() as h:
            h.login()
            with activity.workflow_lock(), patch('api.routers.checklist.run_local_generation') as generate:
                response = h.client.post('/api/v1/premarket-checklist/generate/baselines', headers=h.csrf_headers())
            self.assertEqual(response.status_code, 409)
            generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
