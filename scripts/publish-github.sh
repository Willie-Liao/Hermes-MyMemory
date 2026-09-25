#!/usr/bin/env bash
# Push this plugin root to https://github.com/Willie-Liao/Hermes-MyMemory.git
# and attach the GTE int8 tarball as release gte-int8-v1 (not git LFS).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PACK_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REMOTE="${MYMEMORY_GITHUB_REMOTE:-https://github.com/Willie-Liao/Hermes-MyMemory.git}"
BUNDLE="${MYMEMORY_GTE_BUNDLE:-/tmp/mymemory-gte-int8/gte-multilingual-base-int8.tar.gz}"

if [[ ! -f "$PACK_ROOT/plugin.yaml" ]]; then
  echo "error: run from MyMemory plugin root" >&2
  exit 1
fi

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/mymemory-github.XXXXXX")"
trap 'rm -rf "$WORKDIR"' EXIT
rsync -a \
  --exclude '.git/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude 'node_modules/' \
  --exclude 'weekly/ui/dist/' \
  "$PACK_ROOT/" "$WORKDIR/src/"
cd "$WORKDIR/src"
git init -b main
git add -A
git commit -m "MyMemory plugin snapshot for GitHub install"
git remote add origin "$REMOTE"
git push -u origin HEAD:main

if command -v gh >/dev/null 2>&1 && [[ -f "$BUNDLE" ]]; then
  if gh release view gte-int8-v1 --repo Willie-Liao/Hermes-MyMemory >/dev/null 2>&1; then
    gh release upload gte-int8-v1 "$BUNDLE" --clobber --repo Willie-Liao/Hermes-MyMemory
  else
    gh release create gte-int8-v1 "$BUNDLE" \
      --repo Willie-Liao/Hermes-MyMemory \
      --title "GTE int8 Channel 4" \
      --notes "Pre-exported GTE multilingual-base QInt8 ONNX + tokenizer for scripts/install.sh (no PyTorch on the gateway)."
  fi
else
  echo "note: gh not authenticated or bundle missing; upload $BUNDLE as release asset gte-int8-v1"
fi
