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


def _apply_migration_2(conn: sqlite3.Connection) -> None:
    cols = {
        str(r[1])
        for r in conn.execute("PRAGMA table_info(admin_config_state)").fetchall()
    }
    if "effective_version_id" not in cols:
        conn.execute(
            "ALTER TABLE admin_config_state ADD COLUMN effective_version_id TEXT"
        )
    if "effective_payload_json" not in cols:
        conn.execute(
            "ALTER TABLE admin_config_state ADD COLUMN effective_payload_json TEXT"
        )
    if "effective_armed_at" not in cols:
        conn.execute(
            "ALTER TABLE admin_config_state ADD COLUMN effective_armed_at TEXT"
        )
    # Seed effective from active saved payload when missing.
    row = conn.execute(
        """
        SELECT s.active_version_id, v.payload_json
        FROM admin_config_state s
        LEFT JOIN admin_config_versions v ON v.version_id = s.active_version_id
        WHERE s.id = 1
        """
    ).fetchone()
    if row is not None and row[0] is not None:
        conn.execute(
            """
            UPDATE admin_config_state
            SET effective_version_id = COALESCE(effective_version_id, ?),
                effective_payload_json = COALESCE(effective_payload_json, ?),
                effective_armed_at = COALESCE(effective_armed_at, ?)
            WHERE id = 1
            """,
            (str(row[0]), str(row[1] or "{}"), _utc_now()),
        )


MIGRATIONS: List[Migration] = [
    (1, "initial admin config schema", _apply_migration_1),
    (2, "saved vs effective config payloads", _apply_migration_2),
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
