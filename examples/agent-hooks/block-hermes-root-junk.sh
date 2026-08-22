#!/usr/bin/env bash
# Optional write-gate for HERMES_HOME. Recommended hardening, not required to run MyMemory.
# Copy to ~/.hermes/agent-hooks/, chmod +x, then point config.yaml hooks.pre_tool_call at it.
# Do not copy shell-hooks-allowlist.json from another machine — Hermes records local mtimes.
set -euo pipefail

payload="$(cat -)"
tool=$(echo "$payload" | jq -r '.tool_name // empty')
case "$tool" in
  write_file|patch|terminal|shell) ;;
  *) printf '{}\n'; exit 0 ;;
esac

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_HOME="$(python3 -c "import os; print(os.path.realpath(os.path.expanduser('$HERMES_HOME')))")"
WORKSPACE="${HERMES_WORKSPACE:-$HOME}"

block() {
  printf '{"action":"block","message":"%s"}\n' "$1"
  exit 0
}

allow() {
  printf '{}\n'
  exit 0
}

if [[ "$tool" == "terminal" || "$tool" == "shell" ]]; then
  cmd=$(echo "$payload" | jq -r '.tool_input.command // empty')
  [[ -n "$cmd" && "$cmd" != "null" ]] || allow
  if python3 - "$cmd" <<'PY'
import re, sys
cmd = sys.argv[1]
redirect_to_mem = re.compile(
    r"(?:>>?|tee(?:\s+-a)?)\s*['\"]?[^'\"\s;|&]*memories/",
    re.I,
)
cp_to_mem = re.compile(
    r"(?:^|[\s;|&])(?:cp|mv|rsync|install)\s+"
    r"(?:[^\s;|&]+\s+)*"
    r"['\"]?[^'\"\s;|&]*memories/[^'\"\s;|&]*['\"]?\s*(?:$|[;|&])",
    re.I,
)
if redirect_to_mem.search(cmd) or cp_to_mem.search(cmd):
    sys.exit(0)
sys.exit(1)
PY
  then
    block "Do not write under ~/.hermes/memories/ via shell; use digest/weekly slash or the weekly UI"
  fi
  allow
fi

path=$(echo "$payload" | jq -r '.tool_input.path // .tool_input.file_path // empty')
[[ -n "$path" && "$path" != "null" ]] || { printf '{}\n'; exit 0; }

abs="$(python3 -c "import os,sys; print(os.path.realpath(os.path.expanduser(sys.argv[1])))" "$path" 2>/dev/null || echo "$path")"

if [[ "$abs" == "${HERMES_HOME}/hermes-agent/"* ]]; then
  block "Do not write into ~/.hermes/hermes-agent/ (source tree)."
fi

[[ "$abs" == "${HERMES_HOME}/"* || "$abs" == "$HERMES_HOME" ]] || allow

if [[ "$abs" == "$HERMES_HOME" ]]; then
  block "Do not write directly to ~/.hermes/ root."
fi

rel="${abs#${HERMES_HOME}/}"

case "$rel" in
  memories/MEMORY.md|memories/USER.md)
    block "Use the memory tool for hot memory writes"
    ;;
  .env|auth.json|state.db|state.db-wal|state.db-shm)
    block "Runtime or secret file at ~/.hermes/ root is not agent-editable"
    ;;
  memories/staging/weekly/*|memories/staging/weekly)
    block "Weekly staging is plugin-owned"
    ;;
  memories/staging/.digest-state.json|memories/staging/.weekly-state.json)
    block "Plugin state files are runtime-managed"
    ;;
  memories/staging/daily/*.md|memories/staging/daily/*.MD)
    block "Do not write daily staging with file tools; use digest or the weekly UI"
    ;;
  memories/staging/*|memories/staging|memories/*|memories)
    block "Only plugin/UI/digest pipelines may write under memories/"
    ;;
  config.yaml|SOUL.md|agent-hooks/*|plugins/*|skills/*|scripts/*)
    allow
    ;;
esac

block "Path not on agent allowlist under ~/.hermes/ (${rel})."
