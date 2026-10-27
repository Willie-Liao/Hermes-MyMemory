"""Phase 2: type-priority guardrails on proposer output (check_type_rules)."""

from __future__ import annotations

import itertools

import pytest

from conftest import load_plugin_module


def _operations():
    return load_plugin_module("operations.py", "memory_digest_type_rules_operations")


def _block(block_id: str, block_type: str, *, importance: int = 3, **extra) -> dict:
    return {
        "id": block_id,
        "type": block_type,
        "confidence": "high",
        "status": "candidate",
        "sources": ["session s1"],
        "importance": importance,
        "body": f"{block_type} body for {block_id}.",
        **extra,
    }


TYPES = ("event", "decision", "procedure", "fact")


@pytest.mark.parametrize("survivor_type,absorbed_type", list(itertools.product(TYPES, TYPES)))
def test_merge_is_allowed_only_within_a_type(survivor_type, absorbed_type):
    operations = _operations()
    existing = [
        _block("mem-a", survivor_type, importance=4),
        _block("mem-b", absorbed_type, importance=4),
    ]
    errors = operations.check_type_rules(
        existing,
        [
            {
                "operation": "merge",
                "survivor_id": "mem-a",
                "absorbed_ids": ["mem-b"],
                "reason": "A follow-up: same thread advanced",
            }
        ],
    )
    if survivor_type == absorbed_type:
        assert errors == []
    else:
        assert any("merge must not cross types" in error for error in errors)


def test_merge_survivor_must_hold_the_higher_importance():
    operations = _operations()
    existing = [
        _block("mem-low", "decision", importance=1),
        _block("mem-high", "decision", importance=4),
    ]
    assert any(
        "has lower importance" in error
        for error in operations.check_type_rules(
            existing,
            [
                {
                    "operation": "merge",
                    "survivor_id": "mem-low",
                    "absorbed_ids": ["mem-high"],
                    "reason": "Restatement: identical decision text",
                }
            ],
        )
    )
    assert operations.check_type_rules(
        existing,
        [
            {
                "operation": "merge",
                "survivor_id": "mem-high",
                "absorbed_ids": ["mem-low"],
                "reason": "Restatement: identical decision text; keep higher importance",
            }
        ],
    ) == []


def test_cross_type_drop_of_the_lower_priority_block_is_allowed():
    operations = _operations()
    existing = [
        _block("mem-decision", "decision", importance=4),
        _block("mem-fact", "fact", importance=4),
    ]
    assert operations.check_type_rules(
        existing,
        [
            {
                "operation": "drop",
                "id": "mem-fact",
                "reason": "Cross-type extension; TYPE_PRIORITY decision>fact; pure drop",
            }
        ],
    ) == []


def test_dropping_the_higher_priority_block_is_rejected():
    operations = _operations()
    existing = [
        _block("mem-decision", "decision", importance=4),
        _block("mem-fact", "fact", importance=4),
    ]
    assert any(
        "no block that outranks it" in error
        for error in operations.check_type_rules(
            existing,
            [
                {
                    "operation": "drop",
                    "id": "mem-decision",
                    "reason": "wrongly dropping the winner",
                }
            ],
        )
    )


def test_dropping_an_event_is_always_rejected():
    operations = _operations()
    existing = [
        _block("mem-event", "event", importance=3),
        _block("mem-other-event", "event", importance=5),
    ]
    assert any(
        "must not drop event mem-event" in error
        for error in operations.check_type_rules(
            existing,
            [{"operation": "drop", "id": "mem-event", "reason": "duplicate episode"}],
        )
    )


def test_dropping_a_fact_an_event_cites_is_allowed():
    """Rule 2: cross-type drop is legal even if an event cites the loser; purge scrubs related."""
    operations = _operations()
    existing = [
        _block("mem-event", "event", importance=4, related=["mem-fact"]),
        _block("mem-decision", "decision", importance=4),
        _block("mem-fact", "fact", importance=4),
    ]
    assert (
        operations.check_type_rules(
            existing,
            [
                {
                    "operation": "drop",
                    "id": "mem-fact",
                    "reason": "Cross-type extension of the decision; pure drop",
                }
            ],
        )
        == []
    )


def test_a_freshly_created_event_citing_does_not_block_drop():
    operations = _operations()
    existing = [
        _block("mem-decision", "decision", importance=4),
        _block("mem-fact", "fact", importance=4),
    ]
    proposed = [
        {
            "operation": "create",
            "block": _block(
                "mem-2026-08-08-event-NEW", "event", importance=4, related=["mem-fact"]
            ),
        },
        {
            "operation": "drop",
            "id": "mem-fact",
            "reason": "Cross-type extension of the decision; pure drop",
        },
    ]
    assert operations.check_type_rules(existing, proposed) == []


def test_an_update_that_adds_a_citation_does_not_block_drop():
    operations = _operations()
    existing = [
        _block("mem-event", "event", importance=4),
        _block("mem-decision", "decision", importance=4),
        _block("mem-fact", "fact", importance=4),
    ]
    proposed = [
        {"operation": "update", "id": "mem-event", "changes": {"related": ["mem-fact"]}},
        {
            "operation": "drop",
            "id": "mem-fact",
            "reason": "Cross-type extension of the decision; pure drop",
        },
    ]
    assert operations.check_type_rules(existing, proposed) == []


def test_reasonless_merge_and_drop_are_rejected():
    operations = _operations()
    existing = [
        _block("mem-a", "decision", importance=4),
        _block("mem-b", "decision", importance=3),
        _block("mem-fact", "fact", importance=3),
    ]
    assert any(
        "merge requires a non-empty reason" in error
        for error in operations.check_type_rules(
            existing,
            [{"operation": "merge", "survivor_id": "mem-a", "absorbed_ids": ["mem-b"], "reason": "  "}],
        )
    )
    assert any(
        "drop requires a non-empty reason" in error
        for error in operations.check_type_rules(
            existing, [{"operation": "drop", "id": "mem-fact", "reason": ""}]
        )
    )


def test_type_is_read_from_the_field_not_the_id_prefix():
    """Legacy op- and mem-20260801-slug ids must not be used to infer type."""
    operations = _operations()
    existing = [
        _block("op-1", "decision", importance=4),
        _block("mem-20260801-some-slug", "fact", importance=4),
    ]
    assert operations.check_type_rules(
        existing,
        [
            {
                "operation": "drop",
                "id": "mem-20260801-some-slug",
                "reason": "Cross-type extension of the decision; pure drop",
            }
        ],
    ) == []
    assert any(
        "no block that outranks it" in error
        for error in operations.check_type_rules(
            existing, [{"operation": "drop", "id": "op-1", "reason": "wrong direction"}]
        )
    )


def test_decision_constraint_alias_counts_as_decision():
    operations = _operations()
    existing = [
        _block("mem-a", "decision_constraint", importance=4),
        _block("mem-b", "decision", importance=3),
    ]
    assert operations.check_type_rules(
        existing,
        [
            {
                "operation": "merge",
                "survivor_id": "mem-a",
                "absorbed_ids": ["mem-b"],
                "reason": "B specification: same rule, survivor is concrete",
            }
        ],
    ) == []


def test_guardrail_errors_reach_the_proposer_on_the_next_attempt(tmp_path):
    operations = _operations()
    existing = [
        _block("mem-a", "event", importance=4),
        _block("mem-b", "event", importance=3),
    ]
    attempts: list[tuple[int, tuple[str, ...]]] = []

    def proposer(existing_blocks, new_blocks, *, errors, attempt):
        attempts.append((attempt, errors))
        if attempt < 2:
            return [
                {
                    "operation": "merge",
                    "survivor_id": "mem-a",
                    "absorbed_ids": ["mem-b"],
                    "reason": "  ",
                    "event": {"beginning": "a", "course": "b", "outcome": "c"},
                }
            ]
        return []

    prepared, path = operations.prepare_operations(
        existing,
        [],
        session_id="s1",
        run_id="r1",
        session_dir=tmp_path / "s1",
        proposer=proposer,
    )
    assert prepared == []
    assert [attempt for attempt, _errors in attempts] == [1, 2]
    assert any("non-empty reason" in error for error in attempts[1][1])
    assert path.name == "operations.json"


def test_rewrite_cross_type_merge_becomes_drop():
    operations = _operations()
    existing = [
        _block("mem-event", "event", importance=4),
        _block("mem-fact", "fact", importance=4),
    ]
    rewritten = operations.rewrite_type_priority_ops(
        existing,
        [
            {
                "operation": "merge",
                "survivor_id": "mem-event",
                "absorbed_ids": ["mem-fact"],
                "reason": "same thread",
                "event": {"beginning": "a", "course": "b", "outcome": "c"},
            }
        ],
    )
    assert [(op.operation, op.id) for op in rewritten] == [("drop", "mem-fact")]
    assert operations.check_type_rules(existing, rewritten) == []


def test_rewrite_swaps_merge_survivor_to_higher_importance():
    operations = _operations()
    existing = [
        _block("mem-low", "decision", importance=1),
        _block("mem-high", "decision", importance=4),
    ]
    rewritten = operations.rewrite_type_priority_ops(
        existing,
        [
            {
                "operation": "merge",
                "survivor_id": "mem-low",
                "absorbed_ids": ["mem-high"],
                "reason": "restatement",
                "decision": {"kind": "Decision", "subject": "user", "ruling": "keep"},
            }
        ],
    )
    assert len(rewritten) == 1
    assert rewritten[0].operation == "merge"
    assert rewritten[0].survivor_id == "mem-high"
    assert rewritten[0].absorbed_ids == ["mem-low"]
    assert operations.check_type_rules(existing, rewritten) == []


def test_rewrite_deletes_drop_of_event_and_non_outranked():
    operations = _operations()
    assert (
        operations.rewrite_type_priority_ops(
            [_block("mem-event", "event", importance=3)],
            [{"operation": "drop", "id": "mem-event", "reason": "no"}],
        )
        == []
    )
    rewritten = operations.rewrite_type_priority_ops(
        [
            _block("mem-fact-a", "fact", importance=4),
            _block("mem-fact-b", "fact", importance=4),
        ],
        [{"operation": "drop", "id": "mem-fact-a", "reason": "nothing outranks"}],
    )
    assert rewritten == []
    assert operations.check_type_rules(
        [
            _block("mem-fact-a", "fact", importance=4),
            _block("mem-fact-b", "fact", importance=4),
        ],
        rewritten,
    ) == []


def test_sanitize_operations_drops_junk_fields_and_missing_targets():
    operations = _operations()
    cleaned = operations.sanitize_operations_list(
        [
            {
                "operation": "update",
                "id": "mem-old",
                "changes": {"body": "Changed."},
                "unexpected": True,
            },
            {"operation": "update", "id": "missing", "changes": {"body": "bad"}},
            {"operation": "update", "id": "mem-old", "changes": {}},
        ],
        {"mem-old"},
    )
    assert len(cleaned) == 1
    assert cleaned[0].operation == "update"
    assert cleaned[0].unknown_fields == ()
    assert "unexpected" not in cleaned[0].to_dict()


def test_prepare_rewrites_cross_type_merge_without_retry(tmp_path):
    operations = _operations()
    existing = [
        _block("mem-event", "event", importance=4),
        _block("mem-fact", "fact", importance=4),
    ]

    def proposer(existing_blocks, new_blocks, *, errors, attempt):
        del existing_blocks, new_blocks, errors
        assert attempt == 1
        return [
            {
                "operation": "merge",
                "survivor_id": "mem-event",
                "absorbed_ids": ["mem-fact"],
                "reason": "same thread",
                "event": {"beginning": "a", "course": "b", "outcome": "c"},
            }
        ]

    prepared, _path = operations.prepare_operations(
        existing,
        [],
        session_id="s1",
        run_id="r1",
        session_dir=tmp_path / "s1",
        proposer=proposer,
    )
    assert any(op.operation == "drop" and op.id == "mem-fact" for op in prepared)
    assert not any(op.operation == "merge" for op in prepared)


def test_scrub_related_keeps_week_alive_drops_missing():
    from conftest import load_plugin_module

    composition = load_plugin_module(
        "composition.py", "memory_digest_type_rules_composition"
    )
    digest = load_plugin_module("digest.py", "memory_digest_type_rules_digest")
    blocks = [
        _block(
            "mem-keep",
            "fact",
            related=["mem-alive", "mem-gone", "mem-retire"],
        )
    ]
    scrubbed = composition.scrub_related_on_blocks(
        blocks, keep_ids={"mem-keep", "mem-alive"}
    )
    assert scrubbed[0]["related"] == ["mem-alive"]
    content = (
        "---\n"
        "id: mem-keep\n"
        "type: fact\n"
        "related: [mem-alive, mem-gone, mem-retire]\n"
        "---\n"
        "Factual: keep.\n"
    )
    out = digest._scrub_dangling_related(
        content,
        extra_keep_ids={"mem-alive"},
        retiring_ids={"mem-retire"},
    )
    assert "mem-alive" in out
    assert "mem-gone" not in out
    assert "mem-retire" not in out


def test_scrub_illegal_sources_strips_staging_keeps_real_file(tmp_path):
    digest = load_plugin_module("digest.py", "memory_digest_type_rules_sources")
    content = (
        "---\n"
        "id: mem-keep\n"
        "type: fact\n"
        "entity: Topic\n"
        "confidence: high\n"
        "importance: 3\n"
        "status: candidate\n"
        "sources: [session 20260722_172657_c54f77a8, "
        "file:/root/Me/Personal/notes/note.md, "
        "file:/root/.hermes/memories/staging/daily/2026-08-15.md, "
        "2026-08-15.md]\n"
        "---\n"
        "Factual: keep.\n"
    )
    out = digest._scrub_illegal_sources(content)
    assert "session 20260722_172657_c54f77a8" in out
    assert "file:/root/Me/Personal/notes/note.md" in out
    assert "memories/staging" not in out
    assert "2026-08-15.md" not in out
