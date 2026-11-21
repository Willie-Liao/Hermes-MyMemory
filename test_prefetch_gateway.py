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


def _provider(monkeypatch, tmp_path):
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
    import MyMemory.provider as provider

    monkeypatch.setattr(provider.digest, "on_pre_llm_call", lambda **_k: None)
    monkeypatch.setattr(provider.weekly, "on_pre_llm_call", lambda **_k: None)
    monkeypatch.setattr(provider.digest, "on_agent_end", lambda *_a, **_k: None)
    return provider


def test_prefetch_passes_history_newest_first_and_seeds_once(tmp_path, monkeypatch):
    """A short line seeds earlier user rows once, newest first, and an ack is not stored."""
    provider = _provider(monkeypatch, tmp_path)
    seen: list = []

    def fake(q, **kwargs):
        seen.append((q, kwargs.get("history")))
        return "## Memory / recall  channel=miss  q=x\n"

    monkeypatch.setattr("recall.tools.recall_memory", fake)
    fetches = {"n": 0}

    def fetch(sid, after_id=0):
        fetches["n"] += 1
        return [
            {"id": 1, "role": "user", "content": "older weekly report", "timestamp": 100},
            {"id": 2, "role": "user", "content": "哈哈", "timestamp": 150},
            {"id": 3, "role": "user", "content": "newer wechat note", "timestamp": 200},
        ]

    monkeypatch.setattr("digest.digest._fetch_messages", fetch)
    host = provider.MyMemoryProvider()
    host.prefetch("发给我", session_id="sess")
    assert fetches["n"] == 1
    assert seen[0][1] == [(200.0, "newer wechat note"), (100.0, "older weekly report")]
    host.prefetch("再发", session_id="sess")
    assert fetches["n"] == 1
    assert seen[1][1][0][1] == "发给我"
    host.prefetch("哈哈", session_id="sess")
    assert all(line != "哈哈" for _ts, line in host._user_turns["sess"])


def test_prefetch_seeds_only_a_short_line(tmp_path, monkeypatch):
    """A long line must not read state.db, because it does not need earlier lines."""
    provider = _provider(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "recall.tools.recall_memory",
        lambda *_a, **_k: "## Memory / recall  channel=miss  q=x\n",
    )
    fetches = {"n": 0}
    monkeypatch.setattr(
        "digest.digest._fetch_messages",
        lambda *_a, **_k: fetches.__setitem__("n", fetches["n"] + 1) or [],
    )
    host = provider.MyMemoryProvider()
    host.prefetch("this line is already long enough to carry its own situation", session_id="sess")
    assert fetches["n"] == 0


def test_session_switch_moves_or_drops_the_turn_buffer(tmp_path, monkeypatch):
    """Rewind drops piled lines; a compression switch keeps them on the child session."""
    from collections import deque

    provider = _provider(monkeypatch, tmp_path)
    host = provider.MyMemoryProvider()
    host._user_turns["parent"] = deque([(1.0, "keep me")], maxlen=5)
    host.on_session_switch("child", parent_session_id="parent")
    assert "parent" not in host._user_turns
    assert list(host._user_turns["child"]) == [(1.0, "keep me")]
    host.on_session_switch("child", rewound=True)
    assert "child" not in host._user_turns
