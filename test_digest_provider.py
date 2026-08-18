"""MyMemory provider discovery, prefetch compose, and lifecycle."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_PLUGINS = _HERE.parent
_HERMES_HOME = _PLUGINS.parent
_AGENT = _HERMES_HOME / "hermes-agent"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(_PLUGINS) not in sys.path:
    sys.path.insert(0, str(_PLUGINS))
if str(_AGENT) not in sys.path:
    sys.path.insert(0, str(_AGENT))

from MyMemory.provider import MyMemoryProvider, reset_bootstrap_for_tests
from MyMemory.digest import digest


def _block(*, block_id: str, body: str) -> str:
    return "\n".join(
        [
            "---",
            f"id: {block_id}",
            "type: fact",
            "entity: Gabby",
            "confidence: high",
            "status: candidate",
            "sources: [session s1]",
            "---",
            body,
        ]
    )


def test_init_py_registers_slash_without_hooks():
    text = (_HERE / "__init__.py").read_text(encoding="utf-8")
    assert "register_command" in text
    assert "register_hook" not in text
    assert "hasattr" in text
    assert "register_memory_provider" in text


@pytest.mark.skipif(
    not (_AGENT / "hermes_cli" / "plugins.py").exists(),
    reason="hermes-agent not vendored",
)
def test_plugin_manager_discover_registers_digest_and_weekly_slash(monkeypatch):
    """Gateway discover_plugins() must attach slash before any AIAgent initialize."""
    from hermes_cli import plugins as plugins_mod

    monkeypatch.setenv("HERMES_HOME", str(_HERMES_HOME))
    mgr = plugins_mod.PluginManager()
    monkeypatch.setattr(plugins_mod, "_plugin_manager", mgr)
    monkeypatch.setattr(plugins_mod, "get_plugin_manager", lambda: mgr)
    mgr.discover_and_load()
    cmds = mgr._plugin_commands
    assert cmds["digest"]["handler"].__name__ == "handle_digest"
    assert cmds["weekly"]["handler"].__name__ == "handle_weekly"
    assert "digest" in cmds
    assert "weekly" in cmds


def test_name_and_available():
    p = MyMemoryProvider()
    assert p.name == "MyMemory"
    assert p.is_available() is True


def test_system_prompt_block_stable_and_no_index():
    p = MyMemoryProvider()
    a = p.system_prompt_block()
    b = p.system_prompt_block()
    assert a == b
    assert "Recall index" not in a
    assert a.count("MEMORY.md") == 1
    assert "already in the system prompt" in a


def test_prefetch_compose_no_prewrap(tmp_path, monkeypatch):
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True)
    from memory_staging import hermes_local_today_str

    (daily / f"{hermes_local_today_str()}.md").write_text(
        _block(block_id="mem-1", body="Gabby canteen open.") + "\n",
        encoding="utf-8",
    )
    from MyMemory.weekly import weekly

    monkeypatch.setattr(weekly, "on_pre_llm_call", lambda **_: None)
    p = MyMemoryProvider()
    first = p.prefetch("hello there", session_id="s-pre")
    assert "## Recall index (unlocked window)" in first
    assert "<memory-context>" not in first
    assert "USER.md" not in first
    second = p.prefetch("hello again", session_id="s-pre")
    assert second == ""


def test_prefetch_keeps_weekly_when_digest_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    from MyMemory.weekly import weekly

    monkeypatch.setattr(
        weekly,
        "on_pre_llm_call",
        lambda **_: {"context": "## Weekly Brief\nhello"},
    )
    p = MyMemoryProvider()
    p.prefetch("first", session_id="s-w")
    text = p.prefetch("second", session_id="s-w")
    assert "Weekly Brief" in text
    assert "<memory-context>" not in text


def test_sync_turn_calls_digest_extract(monkeypatch):
    seen = []
    monkeypatch.setattr(digest, "on_agent_end", lambda ctx: seen.append(ctx))
    p = MyMemoryProvider()
    p._agent_context = "primary"
    p.sync_turn("hi", "ok", session_id="s-sync")
    assert seen
    assert seen[0]["session_id"] == "s-sync"


def _stub_bootstrap(monkeypatch) -> None:
    monkeypatch.setattr(digest, "start_digest_clock_thread", lambda: None)
    from MyMemory.weekly import weekly
    from MyMemory.retention import retention

    monkeypatch.setattr(weekly, "run_async", lambda reason: None)
    monkeypatch.setattr(retention, "run_async", lambda reason: None)
    monkeypatch.setattr(
        "MyMemory.provider.weekly_tools.ensure_weekly_tools_registered",
        lambda: None,
    )


def _fresh_plugin_manager(monkeypatch):
    """Skip PluginManager filesystem scan; keep an empty command table."""
    from hermes_cli import plugins as plugins_mod

    mgr = plugins_mod.PluginManager()
    mgr._discovered = True
    monkeypatch.setattr(plugins_mod, "_plugin_manager", mgr)
    monkeypatch.setattr(plugins_mod, "get_plugin_manager", lambda: mgr)
    return mgr


@pytest.mark.skipif(
    not (_AGENT / "hermes_cli" / "plugins.py").exists(),
    reason="hermes-agent not vendored",
)
def test_initialize_registers_digest_and_weekly_slash(monkeypatch):
    from hermes_cli.plugins import get_plugin_commands
    from MyMemory.digest import slash as digest_slash
    from MyMemory.weekly import slash as weekly_slash

    _fresh_plugin_manager(monkeypatch)
    reset_bootstrap_for_tests()
    _stub_bootstrap(monkeypatch)
    p = MyMemoryProvider()
    p.initialize("s1", hermes_home=str(_HERMES_HOME), platform="cli")
    cmds = get_plugin_commands()
    assert cmds["digest"]["handler"] is digest_slash.handle_digest
    assert cmds["weekly"]["handler"] is weekly_slash.handle_weekly


@pytest.mark.skipif(
    not (_AGENT / "hermes_cli" / "plugins.py").exists(),
    reason="hermes-agent not vendored",
)
def test_initialize_registers_slash_in_skip_write_context(monkeypatch):
    from hermes_cli.plugins import get_plugin_commands
    from MyMemory.digest import slash as digest_slash
    from MyMemory.weekly import slash as weekly_slash

    _fresh_plugin_manager(monkeypatch)
    reset_bootstrap_for_tests()
    p = MyMemoryProvider()
    p.initialize(
        "s-cron",
        hermes_home=str(_HERMES_HOME),
        platform="cli",
        agent_context="cron",
    )
    cmds = get_plugin_commands()
    assert cmds["digest"]["handler"] is digest_slash.handle_digest
    assert cmds["weekly"]["handler"] is weekly_slash.handle_weekly


def test_initialize_starts_clock_weekly_retention_once(monkeypatch):
    reset_bootstrap_for_tests()
    clock = []
    weekly_reasons = []
    ret_reasons = []
    monkeypatch.setattr(digest, "start_digest_clock_thread", lambda: clock.append(1))
    from MyMemory.weekly import weekly
    from MyMemory.retention import retention

    monkeypatch.setattr(weekly, "run_async", lambda reason: weekly_reasons.append(reason))
    monkeypatch.setattr(retention, "run_async", lambda reason: ret_reasons.append(reason))
    monkeypatch.setattr("MyMemory.provider.weekly_tools.ensure_weekly_tools_registered", lambda: None)
    p = MyMemoryProvider()
    p.initialize("s1", hermes_home=str(_HERMES_HOME), platform="cli")
    p.initialize("s2", hermes_home=str(_HERMES_HOME), platform="cli")
    assert clock == [1]
    assert weekly_reasons == ["plugin_load"]
    assert ret_reasons == ["plugin_load"]


@pytest.mark.skipif(
    not (_AGENT / "plugins" / "memory" / "__init__.py").exists(),
    reason="hermes-agent not vendored",
)
def test_hermes_loader_discovers_mymemory(monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(_HERMES_HOME))
    from plugins.memory import _is_memory_provider_dir, load_memory_provider

    assert _is_memory_provider_dir(_HERE) is True
    loaded = load_memory_provider("MyMemory")
    assert loaded is not None
    assert loaded.name == "MyMemory"
    assert loaded.is_available() is True
