"""Owner control commands; HTTP acceptance is never broker confirmation."""
from __future__ import annotations
import os

from api.admin_config.store import AdminConfigStore


def _pause(cycle, *, disarm=False):
    if disarm:
        cycle.store.disarm_session(cycle.session_date)
    cycle._pause_entries_for("owner_control")
    cycle._cancel_pending_vwap_on_pause()
    for trade in cycle._iter_management_trades():
        if cycle._is_positively_owned_engine_trade(trade) and trade.remaining_entry_qty > 0:
            cycle._cancel_entry_remainder(trade)


def _entries_cancelled(cycle):
    return all(t.remaining_entry_qty == 0 and t.status != "submission_unknown"
               for t in cycle._iter_management_trades())


def _flat(cycle):
    state = cycle._scan_recovery_state()
    if state["ownership_unresolved"]:
        return False
    for trade in cycle._iter_management_trades():
        if trade.remaining_position_qty or trade.remaining_entry_qty or cycle._sl_submit_unresolved(trade.trade_id):
            return False
        if cycle._is_positively_owned_engine_trade(trade) and not cycle._broker_is_flat(trade):
            return False
    orders, positions, error = cycle._broker_discovery_snapshot()
    if error or any(positions.values()):
        return False
    return not any(o.pending_quantity > 0 or o.status in {"OPEN","TRIGGER PENDING","AMO REQ RECEIVED"} for o in orders)


def _arm(cycle, payload, actor):
    mode = payload.get("execution_mode", "PAPER")
    entry_mode = payload.get("entry_mode", "MANUAL")
    if mode not in {"PAPER","LIVE"} or entry_mode not in {"MANUAL","AUTOPILOT"}:
        raise ValueError("invalid_mode")
    if mode != ("LIVE" if cycle.live_orders_enabled else "PAPER"):
        raise ValueError("mode_switch_requires_stopped_flat_engine_restart")
    if mode == "LIVE" and (payload.get("live_confirmation") is not True or
                           os.environ.get("NIFTY_RADAR_LIVE_WRITES_AUTHORIZED") != "1"):
        raise ValueError("live_execution_not_authorized")
    if payload.get("run_id", cycle.run_id) != cycle.run_id:
        raise ValueError("stale_engine_run")
    if not cycle._arming_readiness():
        raise ValueError("checklist_not_ready")
    if cycle._entry_calendar_block_reason() is not None:
        raise ValueError(cycle._entry_calendar_block_reason())
    cycle.enforce_restart_recovery()
    cycle.enforce_daily_loss()
    state = cycle._scan_recovery_state()
    if state["ownership_unresolved"]:
        raise ValueError("recovery_required")
    previous_arm = cycle.store.session_arm(cycle.session_date)
    if previous_arm and previous_arm["entry_mode"] != entry_mode and not _flat(cycle):
        raise ValueError("mode_switch_requires_flat_reconciled_account")
    if cycle.loss_halt_snapshot.get("halted") or not cycle.loss_halt_snapshot.get("complete"):
        raise ValueError("daily_loss_or_accounting_lock")
    for trade in cycle._iter_management_trades():
        if trade.status == "reconciliation_required" or trade.remaining_entry_qty > 0:
            raise ValueError("orders_require_reconciliation")
        if trade.remaining_position_qty > 0:
            if not cycle._is_positively_owned_engine_trade(trade) or not trade.sl_order_id:
                raise ValueError("protection_unresolved")
            stop = cycle.broker.poll_order(trade.sl_order_id)
            if stop is None or cycle._confirmed_stop_cover_qty(stop) != trade.remaining_position_qty:
                raise ValueError("protection_unresolved")
            if not cycle._broker_qty_matches_engine(trade, cycle.broker.net_position_qty(trade.symbol), trade.remaining_position_qty):
                raise ValueError("position_mismatch")
    feed = cycle._feed_entry_block_reason()
    if feed:
        raise ValueError(feed)
    admin = AdminConfigStore(cycle._admin_store.db_path)
    try:
        expected = payload.get("config_version_id")
        if expected != admin.active_version_id():
            raise ValueError("config_version_conflict")
        admin.arm_effective_config(actor=actor)
        arm = cycle.store.save_session_arm(session_date=cycle.session_date, run_id=cycle.run_id,
            execution_mode=mode, entry_mode=entry_mode, config_version_id=expected, actor=actor)
        admin.set_entries_paused(False)
    finally:
        admin.close()
    cycle._clear_local_entries_lock()
    cycle._sync_pause_from_canonical()
    return arm


def process_commands(cycle):
    for pending in cycle.store.pending_commands():
        cmd = cycle.store.command_record(pending.command_id)
        first = cmd["state"] == "queued"
        kind, payload, actor = cmd["kind"], cmd["payload"], cmd.get("actor") or "owner"
        cid = pending.command_id
        if first:
            cycle.store.set_command_state(cid, "running")
        try:
            if kind in {"arm_session", "resume_entries"}:
                if not first:
                    # A crash during arming is fail-closed, not an implicit retry.
                    cycle.store.set_command_state(cid, "failed", {"reason":"arm_interrupted_rearm_required"})
                    continue
                arm = _arm(cycle, payload, actor)
                cycle.store.set_command_state(cid, "succeeded", {"arm":arm})
            elif kind in {"pause_entries", "disarm", "stop_engine", "switch_manual"}:
                if kind == "stop_engine" and cmd["created_at"] < cycle.started_at:
                    cycle.store.set_command_state(cid, "cancelled", {"reason":"stale_run_stop"})
                    continue
                _pause(cycle, disarm=kind in {"disarm","stop_engine"})
                if kind == "stop_engine":
                    cycle.draining = True
                    done = _flat(cycle)
                    if done:
                        cycle.running = False
                else:
                    done = _entries_cancelled(cycle)
                if kind == "switch_manual" and done:
                    arm = cycle.store.session_arm(cycle.session_date)
                    if arm:
                        cycle.store.save_session_arm(session_date=cycle.session_date, run_id=cycle.run_id,
                            execution_mode=arm["execution_mode"], entry_mode="MANUAL",
                            config_version_id=arm["config_version_id"], actor=actor)
                    # Remain paused; resume is explicit and revalidated.
                cycle.store.set_command_state(cid, "succeeded" if done else "awaiting_broker",
                    {"entry_permission":"disarmed" if kind in {"disarm","stop_engine"} else "paused",
                     "engine_state":"stopping" if kind == "stop_engine" and not done else None})
            elif kind in {"close_position", "close_all"}:
                if kind == "close_position":
                    trade = cycle.store.get_trade(cmd["trade_id"])
                    if trade is None or not cycle._is_positively_owned_engine_trade(trade):
                        raise ValueError("trade_ownership_unresolved")
                    cycle.close_position(trade.trade_id, actor=actor)
                    trade = cycle.store.get_trade(trade.trade_id)
                    done = trade.status in {"closed","skipped","rejected"} and cycle._broker_is_flat(trade)
                    ids = [r["order_id"] for r in cycle.store.list_order_links(trade.trade_id)]
                else:
                    cycle.close_all(actor=actor)
                    done = _flat(cycle)
                    ids = []
                cycle.store.set_command_state(cid, "succeeded" if done else "awaiting_broker", {"broker_order_ids":ids})
            elif kind == "trail_stop":
                trade = cycle.store.get_trade(cmd["trade_id"])
                if trade is None or not cycle._is_positively_owned_engine_trade(trade):
                    raise ValueError("trade_ownership_unresolved")
                requested = float(payload["new_stop"])
                if first:
                    cycle.apply_trail(trade.trade_id, requested, actor=actor)
                trade = cycle.store.get_trade(trade.trade_id)
                stop = cycle.broker.poll_order(trade.sl_order_id) if trade.sl_order_id else None
                confirmed = cycle._confirmed_trail_trigger(stop, requested=requested, tick_size=trade.tick_size)
                cycle.store.set_command_state(cid, "succeeded" if confirmed is not None else "unknown_needs_reconcile",
                    {"requested_stop":requested,"confirmed_stop":confirmed,"broker_order_ids":[trade.sl_order_id]})
            elif kind == "set_auto_trail":
                trade = cycle.store.get_trade(cmd["trade_id"])
                if trade is None or not cycle._is_positively_owned_engine_trade(trade):
                    raise ValueError("trade_ownership_unresolved")
                cycle.set_auto_trail(trade.trade_id, enabled=bool(payload["enabled"]))
                cycle.store.set_command_state(cid,"succeeded",{"enabled":bool(payload["enabled"])})
            elif kind == "reconcile_now":
                _pause(cycle)
                cycle.drive_open()
                cycle.enforce_restart_recovery()
                cycle.enforce_daily_loss()
                state = cycle._scan_recovery_state()
                unresolved = state["ownership_unresolved"] or any(t.status == "reconciliation_required" for t in cycle._iter_management_trades())
                cycle.store.set_command_state(cid,"unknown_needs_reconcile" if unresolved else "succeeded",
                    {"reason":"ownership_or_broker_state_unresolved" if unresolved else "reconciled_entries_paused"})
            elif kind == "approve_entry":
                result = cycle.approve_setup(payload, actor=actor, first=first)
                cycle.store.set_command_state(cid, result.pop("state"), result)
            else:
                raise ValueError("unsupported_command")
        except (ValueError, KeyError) as exc:
            cycle.store.set_command_state(cid, "failed", {"reason":str(exc)})
        except Exception as exc:
            cycle._engage_local_entries_lock("command_reconciliation_required")
            cycle.store.set_command_state(cid,"unknown_needs_reconcile", {"reason":str(exc)})
