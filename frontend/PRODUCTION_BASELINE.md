# Approved production UI

`npm run build` reproduces the exact approved `auto-login-button-20260916121611` browser assets from the committed release snapshot. It verifies the HTML, JavaScript and CSS checksums before copying the complete static directory.

The editable source currently does NOT reproduce that UI. `npm run build:source-preview` builds that source for recovery work only. Frontend source edits will not reach production until source recovery is visually verified and the production build command is deliberately switched back. Backend code remains in the root repository.

This is an exact artifact deployment baseline, not a claim that editable frontend source has been recovered.
