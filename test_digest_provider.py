"""MyMemory provider discovery, prefetch compose, and lifecycle."""

from __future__ import annotations

import json
import sys
import time
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
            "entity: Casey",
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
    assert cmds["monthly"]["handler"].__name__ == "handle_monthly"
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
    assert "Memory / recent days" not in a
    assert a.count("MEMORY.md") == 1
    assert "already in the system prompt" in a


def test_prefetch_compose_no_prewrap(tmp_path, monkeypatch):
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True)
    from memory_staging import hermes_local_today_str

    (daily / f"{hermes_local_today_str()}.md").write_text(
        _block(block_id="mem-1", body="Casey canteen open.") + "\n",
        encoding="utf-8",
    )
    from MyMemory.weekly import weekly

    monkeypatch.setattr(weekly, "on_pre_llm_call", lambda **_: None)
    p = MyMemoryProvider()
    first = p.prefetch("hello there", session_id="s-pre")
    assert "Memory / recent days" in first or "Memory / entity index" in first
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
    monkeypatch.setattr(
        "MyMemory.provider.embed_cache.start_embed_cache_clock_thread",
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
    from MyMemory.monthly.monthly_actions import handle_monthly

    assert cmds["monthly"]["handler"] is handle_monthly


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
    from MyMemory.monthly.monthly_actions import handle_monthly

    assert cmds["monthly"]["handler"] is handle_monthly


def test_initialize_starts_clock_weekly_retention_once(monkeypatch):
    reset_bootstrap_for_tests()
    clock = []
    embed_clock = []
    weekly_reasons = []
    ret_reasons = []
    monkeypatch.setattr(digest, "start_digest_clock_thread", lambda: clock.append(1))
    monkeypatch.setattr(
        "MyMemory.provider.embed_cache.start_embed_cache_clock_thread",
        lambda: embed_clock.append(1),
    )
    from MyMemory.weekly import weekly
    from MyMemory.retention import retention
    monkeypatch.setattr(weekly, "run_async", lambda reason: weekly_reasons.append(reason))
    monkeypatch.setattr(retention, "run_async", lambda reason: ret_reasons.append(reason))
    monkeypatch.setattr("MyMemory.provider.weekly_tools.ensure_weekly_tools_registered", lambda: None)
    p = MyMemoryProvider()
    p.initialize("s1", hermes_home=str(_HERMES_HOME), platform="cli")
    p.initialize("s2", hermes_home=str(_HERMES_HOME), platform="cli")
    assert clock == [1]
    assert embed_clock == [1]
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


_OLD_CANTEEN = "mem-2026-08-01-fact-aaaaaaaaaaaa"
_NEW_CANTEEN = "mem-2026-08-20-fact-bbbbbbbbbbbb"
_ALT_CANTEEN = "mem-2026-08-10-fact-cccccccccccc"


def _fact_card(mem_id: str, entity: str, body: str, *, extra: str = "") -> str:
    return (
        "---\n"
        f"id: {mem_id}\n"
        "type: fact\n"
        f"entity: {entity}\n"
        "confidence: high\n"
        "status: candidate\n"
        f"valid_from: {mem_id[4:14]}\n"
        "valid_to: open\n"
        "sources: [session s-test]\n"
        f"{extra}"
        "---\n"
        f"{body}\n"
    )


def _write_canteen_days(home: Path) -> Path:
    daily = home / "memories" / "staging" / "daily"
    daily.mkdir(parents=True)
    (daily / "2026-08-01.md").write_text(
        _fact_card(_OLD_CANTEEN, "Canteen", "Canteen is open on weekdays."),
        encoding="utf-8",
    )
    (daily / "2026-08-20.md").write_text(
        _fact_card(_NEW_CANTEEN, "Canteen", "Canteen is closed for renovation."),
        encoding="utf-8",
    )
    return daily


def _immediate_digest_threads(monkeypatch) -> list:
    """Run digest background work inline so tests can observe the patch."""
    started: list = []

    class ImmediateThread:
        def __init__(self, target=None, args=(), kwargs=None, name=None, daemon=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}
            started.append(self)

        def start(self):
            if self._target is not None:
                self._target(*self._args, **self._kwargs)

    monkeypatch.setattr(digest.threading, "Thread", ImmediateThread)
    return started


def _wire_provider_home(monkeypatch, tmp_path: Path, *, immediate: bool = True) -> MyMemoryProvider:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(digest, "_fetch_messages", lambda *a, **k: [])
    monkeypatch.setattr(digest, "maybe_run_digest_clock", lambda **k: {"outcome": "idle"})
    if immediate:
        _immediate_digest_threads(monkeypatch)
    p = MyMemoryProvider()
    p._agent_context = "primary"
    p._session_id = "s-recall"
    p._hermes_home = str(tmp_path)
    return p


def _retrieval_state(home: Path, session_id: str = "s-recall") -> dict:
    path = home / "memories" / "staging" / ".digest-state.json"
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return (payload.get("sessions") or {}).get(session_id) or {}


def test_recall_tool_records_ordered_ids_capped_at_eight(tmp_path, monkeypatch):
    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True)
    ids = [f"mem-2026-08-{i:02d}-fact-{chr(97)*12}" for i in range(1, 11)]
    for i, mem_id in enumerate(ids, start=1):
        (daily / f"2026-08-{i:02d}.md").write_text(
            _fact_card(mem_id, "TokenFarm", f"TokenFarm row {i} unique zxqvtoken."),
            encoding="utf-8",
        )
    p = _wire_provider_home(monkeypatch, tmp_path)
    text = p.handle_tool_call("recall_memory", {"query": "TokenFarm zxqvtoken", "k": 20})
    assert "mem-" in text
    entry = _retrieval_state(tmp_path)
    recorded = entry.get("retrieval", {}).get("ids") or entry.get("retrieval_ids")
    assert recorded, "co-retrieved mem-ids must be stored on digest state"
    assert recorded == list(dict.fromkeys(recorded))
    assert len(recorded) <= 8


def test_retrieval_set_is_session_scoped_and_expires(tmp_path, monkeypatch):
    _write_canteen_days(tmp_path)
    p = _wire_provider_home(monkeypatch, tmp_path)
    p.handle_tool_call("recall_memory", {"query": "Canteen"})
    entry = _retrieval_state(tmp_path, "s-recall")
    recorded = (entry.get("retrieval") or {}).get("ids") or []
    assert _OLD_CANTEEN in recorded or _NEW_CANTEEN in recorded

    other = _retrieval_state(tmp_path, "s-other")
    assert not (other.get("retrieval") or {}).get("ids")

    stale = json.loads(
        (tmp_path / "memories" / "staging" / ".digest-state.json").read_text(encoding="utf-8")
    )
    stale["sessions"]["s-recall"]["retrieval"]["recorded_at"] = time.time() - (31 * 60)
    (tmp_path / "memories" / "staging" / ".digest-state.json").write_text(
        json.dumps(stale), encoding="utf-8"
    )
    old_text = (tmp_path / "memories" / "staging" / "daily" / "2026-08-01.md").read_text(
        encoding="utf-8"
    )
    p.sync_turn("that memory is dated", "ok", session_id="s-recall")
    after = (tmp_path / "memories" / "staging" / "daily" / "2026-08-01.md").read_text(
        encoding="utf-8"
    )
    assert after == old_text
    assert "status: rejected" not in after


def test_sync_turn_does_not_block_on_targeted_phase2(tmp_path, monkeypatch):
    _write_canteen_days(tmp_path)
    started: list = []

    class DeferredThread:
        def __init__(self, target=None, args=(), kwargs=None, name=None, daemon=None):
            started.append(target)
            self._target = target

        def start(self):
            return None

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(digest, "_fetch_messages", lambda *a, **k: [])
    monkeypatch.setattr(digest, "maybe_run_digest_clock", lambda **k: {"outcome": "idle"})
    monkeypatch.setattr(digest.threading, "Thread", DeferredThread)
    p = MyMemoryProvider()
    p._agent_context = "primary"
    p.handle_tool_call("recall_memory", {"query": "Canteen"})
    p.sync_turn("hi", "ok", session_id="s-recall")
    assert started, "targeted Phase 2 must be scheduled on a background thread"
    text = (tmp_path / "memories" / "staging" / "daily" / "2026-08-01.md").read_text(
        encoding="utf-8"
    )
    assert "status: rejected" not in text


def test_automatic_contradiction_sets_rejected_by_latest_id(tmp_path, monkeypatch):
    daily = _write_canteen_days(tmp_path)
    p = _wire_provider_home(monkeypatch, tmp_path)
    p.handle_tool_call("recall_memory", {"query": "Canteen"})
    p.sync_turn("what is the canteen status?", "closed", session_id="s-recall")
    old = (daily / "2026-08-01.md").read_text(encoding="utf-8")
    new = (daily / "2026-08-20.md").read_text(encoding="utf-8")
    assert "Canteen is open on weekdays." in old
    assert "status: rejected" in old
    assert f"rejected_reason: rejected by {_NEW_CANTEEN}" in old
    assert "valid_to: 2026-08-20" in old
    assert "status: candidate" in new
    assert "Canteen is closed for renovation." in new


def test_user_correction_reason_when_one_older_target(tmp_path, monkeypatch):
    daily = _write_canteen_days(tmp_path)
    p = _wire_provider_home(monkeypatch, tmp_path)
    p.handle_tool_call("recall_memory", {"query": "Canteen"})
    p.sync_turn("that memory is dated", "ok", session_id="s-recall")
    old = (daily / "2026-08-01.md").read_text(encoding="utf-8")
    assert "status: rejected" in old
    assert "rejected_reason: rejected by user's correction" in old
    assert "Canteen is open on weekdays." in old


def test_ambiguous_user_correction_is_noop(tmp_path, monkeypatch):
    daily = _write_canteen_days(tmp_path)
    (daily / "2026-08-10.md").write_text(
        _fact_card(_ALT_CANTEEN, "Canteen", "Canteen closes at 14:00."),
        encoding="utf-8",
    )
    p = _wire_provider_home(monkeypatch, tmp_path)
    p.handle_tool_call("recall_memory", {"query": "Canteen"})
    before = {
        name: (daily / name).read_text(encoding="utf-8")
        for name in ("2026-08-01.md", "2026-08-10.md", "2026-08-20.md")
    }
    p.sync_turn("that memory is dated", "ok", session_id="s-recall")
    for name, text in before.items():
        assert (daily / name).read_text(encoding="utf-8") == text


def test_failed_external_patch_keeps_retrieval_for_retry(tmp_path, monkeypatch):
    daily = _write_canteen_days(tmp_path)
    p = _wire_provider_home(monkeypatch, tmp_path)
    p.handle_tool_call("recall_memory", {"query": "Canteen"})

    import memory_staging as ms

    monkeypatch.setattr(
        ms,
        "patch_daily_block_status",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    p.sync_turn("that memory is dated", "ok", session_id="s-recall")
    old = (daily / "2026-08-01.md").read_text(encoding="utf-8")
    assert "status: rejected" not in old
    entry = _retrieval_state(tmp_path)
    recorded = (entry.get("retrieval") or {}).get("ids")
    assert recorded, "failed Phase-2 must retain the retrieval set for retry"


def test_recall_tool_schema_exposes_optional_time_bounds():
    from recall.tools import TOOL_SCHEMAS

    schema = next(row for row in TOOL_SCHEMAS if row["name"] == "recall_memory")
    props = schema["parameters"]["properties"]
    assert schema["parameters"]["required"] == ["query"]
    assert "time_from" in props and "time_to" in props
    assert "time_from" not in schema["parameters"]["required"]
    assert "time_to" not in schema["parameters"]["required"]


def test_provider_tool_schemas_include_monthly():
    names = {row["name"] for row in MyMemoryProvider().get_tool_schemas()}
    assert "mymemory_digest" in names
    assert "mymemory_weekly" in names
    assert "mymemory_monthly" in names
