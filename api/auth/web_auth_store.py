"""SQLite store for single-owner website auth (users + sessions)."""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from api.auth import settings
from api.auth.passwords import hash_password, needs_rehash, verify_password


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


@dataclass
class UserRecord:
    id: int
    username: str
    password_hash: str
    mfa_secret: Optional[str]
    mfa_enabled: bool
    mfa_pending_secret: Optional[str]


@dataclass
class SessionRecord:
    id: str
    user_id: int
    csrf_token: str
    created_at: str
    expires_at: str
    last_seen_at: str
    step_up_expires_at: Optional[str]
    username: str
    mfa_enabled: bool


class WebAuthStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else settings.WEB_AUTH_DB_PATH

    def connect(self) -> sqlite3.Connection:
        settings.ensure_parent_dir(self.db_path, mode=0o700)
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    mfa_secret TEXT,
                    mfa_enabled INTEGER NOT NULL DEFAULT 0,
                    mfa_pending_secret TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    csrf_token TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    step_up_expires_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
                """
            )
            conn.commit()
        try:
            self.db_path.chmod(0o600)
        except OSError:
            pass

    def get_user(self) -> Optional[UserRecord]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = 1").fetchone()
        if row is None:
            return None
        return UserRecord(
            id=int(row["id"]),
            username=str(row["username"]),
            password_hash=str(row["password_hash"]),
            mfa_secret=row["mfa_secret"],
            mfa_enabled=bool(row["mfa_enabled"]),
            mfa_pending_secret=row["mfa_pending_secret"],
        )

    def create_owner(self, username: str, password: str) -> UserRecord:
        self.init_db()
        existing = self.get_user()
        if existing is not None:
            raise ValueError("Owner already exists")
        now = _iso(_utc_now())
        pw_hash = hash_password(password)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    id, username, password_hash, mfa_secret, mfa_enabled,
                    mfa_pending_secret, created_at, updated_at
                ) VALUES (1, ?, ?, NULL, 0, NULL, ?, ?)
                """,
                (username, pw_hash, now, now),
            )
            conn.commit()
        user = self.get_user()
        assert user is not None
        return user

    def delete_all_sessions(self, user_id: int = 1) -> int:
        """Revoke every session for the owner (password/MFA security events)."""
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.commit()
            return int(cur.rowcount)

    def reset_password(self, new_password: str) -> None:
        user = self.get_user()
        if user is None:
            raise ValueError("No owner configured")
        now = _iso(_utc_now())
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = 1",
                (hash_password(new_password), now),
            )
            conn.commit()
        self.delete_all_sessions(user.id)

    def clear_mfa(self) -> None:
        user = self.get_user()
        if user is None:
            raise ValueError("No owner configured")
        now = _iso(_utc_now())
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE users
                SET mfa_secret = NULL, mfa_enabled = 0, mfa_pending_secret = NULL,
                    updated_at = ?
                WHERE id = 1
                """,
                (now,),
            )
            conn.commit()
        # MFA reset must invalidate all sessions (not only step-up).
        self.delete_all_sessions(user.id)

    def verify_login(self, username: str, password: str) -> Optional[UserRecord]:
        user = self.get_user()
        if user is None or user.username != username:
            return None
        if not verify_password(user.password_hash, password):
            return None
        if needs_rehash(user.password_hash):
            now = _iso(_utc_now())
            with self.connect() as conn:
                conn.execute(
                    "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = 1",
                    (hash_password(password), now),
                )
                conn.commit()
            user = self.get_user()
        return user

    def create_session(self, user_id: int) -> SessionRecord:
        now = _utc_now()
        session_id = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        expires = now + timedelta(seconds=settings.SESSION_TTL_SECONDS)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    id, user_id, csrf_token, created_at, expires_at,
                    last_seen_at, step_up_expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                """,
                (session_id, user_id, csrf, _iso(now), _iso(expires), _iso(now)),
            )
            conn.commit()
        record = self.get_session(session_id)
        assert record is not None
        return record

    def get_session(self, session_id: str) -> Optional[SessionRecord]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT s.*, u.username, u.mfa_enabled
                FROM sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        expires = _parse(row["expires_at"])
        if expires <= _utc_now():
            self.delete_session(session_id)
            return None
        return SessionRecord(
            id=str(row["id"]),
            user_id=int(row["user_id"]),
            csrf_token=str(row["csrf_token"]),
            created_at=str(row["created_at"]),
            expires_at=str(row["expires_at"]),
            last_seen_at=str(row["last_seen_at"]),
            step_up_expires_at=row["step_up_expires_at"],
            username=str(row["username"]),
            mfa_enabled=bool(row["mfa_enabled"]),
        )

    def touch_session(self, session_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE id = ?",
                (_iso(_utc_now()), session_id),
            )
            conn.commit()

    def delete_session(self, session_id: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.commit()

    def rotate_csrf(self, session_id: str) -> str:
        token = secrets.token_urlsafe(32)
        with self.connect() as conn:
            conn.execute(
                "UPDATE sessions SET csrf_token = ? WHERE id = ?",
                (token, session_id),
            )
            conn.commit()
        return token

    def grant_step_up(self, session_id: str) -> datetime:
        expires = _utc_now() + timedelta(seconds=settings.STEP_UP_TTL_SECONDS)
        with self.connect() as conn:
            conn.execute(
                "UPDATE sessions SET step_up_expires_at = ? WHERE id = ?",
                (_iso(expires), session_id),
            )
            conn.commit()
        return expires

    def has_valid_step_up(self, session: SessionRecord) -> bool:
        if not session.step_up_expires_at:
            return False
        return _parse(session.step_up_expires_at) > _utc_now()

    def set_mfa_pending(self, secret: str) -> None:
        now = _iso(_utc_now())
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET mfa_pending_secret = ?, updated_at = ? WHERE id = 1",
                (secret, now),
            )
            conn.commit()

    def confirm_mfa(self, secret: str) -> None:
        now = _iso(_utc_now())
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE users
                SET mfa_secret = ?, mfa_enabled = 1, mfa_pending_secret = NULL,
                    updated_at = ?
                WHERE id = 1
                """,
                (secret, now),
            )
            conn.commit()
        # Enrollment success: revoke all sessions; client must re-login with MFA.
        self.delete_all_sessions(1)

    def change_password(self, user_id: int, new_password: str) -> None:
        now = _iso(_utc_now())
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (hash_password(new_password), now, user_id),
            )
            conn.commit()
        self.delete_all_sessions(user_id)


_store: Optional[WebAuthStore] = None


def get_web_auth_store() -> WebAuthStore:
    global _store
    if _store is None or _store.db_path != settings.WEB_AUTH_DB_PATH:
        _store = WebAuthStore(settings.WEB_AUTH_DB_PATH)
        _store.init_db()
    return _store


def reset_web_auth_store_cache() -> None:
    global _store
    _store = None
