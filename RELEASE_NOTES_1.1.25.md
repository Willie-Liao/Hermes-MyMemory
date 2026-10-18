## MyMemory 1.1.25

Snapshot from AGENT `main` at release tag **v1.1.25**.

### Release mark

**Fix install bugs and update test files.**

### Highlights

- **install.sh:** ship `scripts/` in the plugin tree; strip AppleDouble `._*` sidecars; resolve Hermes bundled Python (`~/.hermes/tools/python-*`, venv paths); install deps via `pip` or `uv pip`; install pytest/tiktoken when tests run; Channel 4 + YAML merge fixes.
- **run-tests.sh:** align with installer Python resolution and package set.
- **Tests:** refresh provider/install/staging tests; drop retired weekly slash worker harness helpers from `test_weekly_slash.py`.
