# MFA bootstrap sequence (do not expose domain until complete)
#
# 1. Create the single owner locally:
#      python -m api.scripts.bootstrap_owner --username <owner>
# 2. Start API with WEB_AUTH_ENABLED=true and WEB_AUTH_MFA_REQUIRED=false
#    (development / pre-prod). Log in via /api/v1/account/login.
# 3. Enrol TOTP: POST /api/v1/account/mfa/setup then mfa/confirm with a code.
# 4. Set production profile: APP_ENV=production, WEB_AUTH_ENABLED=true,
#    WEB_AUTH_MFA_REQUIRED=true, WEB_AUTH_COOKIE_SECURE=true,
#    KITE_EXPECTED_USER_ID=<id>, WEB_AUTH_ORIGIN_ALLOWLIST=https://<canonical-host>.
#    Restart the API (MFA enforcement has no runtime reload — restart + session wipe).
# 5. Only then perform Phase 4 domain exposure (Caddy/DNS/Zerodha redirect) —
#    not part of Phases 1–3 / this hardening pass.
#
# Emergency: python -m api.scripts.bootstrap_owner --clear-mfa   (clears ALL sessions)
#            python -m api.scripts.bootstrap_owner --reset-password  (clears ALL sessions)
