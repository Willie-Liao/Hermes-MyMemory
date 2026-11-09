## MyMemory 1.1.38

Worker lanes send the lowest reasoning effort the route accepts, then read the reply through one parser.

- A route starts at `off`. A 400 that refuses reasoning off steps to `low` and is remembered. A 400 that rejects the field, including `Invalid request controls`, strips the controls. If that bare request still fails, the same call goes through LiteLLM as `openai/<model>` at that lowest effort.
- `provider_capabilities.<provider>.reasoning_floor: low` skips the wasted off attempt. Custom providers under `providers:` use top-level `reasoning_effort`.
- A cut-off reply doubles `max_tokens` up to the lane ceiling, at most twice. Monthly uses `max_tokens_ceiling` when set.
- Digest, weekly, monthly, and recall share the reply parser, so `content: null` with `tool_calls` still counts.
- Dated notes `RELEASE_NOTES_1.1.22.md` through `RELEASE_NOTES_1.1.36.md` are removed. `install.sh` deletes them on the gateway because rsync does not.
- LiteLLM is pinned at `1.106.2`. `1.108.2` requires `boto3>=1.43.117`, and Hermes bedrock locks `boto3==1.43.115` through v0.23.0, so `hermes update` failed the workspace resolve and disabled MyMemory on Aliyun.
- The install-time comparison cleanup is gone. `drop_retired_comparison_summary` and its test are removed; `install.sh` no longer rewrites month files.
