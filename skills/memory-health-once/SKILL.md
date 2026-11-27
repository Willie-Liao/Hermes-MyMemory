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

Check the memory pipeline from the log of a real session. Quote a line that is already there. Run an aspect only when that real log has no line for it, and run it once.

## When to use

Load this skill for any MyMemory check. That includes the whole pipeline or one part, such as prefetch, recall, expand, the config memory provider, the plugin manager, the batch, the clock, phase 2, wrap daily, or wrap weekly.

- After a memory change, before claiming that part works.

Do not create a virtual Hermes home, a fixture database, or a fake session. Do not look for `health-fixture`, `this is test`, or any other stand-in transcript. Do not copy `state.db`. Do not start a second run of an aspect that already has a real log line.

## Log first

Read `~/.hermes/logs/memory-digest.log` and `~/.hermes/logs/memory-weekly.log`. Search the phrases below in lines from a real session. A hit means that aspect already ran: quote the line and skip it. `Failed to export span batch code: 404` is a trace export, not a failed check.

| Aspect | A real session shows it ran |
|---|---|
| Prefetch, recall, expand | a digest or agent line for prefetch, recall, and expand on this live session |
| Config memory provider | `memory.provider` in `~/.hermes/config.yaml`. Quote the value. `""` means the provider is unset |
| Manager | PluginManager has loaded MyMemory and registered `/digest`, `/weekly`, and `/monthly` |
| Daily, weekly, monthly shape | the `digest-phase1` lane from `/digest` on the past day, and the `weekly` and `monthly` lanes from a real slash, using the provider and model already in config |
| Batch | one extract of a real session in the digest log |
| Clock, phase 2 | the live clock's noon merge, with the card count above the 25-card gate |
| Wrap daily | `## Day wrap-up` in the real daily file for the last day |
| Wrap weekly | the real week file for the last week |

## Run once if the line is missing

Stay on the live home and the live session. Use the provider and model already set in config for that lane. Do not swap in another model.

- Config: if `memory.provider` is `""`, set it to `MyMemory` and restart the gateway. Quote the value after the restart. Leave every other config key as it is.
- Manager: check once that PluginManager lists MyMemory and the three slash commands. Quote that result. Recall still follows `memory.provider`.
- Shape: run the real slash once, in the background, and read the log it writes. Daily is `/digest` for the past day (`/digest YYYY-MM-DD`). Weekly is `/weekly update`. Monthly is `/monthly update`.
- Batch or clock: create one cron that runs the real digest batch or the digest clock on the live session. Let that cron run once, then quote the new log line.
- Wrap daily: open the real file for the last day, `memories/staging/daily/YYYY-MM-DD.md`, and quote `## Day wrap-up` when it is there. Do not write a test daily file.
- Wrap weekly: open the real file for the last week, `memories/staging/weekly/YYYY-Www.md`, and quote what it contains. Do not write a test week file.

After the single run, read the log again and quote the new line. For a wrap, quote the real file. Do not retry a failure in the same turn.
