"""The ARB funnel switch: execution_settings.json can make a session ORB only.

Missing or unreadable means on, so an old settings file trades as before.
When off, every funnel trigger is skipped with its features recorded and
ORB breakouts trade exactly as they did.
"""
from __future__ import annotations

import dataclasses
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import run_execution_engine as runner_cli
from api.services import execution_engine_runner as launcher
from api.services import execution_settings
from api.services.execution_settings import ExecutionSettings
from engine_arb import REASON_FUNNEL_DISABLED, ArbSettings, classify_funnel
from engine_config import arb_session_config
from engine_types import ExecutionState
from nse_trading_calendar import IST
from tests.test_arb_start_switch import DAY, _args
from tests.test_engine_arb_core import features as funnel_features
from tests.test_engine_arb_orb import OrbEngineTestCase
from tests.test_execution_api import BASE, ExecutionApiTestCase


class ClassifyTests(unittest.TestCase):
    def test_off_refuses_a_trigger_that_would_have_scored(self) -> None:
        feats = funnel_features(2)
        self.assertTrue(classify_funnel(feats, ArbSettings(), score0_taken_today=0).ok)
        verdict = classify_funnel(feats, ArbSettings(funnel_enabled=False), score0_taken_today=0)
        self.assertEqual((verdict.ok, verdict.reason), (False, REASON_FUNNEL_DISABLED))


class SettingsFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "execution_settings.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, **fields) -> ExecutionSettings:
        self.path.write_text(json.dumps({"autostart_strategy": "arb", "arb_risk_scale": 0.5,
                                         "arb_total_capital_rupees": 450000, **fields}))
        return execution_settings.load(self.path)

    def test_a_file_without_the_switch_keeps_the_funnel_on(self) -> None:
        settings = self.write()
        self.assertTrue(settings.arb_funnel_enabled)
        self.assertTrue(execution_settings.session_config_for(settings).arb.funnel_enabled)

    def test_false_makes_autostart_orb_only(self) -> None:
        for value in (False, "false", "off", 0):
            settings = self.write(arb_funnel_enabled=value)
            self.assertFalse(settings.arb_funnel_enabled, value)
            config = execution_settings.session_config_for(settings)
            self.assertEqual((config.is_arb, config.arb.risk_scale, config.total_capital_rupees,
                              config.arb.funnel_enabled), (True, 0.5, 450_000.0, False))

    def test_true_turns_it_back_on(self) -> None:
        for value in (True, "true", 1):
            self.assertTrue(self.write(arb_funnel_enabled=value).arb_funnel_enabled, value)

    def test_the_switch_round_trips_through_save(self) -> None:
        execution_settings.save(ExecutionSettings(autostart_strategy="arb", arb_funnel_enabled=False), self.path)
        self.assertFalse(execution_settings.load(self.path).arb_funnel_enabled)


class LaunchTests(unittest.TestCase):
    def test_the_launcher_passes_orb_only_to_the_engine_process(self) -> None:
        off = launcher.build_command(session_config=arb_session_config(funnel_enabled=False),
                                     session_date=DAY, run_id="r", live_orders=True)
        on = launcher.build_command(session_config=arb_session_config(),
                                    session_date=DAY, run_id="r", live_orders=True)
        self.assertIn("--arb-no-funnel", off)
        self.assertNotIn("--arb-no-funnel", on)
        self.assertFalse(runner_cli.build_config(runner_cli.parse_args(off[2:])).arb.funnel_enabled)
        self.assertTrue(runner_cli.build_config(runner_cli.parse_args(on[2:])).arb.funnel_enabled)

    def test_orb_only_changes_nothing_else_about_the_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            off = runner_cli.build_config(_args(Path(tmp), "--strategy-mode", "arb", "--arb-no-funnel"))
            on = runner_cli.build_config(_args(Path(tmp), "--strategy-mode", "arb"))
        self.assertEqual(dataclasses.replace(off.arb, funnel_enabled=True), on.arb)
        self.assertEqual(off.to_risk_limits(), on.to_risk_limits())

    def test_autostart_says_orb_only(self) -> None:
        from api import config
        from api.services import execution_autostart as autostart
        from api.services import observation_autostart

        with tempfile.TemporaryDirectory() as tmp:
            env = {"NIFTY_RADAR_RUNTIME_CACHE_DIR": str(Path(tmp) / "cache"),
                   "EXECUTION_ENGINE_DB_PATH": str(Path(tmp) / "engine.db")}
            with patch.dict(os.environ, env), patch(
                "api.services.execution_engine_runner.start_engine", return_value=(True, "ok", 91)
            ) as start:
                execution_settings.save(ExecutionSettings(autostart_strategy="arb", arb_funnel_enabled=False))
                config.runtime_cache_dir().mkdir(parents=True, exist_ok=True)
                observation_autostart._record_started(DAY, 4242)
                now = datetime.fromisoformat(f"{DAY}T09:15:00").replace(tzinfo=IST)
                outcome = autostart.autostart_tick(now)
                self.assertFalse(start.call_args.kwargs["session_config"].arb.funnel_enabled)
                self.assertEqual(
                    outcome,
                    "execution engine (ARB, stage 0.5, ORB only) started with live Kite orders (pid 91)",
                )


class EngineTests(OrbEngineTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.config = dataclasses.replace(
            self.config, arb=dataclasses.replace(self.config.arb, funnel_enabled=False)
        )

    def test_funnel_skipped_with_its_features_and_orb_still_trades(self) -> None:
        engine = self.engine()
        self.run_triggers(engine, self.trigger("f1", "AAA", score=2), self.orb("o1", "BBB"))
        self.assertEqual(self.skip_reason("f1"), REASON_FUNNEL_DISABLED)
        self.assertEqual(self.arb("f1")["score"], None)
        self.assertIn("volume_ratio", self.arb("f1")["features"])
        stored = self.store.get("o1")
        self.assertEqual((stored.state, stored.qty, stored.extra["risk_cap_rupees"]),
                         (ExecutionState.PROTECTED, 1000, 2500))
        self.assertEqual(self.broker.market_place_count, 1)

    def test_the_desk_heartbeat_shows_the_switch(self) -> None:
        engine = self.engine()
        engine.tick()
        self.assertFalse(engine.arb_snapshot()["funnel_enabled"])


class ApiTests(ExecutionApiTestCase):
    def test_a_desk_save_without_the_switch_keeps_it_off(self) -> None:
        execution_settings.save(ExecutionSettings(autostart_strategy="arb", arb_funnel_enabled=False))
        saved = self.client.post(f"{BASE}/settings", json={"autostart_strategy": "arb", "arb_risk_scale": 0.25})
        self.assertEqual(saved.status_code, 200)
        self.assertFalse(saved.json()["arb_funnel_enabled"])
        self.assertFalse(execution_settings.load().arb_funnel_enabled)
        turned_on = self.client.post(f"{BASE}/settings",
                                     json={"autostart_strategy": "arb", "arb_funnel_enabled": True})
        self.assertTrue(turned_on.json()["arb_funnel_enabled"])

    def test_a_desk_start_follows_the_saved_switch(self) -> None:
        execution_settings.save(ExecutionSettings(autostart_strategy="arb", arb_funnel_enabled=False))
        with patch("api.routers.execution.start_engine", return_value=(True, "started", 7)) as start:
            response = self.client.post(f"{BASE}/start", json={"strategy_mode": "arb", "live_orders": True})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(start.call_args.kwargs["session_config"].arb.funnel_enabled)


if __name__ == "__main__":
    unittest.main()
