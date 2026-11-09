"""The GUC switch travels from execution_settings.json into the engine process.

The engine runs as a subprocess, so a switch the launcher does not put on the
command line stops at the API. Off is the default everywhere: a settings file
without the keys, or a command line without the flags, trades ORB only.
"""
from __future__ import annotations

import dataclasses
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
from engine_config import arb_session_config
from nse_trading_calendar import IST
from tests.test_arb_start_switch import DAY, _args
from tests.test_execution_api import BASE, ExecutionApiTestCase


class LaunchTests(unittest.TestCase):
    def test_the_launcher_passes_the_guc_switch_to_the_engine_process(self) -> None:
        on = launcher.build_command(
            session_config=arb_session_config(
                guc_enabled=True, guc_risk_scale=0.5, guc_ordinary_day_fraction=0.5
            ),
            session_date=DAY, run_id="r", live_orders=True,
        )
        off = launcher.build_command(session_config=arb_session_config(),
                                     session_date=DAY, run_id="r", live_orders=True)
        self.assertIn("--arb-guc", on)
        self.assertNotIn("--arb-guc", off)
        arb_on = runner_cli.build_config(runner_cli.parse_args(on[2:])).arb
        self.assertEqual(
            (arb_on.guc_enabled, arb_on.guc_risk_scale, arb_on.guc_ordinary_day_fraction),
            (True, 0.5, 0.5),
        )
        self.assertFalse(runner_cli.build_config(runner_cli.parse_args(off[2:])).arb.guc_enabled)

    def test_the_switch_changes_nothing_else_about_the_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            on = runner_cli.build_config(_args(Path(tmp), "--strategy-mode", "arb", "--arb-guc",
                                               "--arb-guc-risk-scale", "0.5"))
            off = runner_cli.build_config(_args(Path(tmp), "--strategy-mode", "arb"))
        self.assertEqual(dataclasses.replace(on.arb, guc_enabled=False, guc_risk_scale=1.0), off.arb)
        self.assertEqual(on.to_risk_limits(), off.to_risk_limits())
        self.assertFalse(off.arb.guc_enabled)

    def test_a_hand_launch_cannot_pick_an_unlocked_guc_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Exception):
                runner_cli.build_config(_args(Path(tmp), "--strategy-mode", "arb", "--arb-guc",
                                              "--arb-guc-risk-scale", "0.7"))

    def test_autostart_launches_with_the_saved_guc_settings(self) -> None:
        from api import config
        from api.services import execution_autostart as autostart
        from api.services import observation_autostart

        with tempfile.TemporaryDirectory() as tmp:
            env = {"NIFTY_RADAR_RUNTIME_CACHE_DIR": str(Path(tmp) / "cache"),
                   "EXECUTION_ENGINE_DB_PATH": str(Path(tmp) / "engine.db")}
            with patch.dict(os.environ, env), patch(
                "api.services.execution_engine_runner.start_engine", return_value=(True, "ok", 91)
            ) as start:
                execution_settings.save(ExecutionSettings(
                    autostart_strategy="arb", arb_risk_scale=0.5, arb_funnel_enabled=False,
                    arb_guc_enabled=True, arb_guc_risk_scale=0.5, arb_guc_ordinary_day_fraction=0.5,
                ))
                config.runtime_cache_dir().mkdir(parents=True, exist_ok=True)
                observation_autostart._record_started(DAY, 4242)
                now = datetime.fromisoformat(f"{DAY}T09:15:00").replace(tzinfo=IST)
                outcome = autostart.autostart_tick(now)
                arb = start.call_args.kwargs["session_config"].arb
                self.assertEqual(
                    (arb.risk_scale, arb.funnel_enabled, arb.guc_enabled, arb.guc_risk_scale,
                     arb.guc_ordinary_day_fraction),
                    (0.5, False, True, 0.5, 0.5),
                )
                self.assertEqual(
                    outcome,
                    "execution engine (ARB, stage 0.5, ORB only, GUC x0.5) started with live Kite orders (pid 91)",
                )


class ApiTests(ExecutionApiTestCase):
    def test_a_desk_start_follows_the_saved_guc_switch(self) -> None:
        execution_settings.save(ExecutionSettings(
            autostart_strategy="arb", arb_guc_enabled=True, arb_guc_risk_scale=0.5
        ))
        with patch("api.routers.execution.start_engine", return_value=(True, "started", 7)) as start:
            response = self.client.post(f"{BASE}/start", json={"strategy_mode": "arb", "live_orders": True})
        self.assertEqual(response.status_code, 200)
        arb = start.call_args.kwargs["session_config"].arb
        self.assertEqual((arb.guc_enabled, arb.guc_risk_scale), (True, 0.5))

    def test_a_desk_save_without_the_switch_keeps_it_on(self) -> None:
        execution_settings.save(ExecutionSettings(
            autostart_strategy="arb", arb_guc_enabled=True, arb_guc_risk_scale=0.5
        ))
        saved = self.client.post(f"{BASE}/settings", json={"autostart_strategy": "arb", "arb_risk_scale": 0.5})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual((saved.json()["arb_guc_enabled"], saved.json()["arb_guc_risk_scale"]), (True, 0.5))
        loaded = execution_settings.load()
        self.assertEqual((loaded.arb_guc_enabled, loaded.arb_guc_risk_scale), (True, 0.5))


if __name__ == "__main__":
    unittest.main()
