#!/usr/bin/env bash
# Copy this MyMemory provider into HERMES_HOME/plugins/MyMemory and enable it.
# Does not copy config.yaml, .env, memories, or any live Hermes home.

set -euo pipefail

SKIP_TESTS=false
FORCE=false
for arg in "$@"; do
  case "$arg" in
    --skip-tests) SKIP_TESTS=true ;;
    --force) FORCE=true ;;
    -h|--help)
      echo "Usage: install.sh [--skip-tests] [--force]"
      echo "  --skip-tests  Copy plugin and restart without pytest"
      echo "  --force       Continue even if tests fail"
      exit 0
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PACK_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

resolve_hermes_home() {
  local target="${HERMES_HOME:-$HOME/.hermes}"
  if [[ -L "$target" ]]; then
    target="$(cd "$target" && pwd -P)"
  elif [[ -d "$target" ]]; then
    target="$(cd "$target" && pwd)"
  fi
  echo "$target"
}

resolve_hermes_bin() {
  if [[ -n "${HERMES_BIN:-}" ]] && [[ -x "${HERMES_BIN}" ]]; then
    echo "${HERMES_BIN}"
    return 0
  fi
  if command -v hermes >/dev/null 2>&1; then
    command -v hermes
    return 0
  fi
  if [[ -x "${HOME}/.local/bin/hermes" ]]; then
    echo "${HOME}/.local/bin/hermes"
    return 0
  fi
  return 1
}

HERMES_HOME="$(resolve_hermes_home)"
HERMES_BIN="$(resolve_hermes_bin)" || {
  echo "error: hermes CLI not found (install Hermes first or set HERMES_BIN)" >&2
  exit 1
}

if [[ ! -f "$PACK_ROOT/plugin.yaml" ]]; then
  echo "error: plugin.yaml missing — this pack is not a MyMemory provider root" >&2
  exit 1
fi

if [[ ! -d "$HERMES_HOME" ]]; then
  echo "error: HERMES_HOME not found at $HERMES_HOME" >&2
  echo "hint: install Hermes, then set HERMES_HOME" >&2
  exit 1
fi

DEST="$HERMES_HOME/plugins/MyMemory"
mkdir -p "$DEST" "$HERMES_HOME/agent-hooks"

RSYNC_EXCLUDES=(
  --exclude '.git/'
  --exclude '/scripts/'
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

echo "MyMemory — Hermes memory provider installer"
echo "==========================================="
echo "Pack: $PACK_ROOT"
echo "HERMES_HOME: $HERMES_HOME"
echo "hermes CLI: $HERMES_BIN"
echo ""

rsync -a "${RSYNC_EXCLUDES[@]}" "$PACK_ROOT/" "$DEST/"
echo "installed: plugins/MyMemory"

resolve_runtime_python() {
  if [[ -n "${HERMES_PYTHON:-}" && -x "${HERMES_PYTHON}" ]]; then
    echo "${HERMES_PYTHON}"
    return 0
  fi
  local candidate
  for candidate in \
    "${HERMES_HOME}/hermes-agent/.venv/bin/python" \
    "${HERMES_HOME}/.venv/bin/python" \
    "/usr/local/lib/hermes-agent/venv/bin/python" \
    "/usr/local/lib/hermes-agent/.venv/bin/python"
  do
    if [[ -x "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  return 1
}

echo ""
echo "Channel 4 runtime (onnxruntime, tokenizers, numpy)…"
if RUNTIME_PY="$(resolve_runtime_python)"; then
  if ! "${RUNTIME_PY}" -c "import onnxruntime, tokenizers, numpy" >/dev/null 2>&1; then
    echo "Installing onnxruntime tokenizers numpy into ${RUNTIME_PY}"
    "${RUNTIME_PY}" -m pip install --quiet onnxruntime tokenizers numpy
  fi
  echo "ok: ${RUNTIME_PY} can import onnxruntime, tokenizers, numpy"
else
  echo "note: no Python found — install onnxruntime tokenizers numpy into the Hermes interpreter" >&2
fi
ONNX_DIR="${HOME}/.cache/mymemory/gte-multilingual-base-int8"
ONNX_HINT=""
if [[ -n "${MYMEMORY_GTE_ONNX:-}" ]]; then
  ONNX_HINT="${MYMEMORY_GTE_ONNX}"
fi
if [[ -n "$ONNX_HINT" && -f "$ONNX_HINT" ]]; then
  echo "ok: MYMEMORY_GTE_ONNX=${ONNX_HINT}"
elif [[ -f "${ONNX_DIR}/model_int8.onnx" || -f "${ONNX_DIR}/model.onnx" ]]; then
  echo "ok: GTE int8 weights under ${ONNX_DIR}"
else
  echo "note: Channel 4 ONNX weights missing (not a plugin bug). Export once on a Mac:"
  echo "  pip install 'sentence-transformers' optimum onnx onnxruntime"
  echo "  cd ${DEST} && python -c \"from recall.embed import export_gte_int8; export_gte_int8()\""
fi

if [[ -f "$PACK_ROOT/examples/agent-hooks/block-hermes-root-junk.sh" ]]; then
  cp -p "$PACK_ROOT/examples/agent-hooks/block-hermes-root-junk.sh" \
    "$HERMES_HOME/agent-hooks/block-hermes-root-junk.sh"
  chmod +x "$HERMES_HOME/agent-hooks/block-hermes-root-junk.sh"
  echo "installed: agent-hooks/block-hermes-root-junk.sh"
fi

echo ""
echo "Enabling MyMemory…"
if "${HERMES_BIN}" plugins enable MyMemory --no-allow-tool-override 2>/dev/null; then
  echo "enabled: MyMemory"
else
  echo "note: enable skipped or already enabled — add MyMemory to plugins.enabled in config.yaml"
fi

if [[ "$SKIP_TESTS" == true ]]; then
  echo ""
  echo "Skipping tests (--skip-tests)."
else
  echo ""
  echo "Running tests against installed copy…"
  set +e
  "$SCRIPT_DIR/run-tests.sh" "$HERMES_HOME"
  test_rc=$?
  set -e
  if [[ $test_rc -ne 0 ]]; then
    if [[ "$FORCE" == true ]]; then
      echo ""
      echo "warn: tests failed (exit $test_rc) — continuing because --force was set" >&2
    else
      echo ""
      echo "error: tests failed — install aborted. Re-run with --force to continue" >&2
      exit "$test_rc"
    fi
  fi
fi

echo ""
echo "Restarting Hermes gateway…"
"${HERMES_BIN}" gateway restart

echo ""
echo "Gateway status:"
status_out="$("${HERMES_BIN}" gateway status 2>&1)" || true
printf '%s\n' "$status_out"

echo ""
echo "Merge the sample plugin block from README.md into $HERMES_HOME/config.yaml"
echo "(do not overwrite the whole file). Put API keys in $HERMES_HOME/.env — never in git."
echo "Optional: wire hooks.pre_tool_call to agent-hooks/block-hermes-root-junk.sh"
echo "Then: ${HERMES_BIN} doctor"
