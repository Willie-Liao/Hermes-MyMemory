from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ACTIONS = Path(__file__).with_name("weekly_actions.py")
TOOLS = Path(__file__).with_name("tighten_tools.py")


def _load():
    spec = importlib.util.spec_from_file_location("weekly_actions_tighten", ACTIONS)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_tools():
    spec = importlib.util.spec_from_file_location("tighten_tools_test", TOOLS)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_infer_kind_from_body_prefixes():
    tools = _load_tools()
    assert tools.infer_tighten_kind(
        "Beginning: a; Course: b; Outcome: c"
    ) == "event"
    assert tools.infer_tighten_kind("Obstacle: x; Solution: y") == "procedure"
    assert tools.infer_tighten_kind("Decision: user ship it") == "decision"
    assert tools.infer_tighten_kind("Factual: parents live in HK") == "fact"
    assert tools.infer_tighten_kind("## Path Discipline\nKeep gates.") == "text"
    assert tools.infer_tighten_kind("Obstacle: x; Solution: y", "event") == "event"


def test_render_event_slots():
    tools = _load_tools()
    assert tools.render_tighten_args(
        "event",
        {"beginning": "asked", "course": "ran digest", "outcome": "shipped"},
    ) == "Beginning: asked; Course: ran digest; Outcome: shipped"


def test_tighten_blank_guidance_defaults():
    wa = _load()
    out = wa.tighten_hot_entry(
        text="## Note\nKeep this.",
        guidance="   ",
        call_tools=lambda *_a, **_k: {
            "tool_name": "submit_tighten_text",
            "tool_args": {"text": "Keep this."},
        },
    )
    assert out["tightened"] == "Keep this."
    assert out["kind"] == "text"


def test_tighten_rejects_blank_text():
    wa = _load()
    with pytest.raises(ValueError, match="text"):
        wa.tighten_hot_entry(
            text="  ",
            guidance="Cut by half",
            call_tools=lambda *_a, **_k: {"tool_name": "submit_tighten_text", "tool_args": {"text": "x"}},
        )


def test_tighten_event_tool_renders_body():
    wa = _load()
    captured = {}

    def fake_tools(prompt: str, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return {
            "tool_name": "submit_tighten_event",
            "tool_args": {
                "beginning": "User asked tighten",
                "course": "Worker used JSON slots",
                "outcome": "Body rendered",
            },
        }

    out = wa.tighten_hot_entry(
        text="Beginning: long start; Course: long middle; Outcome: long end",
        guidance="Keep trigger + one sentence",
        call_tools=fake_tools,
    )
    assert out["kind"] == "event"
    assert out["tightened"] == (
        "Beginning: User asked tighten; Course: Worker used JSON slots; Outcome: Body rendered"
    )
    assert captured["kwargs"]["force_tool_name"] == "submit_tighten_event"
    assert "submit_tighten_event" in captured["prompt"]
    assert "Keep trigger + one sentence" in captured["prompt"]
    assert '"beginning"' in captured["prompt"]
    assert captured["prompt"].index("CURRENT_JSON:") < captured["prompt"].index(
        "OPERATOR GUIDANCE:"
    )


def test_tighten_fact_and_procedure_and_decision():
    wa = _load()
    fact = wa.tighten_hot_entry(
        text="Factual: a very long note",
        entry_type="fact",
        call_tools=lambda *_a, **_k: {
            "tool_name": "submit_tighten_fact",
            "tool_args": {"kind": "Factual", "content": "short note"},
        },
    )
    assert fact["tightened"] == "Factual: short note"
    proc = wa.tighten_hot_entry(
        text="Obstacle: x; Solution: y",
        call_tools=lambda *_a, **_k: {
            "tool_name": "submit_tighten_procedure",
            "tool_args": {"obstacle": "empty body", "solution": "use slots"},
        },
    )
    assert proc["tightened"] == "Obstacle: empty body; Solution: use slots"
    decision = wa.tighten_hot_entry(
        text="Decision: user keep yaml",
        call_tools=lambda *_a, **_k: {
            "tool_name": "submit_tighten_decision",
            "tool_args": {
                "kind": "Decision",
                "subject": "user",
                "ruling": "keep YAML slots",
            },
        },
    )
    assert decision["tightened"] == "Decision: user keep YAML slots"


def test_tighten_failed_llm_surfaces_message():
    wa = _load()
    with pytest.raises(ValueError, match="authentication method"):
        wa.tighten_hot_entry(
            text="Beginning: a; Course: b; Outcome: c",
            call_tools=lambda *_a, **_k: {
                "tool_name": None,
                "tool_args": None,
                "failed": True,
                "final_response": (
                    '"Could not resolve authentication method. Expected either '
                    'api_key or auth_token to be set."'
                ),
            },
        )
    wa = _load()
    with pytest.raises(ValueError, match="empty slots"):
        wa.tighten_hot_entry(
            text="Beginning: a; Course: b; Outcome: c",
            call_tools=lambda *_a, **_k: {
                "tool_name": "submit_tighten_event",
                "tool_args": {},
            },
        )


def test_tighten_event_recovers_nested_and_prefixed_payloads():
    wa = _load()
    nested = wa.tighten_hot_entry(
        text="Beginning: a; Course: b; Outcome: c",
        call_tools=lambda *_a, **_k: {
            "tool_name": "submit_tighten_event",
            "tool_args": {
                "arguments": json.dumps(
                    {"Beginning": "asked", "Course": "ran", "Outcome": "shipped"}
                )
            },
        },
    )
    assert nested["tightened"] == (
        "Beginning: asked; Course: ran; Outcome: shipped"
    )
    from_text = wa.tighten_hot_entry(
        text="Beginning: a; Course: b; Outcome: c",
        call_tools=lambda *_a, **_k: {
            "tool_name": "submit_tighten_event",
            "tool_args": {},
            "final_response": "Beginning: short ask; Course: short run; Outcome: short ship",
        },
    )
    assert from_text["tightened"] == (
        "Beginning: short ask; Course: short run; Outcome: short ship"
    )
    wrapped = wa.tighten_hot_entry(
        text="Beginning: a; Course: b; Outcome: c",
        call_tools=lambda *_a, **_k: {
            "tool_name": "submit_tighten_event",
            "tool_args": {
                "events": [
                    {
                        "beginning": "asked",
                        "course": "ran",
                        "outcome": "shipped",
                    }
                ]
            },
        },
    )
    assert wrapped["tightened"] == (
        "Beginning: asked; Course: ran; Outcome: shipped"
    )
    from_msg = wa.tighten_hot_entry(
        text="Beginning: a; Course: b; Outcome: c",
        call_tools=lambda *_a, **_k: {
            "tool_name": "submit_tighten_event",
            "tool_args": {"beginning": "", "course": "", "outcome": ""},
            "final_response": "",
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        "Beginning: msg ask\nCourse: msg run\nOutcome: msg ship"
                    ),
                }
            ],
        },
    )
    assert from_msg["tightened"] == (
        "Beginning: msg ask; Course: msg run; Outcome: msg ship"
    )


def test_tighten_identity_event_fails():
    wa = _load()
    body = "Beginning: a; Course: b; Outcome: c"
    with pytest.raises(ValueError, match="no change"):
        wa.tighten_hot_entry(
            text=body,
            call_tools=lambda *_a, **_k: {
                "tool_name": "submit_tighten_event",
                "tool_args": {
                    "beginning": "a",
                    "course": "b",
                    "outcome": "c",
                },
            },
        )


def test_tighten_feeds_phase1_slots_not_prefixed_body():
    wa = _load()
    captured = {}

    def fake_tools(prompt: str, **kwargs):
        captured["prompt"] = prompt
        return {
            "tool_name": "submit_tighten_decision",
            "tool_args": {"ruling": "ship JSON slots"},
        }

    out = wa.tighten_hot_entry(
        text="Decision: user keep yaml prefixes",
        call_tools=fake_tools,
    )
    assert out["tightened"] == "Decision: user ship JSON slots"
    assert "CURRENT_JSON:" in captured["prompt"]
    assert '"kind": "Decision"' in captured["prompt"]
    assert '"subject": "user"' in captured["prompt"]
    assert "Decision: user keep yaml prefixes" not in captured["prompt"]


def test_tighten_rejects_empty_model_output():
    wa = _load()
    with pytest.raises(ValueError, match="empty"):
        wa.tighten_hot_entry(
            text="Entry",
            guidance="Tighten",
            call_tools=lambda *_a, **_k: {
                "tool_name": "submit_tighten_text",
                "tool_args": {"text": "  "},
            },
        )


def test_merge_mode_uses_reason_and_actions():
    wa = _load()
    captured = {}

    def fake_tools(prompt: str, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return {
            "tool_name": "submit_tighten_text",
            "tool_args": {"text": "Merged entry text"},
        }

    out = wa.tighten_hot_entry(
        mode="merge",
        source_text="A about Alice",
        peer_text="A about Alice and Bob",
        reason="Overlap on Alice",
        actions=["Keep dates", "Drop duplicate"],
        source_ref="USER.md [1]",
        peer_ref="MEMORY.md [2]",
        call_tools=fake_tools,
    )
    assert out == {"tightened": "Merged entry text", "kind": "text"}
    assert captured["kwargs"]["force_tool_name"] == "submit_tighten_text"
    assert "Overlap on Alice" in captured["prompt"]
    assert "Keep dates" in captured["prompt"]
    assert "USER.md [1]" in captured["prompt"]
    assert "MEMORY.md [2]" in captured["prompt"]
    assert "HERMES.md FORMAT" not in captured["prompt"]


def test_merge_mode_hermes_prompt_requires_nested_heading_hierarchy():
    wa = _load()
    captured = {}

    def fake_tools(prompt: str, **kwargs):
        captured["prompt"] = prompt
        return {
            "tool_name": "submit_tighten_text",
            "tool_args": {
                "text": "## Path Discipline\n\nKeep hot writes gated.\n\n### Hook Authority\n\nShell hook is the write-gate."
            },
        }

    out = wa.tighten_hot_entry(
        mode="merge",
        source_text="## Path Discipline\n\nKeep hot writes gated.",
        peer_text="## Hook Authority\n\nShell hook is the write-gate.",
        reason="Overlap on path rules",
        actions=["Nest peer under parent"],
        source_ref="HERMES.md [3]",
        peer_ref="HERMES.md [4]",
        call_tools=fake_tools,
    )
    assert out["tightened"].startswith("## Path Discipline")
    prompt = captured["prompt"]
    assert "HERMES.md FORMAT" in prompt
    assert "ONE top-level ## heading" in prompt
    assert "Nest peer content under the parent using ###" in prompt
    assert "Never emit a second ## heading" in prompt
    assert "Regenerate a clean heading hierarchy" in prompt


def test_merge_mode_rejects_missing_texts():
    wa = _load()
    with pytest.raises(ValueError, match="source_text|peer_text|peer_entries"):
        wa.tighten_hot_entry(
            mode="merge",
            source_text="",
            peer_text="peer",
            reason="x",
            call_tools=lambda *_a, **_k: {
                "tool_name": "submit_tighten_text",
                "tool_args": {"text": "x"},
            },
        )


def test_merge_mode_accepts_multiple_peer_entries():
    wa = _load()
    captured = {}

    def fake_tools(prompt: str, **kwargs):
        captured["prompt"] = prompt
        return {
            "tool_name": "submit_tighten_text",
            "tool_args": {"text": "Merged parent"},
        }

    out = wa.tighten_hot_entry(
        mode="merge",
        source_text="Parent A",
        peer_entries=[
            {"ref": "USER.md [1]", "text": "Peer one"},
            {"ref": "USER.md [2]", "text": "Peer two"},
        ],
        reason="Scattered overlap",
        actions=["Absorb both"],
        source_ref="USER.md [0]",
        call_tools=fake_tools,
    )
    assert out == {"tightened": "Merged parent", "kind": "text"}
    prompt = captured["prompt"]
    assert "PEER 1 — USER.md [1]:" in prompt
    assert "Peer one" in prompt
    assert "PEER 2 — USER.md [2]:" in prompt
    assert "Peer two" in prompt
    assert "Combine them into ONE concise entry" in prompt


def test_merge_mode_multi_peer_hermes_heading_levels():
    wa = _load()
    captured = {}

    def fake_tools(prompt: str, **kwargs):
        captured["prompt"] = prompt
        return {
            "tool_name": "submit_tighten_text",
            "tool_args": {"text": "## Parent\n\n### Peer A\n\n#### Nested\n"},
        }

    wa.tighten_hot_entry(
        mode="merge",
        source_text="## Parent\n\nBody",
        peer_entries=[
            {"ref": "HERMES.md [1]", "text": "## Peer A\n\nA"},
            {"ref": "HERMES.md [2]", "text": "## Peer B\n\nB"},
        ],
        reason="overlap",
        source_ref="HERMES.md [0]",
        call_tools=fake_tools,
    )
    prompt = captured["prompt"]
    assert "HERMES.md FORMAT" in prompt
    assert "ONE top-level ## heading" in prompt
    assert "###" in prompt
    assert "####" in prompt
    assert "Never emit a second ## heading" in prompt


def test_tighten_default_path_uses_oneshot_not_hermes(monkeypatch):
    wa = _load()
    seen: dict = {}
    fake = types.ModuleType("worker_llm")

    def oneshot(prompt: str, **kwargs):
        seen["prompt"] = prompt
        seen["kwargs"] = kwargs
        return {
            "tool_name": kwargs["force_tool_name"],
            "tool_args": {"text": "Keep this."},
        }

    def boom(*_a, **_k):
        raise AssertionError("Hermes run_worker_llm_tools must not run for tighten")

    fake.run_worker_llm_oneshot = oneshot
    fake.run_worker_llm_tools = boom
    monkeypatch.setitem(sys.modules, "worker_llm", fake)
    out = wa.tighten_hot_entry(text="## Note\nKeep this long.")
    assert out == {"tightened": "Keep this.", "kind": "text"}
    assert seen["kwargs"]["plugin"] == "memory-weekly"
    assert seen["kwargs"]["purpose"] == "ui_tighten"
    assert seen["kwargs"]["force_tool_name"] == "submit_tighten_text"
    assert seen["kwargs"]["tool_schema"]["name"] == "submit_tighten_text"
    assert "CURRENT_JSON:" in seen["prompt"]


def test_merge_default_path_uses_oneshot_not_hermes(monkeypatch):
    wa = _load()
    seen: dict = {}
    fake = types.ModuleType("worker_llm")

    def oneshot(prompt: str, **kwargs):
        seen["prompt"] = prompt
        seen["kwargs"] = kwargs
        return {
            "tool_name": "submit_tighten_text",
            "tool_args": {"text": "Merged Alice note"},
        }

    fake.run_worker_llm_oneshot = oneshot
    fake.run_worker_llm_tools = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("Hermes run_worker_llm_tools must not run for merge")
    )
    monkeypatch.setitem(sys.modules, "worker_llm", fake)
    out = wa.tighten_hot_entry(
        mode="merge",
        source_text="A about Alice",
        peer_text="A about Alice and Bob",
        reason="Overlap on Alice",
        source_ref="USER.md [1]",
        peer_ref="MEMORY.md [2]",
    )
    assert out == {"tightened": "Merged Alice note", "kind": "text"}
    assert seen["kwargs"]["force_tool_name"] == "submit_tighten_text"
    assert "SOURCE — USER.md [1]:" in seen["prompt"]
    assert "PEER — MEMORY.md [2]:" in seen["prompt"]


def test_tool_schema_for_kind_matches_force_tool():
    tools = _load_tools()
    assert tools.tool_schema_for_kind("event")["name"] == "submit_tighten_event"
    assert tools.tool_schema_for_kind("text")["name"] == "submit_tighten_text"
