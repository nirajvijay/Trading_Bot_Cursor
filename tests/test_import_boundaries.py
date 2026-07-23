"""
Import-direction enforcement for Strategy Architecture Principles.

Market-data modules must never import strategy modules.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MARKET_DATA_MODULES = (
    "tick_receiver.py",
    "tick_event.py",
    "kite_tick_normalizer.py",
    "one_minute_candle_builder.py",
    "live_one_minute_candle_writer.py",
    "candle_aggregation.py",
    "candle_emission.py",
    "historical_collector.py",
    "baseline_generator.py",
    "five_minute_candle_generator.py",
    "instrument_collector.py",
)

# Composition seams may import both sides; excluded from this check.
COMPOSITION_MODULES = {
    "market_data_coordinator.py",
    "live_candle_pipeline.py",
}

STRATEGY_MODULES = {
    "spike_types",
    "spike_features",
    "spike_metrics",
    "intraday_spike_config",
    "intraday_spike_rules",
    "intraday_spike_detector",
    "intraday_spike_writer",
    "candle_quality_gate",
    "baseline_store",
}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    return names


class ImportBoundaryTests(unittest.TestCase):
    def test_market_data_modules_do_not_import_strategy(self) -> None:
        violations: list[str] = []
        for filename in MARKET_DATA_MODULES:
            path = ROOT / filename
            self.assertTrue(path.exists(), f"missing market-data module: {filename}")
            imported = _imported_modules(path)
            bad = sorted(imported & STRATEGY_MODULES)
            if bad:
                violations.append(f"{filename} imports {bad}")
        self.assertEqual(violations, [])

    def test_composition_modules_exist_as_allowed_seam(self) -> None:
        for filename in COMPOSITION_MODULES:
            self.assertTrue((ROOT / filename).exists(), filename)


if __name__ == "__main__":
    unittest.main()
