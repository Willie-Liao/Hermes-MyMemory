#!/usr/bin/env bash
# Publish a MyMemory snapshot from an AGENT git commit as a GitHub tag + release.
# Plugin tree only (no hermes-home memories). Strips .env; keeps **/.env.example.
#
# Usage: publish-github-at-commit.sh <agent-commit> <version e.g. 1.1.10> [notes-file]
# Env: MYMEMORY_PUSH_MAIN=1 to also force-push main (default: tag only).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PACK_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AGENT_ROOT="$(cd "$PACK_ROOT/../../.." && pwd)"
# shellcheck source=/dev/null
source "$AGENT_ROOT/github-snapshot-identity.sh"

COMMIT="${1:?agent commit sha}"
VERSION="${2:?version like 1.1.10}"
NOTES_FILE="${3:-}"
TAG="v${VERSION}"
REMOTE="${MYMEMORY_GITHUB_REMOTE:-git@github.com:Willie-Liao/Hermes-MyMemory.git}"
PUSH_MAIN="${MYMEMORY_PUSH_MAIN:-0}"

if ! git -C "$AGENT_ROOT" cat-file -e "${COMMIT}^{commit}" 2>/dev/null; then
  echo "error: unknown commit $COMMIT in $AGENT_ROOT" >&2
  exit 1
fi

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/mymemory-github.XXXXXX")"
trap 'rm -rf "$WORKDIR"' EXIT
mkdir -p "$WORKDIR/src"
git -C "$AGENT_ROOT" archive "$COMMIT" hermes-home/plugins/MyMemory \
  | tar -x -C "$WORKDIR/src" --strip-components=3

cd "$WORKDIR/src"
# No secrets or runtime memory in the public plugin repo.
find . -type f \( -name '.env' -o -name '.env.local' -o -name 'auth.json' \) -delete 2>/dev/null || true
while IFS= read -r -d '' f; do
  case "$f" in
    */.env.example) ;;
    *) rm -f "$f" ;;
  esac
done < <(find . -type f -name '.env*' -print0 2>/dev/null || true)
# Tests may embed fake keys in fixtures; scan production paths only.
if grep -rE 'sk-[A-Za-z0-9]{10,}|MINIMAX_CN_API_KEY=[^$<]' \
  --include='*' \
  --exclude='test_*.py' --exclude='*_test.py' --exclude='publish-github*.sh' \
  --exclude-dir='tests' \
  . 2>/dev/null | grep -v '.env.example' | grep -q .; then
  echo "error: suspected secret in snapshot tree; aborting" >&2
  exit 1
fi

git init -b main >/dev/null
git add -A
git -c user.name="$GIT_AUTHOR_NAME" -c user.email="$GIT_AUTHOR_EMAIL" \
  commit -m "Release MyMemory ${VERSION}" >/dev/null
git tag -fa "$TAG" -m "MyMemory ${VERSION}"

GIT_SSH="${MYMEMORY_GIT_SSH:-}"
if [[ -z "$GIT_SSH" && -f "${HOME}/.ssh/aliyun_ubuntu_uxsq" ]]; then
  GIT_SSH="ssh -i ${HOME}/.ssh/aliyun_ubuntu_uxsq -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
fi
if [[ -n "$GIT_SSH" ]]; then
  export GIT_SSH_COMMAND="$GIT_SSH"
fi
git remote add origin "$REMOTE"
git push --force origin "refs/tags/${TAG}"
if [[ "$PUSH_MAIN" == "1" ]]; then
  git push --force origin HEAD:main
fi

if command -v gh >/dev/null 2>&1; then
  ARGS=(release create "$TAG" --repo Willie-Liao/Hermes-MyMemory --title "MyMemory ${VERSION}")
  if [[ -n "$NOTES_FILE" && -f "$NOTES_FILE" ]]; then
    ARGS+=(--notes-file "$NOTES_FILE")
  else
    ARGS+=(--notes "MyMemory ${VERSION} snapshot from AGENT commit ${COMMIT}.")
  fi
  if [[ "$PUSH_MAIN" == "1" ]]; then
    ARGS+=(--latest)
  fi
  if gh release view "$TAG" --repo Willie-Liao/Hermes-MyMemory >/dev/null 2>&1; then
    gh release edit "$TAG" --repo Willie-Liao/Hermes-MyMemory --title "MyMemory ${VERSION}"
    [[ -n "$NOTES_FILE" && -f "$NOTES_FILE" ]] && gh release edit "$TAG" --repo Willie-Liao/Hermes-MyMemory --notes-file "$NOTES_FILE"
  else
    gh "${ARGS[@]}"
  fi
fi
echo "ok: ${TAG} from ${COMMIT}"
