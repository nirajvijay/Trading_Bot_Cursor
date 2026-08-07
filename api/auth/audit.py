"""Append-only auth audit log (never write secrets/tokens/state)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from api.auth import settings


def write_audit(event: str, **fields: Any) -> None:
    forbidden = {
        "request_token",
        "state",
        "access_token",
        "refresh_token",
        "password",
        "api_secret",
        "mfa_secret",
        "totp",
        "csrf_token",
        "session_id",
        "oauth_cookie",
    }
    safe = {k: v for k, v in fields.items() if k.lower() not in forbidden}
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **safe,
    }
    try:
        settings.ensure_parent_dir(settings.AUDIT_LOG_PATH, mode=0o700)
        with open(settings.AUDIT_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True) + "\n")
    except OSError:
        # Audit must never break the request path (e.g. missing /opt in tests/dev).
        pass
