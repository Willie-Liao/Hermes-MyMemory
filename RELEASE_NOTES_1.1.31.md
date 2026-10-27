## MyMemory 1.1.31

### Monthly reduce and clock

- A failed reduce keeps sections that already passed, writes `pending_sections` on the month file, and stores a `.reduce/{YYYY-MM}.json` resume. Later clock runs and `gaps_only` retries patch only the stored gaps instead of rerunning map.
- `/monthly update` and `show` name pending sections so a partial month does not look like a full discard.
- Monthly clock reads `write_at`, `checkpoint_days`, and `retry_days` from config (default 00:05 on the 10th and 20th with three retry days). Close and checkpoint slots retry through the window and record `last_error` when a window expires incomplete.

### Worker LLM

- Digest and monthly worker calls send `thinking: disabled` on OpenAI-compatible routes and Anthropic phase-1 kwargs, with one retry when the provider rejects the thinking field.

### Config restore and slash updates

- When a config file exists but the digest, weekly, or monthly worker lanes are missing, MyMemory copies them back from `~/.hermes/.mymemory-worker-pin.yaml` on the next load. A missing config.yaml is not created. With no pin yet, the lanes default to `minimax-cn` / `MiniMax-M2.5`.
- `/digest --all`, `/digest --batch`, `/digest YYYY-MM-DD`, `/weekly update`, and `/monthly update` return immediately and run the model call on a background thread, so the next chat message is not queued behind the update.
