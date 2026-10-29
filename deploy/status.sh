#!/bin/bash
# Quick health report: agents, local health, who is listening where, Funnel, where the logs are.
set -uo pipefail
[ "$(uname)" = "Darwin" ] || { echo "macOS only (uname=$(uname))." >&2; exit 1; }
UID_NUM="$(id -u)"
LOG_DIR="$HOME/Library/Logs/gldb"

echo "== launchd agents"
for label in com.jay.gldb-backend com.jay.gldb-frontend; do
  if out="$(launchctl print "gui/$UID_NUM/$label" 2>/dev/null)"; then
    state="$(printf '%s\n' "$out" | awk '/^[[:space:]]*state = /{print $3; exit}')"
    pid="$(printf '%s\n' "$out" | awk '/^[[:space:]]*pid = /{print $3; exit}')"
    echo "  $label: state=${state:-?} pid=${pid:--}"
  else
    echo "  $label: NOT LOADED"
  fi
done

echo "== local health (loopback)"
echo "  backend  /health            -> $(curl -s -o /dev/null --max-time 3 -w '%{http_code}' http://127.0.0.1:8000/health) (200 expected)"
echo "  backend  /cases, no secret  -> $(curl -s -o /dev/null --max-time 3 -w '%{http_code}' http://127.0.0.1:8000/cases) (401 expected: gate active)"
echo "  frontend /login             -> $(curl -s -o /dev/null --max-time 3 -w '%{http_code}' http://127.0.0.1:3000/login) (200 expected)"
echo "  frontend /, not signed in   -> $(curl -s -o /dev/null --max-time 3 -w '%{http_code}' http://127.0.0.1:3000/) (303 expected; 503 means setup error in .env.production)"

echo "== listeners (must all be 127.0.0.1 for 3000 and 8000)"
lsof -nP -iTCP:3000 -iTCP:8000 -sTCP:LISTEN 2>/dev/null | sed 's/^/  /'
if lsof -nP -iTCP:3000 -iTCP:8000 -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print $9}' | grep -vE '^(127\.0\.0\.1|\[::1\]|localhost):' | grep -q .; then
  echo "  WARNING: something listens on a non-loopback address"
fi

echo "== tailscale funnel"
TS=""
if command -v tailscale >/dev/null 2>&1; then TS="$(command -v tailscale)"
elif [ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]; then TS=/Applications/Tailscale.app/Contents/MacOS/Tailscale; fi
if [ -n "$TS" ]; then "$TS" funnel status 2>&1 | sed 's/^/  /'; else echo "  tailscale CLI not found"; fi

cat <<MSG
== logs
  tail -f $LOG_DIR/backend.log
  tail -f $LOG_DIR/frontend.log
  restart: launchctl kickstart -k gui/$UID_NUM/com.jay.gldb-backend   (or ...-frontend)
MSG
