## MyMemory 1.1.26

Snapshot from AGENT commit at tag **v1.1.26**.

### Digest & monthly

- Phase-2 Pearson/MI gate history in digest state (outcomes, pair rho/MI, tool-error reasons).
- `/digest` chooses today's rewrite, today's tail, or a past day; prints token totals.
- Monthly synthesis: standing decisions vs one-sitting procedures; nest split aspects (pattern B).
- Installer/run-tests: drop dated W38 harness; ignore live-LLM and Mem_Eval-only tests.

### Recall expand (refresh)

- **Expand inlines direct `related:` neighbors** on the opened card so one `recall_memory` read can show obstacle/procedure bodies without a second hop; PPR lines for non-direct hops stay id-only.
- **`channel=id` expand** dedupes seeds so the opened card’s body is not expanded twice.

### Commits in this snapshot (since 1.1.25)

- Digest Phase-2 gate persistence and empty/tool-error gate reasons
- Monthly key-row synthesis and pattern-B nesting
- Slash handlers off gateway loop (from 1.1.23 refresh path)
- Install: exclude/remove `test_w38_sunday_evening_patch.py` and `debug_w38_worker1_events.py`
