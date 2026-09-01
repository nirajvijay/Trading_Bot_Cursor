"""Idempotent schema migrations for admin_config.db."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Callable, List, Tuple

Migration = Tuple[int, str, Callable[[sqlite3.Connection], None]]

MIGRATION_1_SQL = """
CREATE TABLE IF NOT EXISTS admin_schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_config_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    active_version_id TEXT NOT NULL,
    entries_paused INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS admin_config_versions (
    version_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    parent_version_id TEXT,
    comment TEXT
);

CREATE TABLE IF NOT EXISTS admin_control_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor_username TEXT NOT NULL,
    action TEXT NOT NULL,
    version_id TEXT,
    from_version_id TEXT,
    diff_json TEXT NOT NULL DEFAULT '{}',
    result TEXT NOT NULL,
    detail TEXT,
    step_up_verified INTEGER NOT NULL DEFAULT 1
);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _apply_migration_1(conn: sqlite3.Connection) -> None:
    conn.executescript(MIGRATION_1_SQL)


MIGRATIONS: List[Migration] = [
    (1, "initial admin config schema", _apply_migration_1),
]


def run_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL,
            description TEXT NOT NULL
        )
        """
    )
    applied = {
        int(row[0])
        for row in conn.execute("SELECT version FROM admin_schema_migrations").fetchall()
    }
    for version, description, fn in MIGRATIONS:
        if version in applied:
            continue
        fn(conn)
        conn.execute(
            "INSERT INTO admin_schema_migrations (version, applied_at, description) VALUES (?, ?, ?)",
            (version, _utc_now(), description),
        )
    conn.commit()
