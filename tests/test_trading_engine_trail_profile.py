import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from api.admin_config.store import AdminConfigStore
from trading_engine_risk import staged_r_desired_stop
from trading_engine_trail_profile import DEFAULT_TRAIL_PROFILE, trade_trail_profile, validate_trail_profile


class TrailProfileTests(unittest.TestCase):
    def test_default_and_legacy_match_frozen_table(self):
        self.assertEqual(trade_trail_profile(SimpleNamespace(risk_limits_json=None)),DEFAULT_TRAIL_PROFILE)

    def test_invalid_profiles_rejected(self):
        for changes in ({"trail_stage_one_r":3}, {"trail_stage_two_gap_r":2},
                        {"trail_modify_interval_seconds":1}, {"trail_min_improvement_ticks":2.5},
                        {"trail_stage_one_r":float("nan")}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_trail_profile(changes)

    def test_saved_profile_waits_for_arm_and_old_trade_keeps_its_profile(self):
        with tempfile.TemporaryDirectory() as root:
            admin=AdminConfigStore(Path(root)/"admin.db")
            original=SimpleNamespace(risk_limits_json=json.dumps(admin.load_effective_payload()))
            admin.update_config({"trail_stage_one_r":1.5,"trail_stage_two_gap_r":.25},actor="test")
            self.assertEqual(admin.load_effective_payload()["trail_stage_one_r"],1)
            admin.arm_effective_config(actor="test")
            self.assertEqual(admin.load_effective_payload()["trail_stage_one_r"],1.5)
            self.assertEqual(trade_trail_profile(original)["trail_stage_one_r"],1)
            admin.close()

    def test_custom_gap_mirrors_long_and_short(self):
        common=dict(entry=100,r_value=10,charge_bps=0,tick_size=.05,
                    stage_one_r=1,stage_two_r=2,stage_one_gap_r=1,stage_two_gap_r=.25)
        self.assertEqual(staged_r_desired_stop(direction="UP",initial_stop=90,current_stop=90,
            extreme=125,last_price=124,**common),122.5)
        self.assertEqual(staged_r_desired_stop(direction="DOWN",initial_stop=110,current_stop=110,
            extreme=75,last_price=76,**common),77.5)
