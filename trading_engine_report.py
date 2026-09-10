"""Read-only session-cohort reporting: strategy outcomes are not safety evidence."""
from collections import Counter
from datetime import datetime, timezone
import math
import json

from trading_engine_risk import is_unprotected


def _instant(raw):
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return value if value.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def execution_metrics(trade, events):
    """Derived diagnostics, never broker-ledger charges or invented prices."""
    priced_entry = (trade.filled_qty > 0 and trade.entry_value_est == 0
                    and math.isfinite(trade.entry_value) and trade.entry_value > 0)
    complete = (priced_entry and trade.status == "closed" and not trade.pnl_provisional
                and trade.exit_value_est == 0 and trade.exited_qty == trade.filled_qty
                and trade.remaining_position_qty == 0 and math.isfinite(trade.realised_pnl))
    average = trade.entry_value / trade.filled_qty if priced_entry else None
    charges = (trade.entry_value * trade.charge_bps / 10000
               if complete and trade.charge_bps is not None
               and math.isfinite(trade.charge_bps) and trade.charge_bps >= 0 else None)
    r_cash = (trade.r_value * trade.filled_qty if complete and trade.r_value is not None
              and math.isfinite(trade.r_value) and trade.r_value > 0 else None)
    slippage = None
    if average is not None and trade.entry_estimate > 0 and math.isfinite(trade.entry_estimate):
        direction = 1 if trade.direction == "UP" else -1 if trade.direction == "DOWN" else None
        if direction is not None:
            slippage = direction * (average - trade.entry_estimate) * trade.filled_qty
    first_fill = None
    saw_first_fill = False
    delay = None
    for event in events:
        stamp = _instant(event["at"])
        try:
            payload = json.loads(event["payload_json"] or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        if event["action"] in ("entry_fill", "entry_filled", "partial_entry") and not saw_first_fill:
            saw_first_fill = True
            first_fill = stamp
        cover, remaining = payload.get("protected_qty"), payload.get("remaining_position_qty")
        if (first_fill is not None and stamp is not None and isinstance(cover, (int,float))
                and isinstance(remaining, (int,float)) and remaining > 0 and cover >= remaining):
            elapsed = (stamp - first_fill).total_seconds()
            if elapsed >= 0:
                delay = elapsed
                break
    return {"trade_id": trade.trade_id, "symbol": trade.symbol,
            "price_complete_closed": complete,
            "estimated_round_trip_charges": charges,
            "estimated_net_pnl": trade.realised_pnl - charges if charges is not None else None,
            "gross_r_outcome": trade.realised_pnl / r_cash if r_cash else None,
            "estimated_net_r_outcome": (trade.realised_pnl - charges) / r_cash if r_cash and charges is not None else None,
            "entry_slippage_vs_trigger_inr": slippage,
            "first_observed_fill_to_cover_seconds": delay}


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
        metrics = []
        for trade in trades:
            rows = store.list_events(trade.trade_id)
            events.update(str(e["action"]) for e in rows)
            metrics.append(execution_metrics(trade, rows))
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
                "trade_execution_metrics": metrics,
                "metrics_basis": "Charges use each trade's stamped round-trip bps on confirmed entry value, not actual broker fees. Slippage is signed entry VWAP minus trigger (positive adverse); it is already embedded in P&L and is not deducted again. R uses frozen per-share R times filled quantity. Null means unavailable.",
            },
            "engineering_quality": {
                "current_unprotected_trade_ids": [t.trade_id for t in trades if is_unprotected(t)],
                "current_reconciliation_trade_ids": [t.trade_id for t in trades if t.status == "reconciliation_required"],
                "unresolved_exposure_trade_ids": [t.trade_id for t in trades if t.remaining_position_qty or t.remaining_entry_qty],
                "provisional_accounting_trade_ids": [t.trade_id for t in provisional],
                "missing_original_plan_trade_ids": [t.trade_id for t in trades if t.filled_qty and not t.original_setup_json],
                "event_counts": dict(events),
                "protection_delay_basis": "Local audit time from first observed entry fill to first recorded full remaining-position cover; not exchange latency or a measure of every later exposure interval. Missing evidence is null.",
                "assessment": "Recorded observations only. No fresh broker query; zero incidents is not proof of safety.",
            },
        }
    return {"report_version": "radar-v1-session-2", "session_date": session_date,
            "scope": "Trades originating in this session, including later reconciliations; not a broker calendar-day statement.",
            "generated_at": datetime.now(timezone.utc).isoformat(), "modes": groups,
            "limitations": ["PAPER fills do not establish live execution quality or profitability.",
                            "Unattributed broker orders and external account activity require the Recovery view.",
                            "Open and incomplete P&L is not included in closed-trade outcome totals."]}
