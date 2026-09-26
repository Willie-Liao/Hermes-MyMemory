#!/usr/bin/env bash
# Push this plugin root to https://github.com/Willie-Liao/Hermes-MyMemory.git
# and attach the GTE int8 tarball as release gte-int8-v1 (not git LFS).
#
# Commit author: sources AGENT/github-snapshot-identity.sh (see that file).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PACK_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AGENT_ROOT="$(cd "$PACK_ROOT/../../.." && pwd)"
# shellcheck source=/dev/null
source "$AGENT_ROOT/github-snapshot-identity.sh"
REMOTE="${MYMEMORY_GITHUB_REMOTE:-git@github.com:Willie-Liao/Hermes-MyMemory.git}"
BUNDLE="${MYMEMORY_GTE_BUNDLE:-/tmp/mymemory-gte-int8/gte-multilingual-base-int8.tar.gz}"

if [[ ! -f "$PACK_ROOT/plugin.yaml" ]]; then
  echo "error: run from MyMemory plugin root" >&2
  exit 1
fi

VERSION="$(awk -F': *' '/^version:/{print $2; exit}' "$PACK_ROOT/plugin.yaml")"
TAG="v${VERSION}"

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/mymemory-github.XXXXXX")"
trap 'rm -rf "$WORKDIR"' EXIT
rsync -a \
  --exclude '.git/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude 'node_modules/' \
  --exclude 'weekly/ui/dist/' \
  --exclude 'weekly/debug_w38_worker1_events.py' \
  --exclude 'weekly/test_w38_sunday_evening_patch.py' \
  --exclude '.env' --exclude '.env.local' --exclude 'auth.json' \
  "$PACK_ROOT/" "$WORKDIR/src/"
cd "$WORKDIR/src"
git init -b main
git add -A
git -c user.name="$GIT_AUTHOR_NAME" -c user.email="$GIT_AUTHOR_EMAIL" \
  commit -m "Release MyMemory ${VERSION}"
git tag -a "$TAG" -m "MyMemory ${VERSION}"
if [[ -n "${MYMEMORY_GIT_SSH:-}" ]]; then
  export GIT_SSH_COMMAND="$MYMEMORY_GIT_SSH"
elif [[ -f "${HOME}/.ssh/aliyun_ubuntu_uxsq" ]]; then
  export GIT_SSH_COMMAND="ssh -i ${HOME}/.ssh/aliyun_ubuntu_uxsq -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
fi
git remote add origin "$REMOTE"
git push --force origin HEAD:main
git push --force origin "refs/tags/${TAG}"

if command -v gh >/dev/null 2>&1; then
  if gh release view "$TAG" --repo Willie-Liao/Hermes-MyMemory >/dev/null 2>&1; then
    gh release edit "$TAG" --repo Willie-Liao/Hermes-MyMemory --title "MyMemory ${VERSION}" --latest
  else
    gh release create "$TAG" \
      --repo Willie-Liao/Hermes-MyMemory \
      --title "MyMemory ${VERSION}" \
      --notes "Plugin snapshot from plugin.yaml version ${VERSION}." \
      --latest
  fi
fi

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
