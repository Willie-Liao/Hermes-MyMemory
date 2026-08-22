#!/usr/bin/env bash
# Refresh this public pack from a local MyMemory plugin checkout.
# Set HERMES_PACK_SOURCE to that folder. Never copies a live Hermes home.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PACK_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -z "${HERMES_PACK_SOURCE:-}" ]]; then
  echo "error: set HERMES_PACK_SOURCE to a MyMemory plugin directory" >&2
  echo "hint: export HERMES_PACK_SOURCE=/path/to/plugins/MyMemory" >&2
  exit 1
fi

SRC="${HERMES_PACK_SOURCE}"
if [[ ! -f "$SRC/plugin.yaml" ]]; then
  echo "error: $SRC is not a MyMemory plugin root (missing plugin.yaml)" >&2
  exit 1
fi

RSYNC_EXCLUDES=(
  --exclude '.git/'
  --exclude 'scripts/'
  --exclude 'Install MyMemory.command'
  --exclude '__pycache__/'
  --exclude '.pytest_cache/'
  --exclude 'node_modules/'
  --exclude 'weekly/ui/dist/'
  --exclude '.vite/'
  --exclude '*.pyc'
  --exclude '.DS_Store'
  --exclude '.env'
  --exclude '.env.*'
  --exclude '!.env.example'
  --exclude 'auth.json'
  --exclude 'config.yaml'
  --exclude 'memories/'
  --exclude 'logs/'
  --exclude 'sessions/'
  --exclude '*.db'
  --exclude '*.db-*'
)

echo "Syncing provider from: $SRC"
echo "Into pack: $PACK_ROOT"
rsync -a "${RSYNC_EXCLUDES[@]}" "$SRC/" "$PACK_ROOT/"

FILE_COUNT="$(find "$PACK_ROOT" -type f ! -path '*/.git/*' | wc -l | tr -d ' ')"
echo "Pack refreshed ($FILE_COUNT files). Review git status before pushing."
