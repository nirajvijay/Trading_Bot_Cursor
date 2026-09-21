"""Isolated regressions for V1 PAPER clean-start ledger isolation."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from api import config
from trading_engine_broker import FakeBroker
from trading_engine_cycle import TradingEngineCycle
from trading_engine_paper import PaperBroker
from trading_engine_report import session_report
from trading_engine_store import TradingEngineStore
from trading_engine_types import TriggerCandidate
from trading_engine_v1_paper_clean_start import (
    api_service_is_active,
    archive_legacy_trading_ledger,
    assert_api_writers_quiesced,
    assert_live_disabled,
    candidate_blocked_by_clean_start,
    initialize_v1_paper_ledger,
    inspect_api_service,
    open_paper_broker_for_ledger,
    paper_account_lock_path,
    refuse_live_on_paper_ledger,
    require_paper_v1_ready,
    resolve_live_flags,
    runners_present,
    validate_paper_v1_pair,
)


def _candidate(**kwargs) -> TriggerCandidate:
    base = dict(
        setup_id="setup-1",
        continuation_rule_version="v1",
        session_date="2026-09-11",
        tradingsymbol="AAA",
        instrument_token=1,
        direction="UP",
        trigger_price=100.0,
        pullback_swing_high=None,
        pullback_swing_low=None,
        tick_size=0.05,
        buffer_ticks=1,
        trigger_exchange_ts="2026-09-11T04:00:00+00:00",
        last_price=100.0,
        created_at="2026-09-11T04:00:00+00:00",
    )
    base.update(kwargs)
    return TriggerCandidate(**base)


class V1PaperCleanStartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.legacy = self.root / "trading_engine.db"
        self.paper = self.root / "trading_engine_v1_paper.db"
        self.archive_root = self.root / "archives"
        self.env = patch.dict(
            os.environ,
            {
                "TRADING_ENGINE_LIVE_ORDERS": "false",
                "NIFTY_RADAR_LIVE_WRITES_AUTHORIZED": "0",
                "TRADING_ENGINE_V1_PAPER_DB_PATH": str(self.paper),
                "TRADING_ENGINE_LEGACY_DB_PATH": str(self.legacy),
                "TRADING_ENGINE_DB_PATH": str(self.paper),
            },
            clear=False,
        )
        self.env.start()
        self.host = patch.multiple(
            "trading_engine_v1_paper_clean_start",
            runners_present=lambda: [],
            assert_live_disabled=lambda **k: {"service": {}, "process": None},
            assert_api_writers_quiesced=lambda **k: None,
            apply_active_db_ownership=lambda *a, **k: None,
        )
        self.host.start()

    def tearDown(self) -> None:
        self.host.stop()
        self.env.stop()
        self.tmp.cleanup()

    def _seed_legacy(self) -> str:
        store = TradingEngineStore(self.legacy)
        trade = store.insert_candidate(
            setup_id="legacy-adaniports",
            continuation_rule_version="v1",
            session_date="2026-09-02",
            symbol="ADANIPORTS",
            instrument_token=1,
            direction="UP",
            entry_estimate=100,
            tick_size=0.05,
            trigger_time="2026-09-02T10:00:00+05:30",
        )
        store.update_trade(
            trade.trade_id,
            status="protected_open",
            qty=145,
            remaining_position_qty=145,
            filled_qty=145,
            run_id=None,
            entry_live_orders_enabled=None,
        )
        store.enqueue_command("pause_entries", payload={"note": "legacy"})
        store.close()
        return trade.trade_id

    def _init_paper(self, boundary: str = "2026-09-11T05:00:00+00:00", **kwargs):
        return initialize_v1_paper_ledger(
            paper_db=self.paper,
            signal_not_before=boundary,
            apply_ownership=False,
            env_files=(),
            **kwargs,
        )

    def _write_service_env(self, **flags: str) -> Path:
        path = self.root / "service.env"
        lines = [f"{k}={v}" for k, v in flags.items()]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_legacy_archived_but_cannot_contaminate_new_paper_account(self) -> None:
        trade_id = self._seed_legacy()
        manifest = archive_legacy_trading_ledger(archive_root=self.archive_root)
        init = self._init_paper(
            "2026-09-11T00:00:00+00:00", legacy_archive_label=manifest["label"]
        )
        self.assertTrue(self.legacy.is_file())
        self.assertTrue(Path(manifest["archive_db"]).is_file())
        self.assertEqual(manifest["classification"], "legacy/unresolved")
        self.assertTrue(any(r["trade_id"] == trade_id for r in manifest["unresolved_or_open_rows"]))

        paper = TradingEngineStore(self.paper)
        self.assertTrue(paper.is_paper_v1_namespace())
        meta = paper.account_meta()
        self.assertTrue(meta["initialization_complete"])
        self.assertTrue(meta["paper_account_id"])
        self.assertEqual(paper.list_trades(None), [])
        self.assertEqual(paper.pending_commands(), [])
        report = session_report(paper, "2026-09-02")
        self.assertEqual(report["modes"]["UNKNOWN"]["strategy_outcomes"]["observed_trade_records"], 0)
        paper.close()
        pair = validate_paper_v1_pair(self.paper)
        self.assertEqual(pair["ledger"]["paper_account_id"], pair["account"]["account_id"])
        self.assertFalse(init["resumed"])

    def test_bootstrap_refuses_existing_targets_and_partial_pairs(self) -> None:
        self._init_paper()
        with self.assertRaises(FileExistsError):
            self._init_paper()
        resumed = self._init_paper(allow_resume=True)
        self.assertTrue(resumed["resumed"])
        account = Path(config.trading_engine_paper_account_db_path(self.paper))
        account.unlink()
        with self.assertRaises(FileExistsError) as ctx:
            self._init_paper(allow_resume=True)
        self.assertIn("partially_initialized", str(ctx.exception))

    def test_interrupted_bootstrap_is_not_ready_and_does_not_create_account(self) -> None:
        store = TradingEngineStore(self.paper, allow_create=True)
        store.ensure_paper_v1_namespace(
            signal_not_before="2026-09-11T05:00:00+00:00",
            paper_account_id="acct-interrupted",
            initialization_complete=False,
        )
        with self.assertRaises(RuntimeError) as ctx:
            require_paper_v1_ready(store, self.paper)
        self.assertTrue(
            "incomplete" in str(ctx.exception) or "missing" in str(ctx.exception)
        )
        store.close()
        account = config.trading_engine_paper_account_db_path(self.paper)
        self.assertFalse(account.exists())
        with self.assertRaises((RuntimeError, FileNotFoundError)):
            open_paper_broker_for_ledger(
                self.paper, quote_provider=lambda _s: None, total_capital=300000.0
            )
        self.assertFalse(account.exists())

    def test_deleted_account_after_bootstrap_refuses_recreate(self) -> None:
        init = self._init_paper()
        account = Path(init["paper_account"])
        lock = paper_account_lock_path(account)
        self.assertTrue(account.exists())
        account.unlink()
        if lock.exists():
            lock.unlink()
        store = TradingEngineStore(self.paper)
        with self.assertRaises(RuntimeError) as ctx:
            require_paper_v1_ready(store, self.paper)
        self.assertIn("missing", str(ctx.exception))
        store.close()
        with self.assertRaises((RuntimeError, FileNotFoundError)):
            open_paper_broker_for_ledger(
                self.paper, quote_provider=lambda _s: None, total_capital=300000.0
            )
        self.assertFalse(account.exists())

    def test_swapped_valid_account_is_rejected_on_resume_and_ready(self) -> None:
        init_a = self._init_paper()
        account_a = Path(init_a["paper_account"])
        id_a = init_a["account_meta"]["paper_account_id"]
        # Build a second valid empty account with a different durable id.
        other = self.root / "other_account.db"
        broker = PaperBroker(
            other,
            quote_provider=lambda _s: None,
            total_capital=300000.0,
            allow_create=True,
            account_id="acct-swapped-other",
        )
        broker.close()
        # Swap bytes into the expected account path.
        account_a.write_bytes(other.read_bytes())
        store = TradingEngineStore(self.paper)
        with self.assertRaises(RuntimeError) as ctx:
            require_paper_v1_ready(store, self.paper)
        self.assertIn("mismatch", str(ctx.exception))
        store.close()
        with self.assertRaises(RuntimeError):
            self._init_paper(allow_resume=True)
        # Original ledger id unchanged; no orders invented.
        pair_probe = json.loads(
            sqlite3.connect(account_a)
            .execute("SELECT snapshot FROM paper_account WHERE id=1")
            .fetchone()[0]
        )
        self.assertEqual(pair_probe.get("account_id"), "acct-swapped-other")
        self.assertNotEqual(pair_probe.get("account_id"), id_a)
        self.assertEqual(pair_probe.get("orders"), [])

    def test_restart_reopens_matching_pair_without_creating(self) -> None:
        init = self._init_paper()
        account = Path(init["paper_account"])
        before = account.stat().st_mtime_ns
        store = TradingEngineStore(self.paper)
        require_paper_v1_ready(store, self.paper)
        store.close()
        broker = open_paper_broker_for_ledger(
            self.paper, quote_provider=lambda _s: None, total_capital=300000.0
        )
        try:
            self.assertEqual(broker._account_id, init["account_meta"]["paper_account_id"])
            self.assertEqual(list(broker.orders), [])
        finally:
            broker.close()
        self.assertEqual(account.stat().st_mtime_ns, before)

    def test_unstamped_paper_path_refuses_start_arm_entry(self) -> None:
        store = TradingEngineStore(self.paper, allow_create=True)
        self.assertFalse(store.is_paper_v1_namespace())
        with self.assertRaises(RuntimeError) as ctx:
            require_paper_v1_ready(store, self.paper)
        self.assertIn("namespace_missing", str(ctx.exception))
        store.close()
        missing = self.root / "missing_v1_paper.db"
        with self.assertRaises(FileNotFoundError):
            TradingEngineStore(missing)

    def test_restart_retains_new_account_and_cannot_load_old_commands(self) -> None:
        self._seed_legacy()
        archive_legacy_trading_ledger(archive_root=self.archive_root)
        self._init_paper("2026-09-11T00:00:00+00:00")
        store = TradingEngineStore(self.paper)
        run = store.start_run(session_date="2026-09-11", live_orders_enabled=False, pid=None)
        store.close()
        again = TradingEngineStore(self.paper)
        self.assertEqual(again.latest_run()["run_id"], run)
        self.assertEqual(again.pending_commands(), [])
        legacy = TradingEngineStore(self.legacy)
        self.assertGreaterEqual(len(legacy.pending_commands()), 1)
        legacy.close()
        again.close()

    def test_new_account_unknown_exposure_still_triggers_recovery(self) -> None:
        self._init_paper("2026-09-11T00:00:00+00:00")
        store = TradingEngineStore(self.paper)
        trade = store.insert_candidate(
            setup_id="new-unknown",
            continuation_rule_version="v1",
            session_date="2026-09-10",
            symbol="BBB",
            instrument_token=2,
            direction="UP",
            entry_estimate=50,
            tick_size=0.05,
            trigger_time="2026-09-10T10:00:00+05:30",
        )
        store.update_trade(
            trade.trade_id,
            status="protected_open",
            qty=10,
            remaining_position_qty=10,
            filled_qty=10,
            run_id=None,
            entry_live_orders_enabled=None,
        )
        broker = FakeBroker()
        cycle = TradingEngineCycle(
            store,
            broker,
            live_db=self.root / "live.db",
            session_date="2026-09-11",
            started_at="2026-09-11T03:30:00+00:00",
            run_id=store.start_run(
                session_date="2026-09-11", live_orders_enabled=False, pid=None
            ),
            live_orders_enabled=False,
        )
        state = cycle._scan_recovery_state()
        self.assertTrue(state["blocks_entries"])
        self.assertTrue(any(t.trade_id == trade.trade_id for t in state["provenance_unknown"]))
        cycle._surface_recovery_findings(state)
        refreshed = store.get_trade(trade.trade_id)
        self.assertEqual(refreshed.status, "reconciliation_required")
        store.close()

    def test_live_refuses_paper_only_path_and_namespace_via_launchers(self) -> None:
        self._init_paper("2026-09-11T00:00:00+00:00")
        self.assertTrue(config.is_paper_only_ledger_path(self.paper))
        with self.assertRaises(RuntimeError) as ctx:
            refuse_live_on_paper_ledger(self.paper)
        self.assertIn("PAPER-only", str(ctx.exception))

        other = self.root / "custom_ledger.db"
        store = TradingEngineStore(other)
        store.ensure_paper_v1_namespace(
            signal_not_before="2026-09-11T05:00:00+00:00",
            paper_account_id="custom-acct",
            initialization_complete=False,
        )
        store.close()
        with self.assertRaises(RuntimeError):
            refuse_live_on_paper_ledger(other)

        from live_trading_engine import run
        import argparse

        args = argparse.Namespace(
            live_orders=True,
            status_file=str(self.root / "status.json"),
            stop_file=str(self.root / "stop"),
            trading_db=str(other),
            live_db=str(self.root / "live.db"),
            session_date="2026-09-11",
            total_capital=300000.0,
            runner_status_file=None,
            until_session_close=False,
            poll_seconds=1.0,
        )
        with patch.dict(
            os.environ,
            {
                "TRADING_ENGINE_LIVE_ORDERS": "true",
                "NIFTY_RADAR_LIVE_WRITES_AUTHORIZED": "1",
            },
            clear=False,
        ):
            with self.assertRaises(RuntimeError) as live_ctx:
                run(args)
            self.assertIn("PAPER-only", str(live_ctx.exception))

        from api.services.trading_engine_runner import start_trading_engine

        with patch.dict(
            os.environ,
            {
                "TRADING_ENGINE_DB_PATH": str(other),
                "NIFTY_RADAR_LIVE_WRITES_AUTHORIZED": "1",
            },
            clear=False,
        ):
            ok, message, _pid = start_trading_engine(
                session_date="2026-09-11", confirm_live_orders=True
            )
            self.assertFalse(ok)
            self.assertIn("PAPER-only", message)

    def test_clean_start_boundary_blocks_pre_reset_setup_replay(self) -> None:
        self._init_paper("2026-09-11T05:00:00+00:00")
        store = TradingEngineStore(self.paper)
        run = store.start_run(session_date="2026-09-11", live_orders_enabled=False, pid=None)
        store.save_session_arm(
            session_date="2026-09-11",
            run_id=run,
            execution_mode="PAPER",
            entry_mode="AUTOPILOT",
            config_version_id="t",
            actor="t",
        )
        broker = FakeBroker(auto_fill_entry=True, auto_confirm_sl=True)
        cycle = TradingEngineCycle(
            store,
            broker,
            live_db=self.root / "live.db",
            session_date="2026-09-11",
            started_at="2026-09-11T03:00:00+00:00",
            run_id=run,
            live_orders_enabled=False,
        )
        old = _candidate(
            setup_id="pre-reset",
            created_at="2026-09-11T04:00:00+00:00",
            trigger_exchange_ts="2026-09-11T04:00:00+00:00",
        )
        new = _candidate(
            setup_id="post-reset",
            created_at="2026-09-11T06:00:00+00:00",
            trigger_exchange_ts="2026-09-11T06:00:00+00:00",
        )
        self.assertEqual(cycle._signal_created_at_floor(), "2026-09-11T05:00:00+00:00")
        self.assertTrue(cycle._candidate_before_clean_start(old))
        self.assertFalse(cycle._candidate_before_clean_start(new))
        seen: list[str] = []
        with patch(
            "trading_engine_cycle.fetch_triggered_since", return_value=[old, new]
        ) as fetch, patch.object(
            cycle, "_handle_candidate", side_effect=lambda c: seen.append(c.setup_id)
        ):
            cycle.ingest_triggers()
            self.assertEqual(
                fetch.call_args.kwargs.get("created_at_gte")
                or fetch.call_args[1].get("created_at_gte"),
                "2026-09-11T05:00:00+00:00",
            )
        self.assertEqual(seen, ["post-reset"])
        store.save_session_arm(
            session_date="2026-09-11",
            run_id=run,
            execution_mode="PAPER",
            entry_mode="MANUAL",
            config_version_id="t",
            actor="t",
        )
        with patch(
            "trading_engine_cycle.fetch_triggered_since", return_value=[old]
        ), patch.object(cycle, "_arming_readiness", return_value=True), patch.object(
            cycle, "_entries_paused", return_value=False
        ), patch.object(cycle, "_new_entry_block_reason", return_value=None):
            with self.assertRaises(ValueError) as ctx:
                cycle.approve_setup(
                    {
                        "setup_id": "pre-reset",
                        "continuation_rule_version": "v1",
                    },
                    actor="owner",
                    first=True,
                )
            self.assertIn("clean_start", str(ctx.exception))
        store.close()

    def test_boundary_mixed_offsets_and_delayed_trigger_insertion(self) -> None:
        self._init_paper("2026-09-11T10:30:00+05:30")
        boundary = "2026-09-11T05:00:00+00:00"
        at_boundary = _candidate(
            setup_id="at-boundary",
            created_at="2026-09-11T10:30:00+05:30",
            trigger_exchange_ts="2026-09-11T05:00:00+00:00",
        )
        self.assertFalse(candidate_blocked_by_clean_start(at_boundary, boundary))
        delayed = _candidate(
            setup_id="delayed-old-trigger",
            created_at="2026-09-11T12:00:00+05:30",
            trigger_exchange_ts="2026-09-11T04:00:00+00:00",
        )
        self.assertTrue(candidate_blocked_by_clean_start(delayed, boundary))
        missing = _candidate(setup_id="missing", created_at="", trigger_exchange_ts="")
        self.assertTrue(candidate_blocked_by_clean_start(missing, boundary))
        naive = _candidate(
            setup_id="naive",
            created_at="2026-09-11T06:00:00",
            trigger_exchange_ts="2026-09-11T06:00:00",
        )
        self.assertTrue(candidate_blocked_by_clean_start(naive, boundary))

        store = TradingEngineStore(self.paper)
        run = store.start_run(session_date="2026-09-11", live_orders_enabled=False, pid=None)
        store.save_session_arm(
            session_date="2026-09-11",
            run_id=run,
            execution_mode="PAPER",
            entry_mode="AUTOPILOT",
            config_version_id="t",
            actor="t",
        )
        cycle = TradingEngineCycle(
            store,
            FakeBroker(),
            live_db=self.root / "live.db",
            session_date="2026-09-11",
            started_at="2026-09-11T03:00:00+00:00",
            run_id=run,
            live_orders_enabled=False,
        )
        self.assertEqual(cycle._signal_created_at_floor(), "2026-09-11T05:00:00+00:00")
        seen: list[str] = []
        with patch(
            "trading_engine_cycle.fetch_triggered_since",
            return_value=[delayed, at_boundary],
        ), patch.object(
            cycle, "_handle_candidate", side_effect=lambda c: seen.append(c.setup_id)
        ):
            cycle.ingest_triggers()
        self.assertEqual(seen, ["at-boundary"])
        store.close()

    def test_paper_broker_entry_stop_modify_cancel_never_call_kite_writes(self) -> None:
        from live_trading_engine import _make_broker

        self._init_paper()
        paper_account = config.trading_engine_paper_account_db_path(self.paper)
        kite = MagicMock()
        kite.quote.return_value = {
            "NSE:AAA": {
                "timestamp": "2026-09-11T06:00:00+00:00",
                "depth": {
                    "buy": [{"price": 100.0, "quantity": 10}],
                    "sell": [{"price": 100.05, "quantity": 10}],
                },
            }
        }
        with patch("login._get_kite", return_value=kite):
            broker = _make_broker(
                False, 300000.0, paper_db=paper_account, trading_db=self.paper
            )
            try:
                broker._clock = lambda: __import__(
                    "datetime"
                ).datetime.fromisoformat("2026-09-11T06:00:00+00:00")
                entry = broker.place_limit_mis(
                    tradingsymbol="AAA",
                    transaction_type="BUY",
                    quantity=5,
                    tag="paper-entry",
                    price=100.05,
                )
                stop = broker.place_slm(
                    tradingsymbol="AAA",
                    transaction_type="SELL",
                    quantity=5,
                    tag="paper-sl",
                    trigger_price=95.0,
                )
                broker.modify_slm(stop.order_id, 96.0, quantity=5)
                broker.cancel_order(stop.order_id)
                self.assertIsNotNone(entry.order_id)
                kite.place_order.assert_not_called()
                kite.modify_order.assert_not_called()
                kite.cancel_order.assert_not_called()
                self.assertEqual(kite.place_order.call_count, 0)
                self.assertEqual(kite.modify_order.call_count, 0)
                self.assertEqual(kite.cancel_order.call_count, 0)
            finally:
                broker.close()

    def test_reports_distinguish_legacy_archive_new_paper_and_live_stamps(self) -> None:
        self._seed_legacy()
        archive_legacy_trading_ledger(archive_root=self.archive_root)
        self._init_paper()
        paper = TradingEngineStore(self.paper)
        for setup, mode in (("p1", False), ("l1", True), ("u1", None)):
            t = paper.insert_candidate(
                setup_id=setup,
                continuation_rule_version="v1",
                session_date="2026-09-11",
                symbol="AAA",
                instrument_token=1,
                direction="UP",
                entry_estimate=100,
                tick_size=0.05,
                trigger_time="2026-09-11T10:00:00+05:30",
            )
            paper.update_trade(
                t.trade_id,
                status="closed",
                filled_qty=1,
                exited_qty=1,
                entry_live_orders_enabled=mode,
                realised_pnl=1.0,
                qty_model_version=1,
            )
        report = session_report(paper, "2026-09-11")
        self.assertEqual(report["modes"]["PAPER"]["strategy_outcomes"]["observed_trade_records"], 1)
        self.assertEqual(report["modes"]["LIVE"]["strategy_outcomes"]["observed_trade_records"], 1)
        self.assertEqual(report["modes"]["UNKNOWN"]["strategy_outcomes"]["observed_trade_records"], 1)
        legacy = TradingEngineStore(self.legacy)
        legacy_report = session_report(legacy, "2026-09-02")
        self.assertGreaterEqual(
            legacy_report["modes"]["UNKNOWN"]["strategy_outcomes"]["observed_trade_records"], 1
        )
        paper.close()
        legacy.close()

    def test_cutover_aborts_when_runners_present(self) -> None:
        self._seed_legacy()
        with patch(
            "trading_engine_v1_paper_clean_start.runners_present",
            return_value=["123 live_trading_engine.py"],
        ):
            with self.assertRaises(RuntimeError) as ctx:
                archive_legacy_trading_ledger(archive_root=self.archive_root)
            self.assertIn("runners_present", str(ctx.exception))

    def test_runner_discovery_errors_are_failures(self) -> None:
        with patch(
            "subprocess.run",
            return_value=MagicMock(returncode=2, stdout="", stderr="pgrep boom"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                runners_present()
            self.assertIn("runner_discovery_failed", str(ctx.exception))

    def test_live_flags_require_explicit_disabled_evidence(self) -> None:
        missing = self._write_service_env(TRADING_ENGINE_LIVE_ORDERS="false")
        with self.assertRaises(RuntimeError) as ctx:
            resolve_live_flags(env_files=[missing])
        self.assertIn("live_flag_missing", str(ctx.exception))

        malformed = self._write_service_env(
            TRADING_ENGINE_LIVE_ORDERS="maybe",
            NIFTY_RADAR_LIVE_WRITES_AUTHORIZED="0",
        )
        with self.assertRaises(RuntimeError) as ctx:
            resolve_live_flags(env_files=[malformed])
        self.assertIn("live_flag_malformed", str(ctx.exception))

        enabled = self._write_service_env(
            TRADING_ENGINE_LIVE_ORDERS="true",
            NIFTY_RADAR_LIVE_WRITES_AUTHORIZED="0",
        )
        flags = resolve_live_flags(env_files=[enabled])
        self.assertTrue(flags["live_orders_enabled"])
        with self.assertRaises(RuntimeError):
            assert_live_disabled(env_files=[enabled], capture_running_process=False)

        ok = self._write_service_env(
            TRADING_ENGINE_LIVE_ORDERS="false",
            NIFTY_RADAR_LIVE_WRITES_AUTHORIZED="0",
        )
        flags = resolve_live_flags(env_files=[ok])
        self.assertFalse(flags["live_orders_enabled"])
        self.assertFalse(flags["live_writes_authorized"])
        assert_live_disabled(env_files=[ok], capture_running_process=False)

        with self.assertRaises(RuntimeError):
            resolve_live_flags(env_files=[self.root / "missing.env"])

    def test_api_service_unknown_state_is_not_quiesced(self) -> None:
        with patch(
            "trading_engine_v1_paper_clean_start.inspect_api_service",
            return_value={
                "LoadState": "loaded",
                "ActiveState": "activating",
                "SubState": "start",
                "MainPID": "0",
            },
        ):
            with self.assertRaises(RuntimeError) as ctx:
                api_service_is_active()
            self.assertIn("unknown", str(ctx.exception))
            with self.assertRaises(RuntimeError) as ctx2:
                assert_api_writers_quiesced()
            self.assertIn("unknown", str(ctx2.exception))

        with patch(
            "trading_engine_v1_paper_clean_start.inspect_api_service",
            side_effect=RuntimeError("api_service_inspection_failed:exit=1"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                assert_api_writers_quiesced()
            self.assertIn("inspection_failed", str(ctx.exception))

    def test_apply_ownership_includes_paper_account_lock(self) -> None:
        import trading_engine_v1_paper_clean_start as clean_start

        init = self._init_paper()
        account = Path(init["paper_account"])
        lock = paper_account_lock_path(account)
        self.assertTrue(lock.exists())
        owned: list[Path] = []

        def fake_chown(path, uid, gid):
            owned.append(Path(path))

        # Exercise the real ownership helper (host setUp patches it to a no-op).
        self.host.stop()
        try:
            with patch("pwd.getpwnam", return_value=MagicMock(pw_uid=7)), patch(
                "grp.getgrnam", return_value=MagicMock(gr_gid=8)
            ), patch("os.chown", side_effect=fake_chown), patch("os.chmod"):
                clean_start.apply_active_db_ownership([self.paper, account])
        finally:
            self.host.start()
        self.assertIn(lock.resolve(), {p.resolve() for p in owned})
        self.assertIn(account.resolve(), {p.resolve() for p in owned})
