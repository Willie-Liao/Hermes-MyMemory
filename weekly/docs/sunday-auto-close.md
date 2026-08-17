# Sunday weekly update + close

Hermes no-agent cron job **`weekly-sunday-close`** runs at **`58 23 * * 0`** (Sunday 23:58, `config.yaml` timezone, typically `Asia/Shanghai`) so it stays three minutes after digest leftover extract at 23:55.

## What it does

1. **Sunday only:** `generate_week` for the current ISO week (`reason=update`) via `bridge_cli.py`
2. **Sunday only:** `close_week` for the current ISO week
3. Silent `process_overdue_week_marks(kick_generate=False)` for older open weeks (no chat A/B ask) — also runs on non-Sunday catch-up
4. Prints a notify line on stdout (job `deliver` is **`local`** so Weixin delivery failures cannot leave the run looking unfinished)

Script: `~/.hermes/scripts/weekly-sunday-close.sh` (repo: `hermes-home/scripts/weekly-sunday-close.sh`).

### Why the Sunday gate exists

If the gateway restarts after Sunday 23:58, Hermes may re-fire this job as a “missed” catch-up on **Monday**. Without a gate, blank `close_week` targets the **new** ISO week and can empty-close it. Non-Sunday runs skip current-week generate/close and only run overdue catch-up.

Manual smoke on a weekday:

```bash
HERMES_WEEKLY_SUNDAY_CLOSE_FORCE=1 hermes cron run weekly-sunday-close
```

## Operator commands

Job name: `weekly-sunday-close` (created via `hermes cron create`; id on this host may differ).

```bash
hermes cron list
hermes cron run weekly-sunday-close   # manual smoke
hermes cron pause weekly-sunday-close
hermes cron resume weekly-sunday-close
```

## Edits after auto-close

Use Weekly UI (`/weekly ui`) → Reopen the week, edit, then Close again. Chat no longer injects a “Weekly close note” A/B prompt.

## Job bookkeeping

If `next_run_at` stays stuck in the past (e.g. after a delivery/interrupt race), Hermes may keep replaying this job as “missed.” Prefer `deliver: local`. After a bad stick, bump `next_run_at` to the next Sunday 23:55 so daytime catch-up does not close the current week early on Sunday either.
