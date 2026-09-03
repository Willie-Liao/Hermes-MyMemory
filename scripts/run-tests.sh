#!/usr/bin/env bash
# Run MyMemory unit tests on the installed plugin copy (no live LLM, no home data).
# System python3 has pytest; Hermes venv has `agent` but usually not pytest.
# Put hermes-agent on PYTHONPATH so collection can `from agent.memory_provider import …`.

set -euo pipefail

HERMES_HOME="${1:-${HERMES_HOME:-$HOME/.hermes}}"
PLUGIN="$HERMES_HOME/plugins/MyMemory"

if [[ ! -d "$PLUGIN" ]]; then
  echo "error: MyMemory not installed at $PLUGIN" >&2
  exit 1
fi

resolve_agent_root() {
  local candidate
  for candidate in \
    "${HERMES_HOME}/hermes-agent" \
    "${HOME}/.hermes/hermes-agent"
  do
    if [[ -f "${candidate}/agent/memory_provider.py" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  echo "error: hermes-agent source not found (need agent/memory_provider.py)" >&2
  echo "hint: install Hermes, or set HERMES_AGENT_ROOT" >&2
  return 1
}

resolve_pytest_python() {
  if [[ -n "${HERMES_PYTHON:-}" && -x "${HERMES_PYTHON}" ]]; then
    if "${HERMES_PYTHON}" -c "import pytest" >/dev/null 2>&1; then
      echo "${HERMES_PYTHON}"
      return 0
    fi
  fi
  if command -v python3 >/dev/null 2>&1 && python3 -c "import pytest" >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  echo "error: python3 with pytest not found" >&2
  return 1
}

if [[ -n "${HERMES_AGENT_ROOT:-}" && -f "${HERMES_AGENT_ROOT}/agent/memory_provider.py" ]]; then
  AGENT_ROOT="${HERMES_AGENT_ROOT}"
else
  AGENT_ROOT="$(resolve_agent_root)" || exit 1
fi
PY="$(resolve_pytest_python)" || exit 1

export PYTHONPATH="${AGENT_ROOT}:${PLUGIN}/..:${PYTHONPATH:-}"
export HERMES_DIGEST_LIVE_LLM=0
export REAL_LLM_TEST=0
export PLAN_LOOP_LIVE_LLM=0

cd "$PLUGIN"
echo "pytest python: $PY"
echo "agent root: $AGENT_ROOT"
"$PY" -m pytest \
  --ignore=digest/test_digest_live_typed_prompt.py \
  --ignore=digest/test_digest_live_merge_slots.py \
  -q --tb=line
