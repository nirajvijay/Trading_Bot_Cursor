"""Read-only session-cohort reporting: strategy outcomes are not safety evidence."""
from collections import Counter
from datetime import datetime, timezone
import math

from trading_engine_risk import is_unprotected


def session_report(store, session_date: str) -> dict:
    groups = {}
    for mode in ("PAPER", "LIVE", "UNKNOWN"):
        trades = [t for t in store.list_trades(session_date)
                  if ("UNKNOWN" if t.entry_live_orders_enabled is None else
                      "LIVE" if t.entry_live_orders_enabled else "PAPER") == mode]
        complete_closed = [t for t in trades if t.status == "closed" and not t.pnl_provisional
                           and t.entry_value_est == 0 and t.exit_value_est == 0
                           and math.isfinite(t.realised_pnl)]
        provisional = [t for t in trades if t.pnl_provisional or t.entry_value_est > 0 or t.exit_value_est > 0]
        events = Counter()
        for trade in trades:
            events.update(str(e["action"]) for e in store.list_events(trade.trade_id))
        reasons = Counter(t.skip_reason or t.reject_reason or t.close_reason or t.status for t in trades)
        groups[mode] = {
            "strategy_outcomes": {
                "observed_trade_records": len(trades),
                "filled_setups": len({t.setup_id for t in trades if t.filled_qty > 0}),
                "closed_with_complete_prices": len(complete_closed),
                "wins": sum(t.realised_pnl > 0 for t in complete_closed),
                "losses": sum(t.realised_pnl < 0 for t in complete_closed),
                "breakeven": sum(t.realised_pnl == 0 for t in complete_closed),
                "complete_closed_recorded_pnl": sum(t.realised_pnl for t in complete_closed),
                "pnl_basis": "Stored realised P&L for price-complete closed trades only; not broker-ledger net charges or open MTM.",
                "excluded_incomplete_trade_ids": [t.trade_id for t in provisional],
                "outcome_reasons": dict(reasons),
            },
            "engineering_quality": {
                "current_unprotected_trade_ids": [t.trade_id for t in trades if is_unprotected(t)],
                "current_reconciliation_trade_ids": [t.trade_id for t in trades if t.status == "reconciliation_required"],
                "unresolved_exposure_trade_ids": [t.trade_id for t in trades if t.remaining_position_qty or t.remaining_entry_qty],
                "provisional_accounting_trade_ids": [t.trade_id for t in provisional],
                "missing_original_plan_trade_ids": [t.trade_id for t in trades if t.filled_qty and not t.original_setup_json],
                "event_counts": dict(events),
                "assessment": "Recorded observations only. No fresh broker query; zero incidents is not proof of safety.",
            },
        }
    return {"report_version": "radar-v1-session-1", "session_date": session_date,
            "scope": "Trades originating in this session, including later reconciliations; not a broker calendar-day statement.",
            "generated_at": datetime.now(timezone.utc).isoformat(), "modes": groups,
            "limitations": ["PAPER fills do not establish live execution quality or profitability.",
                            "Unattributed broker orders and external account activity require the Recovery view.",
                            "Open and incomplete P&L is not included in closed-trade outcome totals."]}
