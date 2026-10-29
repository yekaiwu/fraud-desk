#!/bin/bash
# Started by launchd (com.jay.gldb-backend). Loads deploy/.env.production and runs the API on loopback only.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
set -a
# shellcheck disable=SC1091
. "$ROOT/deploy/.env.production"
set +a
# An empty GLDB_PROXY_SECRET would silently switch the backend's gate off (app/access.py), leaving the API open to
# anything that can reach 127.0.0.1:8000. Never start without one.
[ "${#GLDB_PROXY_SECRET}" -ge 16 ] || { echo "GLDB_PROXY_SECRET must be at least 16 characters (see deploy/env.production.example); refusing to start." >&2; exit 78; }
# --app-dir backend: how this repo imports `app` (README). --no-proxy-headers: the peer address stays the
# real peer; the forwarded client address is read by app/access.py only after the proxy secret matched.
# --no-sync: the environment was built by install.sh; never touch the network at boot.
exec uv run --no-sync uvicorn --app-dir backend app.main:app \
  --host 127.0.0.1 --port 8000 --no-proxy-headers --no-server-header
