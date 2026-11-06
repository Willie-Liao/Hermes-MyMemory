## MyMemory 1.1.34

Weekly briefs are the date events and the event cite map. Conflict, Hypothesis, and Possible overdue are no longer rendered, and the unmounted confirm queue cannot be reached.

### Weekly brief

- New briefs stop after the Mon–Sun date section and the event cite map.
- An old file that still has Conflict, Hypothesis, or Possible overdue still loads its day events. Those headings are skipped. Past weeks are not rewritten.
- Week-close `process_overdue_week_marks` is unchanged. Digest still refuses `type: hypothesis`.

### Removed reachability

- The unmounted overdue queue, weekly review save/recall routes, and the span-candidate bridge (`list_weekly_span_candidates`, `resolve_weekly_span`) are gone.
- WeekReview body-slot Save/Recall stays.

### Install

- `scripts/install.sh` copies `skills/memory-health-once` into `$HERMES_HOME/skills/` so a MyMemory install also installs that skill.
- Dated one-off harness names are no longer listed in the installer. Those files are not in the pack. Probe and debug scripts are still deleted by pattern.
- Install stops if `skills/memory-health-once/SKILL.md` is missing from the pack.
- The skill checks `memory.provider` and quotes it. A blank `""` means the provider is unset. It also checks that PluginManager has loaded MyMemory and registered `/digest`, `/weekly`, and `/monthly`.
