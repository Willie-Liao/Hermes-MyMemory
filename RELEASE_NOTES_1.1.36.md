## MyMemory 1.1.36

Oneshot workers can opt into LiteLLM without slowing plugin load.

- A provider capability row with `wire: litellm` sends oneshot calls through `litellm.completion`. Replies stay in the OpenAI tool-call shape, so JSON parse, schema teach, and the usage ledger are unchanged. `litellm_prefix` (for example `gemini`) is added when the model has no `/`.
- `litellm` is imported only on that call, and `LITELLM_LOCAL_MODEL_COST_MAP` is set so the import does not fetch the price table. OpenAI and Anthropic wires never import it.
- `litellm==1.108.2` is pinned in requirements.txt. A dry-run against the Hermes venv left `openai` 2.24.0 and `pydantic` 2.13.4 in place. The install may upgrade `certifi` and `click`.
- A missing requirements line now installs on a background thread. Plugin load returns inside the 10s cap, and the gateway still restarts after the install finishes.
