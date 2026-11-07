## MyMemory 1.1.34

Weekly briefs are the date events and the event cite map. Conflict, Hypothesis, and Possible overdue are no longer rendered, and the unmounted confirm queue cannot be reached.

### Weekly brief

- New briefs stop after the Mon–Sun date section and the event cite map.
- An old file that still has Conflict, Hypothesis, or Possible overdue still loads its day events. Those headings are skipped. Past weeks are not rewritten.
- Week-close `process_overdue_week_marks` is unchanged. Digest still refuses `type: hypothesis`.

### Removed reachability

- The unmounted overdue queue, weekly review save/recall routes, and the span-candidate bridge (`list_weekly_span_candidates`, `resolve_weekly_span`) are gone.
- WeekReview body-slot Save/Recall stays.

### Skill

- `memory-health-once` checks real session logs and real files. Daily shape runs `/digest` for the past day. Wrap daily reads the last day, and wrap weekly reads the last week.
- `scripts/install.sh` stores that skill and writes it into `$HERMES_HOME/skills/` even when the pack has no skill file.
- A blank `memory.provider` is set to `MyMemory`, then the gateway restarts.
