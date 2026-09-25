"""Pick MiniMax/Kimi URL suffixes so oneshot and nested Hermes do not 404.

Hermes chat/cron speak Anthropic Messages; oneshot speaks OpenAI chat.completions.
One env URL cannot serve both. Reshape the same host: nested → /anthropic, oneshot → /v1.
"""

from __future__ import annotations

from typing import Any, Mapping


def resolve_worker_endpoint(
    direct_url: str,
    api_mode: str,
    *,
    settings: Mapping[str, Any] | None = None,
) -> tuple[str, bool]:
    """Return the provider suffix that matches the client. Never a localhost proxy.

    Passing MiniMax `/v1` into Hermes Anthropic (or `/anthropic` into OpenAI oneshot)
    404s. `settings` is unused; kept so callers that passed YAML bags still compile.
    The bool is always False (no translator gateway).
    """
    del settings
    raw = str(direct_url or "").strip().rstrip("/")
    if not raw:
        return raw, False
    host = raw
    for suffix in ("/anthropic/v1", "/anthropic", "/v1"):
        if host.endswith(suffix):
            host = host[: -len(suffix)]
            break
    if str(api_mode or "").strip().lower() == "anthropic_messages":
        return f"{host}/anthropic", False
    return f"{host}/v1", False
