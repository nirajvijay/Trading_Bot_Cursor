"""The /execution API: preconditions, status, positions, events, commands."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from tests.auth_test_helpers import (
    clear_auth_overrides,
    disable_web_auth_overrides,
    make_test_client,
)

BASE = "/api/v1/execution"


def _candidate_row(setup_id: str, symbol: str = "AAA") -> dict:
    return {
        "setup_id": setup_id,
        "continuation_rule_version": "v1",
        "session_date": "2026-09-22",
        "tradingsymbol": symbol,
        "instrument_token": 1,
        "direction": "UP",
        "trigger_price": 110.0,
        "pullback_swing_high": 113.0,
        "pullback_swing_low": 107.0,
        "tick_size": 0.05,
        "buffer_ticks": 1,
        "trigger_exchange_ts": "2026-09-22T10:00:00+00:00",
        "created_at": "2026-09-22T10:00:00+00:00",
        "vwap_classification": "ACCEPT",
        "last_price": 110.0,
        "breakout_candle_volume": 5000,
        "avg_prior_3_1m_volume": 1000.0,
    }


class ExecutionApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.db = self.dir / "execution_engine.db"
        self.status_file = self.dir / "status.json"
        self.marks_file = self.dir / "marks.json"

        self._env = {
            "EXECUTION_ENGINE_DB_PATH": str(self.db),
            "EXECUTION_ENGINE_STATUS_FILE": str(self.status_file),
            "EXECUTION_ENGINE_LIVE_MARK_FILE": str(self.marks_file),
        }
        self._saved = {k: os.environ.get(k) for k in self._env}
        os.environ.update(self._env)

        from api.main import app

        disable_web_auth_overrides()
        self.client = make_test_client()
        self.app = app

    def tearDown(self) -> None:
        clear_auth_overrides()
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    # -- fixtures ----------------------------------------------------

    def write_heartbeat(self, **overrides) -> dict:
        data = {
            "run_id": "run-1",
            "session_date": "2026-09-22",
            "state": "running",
            "pid": os.getpid(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "tick_count": 42,
            "open_positions": 1,
            "unprotected": 0,
            "entries_allowed": True,
            "entries_stopped": False,
            "entries_paused": False,
            "pause_reason": None,
            "realised_loss_today": 500.0,
            "daily_loss_cap": 3000.0,
            "remaining_daily": 2500.0,
            "is_live": False,
            "last_error": None,
            "step_failures": {},
            "escalations": {},
            "stopped_on_purpose": False,
            "stop_reason": None,
        }
        data.update(overrides)
        self.status_file.write_text(json.dumps(data), encoding="utf-8")
        return data

    def write_marks(self, pnl: Optional[dict] = None) -> None:
        payload = {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "pnl": pnl if pnl is not None else {"s1": 600.0},
            "total_pnl": sum((pnl or {"s1": 600.0}).values()),
            "complete": True,
        }
        self.marks_file.write_text(json.dumps(payload), encoding="utf-8")

    def seed_store(self):
        from engine_store import SqlitePositionStore
        from engine_types import ExecutionState, Position, TriggerCandidate

        store = SqlitePositionStore(self.db)
        try:
            store.save_with_event(
                Position(
                    trade_id="s1",
                    candidate=TriggerCandidate(**_candidate_row("s1")),
                    state=ExecutionState.PROTECTED,
                    qty=295,
                    entry_price=110.0,
                    stop_price=106.95,
                    risk_taken_rupees=899.75,
                    entry_order_id="eo1",
                    stop_order_id="so1",
                    run_id="run-1",
                ),
                "protected",
                {"stop_price": 106.95},
            )
            store.save(
                Position(
                    trade_id="s2",
                    candidate=TriggerCandidate(**_candidate_row("s2", "BBB")),
                    state=ExecutionState.CLOSED,
                    qty=100,
                    entry_price=110.0,
                    realised_pnl=-350.0,
                    run_id="run-1",
                    extra={"close_reason": "stop_hit"},
                )
            )
            store.save(
                Position(
                    trade_id="s3",
                    candidate=TriggerCandidate(**_candidate_row("s3", "CCC")),
                    state=ExecutionState.REJECTED,
                    run_id="run-1",
                    extra={"skip_reason": "vwap_reject"},
                )
            )
        finally:
            store.close()


class PreflightTests(ExecutionApiTestCase):
    def test_every_precondition_is_reported_with_its_own_reason(self) -> None:
        response = self.client.get(f"{BASE}/preflight")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        keys = {c["key"] for c in body["checks"]}
        self.assertEqual(
            keys,
            {"market_hours", "before_entry_cutoff", "observation_runner", "no_engine_running"},
        )

    def test_a_refusal_names_which_check_failed(self) -> None:
        response = self.client.get(f"{BASE}/preflight")
        body = response.json()
        if not body["can_start"]:
            self.assertTrue(body["refusals"])
            for key in body["refusals"]:
                check = next(c for c in body["checks"] if c["key"] == key)
                self.assertTrue(check["detail"])

    def test_an_already_running_engine_blocks_start(self) -> None:
        self.write_heartbeat()
        body = self.client.get(f"{BASE}/preflight").json()
        check = next(c for c in body["checks"] if c["key"] == "no_engine_running")
        self.assertFalse(check["ok"])
        self.assertFalse(body["can_start"])

    def test_no_engine_at_all_passes_that_check(self) -> None:
        body = self.client.get(f"{BASE}/preflight").json()
        check = next(c for c in body["checks"] if c["key"] == "no_engine_running")
        self.assertTrue(check["ok"])

    def test_a_crashed_engine_does_not_block_a_restart(self) -> None:
        # Silence without a stop note, from a pid that is long gone.
        self.write_heartbeat(
            pid=999_999_998,
            updated_at=(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
        )
        body = self.client.get(f"{BASE}/preflight").json()
        check = next(c for c in body["checks"] if c["key"] == "no_engine_running")
        self.assertTrue(check["ok"])
        self.assertEqual(body["engine_state"], "crashed")

    def test_a_stopped_engine_does_not_block_a_restart(self) -> None:
        self.write_heartbeat(
            stopped_on_purpose=True,
            stop_reason="eod_squareoff",
            state="stopped",
            updated_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        )
        body = self.client.get(f"{BASE}/preflight").json()
        self.assertEqual(body["engine_state"], "stopped")
        check = next(c for c in body["checks"] if c["key"] == "no_engine_running")
        self.assertTrue(check["ok"])


class StartTests(ExecutionApiTestCase):
    def test_the_cap_sanity_check_is_enforced_at_the_api_boundary(self) -> None:
        response = self.client.post(
            f"{BASE}/start",
            json={
                "caps": {
                    "per_trade_cap_rupees": 5000.0,
                    "daily_loss_cap_rupees": 3000.0,
                }
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("risk_config", response.json()["detail"])

    def test_starting_while_one_is_running_is_refused(self) -> None:
        self.write_heartbeat()
        response = self.client.post(f"{BASE}/start", json={})
        self.assertIn(response.status_code, (400, 409))
        self.assertIn("already running", response.json()["detail"])

    def test_a_refusal_explains_which_precondition_failed(self) -> None:
        response = self.client.post(f"{BASE}/start", json={})
        self.assertEqual(response.status_code, 400)
        detail = response.json()["detail"]
        self.assertTrue(
            any(
                key in detail
                for key in (
                    "market_hours",
                    "before_entry_cutoff",
                    "observation_runner",
                    "no_engine_running",
                )
            ),
            detail,
        )

    def test_tiny_validation_caps_are_accepted_by_validation(self) -> None:
        # Refused for a *precondition* reason, never for the small numbers.
        response = self.client.post(
            f"{BASE}/start",
            json={
                "caps": {
                    "per_trade_cap_rupees": 20.0,
                    "per_trade_cap_vwap_limited_rupees": 10.0,
                    "daily_loss_cap_rupees": 50.0,
                }
            },
        )
        self.assertNotIn("risk_config", str(response.json().get("detail", "")))


class StatusTests(ExecutionApiTestCase):
    def test_no_heartbeat_reads_as_absent_not_crashed(self) -> None:
        body = self.client.get(f"{BASE}/status").json()
        self.assertEqual(body["engine_state"], "absent")

    def test_a_fresh_heartbeat_reads_as_running(self) -> None:
        self.write_heartbeat()
        body = self.client.get(f"{BASE}/status").json()
        self.assertEqual(body["engine_state"], "running")
        self.assertEqual(body["tick_count"], 42)
        self.assertEqual(body["run_id"], "run-1")

    def test_silence_without_a_note_is_reported_as_crashed(self) -> None:
        self.write_heartbeat(
            updated_at=(datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        )
        body = self.client.get(f"{BASE}/status").json()
        self.assertEqual(body["engine_state"], "crashed")
        self.assertEqual(body["engine_reason"], "heartbeat_silent_without_stop_note")

    def test_silence_with_a_note_is_reported_as_stopped_with_its_reason(self) -> None:
        self.write_heartbeat(
            stopped_on_purpose=True,
            stop_reason="daily_loss_breach",
            updated_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        )
        body = self.client.get(f"{BASE}/status").json()
        self.assertEqual(body["engine_state"], "stopped")
        self.assertEqual(body["stop_reason"], "daily_loss_breach")
        self.assertTrue(body["stopped_on_purpose"])

    def test_the_risk_numbers_come_through(self) -> None:
        self.write_heartbeat()
        body = self.client.get(f"{BASE}/status").json()
        self.assertEqual(body["realised_loss_today"], 500.0)
        self.assertEqual(body["daily_loss_cap"], 3000.0)
        self.assertEqual(body["remaining_daily"], 2500.0)

    def test_capital_is_derived_and_includes_buying_power(self) -> None:
        self.write_heartbeat()
        self.seed_store()
        body = self.client.get(f"{BASE}/status").json()
        capital = body["capital"]
        self.assertEqual(capital["total_capital_rupees"], 300_000.0)
        self.assertEqual(capital["leverage_factor"], 5.0)
        self.assertEqual(capital["buying_power_rupees"], 1_500_000.0)
        # One open position of 295 @ 110 at 5x leverage.
        self.assertAlmostEqual(capital["margin_used_rupees"], 295 * 110.0 / 5.0, places=2)
        self.assertLess(capital["remaining_capital_rupees"], 300_000.0)

    def test_total_live_pnl_is_surfaced_with_its_timestamp(self) -> None:
        self.write_heartbeat()
        self.write_marks({"s1": 600.0, "s2": -150.0})
        body = self.client.get(f"{BASE}/status").json()
        self.assertAlmostEqual(body["total_live_pnl"], 450.0)
        self.assertTrue(body["live_pnl_as_of"])
        self.assertTrue(body["live_pnl_complete"])

    def test_entries_allowed_is_false_when_the_engine_is_not_running(self) -> None:
        # A stale heartbeat claiming entries_allowed must not be believed.
        self.write_heartbeat(
            entries_allowed=True,
            updated_at=(datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat(),
        )
        body = self.client.get(f"{BASE}/status").json()
        self.assertFalse(body["entries_allowed"])

    def test_a_pause_reason_is_surfaced(self) -> None:
        self.write_heartbeat(entries_paused=True, pause_reason="feed_stale")
        body = self.client.get(f"{BASE}/status").json()
        self.assertTrue(body["entries_paused"])
        self.assertEqual(body["pause_reason"], "feed_stale")

    def test_escalations_are_surfaced(self) -> None:
        self.write_heartbeat(
            escalations={"ensure_protection": "3 consecutive failures"}
        )
        body = self.client.get(f"{BASE}/status").json()
        self.assertIn("ensure_protection", body["escalations"])


class PositionsTests(ExecutionApiTestCase):
    def test_no_store_yet_returns_empty_lists_not_an_error(self) -> None:
        response = self.client.get(f"{BASE}/positions?session_date=2026-09-22")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["open"], [])
        self.assertEqual(body["closed"], [])

    def test_positions_are_split_into_open_closed_and_rejected(self) -> None:
        self.seed_store()
        body = self.client.get(f"{BASE}/positions?session_date=2026-09-22").json()
        self.assertEqual([p["trade_id"] for p in body["open"]], ["s1"])
        self.assertEqual([p["trade_id"] for p in body["closed"]], ["s2"])
        self.assertEqual([p["trade_id"] for p in body["rejected"]], ["s3"])

    def test_an_open_position_carries_the_numbers_the_desk_needs(self) -> None:
        self.seed_store()
        body = self.client.get(f"{BASE}/positions?session_date=2026-09-22").json()
        row = body["open"][0]
        self.assertEqual(row["tradingsymbol"], "AAA")
        self.assertEqual(row["qty"], 295)
        self.assertEqual(row["entry_price"], 110.0)
        self.assertEqual(row["stop_price"], 106.95)
        self.assertAlmostEqual(row["risk_taken_rupees"], 899.75)
        self.assertEqual(row["state"], "protected")

    def test_live_pnl_is_attached_per_position(self) -> None:
        self.seed_store()
        self.write_marks({"s1": 742.5})
        body = self.client.get(f"{BASE}/positions?session_date=2026-09-22").json()
        self.assertAlmostEqual(body["open"][0]["live_pnl"], 742.5)
        self.assertAlmostEqual(body["total_live_pnl"], 742.5)

    def test_a_closed_position_shows_its_reason_tag(self) -> None:
        self.seed_store()
        body = self.client.get(f"{BASE}/positions?session_date=2026-09-22").json()
        self.assertEqual(body["closed"][0]["close_reason"], "stop_hit")
        self.assertEqual(body["closed"][0]["realised_pnl"], -350.0)

    def test_a_rejected_position_shows_its_skip_reason(self) -> None:
        self.seed_store()
        body = self.client.get(f"{BASE}/positions?session_date=2026-09-22").json()
        self.assertEqual(body["rejected"][0]["skip_reason"], "vwap_reject")

    def test_a_different_session_date_returns_nothing(self) -> None:
        self.seed_store()
        body = self.client.get(f"{BASE}/positions?session_date=2026-09-21").json()
        self.assertEqual(body["open"], [])


class DeskPnlApiTests(ExecutionApiTestCase):
    """B4: totals, Stock Day Total, live/fallback badge, mismatch, unattributed."""

    def write_live_marks(self, pnl: dict, *, sources: dict, feed: dict, reasons=None) -> None:
        payload = {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "pnl": pnl,
            "total_pnl": sum(v for v in pnl.values() if v is not None),
            "complete": True,
            "source": sources,
            "reason": reasons or {},
            "feed": feed,
        }
        self.marks_file.write_text(json.dumps(payload), encoding="utf-8")

    def save(self, trade_id: str, symbol: str, state, **fields) -> None:
        from engine_store import SqlitePositionStore
        from engine_types import Position, TriggerCandidate

        store = SqlitePositionStore(self.db)
        try:
            store.save(
                Position(
                    trade_id=trade_id,
                    candidate=TriggerCandidate(**_candidate_row(trade_id, symbol)),
                    state=state,
                    run_id="run-1",
                    **fields,
                )
            )
        finally:
            store.close()

    def positions(self) -> dict:
        return self.client.get(f"{BASE}/positions?session_date=2026-09-22").json()

    def test_totals_and_badge_come_through(self) -> None:
        self.seed_store()  # s1 AAA open, s2 BBB closed at -350
        self.write_live_marks(
            {"s1": 742.5},
            sources={"s1": "ws"},
            feed={"state": "live", "reason": None, "last_tick_at": "2026-09-22T05:00:00+00:00"},
        )
        body = self.positions()
        self.assertAlmostEqual(body["total_realised_pnl"], -350.0)
        self.assertAlmostEqual(body["total_ongoing_pnl"], 742.5)
        self.assertAlmostEqual(body["total_day_pnl"], 392.5)
        self.assertEqual(body["live_pnl_feed_state"], "live")
        self.assertEqual(body["live_pnl_last_tick_at"], "2026-09-22T05:00:00+00:00")
        open_row = body["open"][0]
        self.assertEqual(open_row["live_pnl_source"], "ws")
        self.assertAlmostEqual(open_row["stock_day_total"], 742.5)
        self.assertAlmostEqual(body["closed"][0]["stock_day_total"], -350.0)

    def test_fallback_state_and_reason_come_through(self) -> None:
        self.seed_store()
        self.write_live_marks(
            {"s1": 100.0},
            sources={"s1": "kite_rest"},
            reasons={"s1": "tick_stale"},
            feed={"state": "fallback", "reason": "tick_stale", "last_tick_at": None},
        )
        body = self.positions()
        self.assertEqual(body["live_pnl_feed_state"], "fallback")
        self.assertEqual(body["live_pnl_feed_reason"], "tick_stale")
        self.assertEqual(body["open"][0]["live_pnl_reason"], "tick_stale")

    def test_same_stock_reentry_stock_day_total(self) -> None:
        from engine_types import ExecutionState

        self.save(
            "r1", "RELIANCE", ExecutionState.CLOSED, qty=10, realised_pnl=500.0,
            extra={"stock_day": {"kite_pnl": 500.0, "ours": 500.0, "diff": 0.0, "mismatch": False}},
        )
        self.save("r2", "RELIANCE", ExecutionState.PROTECTED, qty=10)
        self.write_live_marks({"r2": 200.0}, sources={"r2": "ws"}, feed={"state": "live"})
        body = self.positions()
        self.assertAlmostEqual(body["closed"][0]["stock_day_total"], 500.0)
        self.assertAlmostEqual(body["open"][0]["stock_day_total"], 700.0)
        self.assertAlmostEqual(body["total_day_pnl"], 700.0)

    def test_mismatch_and_unattributed_rows(self) -> None:
        from engine_types import ExecutionState

        self.save(
            "m1", "TCS", ExecutionState.CLOSED, qty=10, realised_pnl=650.0,
            extra={"stock_day": {"kite_pnl": 800.0, "ours": 650.0, "diff": 150.0, "mismatch": True}},
        )
        self.save("u1", "INFY", ExecutionState.CLOSED, qty=10, extra={"close_reason": "unattributed"})
        body = self.positions()
        rows = {r["trade_id"]: r for r in body["closed"]}
        self.assertEqual(rows["m1"]["pnl_mismatch"], {"kite_pnl": 800.0, "ours": 650.0, "diff": 150.0})
        self.assertAlmostEqual(rows["m1"]["stock_day_total"], 800.0)
        self.assertTrue(rows["u1"]["realised_unattributed"])
        self.assertFalse(rows["m1"]["realised_unattributed"])
        self.assertAlmostEqual(body["total_realised_pnl"], 800.0)

    def test_status_carries_the_same_totals(self) -> None:
        self.seed_store()
        self.write_heartbeat()
        self.write_live_marks({"s1": 742.5}, sources={"s1": "ws"}, feed={"state": "live"})
        with mock.patch("api.routers.execution._today", return_value="2026-09-22"):
            body = self.client.get(f"{BASE}/status").json()
        self.assertAlmostEqual(body["total_day_pnl"], 392.5)
        self.assertAlmostEqual(body["total_realised_pnl"], -350.0)
        self.assertAlmostEqual(body["total_ongoing_pnl"], 742.5)
        self.assertEqual(body["live_pnl_feed_state"], "live")

    def test_an_old_marks_file_leaves_the_badge_empty(self) -> None:
        self.seed_store()
        self.write_marks({"s1": 742.5})
        body = self.positions()
        self.assertIsNone(body["live_pnl_feed_state"])
        self.assertIsNone(body["open"][0]["live_pnl_source"])
        self.assertAlmostEqual(body["total_ongoing_pnl"], 742.5)


class EventsTests(ExecutionApiTestCase):
    def test_the_diary_is_returned_in_order(self) -> None:
        self.seed_store()
        body = self.client.get(f"{BASE}/positions/s1/events").json()
        self.assertEqual([e["event_type"] for e in body["events"]], ["protected"])
        self.assertEqual(body["events"][0]["payload"]["stop_price"], 106.95)

    def test_an_unknown_trade_returns_an_empty_diary(self) -> None:
        self.seed_store()
        body = self.client.get(f"{BASE}/positions/nope/events").json()
        self.assertEqual(body["events"], [])

    def test_no_store_yet_returns_an_empty_diary(self) -> None:
        response = self.client.get(f"{BASE}/positions/s1/events")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["events"], [])


class CommandTests(ExecutionApiTestCase):
    def test_a_command_is_refused_when_no_engine_is_running(self) -> None:
        # Queueing for a process that does not exist would silently do nothing.
        response = self.client.post(f"{BASE}/commands", json={"kind": "stop"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "engine_not_running")

    def test_an_unknown_kind_is_rejected(self) -> None:
        self.write_heartbeat()
        response = self.client.post(f"{BASE}/commands", json={"kind": "detonate"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "unknown_command_kind")

    def test_close_position_requires_a_trade_id(self) -> None:
        self.write_heartbeat()
        response = self.client.post(
            f"{BASE}/commands", json={"kind": "close_position"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "trade_id_required")

    def test_each_command_kind_is_accepted_and_starts_pending(self) -> None:
        self.write_heartbeat()
        self.seed_store()
        for payload in (
            {"kind": "stop"},
            {"kind": "start"},
            {"kind": "kill_all"},
            {"kind": "close_position", "trade_id": "s1"},
        ):
            with self.subTest(kind=payload["kind"]):
                response = self.client.post(f"{BASE}/commands", json=payload)
                self.assertEqual(response.status_code, 202)
                body = response.json()
                self.assertEqual(body["status"], "pending")
                self.assertEqual(body["kind"], payload["kind"])

    def test_a_queued_command_can_be_polled_for_its_outcome(self) -> None:
        self.write_heartbeat()
        self.seed_store()
        command_id = self.client.post(f"{BASE}/commands", json={"kind": "stop"}).json()[
            "command_id"
        ]
        response = self.client.get(f"{BASE}/commands/{command_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "pending")

    def test_polling_reflects_a_rejection_so_a_click_is_not_left_optimistic(self) -> None:
        from engine_commands import CommandQueue

        self.write_heartbeat()
        self.seed_store()
        command_id = self.client.post(f"{BASE}/commands", json={"kind": "start"}).json()[
            "command_id"
        ]
        queue = CommandQueue(self.db)
        try:
            queue.reject(command_id, "past_entry_cutoff")
        finally:
            queue.close()
        body = self.client.get(f"{BASE}/commands/{command_id}").json()
        self.assertEqual(body["status"], "rejected")
        self.assertEqual(body["result"]["reason"], "past_entry_cutoff")

    def test_an_unknown_command_id_is_a_404(self) -> None:
        self.write_heartbeat()
        self.seed_store()
        self.assertEqual(self.client.get(f"{BASE}/commands/99999").status_code, 404)


class RouterSurfaceTests(ExecutionApiTestCase):
    def test_the_old_trading_engine_router_is_gone(self) -> None:
        # Coexistence was only needed while the rebuild was additive. The old
        # engine is deleted, so any surviving route would be a dead endpoint.
        stale = [path for path in self.app.openapi()["paths"] if "trading-engine" in path]
        self.assertEqual(stale, [])

    def test_the_new_execution_routes_are_registered(self) -> None:
        paths = set(self.app.openapi()["paths"])
        for path in (
            "/api/v1/execution/status",
            "/api/v1/execution/positions",
            "/api/v1/execution/preflight",
            "/api/v1/execution/start",
            "/api/v1/execution/commands",
        ):
            self.assertIn(path, paths)


if __name__ == "__main__":
    unittest.main()
