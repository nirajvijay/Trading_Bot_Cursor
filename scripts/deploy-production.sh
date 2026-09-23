#!/usr/bin/env bash
# GitHub's manual production workflow calls /opt/nifty-radar/deploy.sh,
# which is installed from this versioned script.
set -Eeuo pipefail

ROOT=/opt/nifty-radar
WORKSPACE="$ROOT/workspace"
CURRENT="$ROOT/current"
STAMP=$(date +%Y%m%d%H%M%S)
mkdir -p "$ROOT/logs" "$ROOT/releases"
exec 9>"$ROOT/logs/deploy.lock"
flock -n 9 || { echo 'Another deployment is running' >&2; exit 1; }
exec > >(tee -a "$ROOT/logs/deploy-$STAMP.log") 2>&1

PREVIOUS=$(readlink -f "$CURRENT")
test -d "$PREVIOUS/frontend/dist"
curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/health >/dev/null
git -C "$WORKSPACE" fetch origin production
SHA=$(git -C "$WORKSPACE" rev-parse origin/production)
RELEASE="$ROOT/releases/deployment-$STAMP-${SHA:0:7}"
mkdir "$RELEASE"
# Build a clean, pinned commit. Local workspace edits cannot enter a release.
git -C "$WORKSPACE" archive "$SHA" | tar -x -C "$RELEASE"
printf '%s\n' "$SHA" > "$RELEASE/RELEASE_SHA"
printf '%s\n' "$PREVIOUS" > "$RELEASE/PREVIOUS_RELEASE"
cd "$RELEASE/frontend"
npm ci --no-audit --no-fund
npm run build
test -s dist/index.html
node --input-type=module -e 'import fs from "node:fs"; const html=fs.readFileSync("dist/index.html","utf8"); const assets=[...html.matchAll(/(?:src|href)="(\/assets\/[^\"]+)"/g)]; if(!assets.length)throw Error("No build assets");for(const [,p] of assets)if(!fs.existsSync("dist"+p))throw Error("Missing "+p);'

# Restart the API only when runtime Python code/dependencies changed.
# Frontend-only recovery leaves the running API and observation processes alone.
backend_digest() {
  (cd "$1"; find api -type f -name '*.py' -print; find . -maxdepth 1 -type f \( -name '*.py' -o -name 'requirements*.txt' \) -print) |
    LC_ALL=C sort | while IFS= read -r file; do sha256sum "$1/$file" | cut -d ' ' -f1; done | sha256sum | cut -d ' ' -f1
}
RESTART_API=false
if [ "$(backend_digest "$PREVIOUS")" != "$(backend_digest "$RELEASE")" ]; then
  RESTART_API=true
  "$ROOT/venv/bin/python" -m pip install -r "$RELEASE/requirements.txt" -r "$RELEASE/requirements-api.txt"
fi

SWITCHED=false
rollback_on_error() {
  local status=$?
  trap - ERR
  if [ "$SWITCHED" = true ]; then
    ln -sfn "$PREVIOUS" "$ROOT/current.rollback"
    mv -Tf "$ROOT/current.rollback" "$CURRENT"
    if [ "$RESTART_API" = true ]; then sudo systemctl restart nifty-radar-api; fi
    echo "Deployment failed; restored $PREVIOUS" >&2
  fi
  exit "$status"
}
trap rollback_on_error ERR
ln -sfn "$RELEASE" "$ROOT/current.next"
mv -Tf "$ROOT/current.next" "$CURRENT"
SWITCHED=true
if [ "$RESTART_API" = true ]; then sudo systemctl restart nifty-radar-api; fi
HEALTHY=false
for attempt in {1..15}; do
  if curl --fail --silent http://127.0.0.1:8000/api/v1/health >/dev/null; then HEALTHY=true; break; fi
  sleep 1
done
test "$HEALTHY" = true
curl --fail --silent --show-error https://njtrading.website/owner | cmp - "$RELEASE/frontend/dist/index.html"
trap - ERR
echo "Deployed production $SHA to $RELEASE"
echo "Previous release preserved: $PREVIOUS"
