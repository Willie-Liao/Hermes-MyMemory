## MyMemory 1.1.35

Worker LLM calls share one parse, validate, and teach path.

- The worker-LLM flag is per context, so a background digest cannot silence recall on the live chat.
- Model JSON accepts JSON5 slips. Plugin-written files stay on strict JSON. `json5` installs from requirements.txt when it is missing.
- Oneshot tool arguments are checked against the sent schema and retried once with the validator message.
- Recall policy, chronicle, and hot health use a forced tool call.
- Provider wire format and fallback live in one capability table, and a quota failover expires after one hour. OpenAI multi-tool phase-1 still uses the nested agent so Hermes can run the tools. Anthropic phase-1 still uses its messages loop, and both read that table.
- Oneshot clients are cached, with a per-purpose timeout, one longer retry when the model stops on length, and a caller-supplied system prompt.
- Usage records share one helper, include a price-table cost, Anthropic cache tokens, and which parse path won.
- Recorded reply fixtures cover a clean tool call and text-wrapped JSON.

Digest Phase-2 uses the same GTE int8 session as recall. No version bump.

- The Pearson/MI gate calls `recall.embed._encode_texts` instead of `all-MiniLM-L6-v2`. Chinese and English cards share one bilingual model, and the gateway runtime no longer installs `torch` or `sentence-transformers`.
- `PHASE2_MI_THRESHOLD` is 0.77. On 3 local staging days (25 same-type multi-pairs, 2026-11-07) that cutoff keeps 21/25, the same rate the old encoder kept at 0.06, and it still keeps the sep29 leave-notice pair.
- `HERMES_PHASE2_SKIP_MINILM` is now `HERMES_PHASE2_SKIP_EMBED`. The ≤2GiB / <256MiB fail-open stays, because the int8 session is still large.
- `ensure_gte_int8` now also covers this gate. Nothing uninstalls the old wheels: on the cloud venv, `pip uninstall torch sentence-transformers` is manual.
