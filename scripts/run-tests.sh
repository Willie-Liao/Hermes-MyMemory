#!/usr/bin/env bash
# Run MyMemory unit tests on the installed plugin copy (no live LLM, no home data).

set -euo pipefail

HERMES_HOME="${1:-${HERMES_HOME:-$HOME/.hermes}}"
PLUGIN="$HERMES_HOME/plugins/MyMemory"

if [[ ! -d "$PLUGIN" ]]; then
  echo "error: MyMemory not installed at $PLUGIN" >&2
  exit 1
fi

export HERMES_DIGEST_LIVE_LLM=0
export REAL_LLM_TEST=0
export PLAN_LOOP_LIVE_LLM=0

cd "$PLUGIN"
python3 -m pytest \
  --ignore=digest/test_digest_live_typed_prompt.py \
  --ignore=digest/test_digest_live_merge_slots.py \
  -q --tb=line
