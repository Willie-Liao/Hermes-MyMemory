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
    "${HOME}/.hermes/hermes-agent" \
    "/usr/local/lib/hermes-agent"
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
# A git Hermes checkout finishes a source update when pytest imports hermes_cli.
# That rewrites .hermes/bin/hermes to a pytest temp interpreter and the suite exits 1.
export HERMES_DISABLE_LAZY_INSTALLS=1

LAUNCHER_DIR="${HERMES_HOME}/hermes-agent/.hermes/bin"
LAUNCHER_BAK="${LAUNCHER_DIR}.mymemory-pytest-bak"
restore_launchers() {
  if [[ -d "$LAUNCHER_BAK" ]]; then
    cp -a "${LAUNCHER_BAK}/." "$LAUNCHER_DIR/"
    rm -rf "$LAUNCHER_BAK"
  fi
}
if [[ -d "$LAUNCHER_DIR" ]]; then
  rm -rf "$LAUNCHER_BAK"
  mkdir -p "$LAUNCHER_BAK"
  cp -a "${LAUNCHER_DIR}/." "$LAUNCHER_BAK/"
  trap restore_launchers EXIT
fi

# /tmp is often a small tmpfs. Pytest basetemp then hits ENOSPC and the
# installer reports a failed plugin before the suite has actually run.
TEST_TMP="${MYMEMORY_TEST_TMPDIR:-${HOME}/.cache/mymemory-pytest}"
rm -rf "$TEST_TMP"
mkdir -p "$TEST_TMP"
export TMPDIR="$TEST_TMP"

cd "$PLUGIN"
echo "pytest python: $PY"
echo "agent root: $AGENT_ROOT"
# test_live_llm_sees_bands_in_both_hermes_slots calls the worker model and does not
# honor REAL_LLM_TEST=0. The other tests in that file stay in the install run.
# test_eval_prefetch_fallback_band_c_weekday_ladder reads the workstation Mem_Eval
# tree beside HERMES_HOME, which a cloud install does not have.
"$PY" -m pytest \
  --ignore=digest/test_digest_live_typed_prompt.py \
  --ignore=digest/test_digest_live_merge_slots.py \
  -k 'not test_live_llm_sees_bands_in_both_hermes_slots and not test_eval_prefetch_fallback_band_c_weekday_ladder' \
  -q --tb=line
