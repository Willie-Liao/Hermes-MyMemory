"""Prefetch has no LiteLLM failover budget; reshape is local and free."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def test_reshape_does_not_consume_prefetch_budget():
    """Removing the proxy means worker URL pick is not a second HTTP timeout."""
    spec = importlib.util.spec_from_file_location(
        "prefetch_timeout_no_gw",
        Path(__file__).with_name("prefetch_timeout.py"),
    )
    assert spec is not None and spec.loader is not None
    pt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pt)
    assert pt.DEFAULT_PREFETCH_TIMEOUT_S >= 20.0


def test_prefetch_stamps_each_guidance_id_once_per_session(tmp_path, monkeypatch):
    """Prefetch must not write; the post-turn stamp counts an id once per session."""
    import sys
    import types
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    agent = types.ModuleType("agent")
    memory_provider = types.ModuleType("agent.memory_provider")

    class MemoryProvider:
        pass

    memory_provider.MemoryProvider = MemoryProvider
    sys.modules.setdefault("agent", agent)
    sys.modules.setdefault("agent.memory_provider", memory_provider)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("RECALL_PERSIST_IN_TEST", "1")
    mem_id = "mem-2026-08-09-fact-000000000099"
    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True)
    daily.joinpath("2026-08-09.md").write_text(
        "---\n"
        f"id: {mem_id}\n"
        "type: fact\n"
        "entity: Guide\n"
        "status: candidate\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    import MyMemory.provider as provider

    monkeypatch.setattr(provider.digest, "on_pre_llm_call", lambda **_k: None)
    monkeypatch.setattr(provider.weekly, "on_pre_llm_call", lambda **_k: None)
    monkeypatch.setattr(provider.digest, "on_agent_end", lambda *_a, **_k: None)
    guided = f"## Memory / recall  channel=monthly_guidance\n- preference {mem_id}  context=-  text  strength=0.00\n"
    monkeypatch.setattr(
        "recall.tools.recall_memory",
        lambda *_a, **_k: guided,
    )
    host = provider.MyMemoryProvider()
    host.prefetch("send the file", session_id="sess-1")
    text = daily.joinpath("2026-08-09.md").read_text(encoding="utf-8")
    assert "recall_n" not in text
    host.sync_turn("send the file", "done", session_id="sess-1")
    assert host._stamp_thread is not None
    host._stamp_thread.join(timeout=2)
    text = daily.joinpath("2026-08-09.md").read_text(encoding="utf-8")
    assert "recall_n: 1" in text
    host.prefetch("send the file", session_id="sess-1")
    host.sync_turn("send the file", "done", session_id="sess-1")
    if host._stamp_thread is not None:
        host._stamp_thread.join(timeout=2)
    text = daily.joinpath("2026-08-09.md").read_text(encoding="utf-8")
    assert "recall_n: 1" in text
    host.prefetch("send the file", session_id="sess-2")
    host.sync_turn("send the file", "again", session_id="sess-2")
    host._stamp_thread.join(timeout=2)
    text = daily.joinpath("2026-08-09.md").read_text(encoding="utf-8")
    assert "recall_n: 2" in text
