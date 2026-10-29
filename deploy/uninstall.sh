#!/bin/bash
# Stop and remove the GLDB launchd agents (docs/deploy.md). Keeps data/ and deploy/.env.production.
#
#   deploy/uninstall.sh                 remove the agents
#   deploy/uninstall.sh --funnel-off    also run `tailscale funnel reset` (stops ALL funnel/serve config on this node)
set -euo pipefail

FUNNEL_OFF=0
for arg in "$@"; do
  case "$arg" in
    --funnel-off) FUNNEL_OFF=1 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done
[ "$(uname)" = "Darwin" ] || { echo "macOS only (uname=$(uname))." >&2; exit 1; }

UID_NUM="$(id -u)"
for label in com.jay.gldb-frontend com.jay.gldb-backend; do
  launchctl bootout "gui/$UID_NUM/$label" 2>/dev/null && echo "stopped $label" || echo "$label was not loaded"
  rm -f "$HOME/Library/LaunchAgents/$label.plist"
done

if [ "$FUNNEL_OFF" = 1 ]; then
  TS=""
  if command -v tailscale >/dev/null 2>&1; then TS="$(command -v tailscale)"
  elif [ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]; then TS=/Applications/Tailscale.app/Contents/MacOS/Tailscale; fi
  [ -n "$TS" ] || { echo "tailscale CLI not found; turn Funnel off with: tailscale funnel reset" >&2; exit 1; }
  "$TS" funnel reset
  echo "funnel reset: the node no longer serves anything publicly"
else
  echo "Funnel was not touched. If it is on, the public URL now answers 502. Turn it off with: $0 --funnel-off"
fi
