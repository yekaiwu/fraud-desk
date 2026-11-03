#!/bin/bash
# Started daily by launchd (com.jay.gldb-sanctions). Rebuilds the sanctions-list snapshot from the official UN, OFAC, EU and UK
# downloads (docs/verification-sources.md section 2) so x_sanctions_screen never runs on a snapshot older than
# GLDB_SANCTIONS_MAX_AGE_DAYS. A list that cannot be fetched keeps its previous entries with their OLD download time.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
set -a
# shellcheck disable=SC1091
. "$ROOT/deploy/.env.production"
set +a
echo "== sanctions update $(date -u +%Y-%m-%dT%H:%M:%SZ)"
uv run --no-sync python backend/scripts/update_sanctions.py
status=$?
echo "== finished with exit code $status"
exit $status
