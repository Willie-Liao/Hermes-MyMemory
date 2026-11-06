## MyMemory 1.1.27

Snapshot from AGENT commit at tag **v1.1.27**.

### Monthly synthesis

- Reduce-field retries for empty `user_image` judgments and cognition gaps (`cognition_empty_reason`, paired supersedes ids).
- Stricter key-row evidence: decision vs procedure ids, drop foreign ids from mixed note lists, clearer gap messages for the next LLM try.
- `worker_llm`: fix nested JSON slice parsing for tool args; capture `reasoning_text` beside one-shot tool calls.

### Hermes home (optional)

- Shorter optional `block-hermes-root-junk.sh` write-gate; example hook synced with live copy.

### Installer / tests

- `run-tests.sh` picks a Python that has both **pytest** and **ruamel.yaml** (Hermes YAML round-trip imports).
- Ignore live monthly reduce eval and Mem_Eval-only tests in the default install suite.

### Commits in this snapshot (since 1.1.26)

- Monthly reduce-field synthesis and worker JSON/reasoning fixes
- Optional Hermes home write-gate simplification
- Retire superseded weekly-ui superpowers design docs from AGENT `docs/`
