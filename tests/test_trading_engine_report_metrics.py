import json
import unittest
from types import SimpleNamespace
from trading_engine_report import execution_metrics


class ReportMetricsTests(unittest.TestCase):
    def trade(self, **changes):
        values = dict(trade_id="one", symbol="AAA", filled_qty=10, exited_qty=10,
                      entry_value=1010., entry_value_est=0., exit_value_est=0.,
                      status="closed", pnl_provisional=False, remaining_position_qty=0,
                      realised_pnl=40., charge_bps=10., r_value=2.,
                      entry_estimate=100., direction="UP")
        return SimpleNamespace(**(values | changes))

    def test_costs_r_and_signed_slippage_not_double_deducted(self):
        result = execution_metrics(self.trade(), [])
        self.assertAlmostEqual(result["estimated_round_trip_charges"], 1.01)
        self.assertAlmostEqual(result["estimated_net_pnl"], 38.99)
        self.assertEqual(result["gross_r_outcome"], 2)
        self.assertEqual(result["entry_slippage_vs_trigger_inr"], 10)
        self.assertAlmostEqual(result["estimated_net_r_outcome"], 38.99 / 20)
        self.assertEqual(execution_metrics(self.trade(direction="DOWN"), [])
                         ["entry_slippage_vs_trigger_inr"], -10)

    def test_incomplete_and_missing_profile_never_fabricate_outcomes(self):
        for changes in ({"entry_value_est":1}, {"pnl_provisional":True},
                        {"exited_qty":5}, {"status":"partial_exit"}):
            result = execution_metrics(self.trade(**changes), [])
            self.assertIsNone(result["estimated_net_pnl"])
            self.assertIsNone(result["gross_r_outcome"])
        result = execution_metrics(self.trade(charge_bps=None, r_value=None), [])
        self.assertIsNone(result["estimated_net_pnl"])
        self.assertIsNone(result["gross_r_outcome"])
        self.assertIsNone(result["first_observed_fill_to_cover_seconds"])

    def test_audit_delay_requires_positive_full_cover_and_aware_times(self):
        def event(action, at, **payload):
            return {"action":action, "at":at, "payload_json":json.dumps(payload)}
        events = [event("entry_fill", "2026-09-10T10:00:00+05:30"),
                  event("partial_entry", "2026-09-10T10:00:01+05:30", protected_qty=2, remaining_position_qty=5),
                  event("protected", "2026-09-10T10:00:03+05:30", protected_qty=5, remaining_position_qty=5)]
        self.assertEqual(execution_metrics(self.trade(), events)["first_observed_fill_to_cover_seconds"], 3)
        events[0]["at"] = "2026-09-10T10:00:00"
        # A later timestamp must not replace an invalid first-fill observation.
        self.assertIsNone(execution_metrics(self.trade(), events)["first_observed_fill_to_cover_seconds"])
