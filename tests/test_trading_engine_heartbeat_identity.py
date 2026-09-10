"""Status must belong to the running session, not merely have fresh timestamps."""
import unittest
from api.queries.trading import current_run_heartbeat


class HeartbeatIdentityTests(unittest.TestCase):
    def test_current_run_required_even_when_snapshot_is_fresh(self):
        run = {"run_id": "new", "session_date": "2026-09-10"}
        heartbeat = {**run, "broker_sync_at": "2026-09-10T05:00:00+00:00"}
        self.assertEqual(current_run_heartbeat(heartbeat, run, running=True,
                                              session_date=run["session_date"]), heartbeat)
        for observed, actual, running, day in (
            ({**heartbeat, "run_id": "old"}, run, True, run["session_date"]),
            ({"session_date": run["session_date"]}, run, True, run["session_date"]),
            (heartbeat, run, False, run["session_date"]),
            (heartbeat, None, True, run["session_date"]),
            (heartbeat, run, True, "2026-09-11"),
        ):
            with self.subTest(observed=observed, running=running, day=day):
                self.assertEqual(current_run_heartbeat(observed, actual,
                    running=running, session_date=day), {})
