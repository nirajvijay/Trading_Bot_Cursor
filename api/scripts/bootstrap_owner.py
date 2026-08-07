"""Bootstrap / maintain the single website owner account.

Examples:
  python -m api.scripts.bootstrap_owner --username owner --password '...'
  python -m api.scripts.bootstrap_owner --reset-password '...'
  python -m api.scripts.bootstrap_owner --clear-mfa

MFA bootstrap sequence (ops):
  1. Create owner locally (this script) with WEB_AUTH_ENABLED=true and MFA not required yet.
  2. Log in on the website, enrol TOTP via /api/v1/account/mfa/setup + mfa/confirm.
  3. Set WEB_AUTH_MFA_REQUIRED=true (production profile) and restart.
  4. Only then expose the domain (Phase 4 — not implemented here).
"""

from __future__ import annotations

import argparse
import getpass
import sys

from api.auth.web_auth_store import get_web_auth_store, reset_web_auth_store_cache


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap NIFTY RADAR website owner")
    parser.add_argument("--username", help="Owner username (create only)")
    parser.add_argument("--password", help="Password (create / reset). Prefer prompt.")
    parser.add_argument(
        "--reset-password",
        nargs="?",
        const="",
        default=None,
        help="Reset owner password (optional value; otherwise prompt)",
    )
    parser.add_argument("--clear-mfa", action="store_true", help="Disable MFA and clear secrets")
    args = parser.parse_args(argv)

    reset_web_auth_store_cache()
    store = get_web_auth_store()
    store.init_db()

    if args.clear_mfa:
        store.clear_mfa()
        print("MFA cleared for owner.")
        return 0

    if args.reset_password is not None:
        password = args.reset_password or args.password or getpass.getpass("New password: ")
        if len(password) < 8:
            print("Password must be at least 8 characters.", file=sys.stderr)
            return 1
        store.reset_password(password)
        print("Owner password reset. Existing sessions cleared.")
        return 0

    if not args.username:
        print("Provide --username to create the owner, or --reset-password / --clear-mfa.", file=sys.stderr)
        return 1
    password = args.password or getpass.getpass("Password: ")
    if len(password) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        return 1
    try:
        user = store.create_owner(args.username, password)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Owner created: {user.username}")
    print(
        "Next: log in → enrol TOTP (mfa/setup + mfa/confirm) → "
        "enable WEB_AUTH_MFA_REQUIRED → then expose domain (Phase 4)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
