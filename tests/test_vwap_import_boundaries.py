"""Import boundary: Kite historical only via vwap_historical_scheduler."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

VWAP_V2_MODULES = [
    "vwap_qualifier_v2.py",
    "vwap_session_cache.py",
    "vwap_arm_registry.py",
    "vwap_snapshot.py",
    "vwap_qualifier_v2_writer.py",
    "vwap_qualifier_v2_features.py",
    "vwap_qualifier_v2_config.py",
    "vwap_qualifier_v2_types.py",
]

ALLOWED_HISTORICAL = {"vwap_historical_scheduler.py"}


class VwapHistoricalGatewayTests(unittest.TestCase):
    def test_only_scheduler_calls_kite_historical(self) -> None:
        for name in VWAP_V2_MODULES:
            path = ROOT / name
            if not path.exists():
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Attribute) and func.attr == "historical_data":
                        self.fail("%s must not call historical_data" % name)


if __name__ == "__main__":
    unittest.main()
