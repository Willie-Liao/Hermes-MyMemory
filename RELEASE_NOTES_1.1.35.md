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
