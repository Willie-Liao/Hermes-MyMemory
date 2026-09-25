# Sunday weekly generate + close

Sunday automation lives in the MyMemory **plugin clock**, not Hermes cron. The digest civil thread (`start_digest_clock_thread`) already wakes at 08/12/16/20/23:55 in `config.yaml` `timezone:` (typically `Asia/Shanghai`). `weekly_clock.maybe_run` rides that loop.

## What it does

1. **Sunday 16:00:** `generate_week` for the current ISO week, start Weekly UI + Cloudflare tunnel, send the `/weekly ui` phone link via the live Weixin `adapter.send` (same adapter cron used after job stdout; prefetch cannot push).
2. **Sunday 23:55:** after digest leftover extract, `close_week` for that ISO week.
3. **Monday+ catch-up:** generate then close only the **ISO week that just ended**. Never close the new Monday week.

State keys in `memories/staging/.weekly-state.json`: `last_sunday_generate_week`, `last_sunday_close_week`. Idempotent per week.

Chat id: `WEEKLY_BRIEF_WEIXIN_TO` or `plugins.entries.MyMemory.weekly_brief_weixin` in `config.yaml`. Gateway must be up with Weixin connected. Send failure is logged; generate is kept.

## Why cron was removed

A stale `jobs.json` `next_run_at` made Hermes treat Sunday close as missed and catch-up-run it. On Sunday that closed the **current** week. Plugin clock catch-up cannot target the new Monday week.

There is no `weekly-sunday-close` / `weekly-brief-runner-sunday` job and no `HERMES_WEEKLY_SUNDAY_CLOSE_FORCE`.

## Edits after auto-close

Use Weekly UI (`/weekly ui`) → Reopen the week, edit, then Close again. Chat no longer injects a “Weekly close note” A/B prompt.
