"""Regression: runtime caches write under NIFTY_RADAR_DATA_ROOT, not releases."""

from __future__ import annotations

import importlib
import os
import stat
import tempfile
import unittest
from pathlib import Path


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class RuntimeCachePathTests(unittest.TestCase):
    _ENV_KEYS = (
        "APP_ENV",
        "NIFTY_RADAR_DATA_ROOT",
        "NIFTY_RADAR_RUNTIME_CACHE_DIR",
        "LOCAL_DATA_DIR",
    )

    def setUp(self) -> None:
        self._prev = {key: os.environ.get(key) for key in self._ENV_KEYS}

    def tearDown(self) -> None:
        for key, value in self._prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._reload_modules()

    def _reload_modules(self):
        import api.config as config

        importlib.reload(config)
        import api.services.token_check_cache as token_check_cache
        import api.services.checklist_cache as checklist_cache

        importlib.reload(token_check_cache)
        importlib.reload(checklist_cache)
        return config, token_check_cache, checklist_cache

    def test_production_paths_under_data_root_not_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            release_local = root / "releases" / "abc123" / "data" / "local"
            release_local.mkdir(parents=True)

            os.environ["APP_ENV"] = "production"
            os.environ["NIFTY_RADAR_DATA_ROOT"] = str(data)
            os.environ.pop("NIFTY_RADAR_RUNTIME_CACHE_DIR", None)
            os.environ["LOCAL_DATA_DIR"] = str(release_local)

            config, token_check_cache, checklist_cache = self._reload_modules()

            cache_root = data / "runtime-cache"
            self.assertEqual(config.runtime_cache_dir(), cache_root)
            self.assertNotIn("/releases/", str(config.runtime_cache_dir()).replace("\\", "/"))

            token_check_cache.write_token_check(valid=True, user_id="u1")
            token_path = cache_root / "token_check.json"
            self.assertTrue(token_path.is_file())
            self.assertEqual(_mode(token_path), 0o600)
            self.assertEqual(_mode(cache_root), 0o700)
            self.assertTrue(str(token_path.resolve()).startswith(str(data.resolve())))
            self.assertNotIn("/releases/", str(token_path.resolve()).replace("\\", "/"))

            from universe_manifest import write_universe_manifest_atomic

            write_universe_manifest_atomic(release_local / "universe_manifest.json")
            checklist_cache.write_checklist_cache(
                {
                    "session_date": "2026-08-07",
                    "overall_status": "ok",
                    "checked_at": "2026-08-07T08:00:00+05:30",
                    "next_step": "Start live observation",
                    "blockers": [],
                }
            )
            checklist_path = cache_root / "checklist_cache.json"
            self.assertTrue(checklist_path.is_file())
            self.assertEqual(_mode(checklist_path), 0o600)
            self.assertNotIn("/releases/", str(checklist_path.resolve()).replace("\\", "/"))

    def test_production_default_data_root_is_host_persistent(self) -> None:
        os.environ["APP_ENV"] = "production"
        os.environ.pop("NIFTY_RADAR_DATA_ROOT", None)
        os.environ.pop("NIFTY_RADAR_RUNTIME_CACHE_DIR", None)

        config, _, _ = self._reload_modules()
        self.assertEqual(config.data_root(), Path("/opt/nifty-radar/data"))
        self.assertEqual(
            config.runtime_cache_dir(),
            Path("/opt/nifty-radar/data/runtime-cache"),
        )

    def test_checklist_override_still_uses_temp_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host_data = root / "host-data"
            os.environ["APP_ENV"] = "production"
            os.environ["NIFTY_RADAR_DATA_ROOT"] = str(host_data)
            os.environ.pop("NIFTY_RADAR_RUNTIME_CACHE_DIR", None)

            _, _, checklist_cache = self._reload_modules()

            from universe_manifest import write_universe_manifest_atomic

            write_universe_manifest_atomic(root / "universe_manifest.json")
            checklist_cache.write_checklist_cache(
                {
                    "session_date": "2026-08-07",
                    "overall_status": "ok",
                    "checked_at": "2026-08-07T08:00:00+05:30",
                    "next_step": "Start live observation",
                    "blockers": [],
                },
                local_data_dir=root,
            )
            path = root / "checklist_cache.json"
            self.assertTrue(path.is_file())
            self.assertEqual(_mode(path), 0o600)
            self.assertFalse(
                (host_data / "runtime-cache" / "checklist_cache.json").exists()
            )

    def test_manifest_write_invalidates_runtime_cache(self) -> None:
        """Manifest updates must clear persistent runtime-cache, not only LOCAL_DATA_DIR."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            release_local = root / "releases" / "abc123" / "data" / "local"
            release_local.mkdir(parents=True)

            os.environ["APP_ENV"] = "production"
            os.environ["NIFTY_RADAR_DATA_ROOT"] = str(data)
            os.environ.pop("NIFTY_RADAR_RUNTIME_CACHE_DIR", None)
            os.environ["LOCAL_DATA_DIR"] = str(release_local)

            _, _, checklist_cache = self._reload_modules()
            from universe_manifest import write_universe_manifest_atomic

            write_universe_manifest_atomic(release_local / "universe_manifest.json")
            checklist_cache.write_checklist_cache(
                {
                    "session_date": "2026-08-07",
                    "overall_status": "ok",
                    "checked_at": "2026-08-07T08:00:00+05:30",
                    "next_step": "Start live observation",
                    "blockers": [],
                }
            )
            runtime_path = data / "runtime-cache" / "checklist_cache.json"
            self.assertTrue(runtime_path.is_file())

            # Second manifest write must clear the persistent runtime cache.
            write_universe_manifest_atomic(release_local / "universe_manifest.json")
            self.assertFalse(runtime_path.exists())


if __name__ == "__main__":
    unittest.main()
