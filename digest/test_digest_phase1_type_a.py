"""Phase-1 type A: flat handlers, compact transform, same-turn repair, dirty importance 2."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
PLUGIN_ROOT = ROOT.parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load(name: str):
    path = ROOT / f"{name}.py"
    mod_name = f"memory_digest_{name}_phase1_type_a"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def _good_flat_fact() -> dict:
    return {
        "blocks": [
            {
                "type": "fact",
                "entity": "Jordan",
                "kind": "Narration",
                "content": "Jordan and User planned a lunch.",
                "involves": [
                    {"entity": "Jordan"},
                    {"entity": "User"},
                ],
                "confidence": "high",
                "importance": 3,
            }
        ]
    }


def test_handle_submit_digest_blocks_ok_and_teach():
    tools = _load("digest_tools")
    tools.reset_phase1_turn_state(session_id="s1")
    bad = tools.handle_submit_digest_blocks({"blocks": [{"type": "fact"}]})
    bad_payload = json.loads(bad)
    assert bad_payload["ok"] is False
    assert bad_payload["errors"]
    assert "patch_digest_blocks" in bad_payload["teach"]
    assert "run_worker_llm" not in bad

    good = tools.handle_submit_digest_blocks(_good_flat_fact())
    good_payload = json.loads(good)
    assert good_payload["ok"] is True
    assert good_payload["errors"] == []
    assert good_payload["args"]["blocks"][0]["type"] == "fact"


def test_handle_patch_digest_blocks_merges_and_accepts():
    tools = _load("digest_tools")
    tools.reset_phase1_turn_state(session_id="s1")
    tools.handle_submit_digest_blocks(
        {
            "blocks": [
                {
                    "type": "fact",
                    "entity": "Jordan",
                    "kind": "Factual",
                    "content": "x",
                    "confidence": "high",
                    "importance": 3,
                }
            ]
        }
    )
    # Narration with 0 involves fails; patch to valid Narration+involves.
    out = tools.handle_patch_digest_blocks(
        {
            "blocks": _good_flat_fact()["blocks"],
        }
    )
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["args"]["blocks"][0]["kind"] == "Narration"


def test_transform_phase1_ok_compact_fail_teach_and_recall_passthrough():
    tools = _load("digest_tools")
    digest = _load("digest")
    tools.reset_phase1_turn_state(session_id="s-transform")
    ok_json = tools.handle_submit_digest_blocks(_good_flat_fact())
    compact_out = tools.transform_phase1_tool_result(
        "submit_digest_blocks", ok_json, session_id="s-transform"
    )
    assert compact_out == json.dumps({"ok": True})
    assert "type: fact" not in compact_out
    assert "Jordan" not in compact_out
    assert "---" not in compact_out

    fail_json = tools.phase1_handler_payload(
        ok=False,
        errors=["blocks[0]: content must be non-empty"],
        teach="VALIDATION FAILED — call patch_digest_blocks",
    )
    teach_out = tools.transform_phase1_tool_result(
        "patch_digest_blocks", fail_json, session_id="s-transform"
    )
    assert teach_out is not None
    assert "VALIDATION FAILED" in teach_out
    assert "patch_digest_blocks" in teach_out

    # Non-phase1 tools leave transform to recall path (None from phase1 helper).
    assert tools.transform_phase1_tool_result("search_files", "{}") is None
    # Hook still returns None for unrelated when no recall pending.
    assert (
        digest.on_transform_session_search_recall(
            tool_name="unrelated_tool",
            result="ok",
            session_id="",
        )
        is None
    )


def test_phase1_same_turn_repair_without_outer_patch_prompt(tmp_path, monkeypatch):
    digest = _load("digest")
    tools = _load("digest_tools")
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(digest, "digest_tools", tools)
    bad = {"blocks": [{"type": "fact", "entity": "Jordan"}]}
    good = _good_flat_fact()
    invoke_count = {"n": 0}

    def fake_tool(
        prompt,
        platform,
        *,
        purpose="",
        force_tool_name="",
        allowed_tool_names=None,
        max_iterations=2,
        **_k,
    ):
        invoke_count["n"] += 1
        assert allowed_tool_names == tools.tool_names_for_phase1()
        assert force_tool_name in ("", None)
        assert "digest-phase1-patch" not in purpose
        return {
            "tool_name": "patch_digest_blocks",
            "tool_args": good,
            "tool_calls": [
                ("submit_digest_blocks", bad),
                ("patch_digest_blocks", good),
            ],
            "messages": [],
        }

    monkeypatch.setattr(digest, "_invoke_digest_worker_tool", fake_tool)
    result = digest.run_phase1_digest_blocks(
        "s1",
        "cli",
        "Jordan and User planned lunch",
        run_id="run-a",
    )
    assert invoke_count["n"] == 1
    assert isinstance(result, digest.ValidatedWorkerResult)
    assert result.accepted_dirty is False
    assert any(b.get("type") == "fact" for b in result.blocks)


def test_run_phase1_persist_uses_sanitized_capture(tmp_path, monkeypatch):
    digest = _load("digest")
    tools = _load("digest_tools")
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(digest, "digest_tools", tools)
    long_content = "Z" * 600
    payload = {
        "blocks": [
            {
                "type": "event",
                "temp_id": "tmp-e1",
                "entity": "Project",
                "predicate": "user_requested_review",
                "participants": [
                    {"entity": "User", "role": "requester"},
                    {"entity": "Assistant", "role": "executor"},
                ],
                "beginning": "user requested review",
                "course": "assistant reviewed sources",
                "outcome": "draft delivered",
                "confidence": "high",
                "importance": 4,
                "related": ["tmp-e2"],
            },
            {
                "type": "event",
                "temp_id": "tmp-e2",
                "entity": "Project",
                "predicate": "user_requested_followup",
                "participants": [
                    {"entity": "User", "role": "requester"},
                    {"entity": "Assistant", "role": "executor"},
                ],
                "beginning": "follow-up started",
                "course": "assistant continued",
                "outcome": "still open",
                "confidence": "high",
                "importance": 4,
            },
            {
                "type": "fact",
                "entity": "Topic",
                "kind": "Factual",
                "content": long_content,
                "confidence": "high",
                "importance": 3,
            },
        ]
    }

    def fake_tool(*_a, **_k):
        return {
            "tool_name": "submit_digest_blocks",
            "tool_args": payload,
            "tool_calls": [("submit_digest_blocks", payload)],
            "messages": [],
        }

    monkeypatch.setattr(digest, "_invoke_digest_worker_tool", fake_tool)
    result = digest.run_phase1_digest_blocks(
        "s-san",
        "cli",
        "transcript",
        run_id="r-san",
        message_start_id=64989,
        message_end_id=65117,
    )
    assert isinstance(result, digest.ValidatedWorkerResult)
    events = [b for b in result.blocks if b.get("type") == "event"]
    facts = [b for b in result.blocks if b.get("type") == "fact"]
    assert len(events) == 2
    locator = "session s-san#64989-65117"
    for block in result.blocks:
        sources = block.get("sources") or []
        assert locator in sources
        assert all(not str(s).startswith("transcript") for s in sources)
    for event in events:
        related = event.get("related") or []
        assert not any("event" in str(r) for r in related)
    assert facts
    assert len(str(facts[0].get("body") or facts[0].get("content") or "")) <= 500
    assert "Z" * 600 not in str(facts[0])


def test_accept_phase1_args_truncates_and_strips_event_related():
    tools = _load("digest_tools")
    bag, errors, notes = tools.accept_phase1_args(
        {
            "blocks": [
                {
                    "type": "event",
                    "temp_id": "tmp-e1",
                    "entity": "Project",
                    "predicate": "user_requested_review",
                    "participants": [
                        {"entity": "User", "role": "requester"},
                        {"entity": "Assistant", "role": "executor"},
                    ],
                    "beginning": "user requested review",
                    "course": "assistant reviewed sources",
                    "outcome": "draft delivered",
                    "confidence": "high",
                    "importance": 4,
                    "related": ["tmp-e2"],
                },
                {
                    "type": "event",
                    "temp_id": "tmp-e2",
                    "entity": "Project",
                    "predicate": "user_requested_followup",
                    "participants": [
                        {"entity": "User", "role": "requester"},
                        {"entity": "Assistant", "role": "executor"},
                    ],
                    "beginning": "a",
                    "course": "b",
                    "outcome": "c",
                    "confidence": "high",
                    "importance": 4,
                },
                {
                    "type": "fact",
                    "entity": "Topic",
                    "kind": "Factual",
                    "content": "Q" * 600,
                    "confidence": "high",
                    "importance": 3,
                },
            ]
        }
    )
    assert errors == []
    assert any("truncated" in n for n in notes)
    assert any("stripped" in n for n in notes)
    event = bag["blocks"][0]
    assert event.get("related") in (None, [])
    assert "tmp-e2" not in (event.get("related") or [])
    assert len(bag["blocks"][2]["content"]) == tools.SLOT_MAX_LENGTH["content"]


def test_phase1_exhaust_clamps_importance_to_2(tmp_path, monkeypatch):
    digest = _load("digest")
    tools = _load("digest_tools")
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(digest, "digest_tools", tools)
    almost = {
        "blocks": [
            {
                "type": "fact",
                "entity": "Topic",
                "kind": "Factual",
                "content": "",
                "confidence": "high",
                "importance": 3,
            }
        ]
    }

    def fake_tool(
        prompt,
        platform,
        *,
        purpose="",
        force_tool_name="",
        allowed_tool_names=None,
        max_iterations=2,
        **_k,
    ):
        return {
            "tool_name": "submit_digest_blocks",
            "tool_args": almost,
            "tool_calls": [
                ("submit_digest_blocks", almost),
                ("patch_digest_blocks", almost),
                ("patch_digest_blocks", almost),
            ],
            "messages": [],
        }

    monkeypatch.setattr(digest, "_invoke_digest_worker_tool", fake_tool)
    result = digest.run_phase1_digest_blocks(
        "s1",
        "cli",
        "topic note",
        run_id="run-dirty",
    )
    assert isinstance(result, digest.ValidatedWorkerResult)
    assert result.accepted_dirty is True
    assert result.attempts == tools.PHASE1_MAX_VALIDATION_ATTEMPTS
    assert all(
        int(b.get("importance")) == tools.IMPORTANCE_DIRTY for b in result.blocks
    )
    log = digest._log_file().read_text(encoding="utf-8")
    assert "worker_accepted_dirty type=phase1" in log
    assert f"importance={tools.IMPORTANCE_DIRTY}" in log


def test_clamp_blocks_importance_dirty_helper():
    tools = _load("digest_tools")
    clamped = tools.clamp_blocks_importance_dirty(_good_flat_fact())
    assert clamped["blocks"][0]["importance"] == tools.IMPORTANCE_DIRTY


def _event_block(**overrides: object) -> dict:
    base = {
        "type": "event",
        "entity": "MemoryDigest",
        "predicate": "user_requested_x",
        "participants": [
            {"entity": "User", "role": "requester"},
            {"entity": "Assistant", "role": "executor"},
        ],
        "beginning": "User asked",
        "course": "Assistant looked",
        "outcome": "Found it",
        "confidence": "high",
        "importance": 3,
        "valid_from": "2026-08-13",
        "valid_to": "open",
    }
    base.update(overrides)
    return base


def test_handler_truncates_overlength_slot_and_stays_ok():
    tools = _load("digest_tools")
    tools.reset_phase1_turn_state(session_id="s1")
    long_beginning = "B" * 157
    payload = json.loads(
        tools.handle_submit_digest_blocks(
            {
                "blocks": [
                    _event_block(beginning=long_beginning),
                ]
            }
        )
    )
    assert payload["ok"] is True
    stored = payload["args"]["blocks"][0]["beginning"]
    assert len(stored) == 156
    assert "teach" not in payload


def test_handler_keeps_first_sentence_of_event_stages():
    tools = _load("digest_tools")
    tools.reset_phase1_turn_state(session_id="s1")
    payload = json.loads(
        tools.handle_submit_digest_blocks(
            {
                "blocks": [
                    _event_block(
                        beginning="User asked for a review. Extra detail about files.",
                        course="Assistant read the sources. Then wrote a draft.",
                        outcome="Draft was delivered. More chatter followed.",
                    )
                ]
            }
        )
    )
    assert payload["ok"] is True
    event = payload["args"]["blocks"][0]
    assert event["beginning"] == "User asked for a review."
    assert event["course"] == "Assistant read the sources."
    assert event["outcome"] == "Draft was delivered."
    assert "teach" not in payload
    tools = _load("digest_tools")
    tools.reset_phase1_turn_state(session_id="s1")
    payload = json.loads(
        tools.handle_submit_digest_blocks(
            {
                "blocks": [
                    _event_block(temp_id="e1"),
                    _event_block(
                        temp_id="e2",
                        related=["e1", "f2", "mem-2026-08-13-event-ABCDEF12"],
                    ),
                    {
                        "type": "fact",
                        "temp_id": "f2",
                        "entity": "X",
                        "kind": "Factual",
                        "content": "ok",
                        "confidence": "high",
                        "importance": 3,
                    },
                ]
            }
        )
    )
    assert payload["ok"] is True
    related = payload["args"]["blocks"][1]["related"]
    assert related == ["f2"]
    assert "teach" not in payload


def test_handler_still_rejects_empty_slots():
    tools = _load("digest_tools")
    tools.reset_phase1_turn_state(session_id="s1")
    payload = json.loads(
        tools.handle_submit_digest_blocks({"blocks": [{"type": "fact"}]})
    )
    assert payload["ok"] is False
    assert payload["errors"]
    assert "patch_digest_blocks" in payload["teach"]
    assert any("content" in str(err) for err in payload["errors"])


def _fact_block(**overrides: object) -> dict:
    base = {
        "type": "fact",
        "entity": "Topic",
        "kind": "Factual",
        "content": "A durable note.",
        "confidence": "high",
        "importance": 3,
    }
    base.update(overrides)
    return base


def test_accept_flattens_nested_type_object():
    tools = _load("digest_tools")
    bag, errors, notes = tools.accept_phase1_args(
        {
            "blocks": [
                {
                    "type": "fact",
                    "fact": {
                        "entity": "Topic",
                        "kind": "Factual",
                        "content": "Nested payload.",
                        "confidence": "high",
                        "importance": 3,
                    },
                }
            ]
        }
    )
    assert errors == []
    assert any("flattened" in n for n in notes)
    assert "fact" not in bag["blocks"][0] or not isinstance(
        bag["blocks"][0].get("fact"), dict
    )
    assert bag["blocks"][0]["content"] == "Nested payload."
    assert bag["blocks"][0]["type"] == "fact"


def test_accept_defaults_kind_and_importance():
    tools = _load("digest_tools")
    bag, errors, notes = tools.accept_phase1_args(
        {
            "blocks": [
                _fact_block(kind="Story", importance=9),
                {
                    "type": "decision",
                    "kind": "Nope",
                    "subject": "user",
                    "ruling": "user wants tea",
                    "confidence": "high",
                    "importance": "high",
                },
            ]
        }
    )
    assert errors == []
    assert bag["blocks"][0]["kind"] == "Factual"
    assert bag["blocks"][0]["importance"] == 3
    assert bag["blocks"][1]["kind"] == "Decision"
    assert bag["blocks"][1]["importance"] == 3
    assert bag["blocks"][1]["ruling"] == "wants tea"
    assert any("kind defaulted" in n for n in notes)


def test_accept_keeps_create_importance_one_through_five():
    """Create-time scores use the full 1–5 scale; 0 still falls back to default 3."""
    tools = _load("digest_tools")
    kept = []
    for n in (1, 2, 3, 4, 5):
        bag, errors, _notes = tools.accept_phase1_args(
            {"blocks": [_fact_block(importance=n)]}
        )
        assert errors == []
        kept.append(bag["blocks"][0]["importance"])
    assert kept == [1, 2, 3, 4, 5]

    zero, errors, notes = tools.accept_phase1_args(
        {"blocks": [_fact_block(importance=0)]}
    )
    assert errors == []
    assert zero["blocks"][0]["importance"] == tools.IMPORTANCE_DEFAULT
    assert any("importance" in n for n in notes)


def test_accept_injects_event_roles():
    tools = _load("digest_tools")
    bag, errors, notes = tools.accept_phase1_args(
        {
            "blocks": [
                _event_block(participants=[{"entity": "Jordan", "role": "guest"}])
            ]
        }
    )
    assert errors == []
    roles = {
        (p["entity"], p["role"])
        for p in bag["blocks"][0]["participants"]
        if isinstance(p, dict)
    }
    assert ("User", "requester") in roles
    assert ("Assistant", "executor") in roles
    assert any("injected" in n for n in notes)


def test_accept_factual_two_involves_becomes_narration():
    tools = _load("digest_tools")
    bag, errors, _notes = tools.accept_phase1_args(
        {
            "blocks": [
                _fact_block(
                    kind="Factual",
                    involves=[{"entity": "Jordan"}, {"entity": "User"}],
                    content="Jordan and User planned lunch.",
                )
            ]
        }
    )
    assert errors == []
    assert bag["blocks"][0]["kind"] == "Narration"


def test_accept_remints_duplicate_temp_id():
    tools = _load("digest_tools")
    bag, errors, notes = tools.accept_phase1_args(
        {
            "blocks": [
                _event_block(temp_id="tmp-dup"),
                _event_block(temp_id="tmp-dup", predicate="user_requested_y"),
            ]
        }
    )
    assert errors == []
    ids = [b["temp_id"] for b in bag["blocks"]]
    assert ids[0] == "tmp-dup"
    assert ids[1] != "tmp-dup"
    assert any("reminted" in n for n in notes)


def test_accept_narration_with_zero_involves_still_teaches():
    tools = _load("digest_tools")
    _bag, errors, _notes = tools.accept_phase1_args(
        {"blocks": [_fact_block(kind="Narration", content="A story with no cast.")]}
    )
    assert errors
    assert any("involves" in e.lower() or "Narration" in e for e in errors)


def test_accept_strips_duplicate_decision_subject_from_ruling():
    tools = _load("digest_tools")
    bag, errors, notes = tools.accept_phase1_args(
        {
            "blocks": [
                {
                    "type": "decision",
                    "kind": "Decision",
                    "subject": "user",
                    "ruling": "user must not auto-drop events",
                    "confidence": "high",
                    "importance": 3,
                }
            ]
        }
    )
    assert errors == []
    assert bag["blocks"][0]["ruling"] == "must not auto-drop events"
    assert any("stripped leading subject" in n for n in notes)


def test_accept_jordan_as_ruling_agent_still_teaches():
    tools = _load("digest_tools")
    _bag, errors, _notes = tools.accept_phase1_args(
        {
            "blocks": [
                {
                    "type": "decision",
                    "kind": "Decision",
                    "subject": "user",
                    "ruling": "Jordan dislikes cilantro",
                    "confidence": "high",
                    "importance": 3,
                }
            ]
        }
    )
    assert errors
    assert any(
        "predicate" in e.lower() or "third party" in e.lower() or "Narration" in e
        for e in errors
    )


def test_accept_empty_decision_ruling_still_teaches():
    tools = _load("digest_tools")
    _bag, errors, _notes = tools.accept_phase1_args(
        {
            "blocks": [
                {
                    "type": "decision",
                    "kind": "Decision",
                    "subject": "user",
                    "ruling": "",
                    "confidence": "high",
                    "importance": 3,
                }
            ]
        }
    )
    assert errors
    assert any("ruling" in e for e in errors)
