#!/usr/bin/env bash
# Production build check for test-manager-frontend.
#
# `next dev` (in the frontend dev container) shares `.next` via the
# ./test-manager-frontend:/app bind mount, so running `next build` while dev is
# up collides over `.next` and fails with the bogus
# "<Html> should not be imported outside of pages/_document" error on /404+/500.
# This stops the dev frontend for the duration of the build and always restarts
# it afterwards (even if the build fails). node_modules is a named Docker volume,
# so the host build uses its own host node_modules and never touches the container.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="docker compose -f $REPO/docker-compose.dev.yml"
FE="$REPO/test-manager-frontend"

restart() { $COMPOSE up -d frontend >/dev/null 2>&1 || true; }
trap restart EXIT  # bring dev back even on build failure

echo "→ stopping frontend dev (frees the shared .next)…"
$COMPOSE stop frontend >/dev/null

if [ ! -d "$FE/node_modules" ]; then
  echo "→ no host node_modules — npm ci…"
  (cd "$FE" && npm ci)
elif [ "$FE/package-lock.json" -nt "$FE/node_modules" ]; then
  echo "→ lockfile changed — npm install…"
  (cd "$FE" && npm install)
fi

echo "→ building (production)…"
(cd "$FE" && npm run build)

echo "✓ frontend build OK"
