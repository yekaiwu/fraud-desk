#!/bin/bash
# Load the committed demo cases (demo-data/demo-data.tar.gz: 10 fictitious cases, their pages and the
# duplicate-document fingerprints) into this machine's data directory.
#
#   deploy/load-demo-data.sh          replaces the current cases with the demo ones (after a backup)
#
# It stops the backend, moves the existing data directory aside to data.bak-<time>, unpacks the demo data,
# and starts the backend again. Nothing is deleted. Run it on the always-on Mac after `git pull`.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVE="$ROOT/demo-data/demo-data.tar.gz"
ENV_FILE="$ROOT/deploy/.env.production"
LABEL=com.jay.gldb-backend
AGENT_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_NUM="$(id -u)"
say() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ -f "$ARCHIVE" ] || die "$ARCHIVE not found. Run git pull first."

# The backend reads GLDB_DATA_DIR from the production env file; fall back to <repo>/data.
DATA_DIR="$ROOT/data"
if [ -f "$ENV_FILE" ]; then
  fromenv="$(awk -F= '/^GLDB_DATA_DIR=/{print substr($0, index($0,"=")+1)}' "$ENV_FILE" | tail -1)"
  [ -n "$fromenv" ] && DATA_DIR="$fromenv"
fi
case "$DATA_DIR" in /*) ;; *) die "GLDB_DATA_DIR must be an absolute path (got: $DATA_DIR)." ;; esac
say "Data directory: $DATA_DIR"

WAS_RUNNING=0
if launchctl print "gui/$UID_NUM/$LABEL" >/dev/null 2>&1; then
  WAS_RUNNING=1
  say "Stopping $LABEL"
  launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
  sleep 2
fi

if [ -e "$DATA_DIR" ]; then
  BACKUP="$DATA_DIR.bak-$(date +%Y%m%d-%H%M%S)"
  say "Moving the current data to $BACKUP"
  mv "$DATA_DIR" "$BACKUP"
fi
mkdir -p "$DATA_DIR"
tar -xzf "$ARCHIVE" -C "$DATA_DIR"
rm -f "$DATA_DIR"/gldb.sqlite3-wal "$DATA_DIR"/gldb.sqlite3-shm
chmod 700 "$DATA_DIR"
say "Unpacked: $(ls "$DATA_DIR" | tr '\n' ' ')"

if [ "$WAS_RUNNING" = 1 ]; then
  [ -f "$AGENT_PLIST" ] || die "Backend was running but $AGENT_PLIST is missing; run deploy/install.sh."
  say "Starting $LABEL"
  launchctl bootstrap "gui/$UID_NUM" "$AGENT_PLIST"
  launchctl enable "gui/$UID_NUM/$LABEL" 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    code="$(curl -s -o /dev/null --max-time 3 -w '%{http_code}' http://127.0.0.1:8000/health || true)"
    [ "$code" = 200 ] && { say "Backend healthy. Demo cases loaded."; exit 0; }
    sleep 2
  done
  die "Backend did not answer /health with 200. See ~/Library/Logs/gldb/."
fi
say "Demo cases loaded. The backend was not running; start it with deploy/install.sh."
