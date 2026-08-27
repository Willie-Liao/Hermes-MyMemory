"""Tests for shared helper-LLM reentry guard."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path


def _load_worker_llm():
    path = Path(__file__).with_name("worker_llm.py")
    spec = importlib.util.spec_from_file_location("worker_llm_test", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _install_fake_openai(monkeypatch, *, seen: dict, message):
    class FakeMessage:
        def __init__(self):
            self.content = message.get("content")
            self.tool_calls = message.get("tool_calls")

    class FakeChoice:
        def __init__(self):
            self.message = FakeMessage()
            self.finish_reason = "tool_calls"

    class FakeUsage:
        prompt_tokens = 12
        completion_tokens = 8
        total_tokens = 20
        prompt_tokens_details = types.SimpleNamespace(cached_tokens=4)
        completion_tokens_details = types.SimpleNamespace(reasoning_tokens=1)

    class FakeResponse:
        choices = [FakeChoice()]
        usage = FakeUsage()

    class FakeCompletions:
        def create(self, **kwargs):
            seen["create"] = kwargs
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        def __init__(self, **kwargs):
            seen["client"] = kwargs
            self.chat = FakeChat()

    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = FakeClient
    monkeypatch.setitem(sys.modules, "openai", fake_openai)


def test_worker_llm_scope_sets_and_clears_flag(monkeypatch):
    wl = _load_worker_llm()
    monkeypatch.delenv(wl._ENV_WORKER_LLM_DEPTH, raising=False)
    assert wl.in_worker_llm() is False
    with wl.worker_llm_scope():
        assert wl.in_worker_llm() is True
    assert wl.in_worker_llm() is False


def test_worker_llm_scope_nests():
    wl = _load_worker_llm()
    with wl.worker_llm_scope():
        assert wl.in_worker_llm() is True
        with wl.worker_llm_scope():
            assert wl.in_worker_llm() is True
        assert wl.in_worker_llm() is True
    assert wl.in_worker_llm() is False


def test_run_worker_llm_records_usage(tmp_path, monkeypatch):
    wl = _load_worker_llm()

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    class FakeAgent:
        def __init__(self, **kwargs):
            self.model = kwargs.get("model") or "fake-model"
            self.session_input_tokens = 11
            self.session_output_tokens = 7
            self.session_cache_read_tokens = 2
            self.session_cache_write_tokens = 1
            self.session_reasoning_tokens = 3
            self.session_total_tokens = 24
            self.session_estimated_cost_usd = 0.0123
            self.session_api_calls = 1

        def run_conversation(self, prompt):
            assert wl.in_worker_llm() is True
            return {"final_response": f"echo:{prompt}"}

    fake_gateway_pkg = types.ModuleType("gateway")
    fake_gateway_run = types.ModuleType("gateway.run")
    fake_gateway_run._resolve_gateway_model = lambda: "fake-model"
    fake_gateway_run._resolve_runtime_agent_kwargs = lambda: {}
    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = FakeAgent

    monkeypatch.setitem(sys.modules, "gateway", fake_gateway_pkg)
    monkeypatch.setitem(sys.modules, "gateway.run", fake_gateway_run)
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    out = wl.run_worker_llm("hi", plugin="memory-digest", purpose="digest")
    assert out == "echo:hi"
    assert wl.in_worker_llm() is False

    ledger = tmp_path / "metrics" / "llm-usage.jsonl"
    assert ledger.is_file()
    line = ledger.read_text(encoding="utf-8").strip().splitlines()[-1]
    rec = json.loads(line)
    assert rec["plugin"] == "memory-digest"
    assert rec["purpose"] == "digest"
    assert rec["input_tokens"] == 11
    assert rec["output_tokens"] == 7
    assert rec["total_tokens"] == 24
    assert abs(rec["cost_usd"] - 0.0123) < 1e-9


def test_run_worker_llm_pops_model_from_runtime_kwargs(tmp_path, monkeypatch):
    """Match gateway: fallback kwargs may include model; never pass it twice."""
    wl = _load_worker_llm()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    seen: dict = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            seen["kwargs"] = dict(kwargs)
            self.model = kwargs.get("model")
            self.session_input_tokens = 0
            self.session_output_tokens = 0
            self.session_cache_read_tokens = 0
            self.session_cache_write_tokens = 0
            self.session_reasoning_tokens = 0
            self.session_total_tokens = 0
            self.session_estimated_cost_usd = 0.0
            self.session_api_calls = 0

        def run_conversation(self, prompt):
            seen["suppress"] = getattr(self, "suppress_status_output", None)
            return {"final_response": "ok"}

    fake_gateway_pkg = types.ModuleType("gateway")
    fake_gateway_run = types.ModuleType("gateway.run")
    fake_gateway_run._resolve_gateway_model = lambda: "primary-model"
    fake_gateway_run._resolve_runtime_agent_kwargs = lambda: {
        "api_key": "k",
        "provider": "minimax-cn",
        "model": "fallback-model",
    }
    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = FakeAgent

    monkeypatch.setitem(sys.modules, "gateway", fake_gateway_pkg)
    monkeypatch.setitem(sys.modules, "gateway.run", fake_gateway_run)
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    assert wl.run_worker_llm("hi", plugin="memory-digest", purpose="digest") == "ok"
    kwargs = seen["kwargs"]
    assert kwargs["model"] == "fallback-model"
    assert seen.get("suppress") is True
    assert "api_key" in kwargs
    assert kwargs.get("provider") == "minimax-cn"


def test_plugin_config_model_overrides_gateway(tmp_path, monkeypatch):
    wl = _load_worker_llm()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        "plugins:\n  entries:\n    memory-digest:\n      provider: xiaomi\n      model: mimo-v2.5\n",
        encoding="utf-8",
    )
    seen: dict = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            seen["kwargs"] = dict(kwargs)
            self.model = kwargs.get("model")
            self.session_input_tokens = 0
            self.session_output_tokens = 0
            self.session_cache_read_tokens = 0
            self.session_cache_write_tokens = 0
            self.session_reasoning_tokens = 0
            self.session_total_tokens = 0
            self.session_estimated_cost_usd = 0.0
            self.session_api_calls = 0

        def run_conversation(self, prompt):
            return {"final_response": "ok"}

    fake_gateway_pkg = types.ModuleType("gateway")
    fake_gateway_run = types.ModuleType("gateway.run")
    fake_gateway_run._resolve_gateway_model = lambda: "primary-model"
    fake_gateway_run._resolve_runtime_agent_kwargs = lambda: {
        "api_key": "kimi-key",
        "provider": "kimi-coding",
        "model": "kimi-k2.6",
        "base_url": "https://api.kimi.com/coding/v1",
    }
    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = FakeAgent

    monkeypatch.setitem(sys.modules, "gateway", fake_gateway_pkg)
    monkeypatch.setitem(sys.modules, "gateway.run", fake_gateway_run)
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    assert wl.run_worker_llm("hi", plugin="memory-digest", purpose="digest") == "ok"
    kwargs = seen["kwargs"]
    assert kwargs["model"] == "mimo-v2.5"
    assert kwargs.get("provider") == "xiaomi"
    assert "api_key" not in kwargs
    assert "base_url" not in kwargs


def test_worker_llm_lanes_override_by_purpose(tmp_path, monkeypatch):
    wl = _load_worker_llm()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        "\n".join(
            [
                "plugins:",
                "  entries:",
                "    memory-digest:",
                "      provider: xiaomi",
                "      model: mimo-v2.5",
                "      worker_llm:",
                "        phase1:",
                "          provider: xiaomi",
                "          model: mimo-v2.5",
                "        phase2:",
                "          provider: kimi-coding",
                "          model: kimi-k2.6",
                "        wrapup:",
                "          provider: xiaomi",
                "          model: mimo-v2.5",
                "    memory-weekly:",
                "      provider: xiaomi",
                "      model: mimo-v2.5",
                "      worker_llm:",
                "        weekly:",
                "          provider: minimax-cn",
                "          model: MiniMax-M3",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    seen: dict = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            seen["kwargs"] = dict(kwargs)
            self.model = kwargs.get("model")
            self.session_input_tokens = 0
            self.session_output_tokens = 0
            self.session_cache_read_tokens = 0
            self.session_cache_write_tokens = 0
            self.session_reasoning_tokens = 0
            self.session_total_tokens = 0
            self.session_estimated_cost_usd = 0.0
            self.session_api_calls = 0

        def run_conversation(self, prompt):
            return {"final_response": "ok"}

    fake_gateway_pkg = types.ModuleType("gateway")
    fake_gateway_run = types.ModuleType("gateway.run")
    fake_gateway_run._resolve_gateway_model = lambda: "primary-model"
    fake_gateway_run._resolve_runtime_agent_kwargs = lambda: {}
    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = FakeAgent

    monkeypatch.setitem(sys.modules, "gateway", fake_gateway_pkg)
    monkeypatch.setitem(sys.modules, "gateway.run", fake_gateway_run)
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    wl.run_worker_llm("hi", plugin="memory-digest", purpose="digest-phase1")
    assert seen["kwargs"]["model"] == "mimo-v2.5"
    assert seen["kwargs"]["provider"] == "xiaomi"

    wl.run_worker_llm("hi", plugin="memory-digest", purpose="digest-dedup-patch")
    assert seen["kwargs"]["model"] == "kimi-k2.6"
    assert seen["kwargs"]["provider"] == "kimi-coding"

    wl.run_worker_llm("hi", plugin="memory-digest", purpose="digest-wrapup")
    assert seen["kwargs"]["model"] == "mimo-v2.5"
    assert seen["kwargs"]["provider"] == "xiaomi"

    wl.run_worker_llm("hi", plugin="memory-digest", purpose="recall_router")
    assert seen["kwargs"]["model"] == "mimo-v2.5"
    assert seen["kwargs"]["provider"] == "xiaomi"

    wl.run_worker_llm("hi", plugin="memory-weekly", purpose="ui_tighten")
    assert seen["kwargs"]["model"] == "MiniMax-M3"
    assert seen["kwargs"]["provider"] == "minimax-cn"


def test_record_worker_usage_fail_open(tmp_path, monkeypatch):
    wl = _load_worker_llm()

    monkeypatch.setattr(wl, "_ledger_path", lambda: tmp_path / "nope" / "x.jsonl")

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", boom)
    wl.record_worker_usage({"plugin": "x", "purpose": "y"})  # must not raise


def test_allowed_forced_worker_tool_names_includes_skips():
    wl = _load_worker_llm()
    catalog = [
        {"function": {"name": "submit_fact_block"}},
        {"function": {"name": "submit_event_block"}},
        {"function": {"name": "skip_digest_worker"}},
        {"function": {"name": "tool_search"}},
    ]
    allowed = wl.allowed_forced_worker_tool_names("submit_fact_block", catalog)
    assert allowed == {"submit_fact_block", "skip_digest_worker"}


def test_run_worker_llm_tools_binds_forced_tool_only(tmp_path, monkeypatch):
    """Forced workers must oneshot with one schema, not construct AIAgent."""
    wl = _load_worker_llm()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("XIAOMI_API_KEY", "test-key")
    monkeypatch.setenv("XIAOMI_BASE_URL", "https://example.test/v1")
    (tmp_path / "config.yaml").write_text(
        "plugins:\n  entries:\n    memory-digest:\n      provider: xiaomi\n      model: mimo-v2.5\n",
        encoding="utf-8",
    )
    seen: dict = {}
    catalog = [
        {"type": "function", "function": {"name": "submit_fact_block", "parameters": {"type": "object"}}},
        {"type": "function", "function": {"name": "submit_event_block", "parameters": {"type": "object"}}},
        {"type": "function", "function": {"name": "skip_digest_worker", "parameters": {"type": "object"}}},
        {"type": "function", "function": {"name": "tool_search", "parameters": {"type": "object"}}},
    ]

    fake_model_tools = types.ModuleType("model_tools")

    def fake_get_tool_definitions(*, enabled_toolsets=None, quiet_mode=False, skip_tool_search_assembly=False):
        seen["skip_tool_search_assembly"] = skip_tool_search_assembly
        seen["enabled_toolsets"] = list(enabled_toolsets or [])
        return list(catalog)

    fake_model_tools.get_tool_definitions = fake_get_tool_definitions
    monkeypatch.setitem(sys.modules, "model_tools", fake_model_tools)

    def boom(*_a, **_k):
        raise AssertionError("AIAgent must not be constructed for forced tools")

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = boom
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    tool_call = types.SimpleNamespace(
        function=types.SimpleNamespace(
            name="submit_fact_block",
            arguments='{"body":"x"}',
        )
    )
    _install_fake_openai(
        monkeypatch,
        seen=seen,
        message={"content": "", "tool_calls": [tool_call]},
    )

    out = wl.run_worker_llm_tools(
        "prompt",
        plugin="memory-digest",
        purpose="digest-fact-submit",
        enabled_toolsets=["memory_digest"],
        force_tool_name="submit_fact_block",
    )
    assert seen["skip_tool_search_assembly"] is True
    create = seen["create"]
    names = [(t.get("function") or {}).get("name") for t in create["tools"]]
    assert names == ["submit_fact_block"]
    assert create["tool_choice"] == {
        "type": "function",
        "function": {"name": "submit_fact_block"},
    }
    assert out["tool_name"] == "submit_fact_block"


def test_run_worker_llm_text_path_does_not_rebind_tools(tmp_path, monkeypatch):
    wl = _load_worker_llm()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    seen: dict = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            self.tools = [{"function": {"name": "should_stay"}}]
            self.valid_tool_names = {"should_stay"}
            self.model = "fake-model"
            self.session_input_tokens = 0
            self.session_output_tokens = 0
            self.session_cache_read_tokens = 0
            self.session_cache_write_tokens = 0
            self.session_reasoning_tokens = 0
            self.session_total_tokens = 0
            self.session_estimated_cost_usd = 0.0
            self.session_api_calls = 0

        def run_conversation(self, prompt):
            seen["tools"] = list(self.tools)
            return {"final_response": "ok"}

    fake_gateway_pkg = types.ModuleType("gateway")
    fake_gateway_run = types.ModuleType("gateway.run")
    fake_gateway_run._resolve_gateway_model = lambda: "fake-model"
    fake_gateway_run._resolve_runtime_agent_kwargs = lambda: {}
    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = FakeAgent
    monkeypatch.setitem(sys.modules, "gateway", fake_gateway_pkg)
    monkeypatch.setitem(sys.modules, "gateway.run", fake_gateway_run)
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    assert wl.run_worker_llm("hi", plugin="memory-weekly", purpose="text") == "ok"
    assert seen["tools"] == [{"function": {"name": "should_stay"}}]


def test_extract_tool_calls_from_fenced_json_in_content():
    """MiMo v2.5 often dumps tool JSON in assistant text instead of tool_calls."""
    wl = _load_worker_llm()
    result = {
        "final_response": "",
        "messages": [
            {
                "role": "assistant",
                "content": (
                    "```json\n"
                    '{"beginning": "a", "course": "b", "outcome": "c"}\n'
                    "```"
                ),
            }
        ],
    }
    found = wl.extract_tool_calls_from_result(
        result, default_tool_name="submit_tighten_event"
    )
    assert found == [
        ("submit_tighten_event", {"beginning": "a", "course": "b", "outcome": "c"})
    ]


def test_extract_tool_calls_flat_shape_and_broken_arguments_string():
    wl = _load_worker_llm()
    result = {
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "name": "submit_tighten_fact",
                        "arguments": '{"kind": "Factual", "content": "x",}',
                    }
                ],
            }
        ]
    }
    found = wl.extract_tool_calls_from_result(result)
    assert found == [("submit_tighten_fact", {"kind": "Factual", "content": "x"})]


def test_extract_tool_calls_drops_tool_describe_keeps_submit():
    """Gateway disclosure hops must not become worker1_summary's recorded tool."""
    wl = _load_worker_llm()
    result = {
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "function": {
                            "name": "tool_describe",
                            "arguments": '{"name": "submit_weekly_summary"}',
                        }
                    },
                    {
                        "function": {
                            "name": "submit_weekly_summary",
                            "arguments": '{"summary": [{"text": "Shipped wrap-up", "weekdays": ["Monday"]}]}',
                        }
                    },
                ],
            }
        ]
    }
    found = wl.extract_tool_calls_from_result(result)
    assert found == [
        (
            "submit_weekly_summary",
            {"summary": [{"text": "Shipped wrap-up", "weekdays": ["Monday"]}]},
        )
    ]


def test_run_worker_llm_tools_falls_back_when_callback_args_empty(tmp_path, monkeypatch):
    wl = _load_worker_llm()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("XIAOMI_API_KEY", "test-key")
    monkeypatch.setenv("XIAOMI_BASE_URL", "https://example.test/v1")
    (tmp_path / "config.yaml").write_text(
        "plugins:\n  entries:\n    memory-weekly:\n      provider: xiaomi\n      model: mimo-v2.5\n",
        encoding="utf-8",
    )
    seen: dict = {}
    fake_model_tools = types.ModuleType("model_tools")
    fake_model_tools.get_tool_definitions = lambda **_k: [
        {"type": "function", "function": {"name": "submit_tighten_event"}},
    ]
    monkeypatch.setitem(sys.modules, "model_tools", fake_model_tools)
    _install_fake_openai(
        monkeypatch,
        seen=seen,
        message={
            "content": '{"beginning": "a", "course": "b", "outcome": "c"}',
            "tool_calls": None,
        },
    )
    out = wl.run_worker_llm_tools(
        "prompt",
        plugin="memory-weekly",
        purpose="ui_tighten",
        enabled_toolsets=["memory_weekly"],
        force_tool_name="submit_tighten_event",
    )
    assert out["tool_name"] == "submit_tighten_event"
    assert out["tool_args"] == {"beginning": "a", "course": "b", "outcome": "c"}


def test_run_worker_llm_oneshot_skips_aiagent(tmp_path, monkeypatch):
    wl = _load_worker_llm()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("XIAOMI_API_KEY", "test-key")
    monkeypatch.setenv("XIAOMI_BASE_URL", "https://example.test/v1")
    (tmp_path / "config.yaml").write_text(
        "plugins:\n  entries:\n    memory-weekly:\n      provider: xiaomi\n      model: mimo-v2.5\n",
        encoding="utf-8",
    )
    seen: dict = {}
    tool_call = types.SimpleNamespace(
        function=types.SimpleNamespace(
            name="submit_tighten_event",
            arguments='{"beginning": "a", "course": "b", "outcome": "c"}',
        )
    )
    _install_fake_openai(
        monkeypatch,
        seen=seen,
        message={"content": "", "tool_calls": [tool_call]},
    )

    def boom(*_a, **_k):
        raise AssertionError("AIAgent must not be constructed for oneshot polish")

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = boom
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    out = wl.run_worker_llm_oneshot(
        "CURRENT_JSON:\n{}",
        plugin="memory-weekly",
        purpose="ui_tighten",
        force_tool_name="submit_tighten_event",
        tool_schema={
            "name": "submit_tighten_event",
            "description": "slots",
            "parameters": {"type": "object", "properties": {}},
        },
    )
    assert "run_agent" not in sys.modules or sys.modules["run_agent"].AIAgent is boom
    assert seen["client"]["api_key"] == "test-key"
    assert seen["client"]["base_url"] == "https://example.test/v1"
    create = seen["create"]
    assert create["model"] == "mimo-v2.5"
    assert create["tool_choice"] == {
        "type": "function",
        "function": {"name": "submit_tighten_event"},
    }
    assert create["tools"][0]["type"] == "function"
    assert create["tools"][0]["function"]["name"] == "submit_tighten_event"
    assert create["messages"][0]["role"] == "system"
    assert "text polisher" in create["messages"][0]["content"].lower()
    assert create["messages"][1]["role"] == "user"
    assert create["messages"][1]["content"] == "CURRENT_JSON:\n{}"
    assert out["tool_name"] == "submit_tighten_event"
    assert out["tool_args"] == {"beginning": "a", "course": "b", "outcome": "c"}
    assert out["failed"] is False
    ledger = tmp_path / "metrics" / "llm-usage.jsonl"
    rec = json.loads(ledger.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["purpose"] == "ui_tighten"
    assert rec["api_calls"] == 1
    assert rec["input_tokens"] == 12
    assert rec["cache_read_tokens"] == 4


def test_run_worker_llm_oneshot_parses_fenced_json_content(tmp_path, monkeypatch):
    wl = _load_worker_llm()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("XIAOMI_API_KEY", "test-key")
    (tmp_path / "config.yaml").write_text(
        "plugins:\n  entries:\n    memory-weekly:\n      provider: xiaomi\n      model: mimo-v2.5\n",
        encoding="utf-8",
    )
    seen: dict = {}
    _install_fake_openai(
        monkeypatch,
        seen=seen,
        message={
            "content": '```json\n{"text": "Keep this."}\n```',
            "tool_calls": None,
        },
    )
    out = wl.run_worker_llm_oneshot(
        "CURRENT_JSON:\n{}",
        plugin="memory-weekly",
        purpose="ui_tighten",
        force_tool_name="submit_tighten_text",
        tool_schema={
            "name": "submit_tighten_text",
            "description": "text",
            "parameters": {"type": "object", "properties": {"text": {"type": "string"}}},
        },
    )
    assert out["tool_name"] == "submit_tighten_text"
    assert out["tool_args"] == {"text": "Keep this."}
