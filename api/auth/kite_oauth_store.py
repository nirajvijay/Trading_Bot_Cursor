"""Opaque Kite OAuth state store (hash-only rows bound to website session)."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from api.auth import settings


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def hash_state(opaque: str) -> str:
    return hashlib.sha256(opaque.encode("utf-8")).hexdigest()


@dataclass
class OAuthStateRow:
    state_hash: str
    session_id: str
    oauth_cookie_id: str
    created_at: str
    expires_at: str
    consumed_at: Optional[str]


class KiteOAuthStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else settings.KITE_OAUTH_STATE_DB_PATH

    def connect(self) -> sqlite3.Connection:
        settings.ensure_parent_dir(self.db_path, mode=0o700)
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS oauth_states (
                    state_hash TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    oauth_cookie_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_oauth_session
                    ON oauth_states(session_id);
                """
            )
            conn.commit()
        try:
            self.db_path.chmod(0o600)
        except OSError:
            pass

    def create_pending(self, session_id: str) -> tuple[str, str]:
        """Create one pending login for session. Returns (opaque_state, oauth_cookie_id)."""
        self.init_db()
        opaque = secrets.token_urlsafe(32)
        cookie_id = secrets.token_urlsafe(24)
        now = _utc_now()
        expires = now + timedelta(seconds=settings.KITE_OAUTH_TTL_SECONDS)
        state_h = hash_state(opaque)
        with self.connect() as conn:
            # One pending login per website session.
            conn.execute(
                """
                DELETE FROM oauth_states
                WHERE session_id = ? AND consumed_at IS NULL
                """,
                (session_id,),
            )
            conn.execute(
                """
                INSERT INTO oauth_states (
                    state_hash, session_id, oauth_cookie_id,
                    created_at, expires_at, consumed_at
                ) VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (state_h, session_id, cookie_id, _iso(now), _iso(expires)),
            )
            conn.commit()
        return opaque, cookie_id

    def clear_pending_for_session(self, session_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM oauth_states WHERE session_id = ? AND consumed_at IS NULL",
                (session_id,),
            )
            conn.commit()

    def consume_atomic(
        self,
        opaque_state: str,
        session_id: str,
        oauth_cookie_id: str,
    ) -> Optional[OAuthStateRow]:
        """Atomically consume a matching pending state. Returns row or None."""
        self.init_db()
        state_h = hash_state(opaque_state)
        now = _utc_now()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM oauth_states
                WHERE state_hash = ?
                  AND session_id = ?
                  AND oauth_cookie_id = ?
                  AND consumed_at IS NULL
                """,
                (state_h, session_id, oauth_cookie_id),
            ).fetchone()
            if row is None:
                return None
            if _parse(row["expires_at"]) <= now:
                conn.execute(
                    "DELETE FROM oauth_states WHERE state_hash = ?",
                    (state_h,),
                )
                conn.commit()
                return None
            consumed_at = _iso(now)
            cur = conn.execute(
                """
                UPDATE oauth_states
                SET consumed_at = ?
                WHERE state_hash = ?
                  AND consumed_at IS NULL
                """,
                (consumed_at, state_h),
            )
            if cur.rowcount != 1:
                conn.commit()
                return None
            conn.commit()
            return OAuthStateRow(
                state_hash=str(row["state_hash"]),
                session_id=str(row["session_id"]),
                oauth_cookie_id=str(row["oauth_cookie_id"]),
                created_at=str(row["created_at"]),
                expires_at=str(row["expires_at"]),
                consumed_at=consumed_at,
            )

    def delete_by_cookie(self, oauth_cookie_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM oauth_states WHERE oauth_cookie_id = ?",
                (oauth_cookie_id,),
            )
            conn.commit()


_store: Optional[KiteOAuthStore] = None


def get_kite_oauth_store() -> KiteOAuthStore:
    global _store
    if _store is None or _store.db_path != settings.KITE_OAUTH_STATE_DB_PATH:
        _store = KiteOAuthStore(settings.KITE_OAUTH_STATE_DB_PATH)
        _store.init_db()
    return _store


def reset_kite_oauth_store_cache() -> None:
    global _store
    _store = None
