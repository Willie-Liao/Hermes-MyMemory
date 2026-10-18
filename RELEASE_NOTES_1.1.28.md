## MyMemory 1.1.28

### Recall — date fidelity guard

- New module-level constant `BAND_FIDELITY_RULE` in `recall/tools.py`.
  The rule is appended to every `render_bands()` block so the LLM only
  states a specific memory date when a matching
  `mem-YYYY-MM-DD-<type>-<4-byte-hash>` id is visible in the surrounding
  context.
- If only a month or relative term is known (e.g. "last August",
  "early September"), the LLM is now instructed to either answer with
  the approximate period or, when the question needs precision, ask in
  the user's language: "I remember this was around {period}, but the
  exact day needs verification — should I look it up?"
- The patch is **non-breaking**: `render_bands` still returns `""` when no
  `blk ::` marker is present, so empty staging trees produce no injection.

### Notes

- Patch is local-only: user permission to modify plugin code recorded in
  `mem-2026-10-17-decision-*`. `scripts/publish-github.sh` is **NOT**
  auto-run; publish is a manual checklist step.
- Rolling back to 1.1.27: restore `recall/tools.py` from
  `/tmp/hermes_ics/tools.py.bak.2026-10-17` and revert
  `plugin.yaml` to `version: 1.1.27`.
