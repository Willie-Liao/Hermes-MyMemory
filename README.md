# MyMemory

Hermes memory **provider**: digest (Phase-1 extract), consolidate (Phase-2), weekly, monthly, retention, and recall. MIT licensed.

This repository is **only the plugin**. It does not contain a live Hermes home, conversation DB, hot memory, staging files, `config.yaml`, or `.env`.

## One-click install (macOS)

1. Install [Hermes](https://github.com/NousResearch/hermes-agent) and run `hermes setup` once.
2. Clone this repo, then double-click **`Install MyMemory.command`**.

```bash
git clone https://github.com/Willie-Liao/Hermes-MyMemory.git
cd Hermes-MyMemory
bash scripts/install.sh
```

Override the install target with `HERMES_HOME=/path/to/.hermes`. Use `--skip-tests` or `--force` if needed. After install, merge the sample `plugins.entries.MyMemory` block below into `$HERMES_HOME/config.yaml` (do not overwrite the whole file) and put worker keys in `$HERMES_HOME/.env`.

This repository is the **plugin root**. Copy or clone it to `$HERMES_HOME/plugins/MyMemory`.

## Install

1. Install [Hermes](https://github.com/NousResearch/hermes-agent) and know your home (`~/.hermes` or `$HERMES_HOME`).
2. Place this tree at `$HERMES_HOME/plugins/MyMemory`.
3. Merge the config below into `$HERMES_HOME/config.yaml` (do not overwrite the whole file).
4. Put worker API keys in `$HERMES_HOME/.env` (never commit them).
5. Create empty `memories/MEMORY.md`, `memories/USER.md`, and `memories/staging/daily/`, `memories/staging/weekly/` if missing.
6. Restart the gateway. Verify with `hermes doctor`, then `/digest status` and `/weekly help` in chat, or `hermes MyMemory digest help`.

### Required config

```yaml
memory:
  provider: MyMemory

timezone: Asia/Shanghai   # civil digest clock; set yours

plugins:
  enabled:
    - MyMemory
  entries:
    MyMemory:
      digest:
        provider: xiaomi          # or your worker provider
        model: mimo-v2.5
        worker_llm:
          phase1: { provider: xiaomi, model: mimo-v2.5 }
          phase2: { provider: xiaomi, model: mimo-v2.5 }
          wrapup: { provider: xiaomi, model: mimo-v2.5 }
      weekly:
        provider: xiaomi
        model: mimo-v2.5
        worker_llm:
          weekly: { provider: xiaomi, model: mimo-v2.5 }
      monthly:
        provider: xiaomi
        model: mimo-v2.5
        worker_llm:
          monthly: { provider: xiaomi, model: mimo-v2.5 }
      retention:
        allow_tool_override: false
```

Do **not** copy `weekly_brief_weixin` chat IDs, `auth.json`, `.env`, `state.db`, live `memories/`, snapshots, or `shell-hooks-allowlist.json` from someone else's machine.

Slash commands `/digest` and `/weekly` register automatically when the plugin loads (`kind: standalone` + `plugins.enabled`). CLI:

```text
hermes MyMemory digest …
hermes MyMemory weekly …
```

Sunday weekly generate/close and digest leftover/merge run on an **in-process civil clock**. No Hermes cron job is required.

### Optional weekly UI

From `weekly/ui/`: `npm install` then `/weekly ui` (or `npm run dev`). Node is optional if you only use chat slash commands.

### Optional write-gate (recommended hardening)

Copy `examples/agent-hooks/block-hermes-root-junk.sh` to `$HERMES_HOME/agent-hooks/`, `chmod +x`, and add:

```yaml
hooks:
  pre_tool_call:
    - command: ~/.hermes/agent-hooks/block-hermes-root-junk.sh
      matcher: write_file|patch|terminal|shell
      timeout: 5
```

Approve the hook on first use. Do not copy another machine's allowlist.

## First-run history

Existing chats live in `$HERMES_HOME/state.db`. Estimate **before** any LLM call:

```text
hermes MyMemory digest history
/digest history
```

Four rolling presets: `1d` (24 hours), `7d`, `30d`, `all`. Each line reports message/session/batch/day counts plus **digest (Phase-1)** and **consolidate (Phase-2)** token and time **bands** (low / typical / high). Time confidence is low: benches span minutes to tens of minutes depending on retries.

```text
hermes MyMemory digest history 7d          # print one estimate, do not run
hermes MyMemory digest history 7d --yes    # confirmed CLI run (synchronous)
/digest history 7d --yes                   # confirmed chat run (background)
/digest history status
/digest history resume --yes
/digest history stop --yes
```

Estimate-only forms never write staging or start workers. Confirmed runs skip messages already past each session bookmark, write cards to the message's civil-day file, then consolidate that day once. Back up `memories/staging/` first: Phase-2 may rewrite affected daily files.

**Phase-2 first-attempt prompt (Pearson/MI gated).** After the local pair filter, the LLM sees one `## Filtered candidate board` JSON object: survivor cards grouped by type, `pairs` as local indexes into that type's `cards` array. Types with no surviving pair are omitted; unpaired cards stay on disk and are not sent. Fail-open (embed error), retry/patch, and wrap-up still use the four typed JSON arrays (`### Existing events` …), including empty `[]`.

## Same-day supersede vs past-day contradiction

**Current (unchanged) same-day supersede.** On today's daily file, Phase-1 persist then `supersede` overwrites the target body, stamps `superseded_at`, and purges the helper. Clock/UI leftover Phase-2 still applies same-file supersedes the same way. Do not use this path to rewrite history.

**After this change: retrieval-grounded past-day update.** When `recall_memory` / `expand_memory` returns cards, MyMemory stores at most **8 ordered mem-ids** on that session's `.digest-state.json` entry for **30 minutes**. After the chat turn, a background Phase-2 check (non-blocking) may patch an **older past-day** card without changing its body or id:

```yaml
valid_to: YYYY-MM-DD
status: rejected
rejected_reason: rejected by <later-mem-id>   # automatic, later card's date
# or
rejected_reason: rejected by user's correction  # explicit user wording
```

Automatic rejection requires same type, same entity, an older target, a later card, and both ids in that fresh retrieval set. A vague “that memory is dated/wrong” updates only when **exactly one** eligible older target exists; several older hits are a no-op. Cross-day merge/drop/supersede and body/source/id edits stay invalid.

Default FTS/entity/embed recall and monthly synthesis **omit** `status: rejected`. Exact mem-id recall still shows `status`, `valid_to`, and `rejected_reason` for audit. There is **no history backfill** — only cards that appear in a fresh recall set (or an explicit user correction of that set) can be closed. A failed patch leaves the old card unchanged and keeps the retrieval set for retry.

## Troubleshooting

## Generated paths (runtime, not shipped)

- `memories/staging/.digest-state.json`
- `memories/staging/daily/YYYY-MM-DD.md`
- `logs/memory-digest.log`
- `metrics/llm-usage.jsonl`

## Troubleshooting

| Symptom | Check |
|---------|--------|
| Unknown `/digest` | Plugin on `plugins.enabled`, `kind: standalone`, restart gateway |
| Provider not used | `memory.provider: MyMemory` |
| Empty estimates | `state.db` exists and has `messages` with `active=1` |
| Worker errors | Provider keys in `.env` matching `plugins.entries.MyMemory` |
| Clock dates wrong | `timezone` in `config.yaml` |

## Privacy and contributions

Tests and prompt examples use synthetic people. Do not add real names, chat IDs, or absolute home paths. Keep secrets out of git. Contributions: tests for the behavior you change, why-docstrings on new public functions.
