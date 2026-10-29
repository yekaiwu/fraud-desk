#!/bin/bash
# Started by launchd (com.jay.gldb-frontend). Loads deploy/.env.production and serves the built Next app
# on loopback only; Tailscale Funnel (or nothing) is the only thing in front of it.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/frontend"
set -a
# shellcheck disable=SC1091
. "$ROOT/deploy/.env.production"
set +a
export NODE_ENV=production
# package.json "start" is `next start -H 127.0.0.1`; the port is explicit here.
exec npm run start -- -p 3000
