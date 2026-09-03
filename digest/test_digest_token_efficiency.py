"""Token-efficiency redesign: tool schemas, merge patch, dry-run, decay, caps."""

from __future__ import annotations

import importlib.util
import inspect
import json
import sys
from pathlib import Path

_PLUGIN = Path(__file__).resolve().parent
if str(_PLUGIN) not in sys.path:
    sys.path.insert(0, str(_PLUGIN))


def _load(name: str):
    path = _PLUGIN / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


digest_tools = _load("digest_tools")
composition = _load("composition")


def test_merge_field_patch_overwrites_only_present_keys():
    previous = {"entity": "memory-digest", "confidence": "maybe", "importance": 3}
    merged = digest_tools.merge_field_patch(previous, {"confidence": "high"})
    assert merged == {
        "entity": "memory-digest",
        "confidence": "high",
        "importance": 3,
    }


def test_submit_and_patch_schemas_enums():
    submit = digest_tools.submit_schema("event")
    assert submit["name"] == "submit_event_block"
    conf = submit["parameters"]["properties"]["confidence"]
    assert conf["enum"] == list(digest_tools.CONFIDENCE_ENUM)
    imp = submit["parameters"]["properties"]["importance"]
    assert digest_tools.IMPORTANCE_WRITE_MIN == 1
    assert imp["minimum"] == digest_tools.IMPORTANCE_WRITE_MIN
    assert imp["maximum"] == digest_tools.IMPORTANCE_MAX
    assert "entity" in submit["parameters"]["required"]

    patch = digest_tools.patch_schema("event")
    assert patch["name"] == "patch_event_block"
    assert patch["parameters"].get("required", []) == []

    fact = digest_tools.submit_schema("fact")
    assert fact["parameters"]["properties"]["kind"]["enum"] == list(
        digest_tools.FACT_KIND_ENUM
    )
    assert "kind" in fact["parameters"]["required"]
    assert "content" in fact["parameters"]["required"]
    for schema in (submit, fact):
        aliases = schema["parameters"]["properties"]["entity_aliases"]
        assert aliases["type"] == "array"
        assert aliases["minItems"] == 1
        assert aliases["uniqueItems"] is True
        assert aliases["items"]["type"] == "string"
        assert "entity_aliases" not in schema["parameters"]["required"]
    procedure = digest_tools.submit_schema("procedure")
    assert "entity_aliases" not in procedure["parameters"]["properties"]


def test_submit_operations_schema_exposes_nested_merge_slots():
    schema = digest_tools.submit_operations_schema()
    op_item = schema["parameters"]["properties"]["operations"]["items"]
    props = op_item["properties"]
    assert "body" not in props
    for nest in ("event", "procedure", "decision", "fact"):
        assert nest in props
        nest_schema = props[nest]
        assert nest_schema["type"] == "object"
        assert nest_schema["additionalProperties"] is False
        assert nest_schema["required"]
        for key in nest_schema["required"]:
            field = nest_schema["properties"][key]
            if field.get("type") == "string" and "enum" not in field:
                assert field.get("minLength") == 1
    patch = digest_tools.patch_operations_schema()
    patch_ops = patch["parameters"]["properties"]["operations"]["items"]
    for nest in ("event", "procedure", "decision", "fact"):
        assert nest in patch_ops["properties"]
    assert "create" not in digest_tools.OP_ENUM
    assert op_item["properties"]["operation"]["enum"] == list(digest_tools.OP_ENUM)


def test_render_body_from_slots_shared_helper():
    body = digest_tools.render_body_from_slots(
        "event",
        {
            "beginning": "a",
            "course": "b",
            "outcome": "c",
        },
    )
    assert body == "Beginning: a; Course: b; Outcome: c"
    huge = digest_tools.render_body_from_slots(
        "event",
        {
            "beginning": "B" * 400,
            "course": "C" * 400,
            "outcome": "O" * 400,
        },
    )
    assert len(huge) <= digest_tools.RENDERED_BODY_MAX
    assert huge.endswith("…")
    fact = digest_tools.render_body_from_slots(
        "fact", {"kind": "Narration", "content": "cast story"}
    )
    assert fact == "Narration: cast story"

def test_render_fact_kind_enum_and_slot_validation():
    factual = digest_tools.render_worker_yaml_from_args(
        "fact",
        {
            "entity": "Jordan",
            "kind": "Factual",
            "content": "Jordan lives in a school dorm",
            "confidence": "high",
            "importance": 3,
        },
        session_id="s1",
        today="2026-08-11",
    )
    assert "Factual: Jordan lives in a school dorm" in factual
    assert digest_tools.validate_worker_slot_args(
        "fact",
        {"kind": "Factual", "content": "Jordan lives in a school dorm"},
    ) == []
    assert any(
        "invalid kind" in e
        for e in digest_tools.validate_worker_slot_args(
            "fact", {"kind": "plain", "content": "x"}
        )
    )
    blank_proc = digest_tools.validate_worker_slot_args(
        "procedure", {"obstacle": "", "solution": ""}
    )
    assert "obstacle must be non-empty" in blank_proc
    assert "solution must be non-empty" in blank_proc


def test_render_event_from_args_and_skip():
    yaml_text = digest_tools.render_worker_yaml_from_args(
        "event",
        {
            "entity": "memory-digest",
            "predicate": "user_requested_tool_call_fill",
            "participants": [
                {"entity": "User", "role": "requester"},
                {"entity": "Assistant", "role": "executor"},
            ],
            "valid_from": "2026-08-11",
            "valid_to": "2026-08-11",
            "beginning": "User asked",
            "course": "Plan redesigned",
            "outcome": "Submit tool carries values",
            "confidence": "high",
            "importance": 3,
        },
        session_id="s1",
        today="2026-08-11",
    )
    assert "type: event" in yaml_text
    assert "Beginning: User asked;" in yaml_text
    assert "confidence: high" in yaml_text
    assert "entity_aliases:" not in yaml_text
    bilingual = digest_tools.render_worker_yaml_from_args(
        "event",
        {
            "entity": "Memory Digest",
            "entity_aliases": ["记忆摘要"],
            "predicate": "user_requested_memory_recall",
            "participants": [
                {"entity": "User", "role": "requester"},
                {"entity": "Assistant", "role": "executor"},
            ],
            "valid_from": "2026-08-24",
            "valid_to": "2026-08-24",
            "beginning": "User asked about 记忆摘要",
            "course": "Assistant traced retrieval",
            "outcome": "The entity was recalled",
            "confidence": "high",
            "importance": 3,
        },
        session_id="s-example",
        today="2026-08-24",
    )
    assert "entity: Memory Digest" in bilingual
    assert "entity_aliases: [记忆摘要]" in bilingual
    assert bilingual.index("entity: Memory Digest") < bilingual.index(
        "entity_aliases: [记忆摘要]"
    )
    assert (
        digest_tools.render_worker_yaml_from_args(
            "event", {"skip": True}, session_id="s1", today="2026-08-11"
        )
        == "skip"
    )


def test_render_stamps_session_source_with_message_range():
    yaml_text = digest_tools.render_worker_yaml_from_args(
        "fact",
        {
            "entity": "Topic",
            "kind": "Factual",
            "content": "an observation",
            "confidence": "high",
            "importance": 3,
            "sources": ["transcript:2026-08-15", "conversation", "file:/tmp/a.html"],
        },
        session_id="20260811_170325_fdef935a",
        today="2026-08-15",
        message_start_id=64989,
        message_end_id=65117,
        user_message_at="2026-08-22T16:01:12+08:00",
        assistant_response_at="2026-08-22T17:10:44+08:00",
        generated_at="2026-08-22T17:16:08+08:00",
    )
    assert "session 20260811_170325_fdef935a#64989-65117" in yaml_text
    assert "user_message_at:" in yaml_text
    assert "2026-08-22T16:01:12+08:00" in yaml_text
    assert "assistant_response_at:" in yaml_text
    assert "2026-08-22T17:10:44+08:00" in yaml_text
    assert "generated_at:" in yaml_text
    assert "2026-08-22T17:16:08+08:00" in yaml_text
    assert yaml_text.index("sources:") < yaml_text.index("user_message_at:")
    assert "transcript:2026-08-15" not in yaml_text
    assert "conversation" not in yaml_text
    assert "file:/tmp/a.html" in yaml_text
    assert digest_tools.session_id_from_source_tag(
        "session 20260811_170325_fdef935a#64989-65117"
    ) == "20260811_170325_fdef935a"
    assert digest_tools.session_id_from_source_tag(
        "session:20260811_170325_fdef935a#64989-65117"
    ) == "20260811_170325_fdef935a"


def test_extra_file_sources_strips_memory_staging_paths():
    kept = digest_tools._extra_file_sources(
        [
            "file:/root/Me/Personal/notes/2026-08-07_memory_digest_合并去重设计方案.md",
            "file:/root/.hermes/memories/staging/daily/2026-08-15.md",
            "file:hermes-home/memories/staging/weekly/2026-W33.md",
            "2026-08-15.md",
            "file:2026-08-15.md",
            "sheet:851003",
            "session 20260722_172657_c54f77a8",
        ]
    )
    assert kept == [
        "file:/root/Me/Personal/notes/2026-08-07_memory_digest_合并去重设计方案.md",
        "sheet:851003",
    ]


def test_dry_run_flags_related_to_retiring():
    existing = [
        {
            "id": "mem-2026-08-11-fact-a",
            "type": "fact",
            "body": "A",
            "confidence": "high",
            "status": "candidate",
            "importance": 3,
            "sources": ["session s"],
        },
        {
            "id": "mem-2026-08-11-fact-b",
            "type": "fact",
            "body": "B",
            "confidence": "high",
            "status": "candidate",
            "importance": 3,
            "sources": ["session s"],
            "related": ["mem-2026-08-11-fact-a"],
        },
    ]
    ops = [{"operation": "drop", "id": "mem-2026-08-11-fact-a"}]
    pre, _post = composition.dry_run_apply(existing, ops)
    errors = composition.composition_errors_after_dry_run(pre, ops)
    assert any("retiring" in e for e in errors)


def test_standalone_decay_only_eligible_existing():
    existing = [
        {
            "id": "mem-2026-08-11-fact-alone",
            "type": "fact",
            "body": "alone",
            "confidence": "high",
            "status": "candidate",
            "importance": 3,
            "sources": ["session s"],
        },
        {
            "id": "mem-2026-08-11-event-1",
            "type": "event",
            "body": "Beginning: a; Course: b; Outcome: c",
            "confidence": "high",
            "status": "candidate",
            "importance": 3,
            "sources": ["session s"],
            "related": ["mem-2026-08-11-fact-linked"],
        },
        {
            "id": "mem-2026-08-11-fact-linked",
            "type": "fact",
            "body": "linked",
            "confidence": "high",
            "status": "candidate",
            "importance": 3,
            "sources": ["session s"],
        },
    ]
    ops = composition.append_standalone_decay_ops(
        [],
        existing,
        eligible_ids={"mem-2026-08-11-fact-alone", "mem-2026-08-11-fact-linked"},
    )
    ids = {
        (op.get("id") if isinstance(op, dict) else op.id)
        for op in ops
        if (op.get("operation") if isinstance(op, dict) else op.operation) == "update"
    }
    assert "mem-2026-08-11-fact-alone" in ids
    assert "mem-2026-08-11-fact-linked" not in ids


def test_create_importance_one_orphan_decrements_to_zero():
    """Create-time 1 is a real score: first orphan decay is 1→0, not an immediate drop."""
    orphan = {
        "id": "mem-2026-08-11-fact-new-low",
        "type": "fact",
        "body": "low",
        "confidence": "high",
        "status": "candidate",
        "importance": 1,
        "sources": ["session s"],
    }
    ops = composition.append_standalone_decay_ops([], [orphan])
    assert ops == [
        {
            "operation": "update",
            "id": "mem-2026-08-11-fact-new-low",
            "changes": {"importance": 0},
        }
    ]


def test_importance_zero_orphan_drops_on_decay():
    """Stored 0 is the drop floor; standalone decay emits drop, not another −1."""
    orphan = {
        "id": "mem-2026-08-11-fact-floor",
        "type": "fact",
        "body": "floor",
        "confidence": "high",
        "status": "candidate",
        "importance": 0,
        "sources": ["session s"],
    }
    ops = composition.append_standalone_decay_ops([], [orphan])
    assert ops == [{"operation": "drop", "id": "mem-2026-08-11-fact-floor"}]


def test_create_importance_two_orphan_decrements_then_survives_one_cycle():
    """Dirty stamp (2) and create-time 2 share the same decay step: 2→1, not drop."""
    orphan = {
        "id": "mem-2026-08-11-fact-dirty-like",
        "type": "fact",
        "body": "mid",
        "confidence": "high",
        "status": "candidate",
        "importance": digest_tools.IMPORTANCE_DIRTY,
        "sources": ["session s"],
    }
    ops = composition.append_standalone_decay_ops([], [orphan])
    assert ops == [
        {
            "operation": "update",
            "id": "mem-2026-08-11-fact-dirty-like",
            "changes": {"importance": 1},
        }
    ]


def test_validate_digest_file_accepts_thirty_one_blocks(monkeypatch):
    digest = _load("digest")

    def _n_block_file(count: int) -> str:
        parts = []
        for i in range(count):
            parts.append(
                "---\n"
                f"id: mem-2026-08-11-fact-{i}\n"
                "type: fact\n"
                "entity: Topic\n"
                "confidence: high\n"
                "importance: 3\n"
                "status: candidate\n"
                "sources: [session s]\n"
                "---\n"
                f"Fact number {i}.\n"
            )
        return "\n".join(parts)

    monkeypatch.setattr(digest, "_week_alive_block_ids", lambda: set())
    content = _n_block_file(31)
    errors = digest._validate_digest_file(content, alive_ids=set())
    assert not any("too many blocks" in e for e in errors)


def test_merge_operations_patch_replaces_full_list_or_one_index():
    previous = [
        {"operation": "update", "id": "a", "changes": {"body": "x"}},
        {"operation": "drop", "id": "b", "reason": "old"},
    ]
    replaced = digest_tools.merge_operations_patch(
        previous, {"operations": [{"operation": "drop", "id": "c", "reason": "dup"}]}
    )
    assert replaced == [{"operation": "drop", "id": "c", "reason": "dup"}]
    patched = digest_tools.merge_operations_patch(
        previous, {"replace_index": 1, "operation_patch": {"reason": "fixed"}}
    )
    assert patched[0] == previous[0]
    assert patched[1]["reason"] == "fixed"
    assert patched[1]["operation"] == "drop"


def test_store_validated_worker_is_blocks_primary(tmp_path, monkeypatch):
    digest = _load("digest")
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    block = {
        "id": "mem-2026-08-11-fact-1",
        "type": "fact",
        "entity": "Topic",
        "confidence": "high",
        "status": "candidate",
        "sources": ["session s1"],
        "body": "A fact.",
    }
    result = digest.ValidatedWorkerResult(
        worker_type="fact",
        session_id="s1",
        run_id="r1",
        attempts=1,
        content="ignored-for-disk",
        blocks=(block,),
    )
    stored = digest._store_validated_worker_result(result)
    assert stored.path is not None
    payload = __import__("json").loads(stored.path.read_text(encoding="utf-8"))
    assert payload["status"] == "validated"
    assert payload["blocks"] == [block]
    assert "content" not in payload


def test_commit_uses_in_memory_ops_without_worker_tmp(tmp_path, monkeypatch):
    digest = _load("digest")
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily = tmp_path / "memories" / "staging" / "daily" / "2026-08-11.md"
    daily.parent.mkdir(parents=True)
    daily.write_text("", encoding="utf-8")
    block = {
        "id": "mem-2026-08-11-fact-new",
        "type": "fact",
        "entity": "Topic",
        "confidence": "high",
        "importance": 3,
        "status": "candidate",
        "sources": ["session s1"],
        "body": "Committed from memory.",
    }
    worker = digest.ValidatedWorkerResult(
        worker_type="fact",
        session_id="s1",
        run_id="r1",
        attempts=1,
        content="",
        blocks=(block,),
        path=None,
    )
    ops = [{"operation": "create", "block": block}]
    assert digest._commit_candidate(
        daily,
        [worker],
        ops,
        session_id="s1",
        run_id="r1",
    )
    text = daily.read_text(encoding="utf-8")
    assert "mem-2026-08-11-fact-new" in text
    assert "Committed from memory." in text


def test_commit_truncates_overlength_body_before_write(tmp_path, monkeypatch):
    digest = _load("digest")
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily = tmp_path / "memories" / "staging" / "daily" / "2026-08-11.md"
    daily.parent.mkdir(parents=True)
    daily.write_text("", encoding="utf-8")
    long_body = "Beginning: " + ("x" * 760)
    assert len(long_body) > digest.MAX_BODY_CHARS
    block = {
        "id": "mem-2026-08-11-event-long",
        "type": "event",
        "entity": "Topic",
        "predicate": "user_requested_x",
        "participants": [
            {"entity": "User", "role": "requester"},
            {"entity": "Assistant", "role": "executor"},
        ],
        "confidence": "high",
        "importance": 3,
        "status": "candidate",
        "sources": ["session s1"],
        "valid_from": "2026-08-11",
        "valid_to": "open",
        "body": long_body,
    }
    worker = digest.ValidatedWorkerResult(
        worker_type="event",
        session_id="s1",
        run_id="r1",
        attempts=1,
        content="",
        blocks=(block,),
        path=None,
    )
    ops = [{"operation": "create", "block": block}]
    assert digest._commit_candidate(
        daily,
        [worker],
        ops,
        session_id="s1",
        run_id="r1",
    )
    written = daily.read_text(encoding="utf-8").split("---")[-1].strip()
    assert len(written) <= digest.MAX_BODY_CHARS
    assert written.endswith("…")


def test_commit_candidate_once_has_no_hard_final_reject():
    digest = _load("digest")
    src = inspect.getsource(digest._commit_candidate_once)
    assert "soft validation notes" in src
    assert "body = _truncate_body(" in src
    assert "raise ValueError" not in src.split("soft validation notes", 1)[1].split(
        "execution_log.transition(\"candidate_validated\")", 1
    )[0]
    assert "_validate_digest_file(candidate)" in src
    # Hard reject path retired: soft log only.
    assert "if soft_errors:" in src


def test_prepare_operations_default_max_attempts_is_five():
    operations = _load("operations")
    assert (
        operations.prepare_operations.__defaults__ is not None
        or "max_attempts: int = 5"
        in inspect.getsource(operations.prepare_operations)
    )
    src = inspect.getsource(operations.prepare_operations)
    assert "max_attempts: int = 5" in src
    assert "proposer_accepted_dirty" in src


def test_proposer_tool_loop_submit_then_patch(tmp_path, monkeypatch):
    digest = _load("digest")
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    calls: list[str] = []
    prompts: list[str] = []

    def fake_tool(prompt, platform, *, purpose, force_tool_name):
        del platform, purpose
        calls.append(force_tool_name)
        prompts.append(prompt)
        if force_tool_name == "submit_operations":
            return {
                "tool_name": "submit_operations",
                "tool_args": {
                    "operations": [
                        {
                            "operation": "update",
                            "id": "missing",
                            "changes": {"body": "bad"},
                        }
                    ]
                },
            }
        return {
            "tool_name": "patch_operations",
            "tool_args": {"operations": []},
        }

    monkeypatch.setattr(digest, "_invoke_digest_worker_tool", fake_tool)

    class _StubMiniLM:
        def encode(self, texts, normalize_embeddings=True):
            del normalize_embeddings
            rows = []
            for raw in texts:
                text = str(raw)
                if "Apple" in text or "alpha-apple" in text:
                    rows.append([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
                elif "Banana" in text:
                    rows.append([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
                elif "Zebra" in text:
                    rows.append([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
                elif "Proc" in text:
                    rows.append([0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
                elif "Event" in text:
                    rows.append([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
                else:
                    rows.append([0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0])
            return rows

    monkeypatch.setattr(digest, "_PHASE2_MINILM", _StubMiniLM())
    monkeypatch.setattr(digest, "_PHASE2_EMBED_CACHE", {})

    skipper = digest.make_llm_proposer("cli", session_id="s1", run_id="r-skip")
    singleton = {
        "id": "mem-2026-08-11-fact-x",
        "type": "fact",
        "entity": "Topic",
        "confidence": "high",
        "importance": 3,
        "status": "candidate",
        "sources": ["session s1"],
        "body": "Fixed.",
    }
    skipped = skipper([], [singleton], errors=(), attempt=1)
    assert calls == []
    assert skipped == []

    class _BoomMiniLM:
        def encode(self, texts, normalize_embeddings=True):
            del texts, normalize_embeddings
            raise RuntimeError("minilm unavailable")

    monkeypatch.setattr(digest, "_PHASE2_MINILM", _BoomMiniLM())
    monkeypatch.setattr(digest, "_PHASE2_EMBED_CACHE", {})
    opener = digest.make_llm_proposer("cli", session_id="s1", run_id="r-open")
    opener([], [singleton], errors=(), attempt=1)
    assert calls[-1] == "submit_operations"
    assert "mem-2026-08-11-fact-x" in prompts[-1]
    assert "Compare only these same-type pairs" not in prompts[-1]

    monkeypatch.setattr(digest, "_PHASE2_MINILM", None)
    monkeypatch.setattr(digest, "_PHASE2_EMBED_CACHE", {})
    monkeypatch.setenv("HERMES_PHASE2_SKIP_MINILM", "1")
    skip_ram = digest.make_llm_proposer("cli", session_id="s1", run_id="r-ram")
    skip_ram([], [singleton, {**singleton, "id": "mem-2026-08-11-fact-y", "body": "Other."}], errors=(), attempt=1)
    assert calls[-1] == "submit_operations"
    monkeypatch.delenv("HERMES_PHASE2_SKIP_MINILM", raising=False)

    monkeypatch.setattr(digest, "_PHASE2_MINILM", _StubMiniLM())
    monkeypatch.setattr(digest, "_PHASE2_EMBED_CACHE", {})
    proposer = digest.make_llm_proposer("cli", session_id="s1", run_id="r1")
    existing = []
    new = [
        {
            "id": "mem-fact-apple",
            "type": "fact",
            "entity": "Topic",
            "confidence": "high",
            "importance": 3,
            "status": "candidate",
            "sources": ["session s1"],
            "body": "Apple related note alpha-apple.",
        },
        {
            "id": "mem-fact-banana",
            "type": "fact",
            "entity": "Topic",
            "confidence": "high",
            "importance": 3,
            "status": "candidate",
            "sources": ["session s1"],
            "body": "Banana related note.",
        },
        {
            "id": "mem-fact-zebra",
            "type": "fact",
            "entity": "Topic",
            "confidence": "high",
            "importance": 3,
            "status": "candidate",
            "sources": ["session s1"],
            "body": "Zebra unrelated note.",
        },
        {
            "id": "mem-proc-a",
            "type": "procedure",
            "entity": "Lock",
            "confidence": "high",
            "importance": 3,
            "status": "candidate",
            "sources": ["session s1"],
            "body": "Proc recover lock.",
        },
        {
            "id": "mem-proc-b",
            "type": "procedure",
            "entity": "Lock",
            "confidence": "high",
            "importance": 3,
            "status": "candidate",
            "sources": ["session s1"],
            "body": "Proc recover lock again.",
        },
        {
            "id": "mem-event-apple",
            "type": "event",
            "entity": "Topic",
            "confidence": "high",
            "importance": 3,
            "status": "candidate",
            "sources": ["session s1"],
            "body": "Event about Apple.",
        },
    ]
    first = proposer(existing, new, errors=(), attempt=1)
    assert calls[-1] == "submit_operations"
    gated = prompts[-1]
    assert "## Filtered candidate board" in gated
    assert "### Existing events" not in gated
    assert "### Existing facts" not in gated
    assert "Compare only these same-type pairs" not in gated
    assert gated.count("## Filtered candidate board") >= 2
    blob = gated.rsplit("## Filtered candidate board", 1)[1]
    blob = blob.split("\n## ", 1)[0].strip()
    start = blob.find("{")
    end = blob.rfind("}")
    assert start >= 0 and end > start
    board = json.loads(blob[start : end + 1])
    assert set(board) <= {"event", "fact", "procedure", "decision"}
    assert "event" not in board
    assert "decision" not in board
    assert "fact" in board and "procedure" in board
    fact_ids = [card["id"] for card in board["fact"]["cards"]]
    proc_ids = [card["id"] for card in board["procedure"]["cards"]]
    # Same-entity facts stay on the board even when MI would drop zebra.
    assert fact_ids == ["mem-fact-apple", "mem-fact-banana", "mem-fact-zebra"]
    assert proc_ids == ["mem-proc-a", "mem-proc-b"]
    assert "mem-event-apple" not in json.dumps(board)
    for bucket in board.values():
        cards = bucket["cards"]
        for left, right in bucket["pairs"]:
            assert isinstance(left, int) and isinstance(right, int)
            assert 0 <= left < len(cards)
            assert 0 <= right < len(cards)
            assert left != right
    compact = json.dumps(board, ensure_ascii=False, separators=(",", ":"))
    id_pairs = {
        kind: {
            "cards": bucket["cards"],
            "pairs": [
                [bucket["cards"][i]["id"], bucket["cards"][j]["id"]]
                for i, j in bucket["pairs"]
            ],
        }
        for kind, bucket in board.items()
    }
    verbose = json.dumps(id_pairs, ensure_ascii=False, separators=(",", ":"))
    assert len(compact) <= len(verbose)
    second = proposer(
        existing, new, errors=("operation[0]: unknown id",), attempt=2
    )
    assert "patch_operations" in calls
    assert list(second) == []
    assert first is not None


def test_oneshot_attempt2_uses_teach_and_foul_touched_patch(monkeypatch, tmp_path):
    """Live Phase-2 oneshot must reuse operations_failed_teach + patch, not a full resubmit."""
    digest = _load("digest")
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    prompts: list[tuple[str, str]] = []

    def fake_oneshot(prompt, platform, *, purpose, force_tool_name="", **_k):
        del platform, purpose
        prompts.append((force_tool_name, prompt))
        if force_tool_name == "submit_operations":
            return {
                "tool_name": "submit_operations",
                "tool_args": {
                    "operations": [
                        {
                            "operation": "update",
                            "id": "mem-keep",
                            "changes": {"importance": 4},
                        }
                    ]
                },
                "failed": False,
            }
        return {
            "tool_name": "patch_operations",
            "tool_args": {"operations": []},
            "failed": False,
        }

    proposer = digest.make_oneshot_proposer(
        session_id="s1", run_id="r1", invoke_tool=fake_oneshot
    )
    class _StubMiniLM:
        def encode(self, texts, normalize_embeddings=True):
            del texts, normalize_embeddings
            return [[1.0, 0.0, 0.0, 0.0], [0.99, 0.14, 0.0, 0.0]]

    monkeypatch.setattr(digest, "_PHASE2_MINILM", _StubMiniLM())
    monkeypatch.setattr(digest, "_PHASE2_EMBED_CACHE", {})
    keep = {
        "id": "mem-keep",
        "type": "fact",
        "entity": "Keep",
        "confidence": "high",
        "importance": 3,
        "status": "candidate",
        "sources": ["session s1"],
        "body": "Keep this.",
    }
    other = {
        "id": "mem-other",
        "type": "fact",
        "entity": "Other",
        "confidence": "high",
        "importance": 3,
        "status": "candidate",
        "sources": ["session s1"],
        "body": "Unrelated.",
    }
    proposer([keep, other], [], errors=(), attempt=1)
    proposer(
        [keep, other],
        [],
        errors=("operation[0]: unknown field on mem-keep",),
        attempt=2,
    )
    force, prompt = prompts[-1]
    assert force == "patch_operations"
    teach = digest_tools.operations_failed_teach(
        ("operation[0]: unknown field on mem-keep",),
        attempt=2,
        max_attempts=2,
    )
    assert teach in prompt
    assert "mem-keep" in prompt
    assert "mem-other" not in prompt



def test_validate_worker_tool_args_rejects_jordan_decision():
    errs = digest_tools.validate_worker_tool_args(
        "decision",
        {
            "kind": "Decision",
            "subject": "Jordan",
            "ruling": "dislikes cilantro",
            "confidence": "high",
            "importance": 4,
        },
    )
    assert errs
    assert any("user" in e.lower() or "Narration" in e for e in errs)
    assert (
        digest_tools.validate_worker_tool_args(
            "decision",
            {
                "kind": "Decision",
                "subject": "user",
                "ruling": "hand in last dirty version",
                "confidence": "explicit",
                "importance": 4,
            },
        )
        == []
    )


def test_validate_worker_tool_args_event_participants():
    base = {
        "beginning": "a",
        "course": "b",
        "outcome": "c",
        "confidence": "high",
        "importance": 4,
    }
    bad = {
        **base,
        "participants": [{"entity": "User", "role": "actor"}],
    }
    errs = digest_tools.validate_worker_tool_args("event", bad)
    assert any("requester" in e or "participants" in e for e in errs)
    good = {
        **base,
        "participants": [
            {"entity": "User", "role": "requester"},
            {"entity": "Assistant", "role": "executor"},
        ],
    }
    assert digest_tools.validate_worker_tool_args("event", good) == []
    clocked = {
        **good,
        "user_message_at": "2026-08-22T16:01:12+08:00",
        "assistant_response_at": "2026-08-22T17:10:44+08:00",
        "generated_at": "2026-08-22T17:16:08+08:00",
    }
    assert digest_tools.validate_worker_tool_args("event", clocked) == []
    with_entity = {**good, "entity": "Memory Digest"}
    assert digest_tools.validate_worker_tool_args(
        "event", {**with_entity, "entity_aliases": ["记忆摘要"]}
    ) == []
    dup = digest_tools.validate_worker_tool_args(
        "event", {**with_entity, "entity_aliases": ["记忆摘要", "记忆摘要"]}
    )
    assert any("unique" in e or "duplicate" in e for e in dup)
    repeat = digest_tools.validate_worker_tool_args(
        "event", {**with_entity, "entity_aliases": ["Memory Digest"]}
    )
    assert any("canonical" in e or "entity" in e for e in repeat)
    empty = digest_tools.validate_worker_tool_args(
        "event", {**with_entity, "entity_aliases": []}
    )
    assert any("entity_aliases" in e for e in empty)
    blank = digest_tools.validate_worker_tool_args(
        "event", {**with_entity, "entity_aliases": [" "]}
    )
    assert any("entity_aliases" in e for e in blank)
    schema = json.dumps(digest_tools.submit_schema("event"))
    assert "user_message_at" not in schema
    assert "assistant_response_at" not in schema
    assert "generated_at" not in schema


def test_render_worker_yaml_quotes_at_session_and_default_sources():
    import yaml

    bag = {
        "kind": "Decision",
        "subject": "user",
        "ruling": "keep going",
        "confidence": "high",
        "importance": 4,
    }
    rendered = digest_tools.render_worker_yaml_from_args(
        "decision", bag, session_id="bench-x", today="2026-08-12"
    )
    fm = rendered.split("---")[1]
    parsed = yaml.safe_load(fm)
    assert parsed["sources"] == ["session bench-x"]

    rendered2 = digest_tools.render_worker_yaml_from_args(
        "decision",
        {**bag, "sources": ["@session:weird"]},
        session_id="bench-x",
        today="2026-08-12",
    )
    parsed2 = yaml.safe_load(rendered2.split("---")[1])
    assert parsed2["sources"] == ["session bench-x"]


def _sample_digest_blocks_payload() -> dict:
    return {
        "blocks": [
            {
                "type": "event",
                "temp_id": "tmp-e1",
                "entity": "memory-digest",
                "predicate": "user_requested_tool_call_fill",
                "participants": [
                    {"entity": "User", "role": "requester"},
                    {"entity": "Assistant", "role": "executor"},
                ],
                "beginning": "User asked for a single digest worker",
                "course": "Plan locked merged submit_digest_blocks schema",
                "outcome": "Refactor ready to implement",
                "confidence": "high",
                "importance": 4,
                "valid_from": "2026-08-12",
                "valid_to": "2026-08-12",
            },
            {
                "type": "decision",
                "kind": "Decision",
                "subject": "user",
                "ruling": "Phase-1 uses one merged tool; Phase-2 dedup stays separate",
                "confidence": "explicit",
                "importance": 4,
                "related": ["tmp-e1"],
            },
        ]
    }


def test_submit_digest_blocks_schema_locked_shape():
    schema = digest_tools.submit_digest_blocks_schema()
    assert schema["name"] == "submit_digest_blocks"
    params = schema["parameters"]
    assert params["additionalProperties"] is False
    assert params["required"] == ["blocks"]
    item = params["properties"]["blocks"]["items"]
    assert "oneOf" in item
    variants = item["oneOf"]
    assert len(variants) == 4
    for variant in variants:
        assert variant["additionalProperties"] is False
        assert "type" in variant["required"]
        assert "const" in variant["properties"]["type"]
        assert "temp_id" in variant["properties"]
        assert "body" not in variant["properties"]
        for nest in ("event", "fact", "procedure", "decision"):
            assert nest not in variant["properties"]
    patch = digest_tools.patch_digest_blocks_schema()
    assert patch["name"] == "patch_digest_blocks"
    assert patch["parameters"]["additionalProperties"] is False
    names = {s["name"] for s in digest_tools.all_tool_schemas()}
    assert "submit_digest_blocks" in names
    assert "patch_digest_blocks" in names
    assert digest_tools.tool_names_for_phase1(mode="submit") == [
        "submit_digest_blocks",
        "patch_digest_blocks",
        "skip_digest_worker",
    ]
    by_const = {v["properties"]["type"]["const"]: v for v in variants}
    event_props = by_const["event"]["properties"]
    assert event_props["beginning"]["maxLength"] == 156
    assert event_props["course"]["maxLength"] == 156
    assert event_props["outcome"]["maxLength"] == 156
    assert "Never another event id" in event_props["related"]["description"]
    assert by_const["fact"]["properties"]["content"]["maxLength"] == 489
    assert by_const["procedure"]["properties"]["obstacle"]["maxLength"] == 239
    assert by_const["procedure"]["properties"]["solution"]["maxLength"] == 239
    assert by_const["decision"]["properties"]["subject"]["maxLength"] == 40
    assert by_const["decision"]["properties"]["ruling"]["maxLength"] == 440
    assert "Never another event id" not in by_const["fact"]["properties"]["related"]["description"]


def test_validate_digest_blocks_args_accepts_sample_and_empty():
    assert digest_tools.validate_digest_blocks_args(_sample_digest_blocks_payload()) == []
    assert digest_tools.validate_digest_blocks_args({"blocks": []}) == []


def test_validate_digest_blocks_args_rejects_nest_and_missing_fields():
    bad_nest = {
        "blocks": [
            {
                "type": "event",
                "fact": {
                    "entity": "x",
                    "kind": "Factual",
                    "content": "y",
                    "confidence": "high",
                    "importance": 3,
                },
            }
        ]
    }
    errs = digest_tools.validate_digest_blocks_args(bad_nest)
    assert any("use flat fields" in e for e in errs)

    missing = {"blocks": [{"type": "event"}]}
    errs2 = digest_tools.validate_digest_blocks_args(missing)
    assert errs2
    assert any("beginning" in e or "entity" in e for e in errs2)


def test_merge_digest_blocks_patch_full_replace_and_index():
    previous = _sample_digest_blocks_payload()
    replaced = digest_tools.merge_digest_blocks_patch(
        previous,
        {
            "blocks": [
                {
                    "type": "fact",
                    "entity": "a",
                    "kind": "Factual",
                    "content": "x",
                    "confidence": "high",
                    "importance": 3,
                }
            ]
        },
    )
    assert len(replaced["blocks"]) == 1
    assert replaced["blocks"][0]["type"] == "fact"

    patched = digest_tools.merge_digest_blocks_patch(
        previous,
        {
            "patch_index": 1,
            "block_patch": {
                "ruling": "patched ruling only",
            },
        },
    )
    assert patched["blocks"][1]["ruling"] == "patched ruling only"
    assert patched["blocks"][1]["kind"] == "Decision"
    assert patched["blocks"][0]["temp_id"] == "tmp-e1"
    assert patched["blocks"][1]["subject"] == "user"
