"""Provider URL reshape: oneshot /v1 vs nested /anthropic. No localhost proxy."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_llm_wire():
    path = Path(__file__).with_name("llm_wire.py")
    spec = importlib.util.spec_from_file_location("llm_wire_test", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_resolve_reshapes_minimax_wires():
    lw = _load_llm_wire()
    url, used = lw.resolve_worker_endpoint(
        "https://api.minimax.chat/v1", "chat_completions"
    )
    assert used is False
    assert url == "https://api.minimax.chat/v1"
    url, used = lw.resolve_worker_endpoint(
        "https://api.minimax.chat/v1", "anthropic_messages"
    )
    assert used is False
    assert url == "https://api.minimax.chat/anthropic"
    url, used = lw.resolve_worker_endpoint(
        "https://api.minimax.chat/anthropic", "chat_completions"
    )
    assert used is False
    assert url == "https://api.minimax.chat/v1"
    url, used = lw.resolve_worker_endpoint(
        "https://api.minimax.chat/anthropic/v1", "chat_completions"
    )
    assert used is False
    assert url == "https://api.minimax.chat/v1"
