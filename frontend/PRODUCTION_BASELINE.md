# Approved production UI

`npm run build` compiles the editable React/TypeScript source in `frontend/src` using TypeScript and Vite. Future frontend changes now reach production through the normal build.

The recovered baseline matches approved release `auto-login-button-20260916121611`:

- Built JavaScript SHA-256: `ff2e03b2ffbb9792746c342fa503e30316301527534830b0921fc7ccab15b570`, byte-for-byte identical to the approved JavaScript.
- Offline browser checks: Radar Stream, Checklist, Execution Desk, Diagnostics & Logs, Settings, and Login have identical text and pixels at 1440 x 1000 with the same fixture data and clock. External fonts were blocked equally on both renders.
- Auto-login completion, restored button state, logout, and API request sequences match. No runtime errors. Tests mock all API traffic and do not generate real tokens or place orders.
- Generated CSS differs only in unused utility rules; rendered pages match.
- NJ approved matching the release's session-only Kite token and Admin actions. Website sign-in/MFA and backend session/CSRF checks remain intact.

Run `verify-release-parity.cjs` with `PARITY_RUNTIME` pointing to a node_modules directory containing Playwright and pngjs, and `PARITY_BROWSER` optionally pointing to an installed Chromium executable. The reference snapshot is immutable; later intentional UI changes should be reviewed against their own acceptance criteria.

`npm run build:approved-snapshot` is an explicit emergency fallback to the preserved compiled release. It is not the normal production build. Backend code remains in the root repository.
