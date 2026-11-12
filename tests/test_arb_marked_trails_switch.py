"""The marked-trails switch travels from execution_settings.json into the
engine process. Off is the default everywhere: a settings file without the
keys, or a command line without the flag, leaves ARB exactly as it was."""
from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run_execution_engine as runner_cli
from api.services import execution_engine_runner as launcher
from api.services import execution_settings
from api.services.execution_settings import ExecutionSettings
from engine_config import arb_session_config
from tests.test_arb_start_switch import DAY, _args
from tests.test_execution_api import BASE, ExecutionApiTestCase


class LaunchTests(unittest.TestCase):
    def test_the_launcher_passes_the_switch_to_the_engine_process(self) -> None:
        on = launcher.build_command(
            session_config=arb_session_config(marked_trails_enabled=True, marked_trails_drop_below_actual=True),
            session_date=DAY, run_id="r", live_orders=True,
        )
        off = launcher.build_command(session_config=arb_session_config(),
                                     session_date=DAY, run_id="r", live_orders=True)
        self.assertIn("--arb-marked-trails", on)
        self.assertIn("--arb-marked-trails-drop-below-actual", on)
        self.assertNotIn("--arb-marked-trails", off)
        arb_on = runner_cli.build_config(runner_cli.parse_args(on[2:])).arb
        self.assertEqual((arb_on.marked_trails_enabled, arb_on.marked_trails_drop_below_actual), (True, True))
        arb_off = runner_cli.build_config(runner_cli.parse_args(off[2:])).arb
        self.assertEqual((arb_off.marked_trails_enabled, arb_off.marked_trails_drop_below_actual), (False, False))

    def test_the_switch_changes_nothing_else_about_the_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            on = runner_cli.build_config(_args(Path(tmp), "--strategy-mode", "arb", "--arb-marked-trails"))
            off = runner_cli.build_config(_args(Path(tmp), "--strategy-mode", "arb"))
        self.assertEqual(dataclasses.replace(on.arb, marked_trails_enabled=False), off.arb)
        self.assertEqual(on.to_risk_limits(), off.to_risk_limits())


class SettingsFileTests(unittest.TestCase):
    def test_missing_keys_mean_off_and_a_round_trip_keeps_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "execution_settings.json"
            path.write_text('{"autostart_strategy": "arb", "arb_risk_scale": 0.5}')
            loaded = execution_settings.load(path)
            self.assertEqual((loaded.arb_marked_trails_enabled, loaded.arb_marked_trails_drop_below_actual),
                             (False, False))
            execution_settings.save(dataclasses.replace(loaded, arb_marked_trails_enabled=True), path)
            again = execution_settings.load(path)
            self.assertTrue(again.arb_marked_trails_enabled)
            self.assertTrue(execution_settings.session_config_for(again).arb.marked_trails_enabled)


class ApiTests(ExecutionApiTestCase):
    def test_a_desk_start_follows_the_saved_switch(self) -> None:
        execution_settings.save(ExecutionSettings(autostart_strategy="arb", arb_marked_trails_enabled=True))
        with patch("api.routers.execution.start_engine", return_value=(True, "started", 7)) as start:
            response = self.client.post(f"{BASE}/start", json={"strategy_mode": "arb", "live_orders": True})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(start.call_args.kwargs["session_config"].arb.marked_trails_enabled)

    def test_a_desk_save_without_the_switch_keeps_it(self) -> None:
        execution_settings.save(ExecutionSettings(autostart_strategy="arb", arb_marked_trails_enabled=True))
        saved = self.client.post(f"{BASE}/settings", json={"autostart_strategy": "arb", "arb_risk_scale": 0.5})
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.json()["arb_marked_trails_enabled"])
        self.assertTrue(execution_settings.load().arb_marked_trails_enabled)


if __name__ == "__main__":
    unittest.main()
