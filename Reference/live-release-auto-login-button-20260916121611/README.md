# Live auto-login release snapshot

This folder preserves the live release that was serving `https://njtrading.website/owner` as `auto-login-button-20260916121611`.

- Release marker: `4d5f60dcaf35238c89da9dc73a72f15da45dba89`
- The exact browser-facing bundle is in `frontend/dist/`.
- The release's accompanying frontend source is in `frontend/src/`.
- The source and compiled bundle are intentionally both preserved because they do not describe the same UI revision. The compiled bundle is the visual authority for the deployed auto-login/heatmap website.
- No runtime data, environment files, credentials, private keys, or server secrets are included.

Bundle fingerprints:

- `frontend/dist/index.html`: `3670385dad25e77f8e29e4461d2ea260e0bc6738845e744a791e8f0a72830af5`
- `frontend/dist/assets/index-DlyfxDlt.js`: `ff2e03b2ffbb9792746c342fa503e30316301527534830b0921fc7ccab15b570`
