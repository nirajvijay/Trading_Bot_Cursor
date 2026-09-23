import unittest
from unittest.mock import patch
from config.nifty100_sector_map import SECTOR_MAP_V1, sector_map_payload
from config.nifty100_symbols import NIFTY_100_SYMBOLS


class SectorMapTests(unittest.TestCase):
    def test_exact_owner_group_counts_and_universe_equality(self):
        result = sector_map_payload()
        self.assertTrue(result["valid"])
        self.assertEqual([len(g["symbols"]) for g in result["sectors"]],
                         [9, 9, 2, 1, 4, 2, 4, 8, 23, 8, 6, 7, 6, 6, 2, 2, 1])
        self.assertEqual({s for g in result["sectors"] for s in g["symbols"]}, set(NIFTY_100_SYMBOLS))

    def test_duplicate_or_missing_symbol_is_hard_block(self):
        bad = SECTOR_MAP_V1[:-1] + (("Telecommunication", "HDFCBANK"),)
        result = sector_map_payload(sectors=bad)
        self.assertFalse(result["valid"])
        self.assertIn("BHARTIARTL", result["reason"])
        with patch("api.services.observation_runner.sector_map_payload", return_value=result):
            from api.services.observation_runner import fetch_checklist_summary
            self.assertEqual(fetch_checklist_summary("2026-08-03")["overall_status"], "failed")
