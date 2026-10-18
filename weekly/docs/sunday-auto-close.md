# Sunday weekly generate + Monday evening patch + close

Sunday automation lives in the MyMemory **plugin clock**, not Hermes cron. The digest civil thread (`start_digest_clock_thread`) already wakes at 00:00/08/12/16/20/23:55 in `config.yaml` `timezone:` (typically `Asia/Shanghai`). `weekly_clock.maybe_run` rides that loop.

## What it does

1. **Sunday 16:00:** `generate_week` for the current ISO week, start Weekly UI + Cloudflare tunnel, send the `/weekly ui` phone link via the live Weixin `adapter.send` (same adapter cron used after job stdout; prefetch cannot push).
2. **Sunday 23:55:** digest leftover extract + day wrap-up only. Does **not** close the week.
3. **Monday 00:00:** if Sunday 16:00 already ran, incrementally patch Sunday 16:00–00:00 daily cards (selected by `generated_at`, leftover `valid_from` unchanged) into the existing YAML Chronicle as Sunday summary bodies, then `close_week`. If Sunday 16:00 never ran, full-generate the previous ISO week then close. **Generate / Re-scan Chronicle weekday is the daily digest filename** (`2026-09-20.md` → Sunday), not leftover card `valid_from`.
4. **Later Monday+ catch-up:** same previous-week target. Never close the new Monday week.

State keys in `memories/staging/.weekly-state.json`: `last_sunday_generate_week`, `last_sunday_evening_patch_week`, `last_sunday_close_week`. Idempotent per week.

Chat id: `WEEKLY_BRIEF_WEIXIN_TO` or `plugins.entries.MyMemory.weekly_brief_weixin` in `config.yaml`. Gateway must be up with Weixin connected. Send failure is logged; generate is kept.

## Why cron was removed

A stale `jobs.json` `next_run_at` made Hermes treat Sunday close as missed and catch-up-run it. On Sunday that closed the **current** week. Plugin clock catch-up cannot target the new Monday week.

There is no `weekly-sunday-close` / `weekly-brief-runner-sunday` job and no `HERMES_WEEKLY_SUNDAY_CLOSE_FORCE`.

## Edits after auto-close

Use Weekly UI (`/weekly ui`) → Reopen the week, edit, then Close again. Chat no longer injects a “Weekly close note” A/B prompt.
