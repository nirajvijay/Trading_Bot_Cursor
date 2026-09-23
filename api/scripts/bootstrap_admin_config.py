#!/usr/bin/env python3
"""Bootstrap admin_config.db with default values if missing."""

from __future__ import annotations

from api import config
from api.admin_config.store import AdminConfigStore


def main() -> int:
    store = AdminConfigStore(config.admin_config_db_path())
    try:
        snap = store.get_config_response()
        print(
            "admin_config ready version=%s paused=%s"
            % (snap["version_id"], snap["entries_paused"])
        )
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
