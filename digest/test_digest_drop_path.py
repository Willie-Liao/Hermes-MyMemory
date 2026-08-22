"""Phase 1: a block's legal exit — drop, purge, reference scrub, supersede."""

from __future__ import annotations

import json

from conftest import load_plugin_module


def _digest(name: str = "memory_digest_drop_path_test"):
    return load_plugin_module("digest.py", name)


def _operations():
    return load_plugin_module("operations.py", "memory_digest_drop_path_operations")


def _fact(block_id: str, *, body: str = "Project uses a single shared owner.", status: str = "candidate") -> str:
    return "\n".join(
        [
            "---",
            f"id: {block_id}",
            "type: fact",
            "entity: Project",
            "confidence: high",
            f"status: {status}",
            "sources: [session s1]",
            "---",
            body,
        ]
    )


def _event(block_id: str, *, related: str = "", body: str = "User asked for a review; assistant delivered it.") -> str:
    lines = [
        "---",
        f"id: {block_id}",
        "type: event",
        "entity: Project",
        "predicate: user_requested_review",
        "participants:",
        "  - {entity: User, role: requester}",
        "  - {entity: Assistant, role: executor}",
    ]
    if related:
        lines.append(f"related: [{related}]")
    lines += [
        "valid_from: 2026-08-02",
        "valid_to: 2026-08-02",
        "confidence: high",
        "status: candidate",
        "sources: [session s1]",
        "---",
        body,
    ]
    return "\n".join(lines)


def _artifact(operations: list[dict]) -> dict:
    return {
        "status": "validated",
        "session_id": "s1",
        "run_id": "r1",
        "operations": operations,
    }


def _daily(tmp_path, content: str):
    daily = tmp_path / "memories" / "staging" / "daily" / "2026-08-02.md"
    daily.parent.mkdir(parents=True)
    daily.write_text(content, encoding="utf-8")
    return daily


def _commit_once(digest, daily, operations, *, base_content):
    return digest._commit_candidate_once(
        daily,
        [],
        _artifact(operations),
        session_id="s1",
        run_id="r1",
        base_content=base_content,
    )


# --- S2: merge-absorbed events may leave the file -------------------------


def test_merge_absorbing_an_event_commits(tmp_path, monkeypatch):
    digest = _digest("memory_digest_event_merge_test")
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    original = (
        "\n\n".join(
            [
                _event("mem-2026-08-02-event-SURVIVOR"),
                _event("mem-2026-08-02-event-ABSORBED", body="User asked again; assistant repeated the review."),
            ]
        )
        + "\n"
    )
    daily = _daily(tmp_path, original)

    ok, errors = _commit_once(
        digest,
        daily,
        [
            {
                "operation": "merge",
                "survivor_id": "mem-2026-08-02-event-SURVIVOR",
                "absorbed_ids": ["mem-2026-08-02-event-ABSORBED"],
                "reason": "C evolution: same review episode",
            }
        ],
        base_content=original,
    )
    assert (ok, errors) == (True, [])
    written = daily.read_text(encoding="utf-8")
    assert "mem-2026-08-02-event-ABSORBED" not in written
    assert "mem-2026-08-02-event-SURVIVOR" in written


def test_event_disappearing_without_a_merge_is_still_rejected(tmp_path, monkeypatch):
    """The S2 exemption must be exactly as wide as merge absorption."""
    digest = _digest("memory_digest_event_stability_test")
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    original = "\n\n".join([_event("mem-2026-08-02-event-KEEP"), _fact("mem-2026-08-02-fact-OTHER")]) + "\n"
    daily = _daily(tmp_path, original)

    before = digest._daily_blocks(original)
    after = "\n\n".join(
        digest._render_digest_block({k: v for k, v in block.items() if k != "body"}, str(block.get("body", "")))
        for block in before
        if block["id"] != "mem-2026-08-02-event-KEEP"
    )
    errors = digest._candidate_invariant_errors(original, after, [])
    assert any("changed stable event id: mem-2026-08-02-event-KEEP" in error for error in errors)


# --- S3: drop, purge, reference scrub -------------------------------------


def test_drop_marks_without_deleting_and_purge_removes(tmp_path):
    operations = _operations()
    blocks = [{"id": "mem-a", "status": "candidate"}, {"id": "mem-b", "status": "candidate"}]
    marked = operations.apply_operation(
        {"operation": "drop", "id": "mem-b", "reason": "cross-type extension loser"}, blocks
    )
    assert [block["id"] for block in marked] == ["mem-a", "mem-b"]
    assert marked[1]["status"] == "dropped"

    survivors, purged = operations.purge_dropped_blocks(marked)
    assert purged == ["mem-b"]
    assert [block["id"] for block in survivors] == ["mem-a"]


def test_purge_scrubs_references_to_purged_ids(tmp_path):
    operations = _operations()
    blocks = [
        {"id": "mem-event", "type": "event", "related": ["mem-detail", "mem-keep"]},
        {"id": "mem-detail", "type": "fact", "status": "dropped"},
        {"id": "mem-keep", "type": "fact", "status": "candidate"},
    ]
    survivors, purged = operations.purge_dropped_blocks(blocks)
    assert purged == ["mem-detail"]
    assert survivors[0]["related"] == ["mem-keep"]


def test_purge_drops_the_reference_key_when_nothing_survives_it(tmp_path):
    operations = _operations()
    blocks = [
        {"id": "mem-event", "type": "event", "related": ["mem-detail"]},
        {"id": "mem-detail", "type": "fact", "status": "dropped"},
    ]
    survivors, _purged = operations.purge_dropped_blocks(blocks)
    assert "related" not in survivors[0]


def test_dropping_a_cited_detail_leaves_no_dangling_reference(tmp_path, monkeypatch):
    """The failure mode that would otherwise only surface in Phase 4."""
    digest = _digest("memory_digest_drop_dangling_test")
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    original = (
        "\n\n".join(
            [
                _fact("mem-2026-08-02-fact-CITED"),
                _fact("mem-2026-08-02-fact-KEPT", body="Project stores notes in one folder."),
                _event("mem-2026-08-02-event-MAIN", related="mem-2026-08-02-fact-CITED"),
            ]
        )
        + "\n"
    )
    daily = _daily(tmp_path, original)

    ok, errors = _commit_once(
        digest,
        daily,
        [
            {
                "operation": "drop",
                "id": "mem-2026-08-02-fact-CITED",
                "reason": "Cross-type extension of the kept decision; pure drop",
            }
        ],
        base_content=original,
    )
    assert (ok, errors) == (True, [])
    written = daily.read_text(encoding="utf-8")
    assert "mem-2026-08-02-fact-CITED" not in written
    assert "dangling related reference" not in "".join(errors)
    assert "status: dropped" not in written


def test_drop_commits_through_the_retrying_entry_point(tmp_path, monkeypatch):
    """_commit_candidate is the second caller GitNexus found; it needs the purge too."""
    digest = _digest("memory_digest_drop_second_caller_test")
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    original = "\n\n".join([_fact("mem-2026-08-02-fact-KEEP"), _fact("mem-2026-08-02-fact-GO", body="Stale note.")]) + "\n"
    daily = _daily(tmp_path, original)
    session_dir = tmp_path / "memories" / "staging" / ".tmp_mem_files" / "s1"
    session_dir.mkdir(parents=True)
    operation_path = session_dir / "operations.json"
    operation_path.write_text(
        json.dumps(
            _artifact(
                [
                    {
                        "operation": "drop",
                        "id": "mem-2026-08-02-fact-GO",
                        "reason": "Restatement of the kept fact; lower importance",
                    }
                ]
            )
        ),
        encoding="utf-8",
    )

    assert digest._commit_candidate(
        daily, [], operation_path, session_id="s1", run_id="r1", base_content=original
    )
    written = daily.read_text(encoding="utf-8")
    assert "mem-2026-08-02-fact-GO" not in written
    assert "mem-2026-08-02-fact-KEEP" in written


def test_purging_every_block_is_refused(tmp_path, monkeypatch):
    digest = _digest("memory_digest_purge_to_zero_test")
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    original = _fact("mem-2026-08-02-fact-ONLY") + "\n"
    daily = _daily(tmp_path, original)

    ok, errors = _commit_once(
        digest,
        daily,
        [{"operation": "drop", "id": "mem-2026-08-02-fact-ONLY", "reason": "duplicate"}],
        base_content=original,
    )
    assert ok is False
    assert any("purge would empty the daily file" in error for error in errors)
    assert daily.read_text(encoding="utf-8") == original


def test_human_rejected_blocks_are_never_purged(tmp_path):
    operations = _operations()
    blocks = [{"id": "mem-a", "status": "rejected"}, {"id": "mem-b", "status": "dropped"}]
    survivors, purged = operations.purge_dropped_blocks(blocks)
    assert purged == ["mem-b"]
    assert [block["id"] for block in survivors] == ["mem-a"]


def test_drop_requires_an_existing_id_and_a_reason():
    operations = _operations()
    assert operations.validate_operation(
        {"operation": "drop", "id": "mem-a", "reason": "duplicate"}, {"mem-a"}
    ) == []
    assert any(
        "drop target does not exist" in error
        for error in operations.validate_operation(
            {"operation": "drop", "id": "mem-missing", "reason": "duplicate"}, {"mem-a"}
        )
    )
    assert any(
        "drop requires reason" in error
        for error in operations.validate_operation(
            {"operation": "drop", "id": "mem-a", "reason": "  "}, {"mem-a"}
        )
    )
    assert any(
        "unknown or irrelevant field" in error
        for error in operations.validate_operation(
            {"operation": "drop", "id": "mem-a", "reason": "duplicate", "changes": {"body": "x"}},
            {"mem-a"},
        )
    )


# --- S4: supersede retires its helper on the same path --------------------


def test_supersede_overwrites_target_and_purges_helper(tmp_path, monkeypatch):
    digest = _digest("memory_digest_supersede_drop_test")
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(digest.digest_operations, "hermes_local_today_str", lambda: "2026-08-02")
    original = (
        "\n\n".join(
            [
                _fact("mem-2026-08-02-fact-TARGET", body="Project uses the old naming scheme."),
                _fact("mem-2026-08-02-fact-HELPER", body="Project uses the new naming scheme."),
            ]
        )
        + "\n"
    )
    daily = _daily(tmp_path, original)

    ok, errors = _commit_once(
        digest,
        daily,
        [
            {
                "operation": "supersede",
                "helper_id": "mem-2026-08-02-fact-HELPER",
                "target_id": "mem-2026-08-02-fact-TARGET",
                "correction": "Project uses the new naming scheme.",
                "confidence": "explicit",
            }
        ],
        base_content=original,
    )
    assert (ok, errors) == (True, [])
    written = daily.read_text(encoding="utf-8")
    assert "mem-2026-08-02-fact-HELPER" not in written
    assert "Project uses the new naming scheme." in written
    assert "superseded_at: 2026-08-02" in written
    assert "status: dropped" not in written


def test_superseded_at_format_is_validated_only_when_present():
    digest = _digest("memory_digest_superseded_at_test")
    good = _fact("mem-2026-08-02-fact-A").replace(
        "sources: [session s1]", "superseded_at: 2026-08-02\nsources: [session s1]"
    )
    bad = _fact("mem-2026-08-02-fact-A").replace(
        "sources: [session s1]", "superseded_at: soon\nsources: [session s1]"
    )
    assert digest._validate_digest_file(good + "\n") == []
    assert digest._validate_digest_file(_fact("mem-2026-08-02-fact-A") + "\n") == []
    assert any(
        "superseded_at must be YYYY-MM-DD" in error
        for error in digest._validate_digest_file(bad + "\n")
    )
