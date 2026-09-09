"""Read-only preview using the same structural-stop, sizing and price-bound rules."""
from dataclasses import asdict, replace
import math
from trading_engine_handoff import fetch_vwap_classification
from trading_engine_quotes import bounded_entry_limit
from trading_engine_risk import size_new_trade, structural_stop_price


def preview_trade(cycle, candidate, *, qty_override=None, stop_tighten=None):
    cfg = cycle._admin_store.load_effective_payload()
    stop = structural_stop_price(direction=candidate.direction,
        swing_high=candidate.pullback_swing_high, swing_low=candidate.pullback_swing_low,
        tick_size=candidate.tick_size, buffer_ticks=candidate.buffer_ticks)
    try:
        quote = cycle.broker.touch_quote(candidate.tradingsymbol)
    except Exception:
        quote = None
    limit = bounded_entry_limit(now=cycle._now_ist(), trigger_time=candidate.trigger_exchange_ts,
        trigger=candidate.trigger_price, stop=stop, tick=candidate.tick_size,
        direction=candidate.direction, quote=quote, expiry=cfg["setup_expiry_seconds"],
        max_quote_age=cfg["max_quote_age_seconds"], drift_r=cfg["max_entry_drift_r"])
    try:
        classification = fetch_vwap_classification(cycle.live_db, session_date=candidate.session_date,
            setup_id=candidate.setup_id, continuation_rule_version=candidate.continuation_rule_version)
    except Exception:
        classification = "UNAVAILABLE"
    blockers = []
    gate = cycle._new_entry_block_reason()
    if gate:
        blockers.append(gate)
    if limit.reason:
        blockers.append(limit.reason)
    if classification not in {"ACCEPT","LIMITED"}:
        blockers.append("vwap_" + str(classification or "unavailable").lower())
    cap = cfg["limited_per_trade_risk_cap_inr"] if classification == "LIMITED" else cfg["per_trade_risk_cap_inr"]
    available = None
    if cycle.live_orders_enabled:
        try:
            available = cycle.broker.available_margins()
        except Exception:
            pass
    decision = size_new_trade(replace(candidate, trigger_price=limit.price or candidate.trigger_price),
        cycle.store.list_trades(cycle.session_date), total_capital=cfg["allocated_capital_inr"],
        leverage_factor=cycle.leverage_factor, per_trade_risk_cap=cap, daily_loss_cap=cfg["daily_loss_cap_inr"],
        max_concurrent_positions=int(cfg["max_concurrent_positions"]),
        max_filled_setups_per_day=int(cfg["max_filled_setups_per_day"]),
        one_per_symbol=bool(cfg["one_position_or_unresolved_entry_per_symbol"]),
        aggregate_notional_cap=cfg["allocated_capital_inr"], charge_bps=cfg["round_trip_charge_bps"],
        slippage_bps=cfg["estimated_slippage_bps"], use_demo_leverage=not cycle.live_orders_enabled,
        available_broker_margin=available)
    if not decision.allow:
        blockers.append(decision.reason)
    qty = decision.qty
    if qty_override is not None:
        if isinstance(qty_override, bool) or not isinstance(qty_override, int) or not 0 < qty_override <= qty:
            blockers.append("quantity_override_exceeds_risk_size")
        else:
            qty = qty_override
    chosen_stop = stop
    if stop_tighten is not None:
        valid = isinstance(stop_tighten, (int,float)) and math.isfinite(stop_tighten) and stop is not None
        if valid:
            valid = stop <= stop_tighten < (limit.price or candidate.trigger_price) if candidate.direction == "UP" else (limit.price or candidate.trigger_price) < stop_tighten <= stop
        if not valid or abs(round(stop_tighten / candidate.tick_size)*candidate.tick_size-stop_tighten) > 1e-9:
            blockers.append("stop_override_must_tighten_on_tick")
        else:
            chosen_stop = stop_tighten
    price = limit.price or candidate.trigger_price
    risk = qty * (abs(price-chosen_stop) + price*(cfg["round_trip_charge_bps"]+cfg["estimated_slippage_bps"])/10000) if chosen_stop is not None else None
    return {"setup_id":candidate.setup_id,"continuation_rule_version":candidate.continuation_rule_version,
        "symbol":candidate.tradingsymbol,"direction":candidate.direction,"structural_stop":stop,
        "proposed_stop":chosen_stop,"proposed_qty":qty,"limit_price":limit.price,
        "quote":asdict(quote) if quote else None,"quote_age_seconds":limit.quote_age,
        "signal_age_seconds":limit.signal_age,"setup_expiry_seconds":cfg["setup_expiry_seconds"],
        "notional":qty*price,"margin_estimate":decision.margin_blocked,"risk_inr":risk,
        "estimated_pnl_at_stop":-risk if risk is not None else None,"vwap_class":classification,
        "config_version_id":cycle._admin_store.effective_version_id(),
        "eligible":not blockers,"blockers":list(dict.fromkeys(blockers))}
