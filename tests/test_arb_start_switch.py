"""Starting an ARB session: config, CLI, launcher guards, settings, autostart, API.

ARB may only be started with real orders, only at the unlocked stage, and
never on a day another strategy has traded. Legacy starts are unchanged.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import run_execution_engine as runner_cli
from api.services import execution_engine_runner as launcher
from api.services import execution_settings
from api.services.execution_settings import ExecutionSettings
from engine_arb import ARB_EXTRA_KEY
from engine_arb_context import LiveArbContext
from engine_commands import CommandQueue
from engine_config import (
    ARB_UNLOCKED_SCALE,
    STRATEGY_ARB,
    STRATEGY_LEGACY_VWAP,
    InvalidSessionConfig,
    SessionRiskConfig,
    arb_session_config,
    validate,
    validate_start,
)
from engine_store import SqlitePositionStore
from engine_types import ExecutionState, Position, TriggerCandidate
from nse_trading_calendar import IST
from tests.test_execution_api import BASE, ExecutionApiTestCase, _candidate_row

DAY = "2026-09-22"


def _args(tmp: Path, *extra: str):
    return runner_cli.parse_args(
        ["--engine-db", str(tmp / "engine.db"), "--live-db", str(tmp / "live.db"),
         "--status-file", str(tmp / "status.json"), "--session-date", DAY, *extra]
    )


def _position(trade_id: str, *, state=ExecutionState.CLOSED, arb=None, symbol="AAA", **fields) -> Position:
    extra = dict(fields.pop("extra", {}))
    if arb is not None:
        extra[ARB_EXTRA_KEY] = arb
    return Position(
        trade_id=trade_id,
        candidate=TriggerCandidate(**_candidate_row(trade_id, symbol)),
        state=state,
        extra=extra,
        **fields,
    )


class ConfigTests(unittest.TestCase):
    def test_an_arb_session_carries_only_stage_and_capital(self) -> None:
        config = arb_session_config()
        self.assertTrue(config.is_arb)
        self.assertEqual((config.arb.risk_scale, config.total_capital_rupees), (0.25, 500_000.0))
        validate_start(config)
        # The risk policy is ARB's scaled backstop, not the legacy caps.
        self.assertEqual(config.to_risk_limits().daily_loss_cap_rupees, 1500.0)

    def test_a_locked_stage_cannot_be_started_though_its_numbers_are_valid(self) -> None:
        for scale in (0.5, 1.0):
            config = arb_session_config(risk_scale=scale)
            validate(config)   # the numbers themselves are fine
            with self.assertRaises(InvalidSessionConfig) as raised:
                validate_start(config)
            self.assertIn("locked", str(raised.exception))

    def test_the_unlocked_stage_is_quarter(self) -> None:
        self.assertEqual(ARB_UNLOCKED_SCALE, 0.25)

    def test_legacy_starts_are_untouched_by_the_stage_rule(self) -> None:
        validate_start(SessionRiskConfig())


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_default_is_still_legacy(self) -> None:
        args = _args(self.tmp)
        self.assertEqual(args.strategy_mode, STRATEGY_LEGACY_VWAP)
        config = runner_cli.build_config(args)
        self.assertFalse(config.is_arb)
        self.assertEqual((config.per_trade_cap_rupees, config.daily_loss_cap_rupees), (900.0, 3000.0))

    def test_arb_mode_builds_an_arb_session(self) -> None:
        config = runner_cli.build_config(_args(self.tmp, "--strategy-mode", "arb", "--total-capital", "400000"))
        self.assertEqual((config.strategy_mode, config.arb.risk_scale, config.total_capital_rupees),
                         (STRATEGY_ARB, 0.25, 400_000.0))

    def test_a_hand_launched_process_cannot_bypass_the_stage_limit(self) -> None:
        with self.assertRaises(InvalidSessionConfig):
            runner_cli.build_config(_args(self.tmp, "--strategy-mode", "arb", "--arb-risk-scale", "0.5"))
        with self.assertRaises(InvalidSessionConfig):
            runner_cli.build_config(_args(self.tmp, "--strategy-mode", "arb", "--arb-risk-scale", "0.07"))

    def _engine(self, *extra: str):
        args = _args(self.tmp, *extra)
        store = SqlitePositionStore(args.engine_db)
        queue = CommandQueue(args.engine_db)
        self.addCleanup(store.close)
        self.addCleanup(queue.close)
        return runner_cli.build_engine(args, store=store, queue=queue, floor_iso="2026-09-22T03:45:00+00:00")

    def test_an_arb_engine_reads_both_signal_sources_and_the_market_context(self) -> None:
        engine = self._engine("--strategy-mode", "arb")
        self.assertTrue(engine.arb_mode)
        self.assertIsInstance(engine.arb_context, LiveArbContext)
        self.assertIs(engine.candidate_source.func, runner_cli.fetch_arb_candidates_since)
        self.assertEqual(engine.risk_policy.limits, engine.session_config.to_risk_limits())

    def test_a_legacy_engine_never_gets_the_arb_source(self) -> None:
        engine = self._engine()
        self.assertFalse(engine.arb_mode)
        self.assertIsNone(engine.arb_context)
        self.assertIs(engine.candidate_source.func, runner_cli.fetch_triggered_with_vwap_since)

    def test_the_heartbeat_says_what_the_session_is(self) -> None:
        status = runner_cli.snapshot_status(self._engine("--strategy-mode", "arb"))
        self.assertEqual((status.strategy_mode, status.total_capital, status.leverage), ("arb", 300_000.0, 5.0))
        self.assertEqual(status.daily_loss_cap, 1500.0)           # the scaled closed-loss backstop
        self.assertEqual(status.arb["risk_scale"], 0.25)
        self.assertEqual((status.arb["orb_risk"], status.arb["hard_day_stop"], status.arb["open_risk_cap"]),
                         (625.0, 1250.0, 1500.0))
        self.assertEqual(status.arb["funnel_risks"], [250.0, 625.0, 1125.0, 1500.0])
        self.assertIsNone(status.arb["halted_reason"])
        legacy = runner_cli.snapshot_status(self._engine())
        self.assertEqual((legacy.strategy_mode, legacy.arb, legacy.daily_loss_cap), ("legacy_vwap", None, 3000.0))

    def test_a_failing_snapshot_never_stops_the_heartbeat(self) -> None:
        engine = self._engine("--strategy-mode", "arb")
        with patch.object(engine, "arb_snapshot", side_effect=RuntimeError("boom")):
            status = runner_cli.snapshot_status(engine)
        self.assertEqual(status.arb, {"error": "boom"})


class LauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "engine.db"
        self.store = SqlitePositionStore(self.db)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def conflict(self, mode: str):
        return launcher.strategy_day_conflict(DAY, mode, db_path=self.db)

    def test_the_command_carries_the_strategy_and_stage(self) -> None:
        arb = launcher.build_command(session_config=arb_session_config(), session_date=DAY,
                                     run_id="r", live_orders=True)
        self.assertEqual(arb[arb.index("--strategy-mode") + 1], "arb")
        self.assertEqual(arb[arb.index("--arb-risk-scale") + 1], "0.25")
        self.assertEqual(arb[-1], "--live-orders")
        legacy = launcher.build_command(session_config=SessionRiskConfig(), session_date=DAY,
                                        run_id="r", live_orders=False)
        self.assertEqual(legacy[legacy.index("--strategy-mode") + 1], "legacy_vwap")
        self.assertNotIn("--arb-risk-scale", legacy)
        # What the launcher sends is something the CLI accepts.
        self.assertTrue(runner_cli.build_config(runner_cli.parse_args(arb[2:])).is_arb)
        self.assertFalse(runner_cli.build_config(runner_cli.parse_args(legacy[2:])).is_arb)

    def test_arb_is_refused_without_real_orders(self) -> None:
        ok, message, pid = launcher._start_engine(session_config=arb_session_config(), live_orders=False)
        self.assertEqual((ok, message, pid), (False, "arb_requires_live_orders", None))

    def test_a_clean_day_allows_either_strategy(self) -> None:
        self.assertIsNone(self.conflict(STRATEGY_ARB))
        self.assertIsNone(self.conflict(STRATEGY_LEGACY_VWAP))
        self.assertIsNone(launcher.strategy_day_conflict(DAY, STRATEGY_ARB, db_path=Path(self._tmp.name) / "none.db"))

    def test_a_legacy_trade_blocks_arb_for_the_day(self) -> None:
        self.store.save(_position("s1", qty=10, entry_price=110.0, realised_pnl=5.0))
        self.assertIn("legacy_vwap already traded today", self.conflict(STRATEGY_ARB))
        self.assertIsNone(self.conflict(STRATEGY_LEGACY_VWAP))

    def test_an_arb_trade_blocks_legacy_for_the_day(self) -> None:
        self.store.save(_position("s1", state=ExecutionState.PROTECTED, qty=10, entry_price=110.0,
                                  arb={"strategy": "ORB", "risk_scale": 0.05}))
        self.assertIn("arb already traded today", self.conflict(STRATEGY_LEGACY_VWAP))
        self.assertIsNone(self.conflict(STRATEGY_ARB))

    def test_skipped_signals_do_not_count_as_trading(self) -> None:
        self.store.save(_position("s1", state=ExecutionState.REJECTED, arb={"strategy": "FUNNEL"},
                                  extra={"skip_reason": "arb_volume_below_min"}))
        self.store.save(_position("s2", state=ExecutionState.REJECTED, extra={"skip_reason": "vwap_reject"}))
        self.assertIsNone(self.conflict(STRATEGY_ARB))
        self.assertIsNone(self.conflict(STRATEGY_LEGACY_VWAP))

    def test_another_days_trades_do_not_count(self) -> None:
        self.store.save(_position("s1", qty=10, entry_price=110.0, realised_pnl=5.0))
        self.assertIsNone(launcher.strategy_day_conflict("2026-09-23", STRATEGY_ARB, db_path=self.db))

    def test_a_day_arb_stopped_can_only_be_restarted_as_arb(self) -> None:
        self.store.save_arb_day(DAY, halted_reason="hard_day_stop", halted_at="x", halted_mtm=-250.0)
        self.assertIn("ARB stopped for the day (hard_day_stop)", self.conflict(STRATEGY_LEGACY_VWAP))
        # An ARB restart is allowed: it restores the halt and only flattens.
        self.assertIsNone(self.conflict(STRATEGY_ARB))

    def test_an_unreadable_database_refuses_rather_than_guesses(self) -> None:
        bad = Path(self._tmp.name) / "bad.db"
        bad.write_text("not a database")
        self.assertIn("unreadable", launcher.strategy_day_conflict(DAY, STRATEGY_ARB, db_path=bad))

    def test_preflight_reports_the_rule_with_its_own_reason(self) -> None:
        self.store.save(_position("s1", qty=10, entry_price=110.0, realised_pnl=5.0))
        now = datetime.fromisoformat(f"{DAY}T10:00:00").replace(tzinfo=IST)
        with patch.dict(os.environ, {"EXECUTION_ENGINE_DB_PATH": str(self.db),
                                     "EXECUTION_ENGINE_STATUS_FILE": str(Path(self._tmp.name) / "s.json")}):
            checks = {c.key: c for c in launcher.preflight(arb_session_config(), now=now).checks}
            self.assertFalse(checks["one_strategy_per_day"].ok)
            self.assertIn("one strategy per day", checks["one_strategy_per_day"].detail)
            self.assertTrue(checks["risk_config"].ok)
            locked = {c.key: c for c in launcher.preflight(arb_session_config(risk_scale=0.5), now=now).checks}
            self.assertFalse(locked["risk_config"].ok)
            self.assertIn("locked", locked["risk_config"].detail)


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "execution_settings.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_nothing_saved_means_legacy(self) -> None:
        settings = execution_settings.load(self.path)
        self.assertEqual(settings.autostart_strategy, STRATEGY_LEGACY_VWAP)
        self.assertEqual(execution_settings.session_config_for(settings), SessionRiskConfig())

    def test_arb_round_trips(self) -> None:
        execution_settings.save(ExecutionSettings(autostart_strategy="arb", arb_total_capital_rupees=450_000.0,
                                                  updated_by="nj"), self.path)
        settings = execution_settings.load(self.path)
        config = execution_settings.session_config_for(settings)
        self.assertEqual((settings.autostart_strategy, settings.updated_by), ("arb", "nj"))
        self.assertEqual((config.strategy_mode, config.arb.risk_scale, config.total_capital_rupees),
                         ("arb", 0.25, 450_000.0))

    def test_a_locked_stage_or_unknown_strategy_cannot_be_saved(self) -> None:
        with self.assertRaises(ValueError):
            execution_settings.save(ExecutionSettings(autostart_strategy="arb", arb_risk_scale=0.5), self.path)
        with self.assertRaises(ValueError):
            execution_settings.save(ExecutionSettings(autostart_strategy="martingale"), self.path)
        with self.assertRaises(ValueError):
            execution_settings.save(ExecutionSettings(autostart_strategy="arb", arb_total_capital_rupees=0), self.path)
        self.assertFalse(self.path.exists())

    def test_a_broken_or_tampered_file_falls_back_to_legacy(self) -> None:
        for content in ("{not json", json.dumps({"autostart_strategy": "martingale"}),
                        json.dumps({"autostart_strategy": "arb", "arb_risk_scale": 1.0}),
                        json.dumps({"autostart_strategy": "arb", "arb_risk_scale": "x"}), json.dumps([1, 2])):
            self.path.write_text(content)
            self.assertEqual(execution_settings.load(self.path).autostart_strategy, STRATEGY_LEGACY_VWAP, content)


class AutostartTests(unittest.TestCase):
    def test_autostart_launches_what_the_desk_saved(self) -> None:
        from api.services import execution_autostart as autostart
        from api.services import observation_autostart
        from api import config

        with tempfile.TemporaryDirectory() as tmp:
            env = {"NIFTY_RADAR_RUNTIME_CACHE_DIR": str(Path(tmp) / "cache"),
                   "EXECUTION_ENGINE_DB_PATH": str(Path(tmp) / "engine.db")}
            with patch.dict(os.environ, env), patch(
                "api.services.execution_engine_runner.start_engine", return_value=(True, "ok", 91)
            ) as start:
                execution_settings.save(ExecutionSettings(autostart_strategy="arb"))
                config.runtime_cache_dir().mkdir(parents=True, exist_ok=True)
                observation_autostart._record_started(DAY, 4242)
                now = datetime.fromisoformat(f"{DAY}T09:15:00").replace(tzinfo=IST)
                outcome = autostart.autostart_tick(now)
                sent = start.call_args.kwargs
                self.assertTrue(sent["session_config"].is_arb)
                self.assertEqual(sent["session_config"].arb.risk_scale, 0.25)
                self.assertTrue(sent["live_orders"])
                self.assertEqual(outcome, "execution engine (ARB, stage 0.25) started with live Kite orders (pid 91)")


class StartApiTests(ExecutionApiTestCase):
    def test_arb_without_real_orders_is_refused_at_the_api(self) -> None:
        response = self.client.post(f"{BASE}/start", json={"strategy_mode": "arb", "live_orders": False})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "arb_requires_live_orders")

    def test_an_unknown_strategy_is_refused(self) -> None:
        response = self.client.post(f"{BASE}/start", json={"strategy_mode": "martingale"})
        self.assertEqual((response.status_code, response.json()["detail"]), (400, "unknown_strategy_mode"))

    def test_a_locked_stage_is_refused_with_its_reason(self) -> None:
        response = self.client.post(
            f"{BASE}/start", json={"strategy_mode": "arb", "live_orders": True, "arb_risk_scale": 0.5}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("locked", response.json()["detail"])

    def test_an_arb_start_reaches_the_launcher_as_an_arb_session(self) -> None:
        with patch("api.routers.execution.start_engine", return_value=(True, "started", 7)) as start:
            response = self.client.post(
                f"{BASE}/start",
                json={"strategy_mode": "arb", "live_orders": True,
                      "caps": {"total_capital_rupees": 450000}},
            )
        self.assertEqual(response.status_code, 200)
        sent = start.call_args.kwargs
        self.assertTrue(sent["session_config"].is_arb)
        self.assertEqual((sent["session_config"].arb.risk_scale, sent["session_config"].total_capital_rupees),
                         (0.25, 450_000.0))
        self.assertTrue(sent["live_orders"])

    def test_a_plain_start_is_still_legacy(self) -> None:
        with patch("api.routers.execution.start_engine", return_value=(True, "started", 7)) as start:
            self.client.post(f"{BASE}/start", json={})
        self.assertFalse(start.call_args.kwargs["session_config"].is_arb)

    def test_settings_show_the_stages_and_what_is_unlocked(self) -> None:
        body = self.client.get(f"{BASE}/settings").json()
        self.assertEqual((body["autostart_strategy"], body["unlocked_scale"], body["day_lock_keep"]),
                         ("legacy_vwap", 0.25, 0.4))
        stages = {s["key"]: s for s in body["stages"]}
        self.assertEqual([s["key"] for s in body["stages"]], ["micro", "quarter", "half", "full"])
        self.assertEqual([s["unlocked"] for s in body["stages"]], [True, True, False, False])
        self.assertEqual((stages["micro"]["orb_risk"], stages["micro"]["hard_day_stop"]), (125.0, 250.0))
        self.assertEqual(stages["full"]["funnel_risks"], [1000.0, 2500.0, 4500.0, 6000.0])

    def test_the_autostart_choice_is_saved_and_read_back(self) -> None:
        saved = self.client.post(
            f"{BASE}/settings",
            json={"autostart_strategy": "arb", "arb_risk_scale": 0.05, "arb_total_capital_rupees": 400000},
        )
        self.assertEqual(saved.status_code, 200)
        body = self.client.get(f"{BASE}/settings").json()
        self.assertEqual((body["autostart_strategy"], body["arb_total_capital_rupees"]), ("arb", 400000.0))
        self.assertIsNotNone(body["updated_at"])
        refused = self.client.post(
            f"{BASE}/settings", json={"autostart_strategy": "arb", "arb_risk_scale": 0.5}
        )
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(self.client.get(f"{BASE}/settings").json()["arb_risk_scale"], 0.05)

    def test_status_passes_through_the_strategy_and_arb_day(self) -> None:
        self.write_heartbeat(strategy_mode="arb", total_capital=500000.0, leverage=5.0,
                             arb={"risk_scale": 0.05, "day_mtm": -40.0, "lock_armed": False})
        body = self.client.get(f"{BASE}/status").json()
        self.assertEqual((body["strategy_mode"], body["arb"]["day_mtm"]), ("arb", -40.0))
        self.assertEqual(body["caps"]["daily_loss_cap_rupees"], 3000.0)
        legacy = self.write_heartbeat()
        self.assertNotIn("strategy_mode", legacy)
        body = self.client.get(f"{BASE}/status").json()
        self.assertEqual((body["strategy_mode"], body["arb"]), ("legacy_vwap", None))

    def test_capital_shown_is_what_the_session_started_with(self) -> None:
        self.seed_store()
        self.write_heartbeat(total_capital=500000.0, leverage=5.0)
        self.assertEqual(self.client.get(f"{BASE}/status").json()["capital"]["total_capital_rupees"], 500000.0)
        self.write_heartbeat()   # an older engine wrote no capital: the default, as before
        self.assertEqual(self.client.get(f"{BASE}/status").json()["capital"]["total_capital_rupees"], 300000.0)

    def test_positions_carry_the_arb_columns(self) -> None:
        store = SqlitePositionStore(self.db)
        try:
            orb = dict(_candidate_row("o1", "AAA"), strategy="ORB", orb_level=109.9, planned_stop=106.5)
            store.save(Position(
                trade_id="o1", candidate=TriggerCandidate(**orb), state=ExecutionState.PROTECTED,
                qty=30, entry_price=110.0, stop_price=110.85, risk_taken_rupees=105.0, stop_order_id="so",
                extra={"initial_stop_price": 106.5,
                       ARB_EXTRA_KEY: {"strategy": "ORB", "risk_scale": 0.05, "base_risk": 125.0,
                                       "risk_used": 125.0, "half_size": False}},
            ))
            store.save(_position("f1", symbol="BBB", qty=10, entry_price=110.11, stop_price=107.0, realised_pnl=31.1,
                                 arb={"strategy": "FUNNEL", "score": 2, "risk_scale": 0.05},
                                 extra={"initial_stop_price": 107.0, "close_reason": "stop_hit"}))
            store.save(_position("s9", symbol="CCC", qty=10, entry_price=110.0, realised_pnl=1.0))
        finally:
            store.close()
        body = self.client.get(f"{BASE}/positions", params={"session_date": DAY}).json()
        (open_row,) = body["open"]
        self.assertEqual((open_row["source"], open_row["signal_price"]), ("ORB", 109.9))
        self.assertAlmostEqual(open_row["entry_slippage_bps"], 9.1, places=1)   # 110.0 against the 109.9 level
        self.assertEqual((open_row["lock_stage"], open_row["risk_freed"]), ("locked", True))
        self.assertEqual(open_row["arb"]["risk_used"], 125.0)
        closed = {row["trade_id"]: row for row in body["closed"]}
        self.assertEqual((closed["f1"]["source"], closed["f1"]["r_gross"]), ("F2", 1.0))
        self.assertAlmostEqual(closed["f1"]["entry_slippage_bps"], 10.0, places=1)
        # A legacy trade has none of it.
        self.assertEqual((closed["s9"]["source"], closed["s9"]["arb"], closed["s9"]["r_gross"]), (None, None, None))


if __name__ == "__main__":
    unittest.main()
