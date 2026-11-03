#!/bin/bash
# Add the six showcase cases (sanctions hit, live lookup verified and wrong-date, AI-generated image, hidden instruction,
# receipt that does not add up) to the running demo. Nothing existing is touched.
#
#   deploy/add-showcases.sh              add the ones that are not there yet
#   deploy/add-showcases.sh --replace    delete and redo any that are there (after a code or settings change)
#
# Needs the backend running with the outside sources on (GLDB_SANCTIONS_SCREEN=on, GLDB_GLEIF=on,
# GLDB_REGISTRY_LIVE=true; docs/verification-sources.md). Cost: about one cent of model use per case.
# Do not run deploy/load-demo-data.sh for this: that replaces all cases.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$(mktemp -d)/showcases"
cd "$ROOT"
uv run --no-sync python backend/scripts/make_showcase_bundles.py --out "$OUT"
uv run --no-sync python backend/scripts/upload_showcases.py --dir "$OUT" "$@"
