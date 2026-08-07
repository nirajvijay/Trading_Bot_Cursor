"""Regression: persistent local market-data paths and SSH-safe copy commands."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _clear_path_env(env: dict) -> dict:
    for key in (
        "LOCAL_DATA_DIR",
        "LIVE_DB_PATH",
        "INSTRUMENTS_DB_PATH",
        "HISTORICAL_DB_PATH",
        "BASELINES_DB_PATH",
        "NIFTY_RADAR_RUNTIME_CACHE_DIR",
        "PROD_PYTHON",
        "PROD_APP_ROOT",
    ):
        env.pop(key, None)
    return env


class PersistentLocalPathTests(unittest.TestCase):
    _ENV_KEYS = (
        "APP_ENV",
        "NIFTY_RADAR_DATA_ROOT",
        "NIFTY_RADAR_RUNTIME_CACHE_DIR",
        "LOCAL_DATA_DIR",
        "LIVE_DB_PATH",
        "INSTRUMENTS_DB_PATH",
        "HISTORICAL_DB_PATH",
        "BASELINES_DB_PATH",
        "PROD_PYTHON",
        "PROD_APP_ROOT",
    )

    def setUp(self) -> None:
        self._prev = {key: os.environ.get(key) for key in self._ENV_KEYS}

    def tearDown(self) -> None:
        for key, value in self._prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._reload_config_and_generation()

    def _reload_config_and_generation(self):
        import api.config as config

        importlib.reload(config)
        import api.services.local_data_generation as local_data_generation

        importlib.reload(local_data_generation)
        return config, local_data_generation

    def test_production_defaults_under_data_root_local_not_releases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            os.environ["APP_ENV"] = "production"
            os.environ["NIFTY_RADAR_DATA_ROOT"] = str(data)
            os.environ.pop("LOCAL_DATA_DIR", None)
            os.environ.pop("LIVE_DB_PATH", None)
            os.environ.pop("INSTRUMENTS_DB_PATH", None)
            os.environ.pop("HISTORICAL_DB_PATH", None)
            os.environ.pop("BASELINES_DB_PATH", None)

            config, _ = self._reload_config_and_generation()

            local = data / "local"
            self.assertEqual(config.local_data_dir(), local)
            self.assertEqual(config.LOCAL_DATA_DIR, local)
            self.assertEqual(config.live_db_path(), local / "nifty50_live_1m.db")
            self.assertEqual(config.LIVE_DB_PATH, local / "nifty50_live_1m.db")
            self.assertEqual(config.INSTRUMENTS_DB_PATH, local / "nifty50_instruments.db")
            self.assertEqual(config.HISTORICAL_DB_PATH, local / "nifty50_historical.db")
            self.assertEqual(config.BASELINES_DB_PATH, local / "nifty50_baselines.db")
            self.assertEqual(config.runtime_cache_dir(), data / "runtime-cache")

            for path in (
                config.local_data_dir(),
                config.live_db_path(),
                config.INSTRUMENTS_DB_PATH,
                config.HISTORICAL_DB_PATH,
                config.BASELINES_DB_PATH,
                config.runtime_cache_dir(),
            ):
                self.assertNotIn("/releases/", str(path).replace("\\", "/"))

    def test_production_default_host_paths_without_data_root_override(self) -> None:
        os.environ["APP_ENV"] = "production"
        os.environ.pop("NIFTY_RADAR_DATA_ROOT", None)
        os.environ.pop("LOCAL_DATA_DIR", None)
        os.environ.pop("LIVE_DB_PATH", None)

        config, _ = self._reload_config_and_generation()
        self.assertEqual(config.data_root(), Path("/opt/nifty-radar/data"))
        self.assertEqual(config.local_data_dir(), Path("/opt/nifty-radar/data/local"))
        self.assertEqual(
            config.live_db_path(),
            Path("/opt/nifty-radar/data/local/nifty50_live_1m.db"),
        )
        self.assertEqual(config.PROD_PYTHON, "/opt/nifty-radar/venv/bin/python")
        self.assertEqual(config.PROD_APP_ROOT, Path("/opt/nifty-radar/current"))

    def test_build_command_absolute_under_local_rejects_releases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = root / "local"
            local.mkdir(parents=True)
            releases_db = root / "releases" / "abc123" / "data" / "local" / "nifty50_instruments.db"
            releases_db.parent.mkdir(parents=True)

            os.environ.pop("APP_ENV", None)
            os.environ.pop("NIFTY_RADAR_DATA_ROOT", None)
            os.environ["LOCAL_DATA_DIR"] = str(local)
            os.environ.pop("INSTRUMENTS_DB_PATH", None)
            os.environ.pop("HISTORICAL_DB_PATH", None)
            os.environ.pop("BASELINES_DB_PATH", None)

            config, gen = self._reload_config_and_generation()
            self.assertEqual(config.LOCAL_DATA_DIR, local)

            command = gen._build_command("instruments")
            db_args = [arg for arg in command if arg.endswith(".db")]
            self.assertTrue(db_args)
            for arg in db_args:
                path = Path(arg)
                self.assertTrue(path.is_absolute(), arg)
                self.assertTrue(str(path).startswith(str(local.resolve())))
                self.assertNotIn("/releases/", str(path).replace("\\", "/"))
                self.assertTrue(gen._path_allowed_for_generation(path))

            self.assertFalse(gen._path_allowed_for_generation(releases_db))
            with patch.object(config, "LOCAL_INSTRUMENTS_DB_PATH", releases_db), patch.object(
                config, "INSTRUMENTS_DB_PATH", releases_db
            ):
                with self.assertRaises(ValueError) as ctx:
                    gen._build_command("instruments")
                self.assertIn("Refusing to write outside local data dir", str(ctx.exception))

    def test_copy_command_fully_qualified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            os.environ["APP_ENV"] = "production"
            os.environ["NIFTY_RADAR_DATA_ROOT"] = str(data)
            os.environ.pop("LOCAL_DATA_DIR", None)
            os.environ.pop("LIVE_DB_PATH", None)
            os.environ.pop("INSTRUMENTS_DB_PATH", None)
            os.environ.pop("HISTORICAL_DB_PATH", None)
            os.environ.pop("BASELINES_DB_PATH", None)
            os.environ.pop("PROD_PYTHON", None)
            os.environ.pop("PROD_APP_ROOT", None)

            config, gen = self._reload_config_and_generation()
            cmd = gen.get_generate_command("instruments")

            self.assertIn("cd /opt/nifty-radar/current", cmd)
            self.assertIn("/opt/nifty-radar/venv/bin/python", cmd)
            self.assertIn("/opt/nifty-radar/current/instrument_collector.py", cmd)
            instruments_db = str((data / "local" / "nifty50_instruments.db").resolve())
            self.assertIn(instruments_db, cmd)
            self.assertNotIn("/releases/", cmd.replace("\\", "/"))
            self.assertTrue(cmd.startswith("cd /opt/nifty-radar/current &&"))

            hist = gen.get_generate_command("historical")
            self.assertIn("/opt/nifty-radar/current/historical_collector.py", hist)
            self.assertIn(str((data / "local" / "nifty50_historical.db").resolve()), hist)
            self.assertIn("--instruments-db", hist)
            self.assertNotIn("/releases/", hist.replace("\\", "/"))

    def test_cli_defaults_via_fresh_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "host-data"
            env = _clear_path_env(os.environ.copy())
            env["APP_ENV"] = "production"
            env["NIFTY_RADAR_DATA_ROOT"] = str(data)
            env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)

            script = r"""
from pathlib import Path
import instrument_collector
import historical_collector
import five_minute_candle_generator
import baseline_generator
import baseline_store
import live_one_minute_candle_writer
import pullback_ema_seed

local = Path(%r) / "local"
assert instrument_collector.DEFAULT_DB_PATH == local / "nifty50_instruments.db"
assert instrument_collector.LOCAL_DATA_DIR == local
assert historical_collector.DEFAULT_DB_PATH == local / "nifty50_historical.db"
assert historical_collector.DEFAULT_INSTRUMENTS_DB_PATH == local / "nifty50_instruments.db"
assert five_minute_candle_generator.DEFAULT_DB_PATH == local / "nifty50_historical.db"
assert baseline_generator.DEFAULT_HISTORICAL_DB_PATH == local / "nifty50_historical.db"
assert baseline_generator.DEFAULT_BASELINES_DB_PATH == local / "nifty50_baselines.db"
assert baseline_store.DEFAULT_BASELINES_DB_PATH == local / "nifty50_baselines.db"
assert live_one_minute_candle_writer.DEFAULT_DB_PATH == local / "nifty50_live_1m.db"
assert pullback_ema_seed.DEFAULT_HISTORICAL_DB == local / "nifty50_historical.db"
print("ok")
""" % str(data)

            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=str(Path(__file__).resolve().parent.parent),
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result.returncode,
                0,
                msg=f"stdout={result.stdout!r}\nstderr={result.stderr!r}",
            )
            self.assertIn("ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
