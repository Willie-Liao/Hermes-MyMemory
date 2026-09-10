#!/usr/bin/env bash
# Copy this MyMemory provider into HERMES_HOME/plugins/MyMemory and enable it.
# Does not copy pack config.yaml, .env, memories, or any live Hermes home.
# After enable, pins digest/weekly/monthly workers (not chat model:) from TTY
# choice or the current chat provider — so install does not leave Xiaomi YAML
# as a required hand-merge.

set -euo pipefail

SKIP_TESTS=false
FORCE=false
SKIP_CONFIG=false
WORKER_FROM_CHAT=false
for arg in "$@"; do
  case "$arg" in
    --skip-tests) SKIP_TESTS=true ;;
    --force) FORCE=true ;;
    --skip-config) SKIP_CONFIG=true ;;
    --worker-from-chat) WORKER_FROM_CHAT=true ;;
    -h|--help)
      echo "Usage: install.sh [--skip-tests] [--force] [--skip-config] [--worker-from-chat]"
      echo "  --skip-tests         Copy plugin and restart without pytest"
      echo "  --force              Continue even if tests fail"
      echo "  --skip-config        Do not write worker lanes or .env"
      echo "  --worker-from-chat   Pin MyMemory workers to config.yaml model: (no TTY menu)"
      exit 0
      ;;
  esac
done

if [[ "${MYMEMORY_SKIP_CONFIG:-}" == "1" ]]; then
  SKIP_CONFIG=true
fi

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

# Pre-exported GTE int8 (no PyTorch on the gateway). GitHub Release, not git.
GTE_CACHE_DIR="${MYMEMORY_GTE_CACHE:-$HOME/.cache/mymemory/gte-multilingual-base-int8}"
GTE_BUNDLE_URL="${MYMEMORY_GTE_BUNDLE_URL:-https://github.com/Willie-Liao/Hermes-MyMemory/releases/download/gte-int8-v1/gte-multilingual-base-int8.tar.gz}"

gte_int8_ready() {
  local dir="$1"
  [[ -f "${dir}/model_int8.onnx" || -f "${dir}/model.onnx" ]] && [[ -f "${dir}/tokenizer.json" ]]
}

ensure_gte_int8() {
  # Download Channel 4 weights so double-click install does not need a Mac PyTorch export.
  if [[ "${MYMEMORY_SKIP_GTE:-}" == "1" ]]; then
    echo "note: skipping GTE int8 (MYMEMORY_SKIP_GTE=1)"
    return 0
  fi
  if [[ -n "${MYMEMORY_GTE_ONNX:-}" && -f "${MYMEMORY_GTE_ONNX}" ]]; then
    echo "ok: MYMEMORY_GTE_ONNX=${MYMEMORY_GTE_ONNX}"
    return 0
  fi
  mkdir -p "$GTE_CACHE_DIR"
  if gte_int8_ready "$GTE_CACHE_DIR"; then
    echo "ok: GTE int8 weights under ${GTE_CACHE_DIR}"
    return 0
  fi
  echo "Downloading Channel 4 GTE int8 (one-time, ~200 MB) from:"
  echo "  ${GTE_BUNDLE_URL}"
  local tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/mymemory-gte.XXXXXX.tar.gz")"
  if ! curl -fL --retry 5 --retry-delay 2 -o "$tmp" "$GTE_BUNDLE_URL"; then
    rm -f "$tmp"
    echo "error: GTE int8 download failed. Set MYMEMORY_GTE_BUNDLE_URL or export locally:" >&2
    echo "  python -c \"from recall.embed import export_gte_int8; export_gte_int8()\"" >&2
    return 1
  fi
  tar -xzf "$tmp" -C "$GTE_CACHE_DIR"
  rm -f "$tmp"
  if ! gte_int8_ready "$GTE_CACHE_DIR"; then
    echo "error: GTE bundle unpacked but model_int8.onnx or tokenizer.json is missing" >&2
    return 1
  fi
  echo "ok: GTE int8 installed under ${GTE_CACHE_DIR}"
}
echo ""
echo "Channel 4 runtime (onnxruntime, tokenizers, numpy)…"
if RUNTIME_PY="$(resolve_runtime_python)"; then
  if [[ "${MYMEMORY_SKIP_GTE:-}" != "1" ]]; then
    if ! "${RUNTIME_PY}" -c "import onnxruntime, tokenizers, numpy" >/dev/null 2>&1; then
      echo "Installing onnxruntime tokenizers numpy into ${RUNTIME_PY}"
      "${RUNTIME_PY}" -m pip install --quiet onnxruntime tokenizers numpy
    fi
    echo "ok: ${RUNTIME_PY} can import onnxruntime, tokenizers, numpy"
  else
    echo "note: skipping onnxruntime install (MYMEMORY_SKIP_GTE=1)"
  fi
else
  echo "note: no Python found — install onnxruntime tokenizers numpy into the Hermes interpreter" >&2
  RUNTIME_PY=""
fi
echo ""
echo "Channel 4 GTE int8 weights…"
ensure_gte_int8

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

# Pin workers in the live Hermes config. Chat model: stays untouched.
apply_mymemory_workers() {
  local py="${RUNTIME_PY:-}"
  if [[ -z "$py" ]]; then
    if command -v python3 >/dev/null 2>&1; then
      py="$(command -v python3)"
    else
      echo "note: no Python — skip worker config; see README.md for YAML shape" >&2
      return 0
    fi
  fi

  local detect
  detect="$("$py" - "$HERMES_HOME" <<'PY'
"""Read chat model: and whether the matching .env key exists (never print secrets)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

home = Path(sys.argv[1])
cfg_path = home / "config.yaml"
env_path = home / ".env"


def provider_env_names(provider: str) -> tuple[str, str]:
    plug = str(provider or "").strip().lower() or "xiaomi"
    if plug == "kimi-coding":
        return ("KIMI_API_KEY", "KIMI_BASE_URL")
    prefix = plug.replace("-", "_").upper()
    return f"{prefix}_API_KEY", f"{prefix}_BASE_URL"


def dotenv_has(path: Path, key: str) -> bool:
    if os.environ.get(key, "").strip():
        return True
    if not path.is_file():
        return False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        k, _, val = line.partition("=")
        if k.strip() == key and val.strip().strip("\"'"):
            return True
    return False


def load_mapping(path: Path) -> dict:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        from ruamel.yaml import YAML

        data = YAML(typ="safe").load(text)
    except Exception:
        import yaml

        data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


raw = load_mapping(cfg_path)
model = raw.get("model") if isinstance(raw.get("model"), dict) else {}
provider = str(model.get("provider") or "").strip()
chat_model = str(model.get("default") or model.get("model") or "").strip()
base_url = str(model.get("base_url") or "").strip()
key_env, _url_env = provider_env_names(provider)
print(
    json.dumps(
        {
            "provider": provider,
            "model": chat_model,
            "base_url": base_url,
            "key_env": key_env,
            "key_set": dotenv_has(env_path, key_env),
        }
    )
)
PY
)"

  local chat_provider chat_model chat_url key_env key_set
  chat_provider="$("$py" -c 'import json,sys; print(json.loads(sys.argv[1]).get("provider") or "")' "$detect")"
  chat_model="$("$py" -c 'import json,sys; print(json.loads(sys.argv[1]).get("model") or "")' "$detect")"
  chat_url="$("$py" -c 'import json,sys; print(json.loads(sys.argv[1]).get("base_url") or "")' "$detect")"
  key_env="$("$py" -c 'import json,sys; print(json.loads(sys.argv[1]).get("key_env") or "")' "$detect")"
  key_set="$("$py" -c 'import json,sys; print("yes" if json.loads(sys.argv[1]).get("key_set") else "no")' "$detect")"

  echo ""
  echo "MyMemory workers (digest / weekly / monthly) — Hermes chat model: is not changed."
  if [[ -z "$chat_provider" || -z "$chat_model" ]]; then
    echo "note: no model.provider / model.default in $HERMES_HOME/config.yaml — skip worker pin."
    echo "See README.md for the plugins.entries.MyMemory YAML shape."
    return 0
  fi
  echo "Current chat: provider=${chat_provider}  model=${chat_model}"
  echo "  base_url=${chat_url:-"(none)"}"
  echo "  ${key_env}: ${key_set}"

  local mode="chat"
  local w_provider="$chat_provider"
  local w_model="$chat_model"
  local w_url=""
  local w_key=""

  if [[ "$WORKER_FROM_CHAT" == true ]]; then
    mode="chat"
  elif [[ -n "${MYMEMORY_WORKER_PROVIDER:-}" && -n "${MYMEMORY_WORKER_MODEL:-}" ]]; then
    mode="custom"
    w_provider="${MYMEMORY_WORKER_PROVIDER}"
    w_model="${MYMEMORY_WORKER_MODEL}"
    w_url="${MYMEMORY_WORKER_BASE_URL:-}"
    w_key="${MYMEMORY_WORKER_API_KEY:-}"
    echo "Using MYMEMORY_WORKER_* env for a non-TTY custom pin."
  elif [[ -t 0 ]]; then
    echo ""
    echo "  1) Use current chat model for workers"
    echo "  2) Custom provider, model, API key, base URL"
    local choice=""
    read -r -p "Choice [1]: " choice || true
    choice="${choice:-1}"
    if [[ "$choice" == "2" ]]; then
      mode="custom"
      read -r -p "Provider id (e.g. minimax-cn): " w_provider
      read -r -p "Model name: " w_model
      read -r -p "Base URL (empty = inherit if same provider as chat): " w_url
      read -r -s -p "API key (empty = leave .env unchanged): " w_key
      echo ""
    fi
  else
    echo "Non-TTY: pinning workers to the current chat model (--worker-from-chat)."
  fi

  if [[ "$mode" == "custom" ]]; then
    w_provider="${w_provider// /}"
    w_model="$(echo "$w_model" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    w_url="$(echo "$w_url" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    if [[ -z "$w_provider" || -z "$w_model" ]]; then
      echo "error: custom provider and model are required" >&2
      return 1
    fi
    local chat_lc w_lc
    chat_lc="$(echo "$chat_provider" | tr '[:upper:]' '[:lower:]')"
    w_lc="$(echo "$w_provider" | tr '[:upper:]' '[:lower:]')"
    if [[ "$w_lc" != "$chat_lc" && -z "$w_url" ]]; then
      echo "error: base URL is required when the worker provider differs from chat" >&2
      return 1
    fi
  fi

  if [[ "$mode" == "chat" && "$key_set" != "yes" ]]; then
    echo "warn: ${key_env} is missing — workers are pinned but ingest will fail closed until the key is in $HERMES_HOME/.env" >&2
  fi

  if [[ "$mode" == "custom" && -n "$w_key" ]]; then
    "$py" - "$HERMES_HOME" "$w_provider" "$w_key" <<'PY'
"""Upsert PROVIDER_API_KEY in .env without printing the value."""
from __future__ import annotations

import sys
from pathlib import Path

home = Path(sys.argv[1])
provider = sys.argv[2]
secret = sys.argv[3]
plug = str(provider or "").strip().lower() or "xiaomi"
if plug == "kimi-coding":
    key_env = "KIMI_API_KEY"
else:
    key_env = f"{plug.replace('-', '_').upper()}_API_KEY"
path = home / ".env"
lines: list[str] = []
if path.is_file():
    lines = path.read_text(encoding="utf-8").splitlines()
found = False
out: list[str] = []
prefix = f"{key_env}="
export_prefix = f"export {key_env}="
for line in lines:
    stripped = line.strip()
    if stripped.startswith(prefix) or stripped.startswith(export_prefix):
        out.append(f"{key_env}={secret}")
        found = True
    else:
        out.append(line)
if not found:
    if out and out[-1] != "":
        out.append("")
    out.append(f"{key_env}={secret}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
path.chmod(0o600)
print(f"ok: wrote {key_env} in {path} (value hidden)")
PY
  fi

  "$py" - "$HERMES_HOME" "$w_provider" "$w_model" "$w_url" "$chat_provider" <<'PY'
"""Merge MyMemory digest/weekly/monthly worker lanes; leave chat model: and extra keys."""
from __future__ import annotations

import sys
from pathlib import Path

home = Path(sys.argv[1])
provider = sys.argv[2].strip()
model = sys.argv[3].strip()
base_url = sys.argv[4].strip()
chat_provider = sys.argv[5].strip().lower()
cfg_path = home / "config.yaml"
if not cfg_path.is_file():
    print(f"note: no {cfg_path} — skip YAML merge", file=sys.stderr)
    sys.exit(0)

text = cfg_path.read_text(encoding="utf-8")
engine = "pyyaml"
ruamel_yaml = None
data = None
try:
    from ruamel.yaml import YAML

    ruamel_yaml = YAML()
    ruamel_yaml.preserve_quotes = True
    data = ruamel_yaml.load(text)
    engine = "ruamel"
except Exception:
    import yaml

    data = yaml.safe_load(text)

if not isinstance(data, dict):
    print("error: config.yaml is not a mapping", file=sys.stderr)
    sys.exit(1)

bak = cfg_path.with_name("config.yaml.bak-mymemory-install")
bak.write_text(text, encoding="utf-8")

same_as_chat = provider.strip().lower() == chat_provider
lane_url = "" if same_as_chat else base_url


def as_dict(node):
    return node if isinstance(node, dict) else {}


def set_lane(bag: dict, *, lanes: list[str]) -> None:
    bag["provider"] = provider
    bag["model"] = model
    if lane_url:
        bag["base_url"] = lane_url
    wl = as_dict(bag.get("worker_llm"))
    bag["worker_llm"] = wl
    for name in lanes:
        row = as_dict(wl.get(name))
        row["provider"] = provider
        row["model"] = model
        if lane_url:
            row["base_url"] = lane_url
        wl[name] = row


data["memory"] = as_dict(data.get("memory"))
data["memory"]["provider"] = "MyMemory"

plugins = as_dict(data.get("plugins"))
data["plugins"] = plugins
enabled = plugins.get("enabled")
if not isinstance(enabled, list):
    enabled = []
    plugins["enabled"] = enabled
if "MyMemory" not in [str(x) for x in enabled]:
    enabled.append("MyMemory")

entries = as_dict(plugins.get("entries"))
plugins["entries"] = entries
mm = as_dict(entries.get("MyMemory"))
entries["MyMemory"] = mm

digest = as_dict(mm.get("digest"))
mm["digest"] = digest
set_lane(digest, lanes=["phase1", "phase2", "wrapup"])

weekly = as_dict(mm.get("weekly"))
mm["weekly"] = weekly
set_lane(weekly, lanes=["weekly"])

monthly = as_dict(mm.get("monthly"))
mm["monthly"] = monthly
set_lane(monthly, lanes=["monthly"])

retention = as_dict(mm.get("retention"))
mm["retention"] = retention
if "allow_tool_override" not in retention:
    retention["allow_tool_override"] = False

if engine == "ruamel" and ruamel_yaml is not None:
    with cfg_path.open("w", encoding="utf-8") as fh:
        ruamel_yaml.dump(data, fh)
    print(f"ok: worker lanes → {provider} / {model} (round-trip YAML). Backup: {bak}")
else:
    import yaml

    dumped = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    cfg_path.write_text(dumped, encoding="utf-8")
    print(
        f"ok: worker lanes → {provider} / {model} (PyYAML dump may reformat). Backup: {bak}"
    )
PY
}

if [[ "$SKIP_CONFIG" == true ]]; then
  echo ""
  echo "Skipping worker config (--skip-config)."
else
  apply_mymemory_workers
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
echo "Installer applied MyMemory worker lanes (backup: $HERMES_HOME/config.yaml.bak-mymemory-install when YAML was written)."
echo "Chat model: was not changed. Secrets stay in $HERMES_HOME/.env — never in git."
echo "Optional: wire hooks.pre_tool_call to agent-hooks/block-hermes-root-junk.sh"
echo "Then: ${HERMES_BIN} doctor"
