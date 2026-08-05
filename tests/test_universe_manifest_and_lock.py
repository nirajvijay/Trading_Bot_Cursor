"""Tests for universe manifest write/validate and generation lock."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api.services.generation_lock import (
    GenerationLockBusy,
    acquire_generation_lock,
    lock_path,
    release_generation_lock,
)
from universe_manifest import (
    UNIVERSE_NAME,
    UNIVERSE_SIZE,
    build_manifest_dict,
    symbol_list_checksum,
    validate_universe_manifest,
    write_universe_manifest_atomic,
)


class UniverseManifestTests(unittest.TestCase):
    def test_missing_is_not_initialized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = validate_universe_manifest(Path(tmp) / "universe_manifest.json")
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "not_initialized")

    def test_atomic_write_and_validate_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "universe_manifest.json"
            write_universe_manifest_atomic(path)
            result = validate_universe_manifest(path)
            self.assertTrue(result.ok)
            self.assertEqual(result.status, "ok")
            data = json.loads(path.read_text())
            self.assertEqual(data["universe"], UNIVERSE_NAME)
            self.assertEqual(data["universe_size"], UNIVERSE_SIZE)
            self.assertEqual(data["symbol_list_checksum"], symbol_list_checksum())

    def test_checksum_mismatch_is_hard_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "universe_manifest.json"
            payload = build_manifest_dict()
            payload["symbol_list_checksum"] = "deadbeef"
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = validate_universe_manifest(path)
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "failed")
            self.assertIn("checksum", result.message.lower())

    def test_wrong_universe_hard_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "universe_manifest.json"
            payload = build_manifest_dict()
            payload["universe"] = "NIFTY_50"
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = validate_universe_manifest(path)
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "failed")


class GenerationLockTests(unittest.TestCase):
    def test_busy_when_pid_alive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = acquire_generation_lock("instruments", local_data_dir=root)
            self.assertTrue(path.exists())
            with self.assertRaises(GenerationLockBusy) as ctx:
                acquire_generation_lock("historical", local_data_dir=root)
            self.assertIn("another generation task is running", str(ctx.exception))
            release_generation_lock(path, local_data_dir=root)
            self.assertFalse(lock_path(root).exists())

    def test_stale_lock_reclaimed_when_pid_dead(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stale = lock_path(root)
            stale.write_text(
                json.dumps(
                    {
                        "active_task": "baselines",
                        "pid": 99999999,
                        "started_at": "2026-01-01T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            with patch("api.services.generation_lock._pid_alive", return_value=False):
                path = acquire_generation_lock("instruments", local_data_dir=root)
            self.assertEqual(json.loads(path.read_text())["pid"], os.getpid())
            release_generation_lock(path, local_data_dir=root)

    def test_concurrent_acquire_exactly_one_wins(self) -> None:
        import threading

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            barrier = threading.Barrier(2)
            results: list[object] = [None, None]

            def worker(index: int, task: str) -> None:
                barrier.wait(timeout=5)
                try:
                    path = acquire_generation_lock(task, local_data_dir=root)
                    results[index] = path
                except GenerationLockBusy as exc:
                    results[index] = exc

            t0 = threading.Thread(target=worker, args=(0, "instruments"))
            t1 = threading.Thread(target=worker, args=(1, "historical"))
            t0.start()
            t1.start()
            t0.join(timeout=5)
            t1.join(timeout=5)

            wins = [r for r in results if isinstance(r, Path)]
            losses = [r for r in results if isinstance(r, GenerationLockBusy)]
            self.assertEqual(len(wins), 1, f"expected exactly one winner, got {results!r}")
            self.assertEqual(len(losses), 1, f"expected exactly one busy loser, got {results!r}")
            self.assertTrue(wins[0].exists())
            self.assertIn("another generation task is running", str(losses[0]))
            release_generation_lock(wins[0], local_data_dir=root)
            self.assertFalse(lock_path(root).exists())


if __name__ == "__main__":
    unittest.main()
