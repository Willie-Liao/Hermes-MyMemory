---
name: memory-health-once
description: Use for any MyMemory check, like config or one part.
version: 0.1.0
author: Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [memory, digest, phase2, weekly, logs]
---

# Memory health once

Check the memory pipeline from the log. Report a line that is already there. Run an aspect only when the log has no line for it, and run it once.

## When to use

Load this skill for any MyMemory check. That includes the whole pipeline or one part, such as prefetch, recall, expand, the config memory provider, the plugin manager, the 12-message batch, phase 2, wrap daily, or wrap weekly.

- After a memory change, before claiming that part works.

Do not use this to edit memory, copy `state.db`, or start a second run of an aspect that already has a log line.

## Log first

Read `~/.hermes/logs/memory-digest.log` and `~/.hermes/logs/memory-weekly.log`. Search the phrases below. A hit means that aspect already ran: quote the line and skip it. `Failed to export span batch code: 404` is a trace export, not a failed check.

| Aspect | Log shows it ran |
|---|---|
| Prefetch, recall, expand | a digest or agent line for the live prefetch, recall, and expand on this session |
| Config memory provider | `memory.provider` in `~/.hermes/config.yaml`. Quote the value. `""` means the provider is unset |
| Manager | PluginManager has loaded MyMemory and registered `/digest`, `/weekly`, and `/monthly` |
| Daily, weekly, monthly shape | the `digest-phase1`, `weekly`, and `monthly` lanes using the provider and model already in config |
| 12-message batch | one extract of 12 user and 12 assistant turns, not the live `state.db` |
| Phase 2 | noon merge, card count above the 25-card gate |
| Wrap daily | `## Day wrap-up` written for the same daily file phase 2 counted |
| Wrap weekly | a week file from `submit_weekly_thread`, then the Sunday weekly clock |

## Run once if the line is missing

Use the provider and model already set in config for that lane. Do not swap in another model. Do not read or copy the live `state.db`.

- Config: read `memory.provider` once and quote it. A blank `""` is the result. Do not write `config.yaml`.
- Manager: if the log has no manager line, check once that PluginManager lists MyMemory and the three slash commands. Quote that result. Recall still follows `memory.provider`, so a loaded plugin with `memory.provider: ""` is still unset.
- Shape: one worker call per missing lane.
- Batch: one extract of the fake turns `this is test` / `this is a response of test` (12 user, 12 assistant). Leave the tool row in the transcript. It does not count toward 12.
- Phase 2 and wrap daily share one daily file. Fill `submit_digest_blocks` with 33 flat cards, render them with the digest-1 helpers, then let the noon clock and the 23:59 wrap read that file. Do not extract a second batch for the wrap.
- Wrap weekly: fill `submit_weekly_thread` for the same week, write that week file, then let the Sunday weekly clock run once.

After the single run, read the log again and quote the new line. Do not retry a failure in the same turn.
