from __future__ import annotations

import re

from conftest import load_plugin_module


def _operations():
    return load_plugin_module("operations.py", "memory_digest_operations_test")


def _block(block_id: str, body: str = "A durable note.") -> dict:
    return {
        "id": block_id,
        "type": "fact",
        "entity": "Project",
        "confidence": "high",
        "status": "candidate",
        "sources": ["session s1"],
        "body": body,
    }


def test_operation_shapes_normalize_and_validate():
    operations = _operations()
    create = operations.normalize_operation(
        {
            "operation": "create",
            "block": _block("mem-2026-08-02-op-new"),
        }
    )
    assert operations.validate_operation(create, {"mem-old"}) == []

    update = operations.normalize_operation(
        {"operation": "update", "id": "mem-old", "changes": {"body": "Changed."}}
    )
    assert operations.validate_operation(update, {"mem-old"}) == []


def test_operation_validation_rejects_unknown_and_immutable_ids():
    operations = _operations()
    errors = operations.validate_operation(
        {
            "operation": "update",
            "id": "mem-old",
            "changes": {"id": "mem-other"},
        },
        {"mem-old"},
    )
    assert any("id" in error for error in errors)
    assert operations.validate_operation({"operation": "unknown"}, set())


def test_direct_create_validation_requires_canonical_id_but_legacy_targets_pass():
    operations = _operations()
    invalid = operations.validate_operation(
        {"operation": "create", "block": _block("mem-created")},
        set(),
    )
    assert any("canonical" in error for error in invalid)
    assert operations.validate_operation(
        {
            "operation": "create",
            "block": _block("mem-2026-08-02-op-ABC123"),
        },
        set(),
    ) == []
    assert operations.validate_operation(
        {"operation": "update", "id": "mem-legacy", "changes": {"body": "New."}},
        {"mem-legacy"},
    ) == []


def test_merge_removes_absorbed_block_and_retargets_references():
    operations = _operations()
    blocks = [
        _block("mem-survivor"),
        {**_block("mem-absorbed"), "body": "Duplicate note."},
        {
            **_block("mem-ref"),
            "related": ["mem-absorbed"],
            "supersedes": ["mem-absorbed"],
        },
    ]
    merged = operations.apply_operation(
        {
            "operation": "merge",
            "survivor_id": "mem-survivor",
            "absorbed_ids": ["mem-absorbed"],
            "reason": "same record",
        },
        blocks,
    )
    assert {block["id"] for block in merged} == {"mem-survivor", "mem-ref"}
    ref = next(block for block in merged if block["id"] == "mem-ref")
    assert ref["related"] == ["mem-survivor"]
    assert ref["supersedes"] == ["mem-survivor"]


def test_update_preserves_existing_id_and_build_operator_does_not_write():
    operations = _operations()
    existing = [_block("mem-existing", "Old note.")]
    new = [_block("mem-existing", "New note."), _block("mem-created")]
    proposed = operations.build_update_operations(existing, new)
    assert [op.operation for op in proposed] == ["update", "create"]
    assert proposed[0].id == "mem-existing"
    assert proposed[1].block["id"] != "mem-created"


def test_new_candidate_id_is_code_owned_even_when_candidate_has_id():
    operations = _operations()
    ids = iter(["mem-2026-08-02-code-1", "mem-2026-08-02-code-2"])
    proposed = operations.build_update_operations(
        [],
        [_block("mem-llm-1"), _block("mem-llm-2")],
        id_factory=lambda: next(ids),
    )
    assert [op.block["id"] for op in proposed] == [
        "mem-2026-08-02-code-1",
        "mem-2026-08-02-code-2",
    ]


def test_default_create_ids_match_canonical_digest_schema():
    operations = _operations()
    proposed = operations.build_update_operations([], [_block("llm-supplied")])
    assert re.fullmatch(
        r"mem-\d{4}-\d{2}-\d{2}-fact-[A-Za-z0-9_-]+",
        proposed[0].block["id"],
    )


def test_create_ids_use_block_type_segment():
    operations = _operations()
    event = {
        **_block("llm-event", "Beginning: request; Course: review; Outcome: done."),
        "type": "event",
        "entity": "Project",
        "predicate": "user_requested_review",
        "participants": [{"entity": "User", "role": "requester"}],
        "valid_from": "2026-08-02",
        "valid_to": "2026-08-02",
    }
    proposed = operations.build_update_operations(
        [],
        [event, _block("llm-fact"), {**_block("llm-proc"), "type": "procedure"}],
    )
    by_type = {op.block["type"]: op.block["id"] for op in proposed if op.operation == "create"}
    assert re.fullmatch(r"mem-\d{4}-\d{2}-\d{2}-event-[A-Za-z0-9_-]+", by_type["event"])
    assert re.fullmatch(r"mem-\d{4}-\d{2}-\d{2}-fact-[A-Za-z0-9_-]+", by_type["fact"])
    assert re.fullmatch(
        r"mem-\d{4}-\d{2}-\d{2}-procedure-[A-Za-z0-9_-]+", by_type["procedure"]
    )


def test_event_create_links_created_detail_ids_without_copying_detail_body():
    operations = _operations()
    event = {
        **_block("llm-event", "Beginning: request; Course: review; Outcome: delivered."),
        "type": "event",
        "entity": "Project",
        "predicate": "user_requested_review",
        "participants": [
            {"entity": "User", "role": "requester"},
            {"entity": "Assistant", "role": "executor"},
        ],
        "valid_from": "2026-08-02",
        "valid_to": "2026-08-02",
    }
    fact = _block("llm-fact", "The project has a stable requirement.")
    proposed = operations.build_update_operations([], [event, fact])
    event_op = next(op for op in proposed if op.operation == "create" and op.block["type"] == "event")
    fact_op = next(op for op in proposed if op.operation == "create" and op.block["type"] == "fact")
    assert event_op.block["related"] == [fact_op.block["id"]]
    assert fact_op.block["body"] not in event_op.block["body"]


def test_create_importance_one_still_links_into_event_related():
    """Create-time 1 is a real score, so Pass 2 must still attach it via related:."""
    operations = _operations()
    event = {
        **_block("llm-event", "Beginning: request; Course: review; Outcome: delivered."),
        "type": "event",
        "entity": "Project",
        "predicate": "user_requested_review",
        "participants": [
            {"entity": "User", "role": "requester"},
            {"entity": "Assistant", "role": "executor"},
        ],
        "valid_from": "2026-08-02",
        "valid_to": "2026-08-02",
        "importance": 3,
    }
    fact = {
        **_block("llm-fact", "The project has a stable requirement."),
        "importance": 1,
    }
    proposed = operations.build_update_operations([], [event, fact])
    event_op = next(
        op for op in proposed if op.operation == "create" and op.block["type"] == "event"
    )
    fact_op = next(
        op for op in proposed if op.operation == "create" and op.block["type"] == "fact"
    )
    assert event_op.block.get("related") == [fact_op.block["id"]]


def test_importance_zero_excluded_from_event_related_link():
    """Terminal 0 (decay floor) stays non-associable so spent cards skip related:."""
    operations = _operations()
    event = {
        **_block("llm-event", "Beginning: request; Course: review; Outcome: delivered."),
        "type": "event",
        "entity": "Project",
        "predicate": "user_requested_review",
        "participants": [
            {"entity": "User", "role": "requester"},
            {"entity": "Assistant", "role": "executor"},
        ],
        "valid_from": "2026-08-02",
        "valid_to": "2026-08-02",
        "importance": 3,
    }
    fact = {
        **_block("llm-fact", "Already at drop floor."),
        "importance": 0,
    }
    proposed = operations.build_update_operations([], [event, fact])
    event_op = next(
        op for op in proposed if op.operation == "create" and op.block["type"] == "event"
    )
    fact_op = next(
        op for op in proposed if op.operation == "create" and op.block["type"] == "fact"
    )
    assert fact_op.block["id"] not in event_op.block.get("related", [])


def test_dirty_importance_two_still_links_into_event_related():
    """IMPORTANCE_DIRTY (2) stays associable; only importance 0 is excluded."""
    operations = _operations()
    event = {
        **_block("llm-event", "Beginning: request; Course: review; Outcome: delivered."),
        "type": "event",
        "entity": "Project",
        "predicate": "user_requested_review",
        "participants": [
            {"entity": "User", "role": "requester"},
            {"entity": "Assistant", "role": "executor"},
        ],
        "valid_from": "2026-08-02",
        "valid_to": "2026-08-02",
        "importance": 3,
    }
    fact = {
        **_block("llm-fact", "Dirty-accepted but still chain-worthy."),
        "importance": 2,
    }
    proposed = operations.build_update_operations([], [event, fact])
    event_op = next(
        op for op in proposed if op.operation == "create" and op.block["type"] == "event"
    )
    fact_op = next(
        op for op in proposed if op.operation == "create" and op.block["type"] == "fact"
    )
    assert event_op.block.get("related") == [fact_op.block["id"]]


def test_operator_emits_merge_for_explicit_existing_duplicate():
    operations = _operations()
    existing = [_block("mem-survivor", "Canonical note."), _block("mem-duplicate", "Duplicate note.")]
    duplicate = {
        **_block("mem-duplicate", "Duplicate note."),
        "merge_into": "mem-survivor",
    }
    proposed = operations.build_update_operations(existing, [duplicate])
    assert [(op.operation, op.survivor_id, op.absorbed_ids) for op in proposed] == [
        ("merge", "mem-survivor", ["mem-duplicate"])
    ]


def test_operator_rejects_new_hypothesis_output_retired():
    operations = _operations()
    hypothesis = {
        **_block("llm-hypothesis", "The project may change direction."),
        "type": "hypothesis",
    }

    assert operations.build_update_operations([], [hypothesis]) == []


def test_operator_ignores_hypothesis_same_id_update():
    operations = _operations()
    existing = [
        {
            **_block("mem-hypothesis", "The project may change direction."),
            "type": "hypothesis",
            "confidence": "medium",
        }
    ]
    mutated = {
        **existing[0],
        "confidence": "explicit",
        "body": "The project will definitely change direction.",
    }

    proposed = operations.build_update_operations(existing, [mutated])

    assert proposed == []


def test_operator_ignores_hypothesis_to_hypothesis_merge():
    operations = _operations()
    existing = [
        {
            **_block("mem-hypothesis-survivor", "The project may change direction."),
            "type": "hypothesis",
        },
        {
            **_block("mem-hypothesis-duplicate", "The project may change teams."),
            "type": "hypothesis",
        },
    ]
    duplicate = {
        **existing[1],
        "merge_into": "mem-hypothesis-survivor",
    }

    proposed = operations.build_update_operations(existing, [duplicate])

    assert proposed == []


def test_operator_emits_explicit_correction_supersede():
    operations = _operations()
    existing = [_block("mem-target", "Incorrect note.")]
    correction = {**_block("llm-correction", "Correct note."), "supersedes": ["mem-target"]}
    proposed = operations.build_update_operations(existing, [correction])
    assert [op.operation for op in proposed] == ["create", "supersede"]
    assert proposed[1].target_id == "mem-target"
    assert proposed[1].helper_id == proposed[0].block["id"]


def test_existing_board_supersede_emits_when_new_blocks_empty():
    """Same-day leftover: helper already on the file still emits overwrite."""
    operations = _operations()
    target = _block("mem-target", "Incorrect note.")
    helper = {
        **_block("mem-helper", "Correct note."),
        "supersedes": ["mem-target"],
        "confidence": "explicit",
    }
    proposed = operations.build_update_operations([target, helper], [])
    supersedes = [op for op in proposed if op.operation == "supersede"]
    assert len(supersedes) == 1
    assert supersedes[0].helper_id == "mem-helper"
    assert supersedes[0].target_id == "mem-target"
    assert supersedes[0].correction == "Correct note."


def test_supersede_skips_target_not_on_board():
    """Cross-day / missing ids stay pointers; apply cannot see another file."""
    operations = _operations()
    helper = {
        **_block("mem-helper", "Correct note."),
        "supersedes": ["mem-2026-08-21-fact-OTHER"],
        "confidence": "explicit",
    }
    proposed = operations.build_update_operations([helper], [])
    assert [op.operation for op in proposed if op.operation == "supersede"] == []


def test_prepare_rewrites_custom_proposer_create_ids_and_references(tmp_path):
    operations = _operations()

    def proposer(existing, new, *, errors, attempt):
        return [
            {"operation": "create", "block": _block("llm-custom")},
            {
                "operation": "update",
                "id": "llm-custom",
                "changes": {"related": ["llm-custom"]},
            },
        ]

    prepared, _path = operations.prepare_operations(
        [],
        [_block("ignored")],
        session_id="s1",
        run_id="r1",
        session_dir=tmp_path / "s1",
        proposer=proposer,
    )
    generated_id = prepared[0].block["id"]
    assert generated_id != "llm-custom"
    assert re.fullmatch(r"mem-\d{4}-\d{2}-\d{2}-[A-Za-z0-9_-]+", generated_id)
    assert prepared[1].id == generated_id
    assert prepared[1].changes["related"] == [generated_id]


def test_merge_preserves_metadata_and_retargets_all_references():
    operations = _operations()
    survivor = {
        **_block("mem-survivor"),
        "sources": ["a"],
        "valid_from": "2026-08-02",
        "valid_to": "open",
        "importance": 3,
        "participants": [{"entity": "User", "role": "requester"}],
    }
    absorbed = {
        **_block("mem-absorbed"),
        "sources": ["b"],
        "valid_from": "2026-08-01",
        "valid_to": "2026-08-03",
        "importance": 5,
        "participants": [{"entity": "Assistant", "role": "executor"}],
        "related": ["mem-other"],
    }
    merged = operations.apply_operation(
        {
            "operation": "merge",
            "survivor_id": "mem-survivor",
            "absorbed_ids": ["mem-absorbed"],
            "reason": "same record",
        },
        [survivor, absorbed, {**_block("mem-ref"), "related": ["mem-absorbed"]}],
    )
    result = next(block for block in merged if block["id"] == "mem-survivor")
    assert result["sources"] == ["a", "b"]
    assert result["valid_from"] == "2026-08-01"
    assert result["valid_to"] == "open"
    assert result["importance"] == 5
    assert result["participants"] == [
        {"entity": "User", "role": "requester"},
        {"entity": "Assistant", "role": "executor"},
    ]
    assert all("mem-absorbed" not in str(block) for block in merged)


def test_operation_validation_rejects_unknown_fields_and_missing_merge_reason():
    operations = _operations()
    unknown = {
        "operation": "update",
        "id": "mem-old",
        "changes": {"body": "Changed."},
        "unexpected": True,
    }
    assert operations.validate_operation(unknown, {"mem-old"})
    assert operations.validate_operation(
        {
            "operation": "merge",
            "survivor_id": "mem-old",
            "absorbed_ids": ["mem-other"],
        },
        {"mem-old", "mem-other"},
    )


def test_malformed_operation_proposals_retry_five_times_then_dirty_accept(tmp_path):
    operations = _operations()
    attempts = []

    def proposer(existing, new, *, errors, attempt):
        attempts.append(attempt)
        return [
            {
                "operation": "merge",
                "survivor_id": "mem-old",
                "absorbed_ids": ["mem-other"],
                "reason": "",
                "fact": {"kind": "Factual", "content": "x"},
            }
        ]

    prepared, path = operations.prepare_operations(
        [_block("mem-old"), _block("mem-other")],
        [_block("mem-new")],
        session_id="s1",
        run_id="r1",
        session_dir=tmp_path / "s1",
        proposer=proposer,
        max_attempts=5,
    )
    assert attempts == [1, 2, 3, 4, 5]
    assert prepared  # last proposal handed in dirty
    assert path.name == "operations.json"


def test_empty_operation_proposals_still_raise_after_five(tmp_path):
    operations = _operations()
    attempts = []

    def proposer(existing, new, *, errors, attempt):
        attempts.append(attempt)
        return None

    try:
        operations.prepare_operations(
            [_block("mem-old")],
            [_block("mem-new")],
            session_id="s1",
            run_id="r1",
            session_dir=tmp_path / "s1",
            proposer=proposer,
            max_attempts=5,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError when no usable proposal exists")
    assert attempts == [1, 2, 3, 4, 5]


def test_update_operator_stores_operations_without_writing_daily_file(tmp_path, monkeypatch):
    digest = load_plugin_module("digest.py", "memory_digest_operations_digest_test")
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily = tmp_path / "memories" / "staging" / "daily" / "2026-08-02.md"
    daily.parent.mkdir(parents=True)
    original = "---\nid: mem-old\n---\nOld note.\n"
    daily.write_text(original, encoding="utf-8")
    _prepared, operation_path = digest._run_update_operator(
        [_block("mem-old", "Old note.")],
        [_block("mem-new", "New note.")],
        session_id="s1",
        run_id="r1",
    )
    assert daily.read_text(encoding="utf-8") == original
    assert operation_path == (
        tmp_path / "memories" / "staging" / ".tmp_mem_files" / "s1" / "operations.json"
    )


def test_supersede_requires_explicit_confidence_and_existing_target():
    operations = _operations()
    operation = {
        "operation": "supersede",
        "helper_id": "mem-helper",
        "target_id": "mem-target",
        "correction": "The corrected durable statement.",
        "confidence": "explicit",
    }
    assert operations.validate_operation(operation, {"mem-helper", "mem-target"}) == []
    invalid = {**operation, "confidence": "high"}
    assert operations.validate_operation(invalid, {"mem-helper", "mem-target"})


def test_prepare_operations_retries_before_storing_validated_artifact(tmp_path):
    operations = _operations()
    attempts: list[tuple[int, tuple[str, ...]]] = []

    def proposer(existing, new, *, errors, attempt):
        attempts.append((attempt, errors))
        if attempt < 2:
            return [
                {
                    "operation": "merge",
                    "survivor_id": "mem-old",
                    "absorbed_ids": ["mem-other"],
                    "reason": "",
                    "fact": {"kind": "Factual", "content": "x"},
                }
            ]
        return operations.build_update_operations(existing, new)

    prepared, path = operations.prepare_operations(
        [_block("mem-old"), _block("mem-other")],
        [_block("mem-new")],
        session_id="s1",
        run_id="r1",
        session_dir=tmp_path / "operations" / "s1",
        proposer=proposer,
    )
    assert len(prepared) >= 1  # create + optional standalone decay
    assert [item[0] for item in attempts] == [1, 2]
    assert attempts[1][1]
    assert path.name == "operations.json"
    assert path.parent.name == "s1"
    stored = path.read_text(encoding="utf-8")
    assert '"session_id": "s1"' in stored
    assert '"run_id": "r1"' in stored


def test_apply_create_update_and_supersede(monkeypatch):
    operations = _operations()
    monkeypatch.setattr(operations, "hermes_local_today_str", lambda: "2026-08-02")
    blocks = [_block("mem-target"), _block("mem-helper")]
    created = operations.apply_operation(
        {"operation": "create", "block": _block("mem-created")}, blocks
    )
    updated = operations.apply_operation(
        {"operation": "update", "id": "mem-target", "changes": {"body": "Updated."}},
        created,
    )
    corrected = operations.apply_operation(
        {
            "operation": "supersede",
            "helper_id": "mem-helper",
            "target_id": "mem-target",
            "correction": "Corrected.",
            "confidence": "explicit",
        },
        updated,
    )
    assert len(corrected) == 3
    target = next(block for block in corrected if block["id"] == "mem-target")
    helper = next(block for block in corrected if block["id"] == "mem-helper")
    assert target["body"] == "Corrected."
    assert target["superseded_at"] == "2026-08-02"
    assert "supersedes" not in helper
    assert helper["related"] == ["mem-target"]
    assert helper["status"] == "dropped"

    survivors, purged = operations.purge_dropped_blocks(corrected)
    assert purged == ["mem-helper"]
    assert [block["id"] for block in survivors] == ["mem-target", "mem-created"]


def test_event_linking_excludes_supersede_helpers():
    operations = _operations()
    event = {
        **_block("mem-event", "Beginning: request; Course: review; Outcome: corrected."),
        "type": "event",
        "entity": "Project",
        "predicate": "user_requested_review",
        "participants": [
            {"entity": "User", "role": "requester"},
            {"entity": "Assistant", "role": "executor"},
        ],
        "valid_from": "2026-08-02",
        "valid_to": "2026-08-02",
    }
    target = _block("mem-target", "Incorrect note.")
    correction = {**_block("llm-correction", "Correct note."), "supersedes": ["mem-target"]}
    proposed = operations.build_update_operations([event, target], [event, correction])
    helper_id = next(op.block["id"] for op in proposed if op.operation == "create")
    event_updates = [
        op for op in proposed if op.operation == "update" and op.id == "mem-event"
    ]
    assert not event_updates or helper_id not in event_updates[0].changes.get("related", [])


def test_operation_log_records_successful_state_progression(tmp_path):
    operation_log = load_plugin_module("operation_log.py", "memory_digest_operation_log_test")
    log = operation_log.ExecutionLog.create(
        tmp_path / "s1",
        session_id="s1",
        run_id="r1",
        base_content="canonical\n",
        operations=[{"operation": "create", "block": {"id": "mem-new"}}],
    )

    assert log.state == "prepared"
    log.transition("validated")
    log.transition("executing")
    log.transition("candidate_validated")
    log.transition("committed")

    payload = __import__("json").loads((tmp_path / "s1" / "execution.json").read_text())
    assert payload["state"] == "committed"
    assert payload["session_id"] == "s1"
    assert payload["run_id"] == "r1"
    assert payload["base_file_hash"] == operation_log.content_hash("canonical\n")
    assert payload["operations"][0]["operation"] == "create"
    assert payload["state_timestamps"]["committed"]


def test_operation_log_marks_interrupted_execution_failed_without_commit(tmp_path):
    operation_log = load_plugin_module(
        "operation_log.py", "memory_digest_operation_log_recovery_test"
    )
    log = operation_log.ExecutionLog.create(
        tmp_path / "s1",
        session_id="s1",
        run_id="r1",
        base_content="canonical\n",
        operations=[],
    )
    log.transition("validated")
    log.transition("executing")

    recovered = operation_log.recover_interrupted(tmp_path / "s1" / "execution.json")

    assert recovered["state"] == "failed"
    assert recovered["failure"]["kind"] == "interrupted"
    assert recovered["failure"]["prior_state"] == "executing"


def test_operation_log_recovers_every_non_terminal_state_conservatively(tmp_path):
    operation_log = load_plugin_module(
        "operation_log.py", "memory_digest_operation_log_all_recovery_test"
    )
    transitions = {
        "prepared": (),
        "validated": ("validated",),
        "executing": ("validated", "executing"),
        "candidate_validated": ("validated", "executing", "candidate_validated"),
    }

    for state, prior_states in transitions.items():
        session_dir = tmp_path / state
        log = operation_log.ExecutionLog.create(
            session_dir,
            session_id=state,
            run_id="r1",
            base_content="canonical\n",
            operations=[],
        )
        for prior_state in prior_states:
            log.transition(prior_state)

        recovered = operation_log.recover_interrupted(session_dir / "execution.json")

        assert recovered["state"] == "failed"
        assert recovered["failure"]["prior_state"] == state
        assert recovered["failure"]["kind"] == "interrupted"


def test_candidate_validated_interruption_fails_and_cleans_after_terminal_state(
    tmp_path, monkeypatch
):
    digest = load_plugin_module("digest.py", "memory_digest_candidate_interrupt_test")
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily = tmp_path / "memories" / "staging" / "daily" / "2026-08-02.md"
    daily.parent.mkdir(parents=True)
    original = (
        "---\n"
        "id: mem-2026-08-02-old\n"
        "type: fact\n"
        "entity: Project\n"
        "confidence: high\n"
        "status: candidate\n"
        "sources: [session s1]\n"
        "---\n"
        "Old note.\n"
    )
    daily.write_text(original, encoding="utf-8")
    session_dir = tmp_path / "memories" / "staging" / ".tmp_mem_files" / "s1"
    session_dir.mkdir(parents=True)
    operation_path = session_dir / "operations.json"
    operation_path.write_text(
        __import__("json").dumps(
            {
                "status": "validated",
                "session_id": "s1",
                "run_id": "r1",
                "operations": [
                    {
                        "operation": "update",
                        "id": "mem-2026-08-02-old",
                        "changes": {"body": "New note."},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    worker_path = session_dir / "event-result.json"
    worker_path.write_text(
        __import__("json").dumps(
            {
                "status": "validated",
                "session_id": "s1",
                "run_id": "r1",
                "worker_type": "event",
                "blocks": [],
            }
        ),
        encoding="utf-8",
    )
    other_run_path = (
        tmp_path / "memories" / "staging" / ".tmp_mem_files" / "s2" / "event-result.json"
    )
    other_run_path.parent.mkdir(parents=True)
    other_run_path.write_text(
        __import__("json").dumps(
            {"status": "validated", "session_id": "s1", "run_id": "r2"}
        ),
        encoding="utf-8",
    )

    def interrupted_replacement(_path, _content):
        raise OSError("simulated interruption before replacement")

    monkeypatch.setattr(digest, "_rewrite_daily_file", interrupted_replacement)
    assert not digest._commit_candidate(
        daily, [worker_path], operation_path, session_id="s1", run_id="r1", base_content=original
    )

    payload = __import__("json").loads((session_dir / "execution.json").read_text())
    assert payload["state"] == "failed"
    assert payload["failure"]["state"] == "candidate_validated"
    assert daily.read_text(encoding="utf-8") == original
    assert operation_path.exists() is False
    assert worker_path.exists() is False
    assert other_run_path.exists()
    assert (session_dir / "execution.json").exists()


def test_failed_candidate_leaves_canonical_daily_file_unchanged(tmp_path, monkeypatch):
    """Identity mismatch still aborts before rewrite (final soft gate is not the stop)."""
    digest = load_plugin_module("digest.py", "memory_digest_operation_failure_test")
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily = tmp_path / "memories" / "staging" / "daily" / "2026-08-02.md"
    daily.parent.mkdir(parents=True)
    original = "---\nid: mem-old\n---\nOld note.\n"
    daily.write_text(original, encoding="utf-8")

    operation_path = tmp_path / "memories" / "staging" / ".tmp_mem_files" / "s1" / "operations.json"
    operation_path.parent.mkdir(parents=True)
    operation_path.write_text(
        __import__("json").dumps(
            {
                "status": "validated",
                "session_id": "s2",
                "run_id": "r1",
                "operations": [
                    {
                        "operation": "update",
                        "id": "mem-old",
                        "changes": {"body": "Should not land."},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert not digest._commit_candidate(
        daily,
        [],
        operation_path,
        session_id="s1",
        run_id="r1",
        base_content=original,
    )
    assert daily.read_text(encoding="utf-8") == original
    execution = tmp_path / "memories" / "staging" / ".tmp_mem_files" / "s1" / "execution.json"
    payload = __import__("json").loads(execution.read_text(encoding="utf-8"))
    assert payload["state"] == "failed"


def test_successful_candidate_reaches_committed_after_replacement(tmp_path, monkeypatch):
    digest = load_plugin_module("digest.py", "memory_digest_operation_success_test")
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily = tmp_path / "memories" / "staging" / "daily" / "2026-08-02.md"
    daily.parent.mkdir(parents=True)
    original = (
        "---\n"
        "id: mem-2026-08-02-old\n"
        "type: fact\n"
        "entity: Project\n"
        "confidence: high\n"
        "status: candidate\n"
        "sources: [session s1]\n"
        "---\n"
        "Old note.\n"
    )
    daily.write_text(original, encoding="utf-8")
    operation_path = tmp_path / "memories" / "staging" / ".tmp_mem_files" / "s1" / "operations.json"
    operation_path.parent.mkdir(parents=True)
    operation_path.write_text(
        __import__("json").dumps(
            {
                "status": "validated",
                "session_id": "s1",
                "run_id": "r1",
                "operations": [
                    {
                        "operation": "update",
                        "id": "mem-2026-08-02-old",
                        "changes": {"body": "New note."},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert digest._commit_candidate(
        daily,
        [],
        operation_path,
        session_id="s1",
        run_id="r1",
        base_content=original,
    )
    assert "New note." in daily.read_text(encoding="utf-8")
    payload = __import__("json").loads(
        (operation_path.parent / "execution.json").read_text(encoding="utf-8")
    )
    assert payload["state"] == "committed"
    assert payload["state_timestamps"]["candidate_validated"] <= payload["state_timestamps"]["committed"]
    assert operation_path.exists() is False


def test_replacement_success_with_commit_log_failure_is_uncertain(tmp_path, monkeypatch):
    digest = load_plugin_module("digest.py", "memory_digest_uncertain_commit_test")
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily = tmp_path / "memories" / "staging" / "daily" / "2026-08-02.md"
    daily.parent.mkdir(parents=True)
    original = (
        "---\n"
        "id: mem-2026-08-02-old\n"
        "type: fact\n"
        "entity: Project\n"
        "confidence: high\n"
        "status: candidate\n"
        "sources: [session s1]\n"
        "---\n"
        "Old note.\n"
    )
    daily.write_text(original, encoding="utf-8")
    operation_path = (
        tmp_path / "memories" / "staging" / ".tmp_mem_files" / "s1" / "operations.json"
    )
    operation_path.parent.mkdir(parents=True)
    operation_path.write_text(
        __import__("json").dumps(
            {
                "status": "validated",
                "session_id": "s1",
                "run_id": "r1",
                "operations": [
                    {
                        "operation": "update",
                        "id": "mem-2026-08-02-old",
                        "changes": {"body": "Replacement succeeded."},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    original_transition = digest.digest_operation_log.ExecutionLog.transition

    def fail_commit_log(self, state, **kwargs):
        if state == "committed":
            raise OSError("simulated commit-log failure")
        return original_transition(self, state, **kwargs)

    monkeypatch.setattr(
        digest.digest_operation_log.ExecutionLog, "transition", fail_commit_log
    )
    assert not digest._commit_candidate(
        daily,
        [],
        operation_path,
        session_id="s1",
        run_id="r1",
        base_content=original,
    )
    assert "Replacement succeeded." in daily.read_text(encoding="utf-8")
    execution = operation_path.parent / "execution.json"
    payload = __import__("json").loads(execution.read_text(encoding="utf-8"))
    assert payload["state"] == "uncertain"
    assert payload["failure"]["kind"] == "recovery_required"
    assert operation_path.exists() is False


def test_write_failure_after_replacement_is_recovered_as_uncertain(tmp_path, monkeypatch):
    digest = load_plugin_module("digest.py", "memory_digest_write_failure_recovery_test")
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily = tmp_path / "memories" / "staging" / "daily" / "2026-08-02.md"
    daily.parent.mkdir(parents=True)
    original = (
        "---\n"
        "id: mem-2026-08-02-old\n"
        "type: fact\n"
        "entity: Project\n"
        "confidence: high\n"
        "status: candidate\n"
        "sources: [session s1]\n"
        "---\n"
        "Old note.\n"
    )
    daily.write_text(original, encoding="utf-8")
    operation_path = (
        tmp_path / "memories" / "staging" / ".tmp_mem_files" / "s1" / "operations.json"
    )
    operation_path.parent.mkdir(parents=True)
    operation_path.write_text(
        __import__("json").dumps(
            {
                "status": "validated",
                "session_id": "s1",
                "run_id": "r1",
                "operations": [
                    {
                        "operation": "update",
                        "id": "mem-2026-08-02-old",
                        "changes": {"body": "Replacement succeeded."},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    original_write = digest.digest_operation_log.ExecutionLog._write
    failed_once = {"value": False}

    def write_then_fail(self):
        if self.payload.get("state") == "committed" and not failed_once["value"]:
            failed_once["value"] = True
            original_write(self)
            raise OSError("write failed after committed payload persisted")
        return original_write(self)

    monkeypatch.setattr(digest.digest_operation_log.ExecutionLog, "_write", write_then_fail)
    assert not digest._commit_candidate(
        daily,
        [],
        operation_path,
        session_id="s1",
        run_id="r1",
        base_content=original,
    )
    assert "Replacement succeeded." in daily.read_text(encoding="utf-8")
    payload = __import__("json").loads(
        (operation_path.parent / "execution.json").read_text(encoding="utf-8")
    )
    assert payload["state"] == "uncertain"
    assert payload["failure"]["kind"] == "recovery_required"
    assert payload["state"] != "committed"
    assert payload["state"] != "failed"


def test_merge_unions_involves_by_entity_and_keeps_narration_body():
    operations = _operations()
    survivor = {
        **_block("mem-survivor"),
        "body": "Narration: Jordan lives in a school dorm so dates default outside.",
        "involves": [{"entity": "Alex Chen", "role": "partner"}],
        "importance": 4,
    }
    absorbed = {
        **_block("mem-absorbed"),
        "body": "Jordan dislikes cilantro.",
        "involves": [{"entity": "Alex Chen"}, {"entity": "Roommate"}],
        "importance": 3,
    }
    merged = operations.apply_operation(
        {
            "operation": "merge",
            "survivor_id": "mem-survivor",
            "absorbed_ids": ["mem-absorbed"],
            "reason": "same Jordan cast story",
        },
        [survivor, absorbed],
    )
    out = next(block for block in merged if block["id"] == "mem-survivor")
    assert out["body"].startswith("Narration:")
    by_entity = {item["entity"]: item for item in out["involves"]}
    assert by_entity["Alex Chen"]["role"] == "partner"
    assert by_entity["Roommate"] == {"entity": "Roommate"}


def test_merge_two_plain_facts_with_cast_becomes_narration():
    operations = _operations()
    survivor = {
        **_block("mem-survivor"),
        "body": "Jordan lives in a school dorm.",
        "involves": [{"entity": "Alex Chen"}],
        "importance": 4,
    }
    absorbed = {
        **_block("mem-absorbed"),
        "body": "Jordan dislikes cilantro.",
        "involves": [{"entity": "Roommate"}],
        "importance": 3,
    }
    merged = operations.apply_operation(
        {
            "operation": "merge",
            "survivor_id": "mem-survivor",
            "absorbed_ids": ["mem-absorbed"],
            "reason": "same Jordan cast story",
        },
        [survivor, absorbed],
    )
    out = next(block for block in merged if block["id"] == "mem-survivor")
    assert out["body"].startswith("Narration:")
    entities = {item["entity"] for item in out["involves"]}
    assert entities == {"Alex Chen", "Roommate"}


def test_merge_nested_event_rewrites_body_without_concat():
    operations = _operations()
    survivor = {
        **_block("mem-e-surv"),
        "type": "event",
        "body": "Beginning: old start; Course: old mid; Outcome: old end",
        "importance": 5,
    }
    absorbed = {
        **_block("mem-e-abs"),
        "type": "event",
        "body": "Beginning: other; Course: other; Outcome: other",
        "importance": 4,
    }
    merged = operations.apply_operation(
        {
            "operation": "merge",
            "survivor_id": "mem-e-surv",
            "absorbed_ids": ["mem-e-abs"],
            "reason": "C evolution",
            "event": {
                "beginning": "stall investigated after unfinished report",
                "course": "ImportError recovered then batch B failed",
                "outcome": "daily file not written",
            },
        },
        [survivor, absorbed],
    )
    out = next(block for block in merged if block["id"] == "mem-e-surv")
    assert out["body"].startswith("Beginning: stall investigated")
    assert "old start" not in out["body"]
    assert "Beginning: other" not in out["body"]
    assert "mem-e-abs" not in {b["id"] for b in merged}


def test_merge_nested_fact_factual_into_narration_drops_absorbed():
    operations = _operations()
    survivor = {
        **_block("mem-n-surv"),
        "body": "Narration: Jordan lives in a school dorm so dates default outside.",
        "involves": [{"entity": "Alex Chen"}, {"entity": "Roommate"}],
        "importance": 4,
    }
    absorbed = {
        **_block("mem-f-abs"),
        "body": "Factual: Jordan dislikes cilantro.",
        "importance": 3,
    }
    op = {
        "operation": "merge",
        "survivor_id": "mem-n-surv",
        "absorbed_ids": ["mem-f-abs"],
        "reason": "absorb Factual into Narration",
        "fact": {
            "kind": "Narration",
            "content": (
                "Jordan lives in a school dorm so dates default outside; "
                "dislikes cilantro"
            ),
        },
    }
    blocks = {b["id"]: b for b in [survivor, absorbed]}
    assert operations.validate_operation(
        op, blocks, blocks_by_id=blocks
    ) == []
    merged = operations.apply_operation(op, [survivor, absorbed])
    assert {b["id"] for b in merged} == {"mem-n-surv"}
    out = merged[0]
    assert out["body"].startswith("Narration:")
    assert "dislikes cilantro" in out["body"]
    assert "Factual:" not in out["body"]


def test_merge_validate_rejects_wrong_nest_and_empty_slots():
    operations = _operations()
    survivor = {
        **_block("mem-e-surv"),
        "type": "event",
        "body": "Beginning: a; Course: b; Outcome: c",
    }
    absorbed = {
        **_block("mem-e-abs"),
        "type": "event",
        "body": "Beginning: x; Course: y; Outcome: z",
    }
    blocks = {b["id"]: b for b in [survivor, absorbed]}
    wrong = operations.validate_operation(
        {
            "operation": "merge",
            "survivor_id": "mem-e-surv",
            "absorbed_ids": ["mem-e-abs"],
            "reason": "C evolution",
            "fact": {"kind": "Factual", "content": "wrong nest"},
        },
        blocks,
        blocks_by_id=blocks,
    )
    assert any("survivor type" in e or "unexpected nest" in e for e in wrong)
    empty = operations.validate_operation(
        {
            "operation": "merge",
            "survivor_id": "mem-e-surv",
            "absorbed_ids": ["mem-e-abs"],
            "reason": "C evolution",
            "event": {"beginning": "", "course": "c", "outcome": "o"},
        },
        blocks,
        blocks_by_id=blocks,
    )
    assert any("beginning" in e for e in empty)


def test_merge_validate_requires_narration_kind_for_cast():
    operations = _operations()
    survivor = {
        **_block("mem-n-surv"),
        "body": "Narration: Jordan lives in a school dorm.",
        "involves": [{"entity": "Alex Chen"}, {"entity": "Roommate"}],
    }
    absorbed = {
        **_block("mem-f-abs"),
        "body": "Factual: Jordan dislikes cilantro.",
    }
    blocks = {b["id"]: b for b in [survivor, absorbed]}
    errors = operations.validate_operation(
        {
            "operation": "merge",
            "survivor_id": "mem-n-surv",
            "absorbed_ids": ["mem-f-abs"],
            "reason": "cast story",
            "fact": {"kind": "Factual", "content": "should have been Narration"},
        },
        blocks,
        blocks_by_id=blocks,
    )
    assert any("Narration" in e for e in errors)


def test_merge_truncates_rendered_nest_body_to_max():
    operations = _operations()
    long = "x" * 600
    survivor = {**_block("mem-ff-surv"), "body": "Factual: short", "importance": 4}
    absorbed = {**_block("mem-ff-abs"), "body": "Factual: other", "importance": 3}
    merged = operations.apply_operation(
        {
            "operation": "merge",
            "survivor_id": "mem-ff-surv",
            "absorbed_ids": ["mem-ff-abs"],
            "reason": "restatement",
            "fact": {"kind": "Factual", "content": long},
        },
        [survivor, absorbed],
    )
    out = next(block for block in merged if block["id"] == "mem-ff-surv")
    assert len(out["body"]) <= operations.MAX_BODY_CHARS
    assert out["body"].endswith("…")


def test_merge_into_emits_nested_slots_from_survivor():
    operations = _operations()
    existing = [
        {
            **_block("mem-survivor"),
            "body": "Factual: Jordan prefers outdoor dates",
            "importance": 4,
        },
        {
            **_block("mem-duplicate"),
            "body": "Factual: Jordan prefers outdoor dates",
            "importance": 3,
        },
    ]
    ops = operations.build_update_operations(
        existing,
        [
            {
                **_block("mem-duplicate"),
                "merge_into": "mem-survivor",
                "body": "Factual: Jordan prefers outdoor dates",
            }
        ],
    )
    merge_ops = [op for op in ops if op.operation == "merge"]
    assert len(merge_ops) == 1
    assert merge_ops[0].fact is not None
    assert merge_ops[0].fact.get("kind") == "Factual"
    assert "outdoor" in merge_ops[0].fact.get("content", "")


def test_merge_rejects_legacy_body_field():
    operations = _operations()
    errors = operations.validate_operation(
        {
            "operation": "merge",
            "survivor_id": "mem-a",
            "absorbed_ids": ["mem-b"],
            "reason": "restatement",
            "body": "should not be allowed",
        },
        {"mem-a", "mem-b"},
    )
    assert any("body" in e for e in errors)


def test_merge_decision_nest_rejects_third_party_ruling():
    operations = _operations()
    survivor = {
        **_block("mem-d-surv"),
        "type": "decision",
        "body": "Decision: user must not auto-drop events",
        "importance": 4,
    }
    absorbed = {
        **_block("mem-d-abs"),
        "type": "decision",
        "body": "Decision: user keep the request skeleton",
        "importance": 3,
    }
    blocks = {b["id"]: b for b in [survivor, absorbed]}
    errors = operations.validate_operation(
        {
            "operation": "merge",
            "survivor_id": "mem-d-surv",
            "absorbed_ids": ["mem-d-abs"],
            "reason": "same standing pref",
            "decision": {
                "kind": "Decision",
                "subject": "user",
                "ruling": "Jordan dislikes cilantro",
            },
        },
        blocks,
        blocks_by_id=blocks,
    )
    assert errors
    assert any("predicate" in e.lower() or "third party" in e.lower() for e in errors)


def test_merge_into_nest_slots_parses_decision_predicate():
    operations = _operations()
    slots = operations._merge_into_nest_slots(
        {
            "id": "mem-d",
            "type": "decision",
            "body": "Decision: user wants tea",
        },
        "decision",
    )
    assert slots is not None
    assert slots.get("subject") == "user"
    assert slots.get("ruling") == "wants tea"
    assert "Decision:" not in slots.get("ruling", "")


def test_external_update_must_be_metadata_only():
    operations = _operations()
    old = {
        **_block("mem-2026-08-01-fact-aaaaaaaaaaaa"),
        "entity": "Canteen",
        "valid_from": "2026-08-01",
        "valid_to": "open",
    }
    new = {
        **_block("mem-2026-08-20-fact-bbbbbbbbbbbb"),
        "entity": "Canteen",
        "valid_from": "2026-08-20",
        "valid_to": "open",
    }
    allowed = operations.check_type_rules(
        [old, new],
        [
            {
                "operation": "update",
                "id": old["id"],
                "changes": {
                    "valid_to": "2026-08-20",
                    "status": "rejected",
                    "rejected_reason": "rejected by mem-2026-08-20-fact-bbbbbbbbbbbb",
                },
            }
        ],
        current_day="2026-08-20",
        retrieval_ids=[old["id"], new["id"]],
    )
    assert allowed == []

    body_change = operations.check_type_rules(
        [old, new],
        [{"operation": "update", "id": old["id"], "changes": {"body": "rewritten"}}],
        current_day="2026-08-20",
        retrieval_ids=[old["id"], new["id"]],
    )
    assert body_change

    merge = operations.check_type_rules(
        [old, new],
        [
            {
                "operation": "merge",
                "survivor_id": new["id"],
                "absorbed_ids": [old["id"]],
                "reason": "same canteen",
                "fact": {"kind": "Factual", "content": "x"},
            }
        ],
        current_day="2026-08-20",
        retrieval_ids=[old["id"], new["id"]],
    )
    assert merge

    bad_reason = operations.check_type_rules(
        [old, new],
        [
            {
                "operation": "update",
                "id": old["id"],
                "changes": {
                    "valid_to": "2026-08-20",
                    "status": "rejected",
                    "rejected_reason": "nope",
                },
            }
        ],
        current_day="2026-08-20",
        retrieval_ids=[old["id"], new["id"]],
    )
    assert bad_reason

    too_early = operations.check_type_rules(
        [old, new],
        [
            {
                "operation": "update",
                "id": old["id"],
                "changes": {
                    "valid_to": "2026-07-01",
                    "status": "rejected",
                    "rejected_reason": "rejected by user's correction",
                },
            }
        ],
        current_day="2026-08-20",
        retrieval_ids=[old["id"], new["id"]],
    )
    assert too_early