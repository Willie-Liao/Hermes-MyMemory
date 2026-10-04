from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from conftest import fail_closed_tool_capture, load_plugin_module


def _load_digest():
    return load_plugin_module("digest.py", "memory_digest_validation_test")


def _block(
    *,
    block_id: str = "mem-1",
    type_: str = "fact",
    entity: str | None = "Casey",
    body: str = "Casey chose home-packed lunch.",
    status: str = "candidate",
    extra: str = "",
) -> str:
    lines = [
        "---",
        f"id: {block_id}",
        f"type: {type_}",
    ]
    if entity is not None:
        lines.append(f"entity: {entity}")
    lines += [
        "confidence: high",
        f"status: {status}",
        "sources: [session s1]",
    ]
    if extra:
        lines.append(extra)
    lines += ["---", body]
    return "\n".join(lines)


def test_valid_single_fact():
    digest = _load_digest()
    assert digest._validate_digest_content(_block()) == []


def test_decision_constraint_alias_normalizes_to_canonical_decision():
    digest = _load_digest()
    raw = _block(
        type_="decision_constraint",
        entity=None,
        body="User prefers concise responses.",
    )

    normalized = digest._normalize_digest_content(raw, session_id="s1")

    assert "type: decision\n" in normalized
    assert digest._validate_digest_content(normalized) == []


def test_worker_validator_accepts_each_assigned_worker_contract():
    digest = _load_digest()
    blocks = {
        "event": _block(
            type_="event",
            entity="Project",
            body="Beginning: user requested review; Course: assistant reviewed sources; Outcome: draft delivered.",
            extra=(
                "predicate: user_requested_review\n"
                "participants:\n"
                "  - {entity: User, role: requester}\n"
                "  - {entity: Assistant, role: executor}\n"
                "valid_from: 2026-08-01\n"
                "valid_to: open"
            ),
        ),
        "fact": _block(
            type_="fact",
            body="Project has a stable deadline.",
            extra="valid_from: 2026-08-01\nvalid_to: open",
        ),
        "procedure": _block(
            type_="procedure",
            entity=None,
            body="Obstacle: sources were scattered; Solution: use an abstract source-triage checklist.",
        ),
        "decision": _block(
            type_="decision",
            entity=None,
            body="Preference: user prefers concise review summaries.",
        ),
    }
    narration = _block(
        type_="fact",
        entity="Jordan",
        body=(
            "Narration: Jordan is Alex's partner; lives in a school dorm; "
            "dislikes cilantro."
        ),
        extra=(
            "involves:\n"
            "  - {entity: Alex Chen, role: partner}\n"
            "  - {entity: Roommate}\n"
        ),
    )

    for worker_type, content in blocks.items():
        assert digest._validate_worker_output(content, worker_type) == []
    assert digest._validate_worker_output(narration, "fact") == []
    third_party = _block(
        type_="decision",
        entity="Jordan",
        body="Preference: Jordan dislikes cilantro.",
    )
    assert any(
        "user/User" in error or "USER.md" in error or "user" in error.lower()
        for error in digest._validate_worker_output(third_party, "decision")
    )

def test_worker_validator_rejects_wrong_assigned_type():
    digest = _load_digest()
    content = _block(type_="fact")

    errors = digest._validate_worker_output(content, "procedure")

    assert any("assigned worker type procedure" in error for error in errors)
    assert any("returned type fact" in error for error in errors)


def test_worker_validator_rejects_blank_procedure_slots():
    digest = _load_digest()
    content = _block(
        type_="procedure",
        entity=None,
        body="Obstacle: ; Solution:",
    )
    errors = digest._validate_worker_output(content, "procedure")
    assert any("obstacle must be non-empty" in e for e in errors)
    assert any("solution must be non-empty" in e for e in errors)


def test_worker_validator_accepts_factual_prefix_and_legacy_plain():
    digest = _load_digest()
    factual = _block(
        type_="fact",
        body="Factual: Jordan lives in a school dorm.",
    )
    legacy = _block(
        type_="fact",
        body="Jordan lives in a school dorm.",
    )
    assert digest._validate_worker_output(factual, "fact") == []
    assert digest._validate_worker_output(legacy, "fact") == []


def test_worker_validator_rejects_blank_factual_content():
    digest = _load_digest()
    content = _block(type_="fact", body="Factual:")
    errors = digest._validate_worker_output(content, "fact")
    assert any("content must be non-empty" in e for e in errors)


def test_worker_validator_rejects_missing_type_specific_fields():
    digest = _load_digest()
    cases = [
        (
            "event",
            _block(
                type_="event",
                entity="Project",
                body="Beginning: update; Course: worked; Outcome: done.",
                extra=(
                    "predicate: project_update\n"
                    "valid_from: 2026-08-01\n"
                    "valid_to: open"
                ),
            ),
            "participants",
        ),
        (
            "fact",
            _block(type_="fact", entity=None),
            "requires an entity",
        ),
        (
            "procedure",
            _block(
                type_="procedure",
                entity=None,
                body="Obstacle: delivery failed; Solution: retry with [tool] terminal.",
            ),
            "raw tool logs",
        ),
        (
            "decision",
            _block(
                type_="decision",
                entity="Jordan",
                body="Preference: Jordan dislikes cilantro.",
            ),
            "user/User",
        ),
        (
            "fact",
            _block(
                type_="fact",
                entity="Jordan",
                body="Jordan lives in a dorm.",
                extra=(
                    "involves:\n"
                    "  - {entity: Alex Chen}\n"
                    "  - {entity: Roommate}\n"
                ),
            ),
            "Narration:",
        ),
    ]

    for worker_type, content, expected in cases:
        errors = digest._validate_worker_output(content, worker_type)
        assert any(expected in error for error in errors), (worker_type, errors)


def test_worker_validator_allows_semicolons_inside_procedure_obstacle():
    digest = _load_digest()
    content = _block(
        type_="procedure",
        entity=None,
        body=(
            "Obstacle: path lacked a flag; retry needed; "
            "Solution: use abstract file-delivery."
        ),
    )
    assert digest._validate_worker_output(content, "procedure") == []


def test_worker_validator_allows_tool_rendered_decision_without_prefix_reject():
    """Kind/subject/ruling slots own the Preference:/Decision: prefix."""
    digest = _load_digest()
    content = _block(
        type_="decision",
        entity=None,
        body="Preference: user prefers concise review summaries.",
    )
    assert digest._validate_worker_output(content, "decision") == []


def test_worker_validator_allows_semicolons_inside_event_course():
    """Joined body may contain ';' inside a stage; tool slots own stage structure."""
    digest = _load_digest()
    content = _block(
        type_="event",
        entity="Project",
        body=(
            "Beginning: user requested review; Course: assistant reviewed sources; "
            "user added a follow-up; Outcome: draft delivered."
        ),
        extra=(
            "predicate: user_requested_review\n"
            "participants:\n"
            "  - {entity: User, role: requester}\n"
            "  - {entity: Assistant, role: executor}\n"
            "valid_from: 2026-08-01\n"
            "valid_to: open"
        ),
    )

    errors = digest._validate_worker_output(content, "event")

    assert errors == []


def test_worker_validator_requires_user_and_assistant_event_participants():
    digest = _load_digest()
    content = _block(
        type_="event",
        entity="Project",
        body="Beginning: user requested review; Course: assistant reviewed sources; Outcome: draft delivered.",
        extra=(
            "predicate: user_requested_review\n"
            "participants:\n"
            "  - {entity: User, role: requester}\n"
            "valid_from: 2026-08-01\n"
            "valid_to: open"
        ),
    )

    errors = digest._validate_worker_output(content, "event")

    assert any("User/requester and Assistant/executor" in error for error in errors)


def test_worker_validator_accepts_legacy_decision_constraint_input_alias():
    digest = _load_digest()
    content = _block(
        type_="decision_constraint",
        entity=None,
        body="Decision: user wants concise responses.",
    )

    assert digest._validate_worker_output(content, "decision") == []
    normalized = digest._normalize_digest_content(content, session_id="s1")
    assert "type: decision\n" in normalized
    assert "type: decision_constraint\n" not in normalized


def test_event_first_worker_contract_rejects_new_hypothesis_output():
    digest = _load_digest()
    content = _block(
        type_="hypothesis",
        body="Casey may opt out of the canteen.",
    )

    prompt = digest._worker_prompt(
        "fact",
        "Casey discussed lunch.",
        session_id="s1",
        platform="cli",
        run_id="r1",
    )

    assert (
        ("type: fact | procedure | decision | event" in prompt)
        or ("Assigned type=fact" in prompt)
        or ("emit fact slots" in prompt)
        or ("You MUST call exactly one tool" in prompt)
    )
    assert "type: fact | procedure | decision | hypothesis | event" not in prompt
    errors = digest._validate_worker_output(content, "fact")
    assert any("assigned worker type fact" in error for error in errors)


def test_worker_output_rejects_unknown_type_before_temporary_acceptance():
    digest = _load_digest()
    invalid = _block(type_="temporary_worker_result")

    errors = digest._validate_digest_content(invalid)

    assert any("invalid type" in error for error in errors)


def test_valid_three_blocks():
    digest = _load_digest()
    content = "\n\n".join(
        _block(block_id=f"mem-{i}", body=f"Durable fact number {i}.") for i in range(3)
    )
    assert digest._validate_digest_content(content) == []


def test_accepts_many_blocks_per_append():
    digest = _load_digest()
    content = "\n\n".join(
        _block(block_id=f"mem-{i}", body=f"Durable fact number {i}.") for i in range(6)
    )
    assert digest._validate_digest_content(content) == []


def test_event_requires_entity_and_span():
    digest = _load_digest()
    content = _block(
        type_="event",
        entity=None,
        body="Delivered xlsx to parent.",
        extra="valid_from: 2026-06-24\nvalid_to: open",
    )
    errors = digest._validate_digest_content(content)
    assert any("requires an entity" in e for e in errors)

    missing_span = _block(
        type_="event",
        entity="Riley",
        body="Drafted parent reply.",
    )
    errors = digest._validate_digest_content(missing_span)
    assert any("type event requires both valid_from and valid_to" in e for e in errors)

    valid_event = _block(
        type_="event",
        entity="Riley",
        body="Drafted parent reply.",
        extra="predicate: grade_dispute\nvalid_from: 2026-06-24\nvalid_to: open",
    )
    assert digest._validate_digest_content(valid_event) == []


def test_event_requires_predicate():
    digest = _load_digest()
    content = _block(
        type_="event",
        entity="Riley",
        body="Drafted parent reply.",
        extra="valid_from: 2026-06-24\nvalid_to: open",
    )
    errors = digest._validate_digest_content(content)
    assert any("requires predicate" in e for e in errors)


def test_sparse_event_participants_pass():
    digest = _load_digest()
    content = _block(
        type_="event",
        entity="Riley",
        body="Morgan laoshi commented Design D 0.",
        extra=(
            "predicate: grade_dispute\n"
            "participants:\n"
            "  - {entity: Morgan}\n"
            "valid_from: 2026-06-24\n"
            "valid_to: open"
        ),
    )
    assert digest._validate_digest_content(content) == []


def test_event_rejects_involves():
    digest = _load_digest()
    content = _block(
        type_="event",
        entity="Riley",
        body="Drafted parent reply.",
        extra=(
            "predicate: grade_dispute\n"
            "involves: [Morgan]\n"
            "valid_from: 2026-06-24\n"
            "valid_to: open"
        ),
    )
    errors = digest._validate_digest_content(content)
    assert any("must use participants, not involves" in e for e in errors)


def test_event_with_file_sheet_sources():
    digest = _load_digest()
    content = _block(
        type_="event",
        entity="Sports Day Analysis",
        body="Signup counts aggregated from per-house sheets.",
        extra=(
            "predicate: signup_tally\n"
            "valid_from: 2026-06-25\n"
            "valid_to: open\n"
            "sources: [session s1, sheet:851003, file:/tmp/roster.xlsx]"
        ),
    )
    assert digest._validate_digest_content(content) == []


def test_normalize_dedupes_participants_and_strips_primary():
    digest = _load_digest()
    raw = _block(
        type_="event",
        entity="Riley",
        body="Grade dispute ongoing.",
        extra=(
            "predicate: grade_dispute\n"
            "participants:\n"
            "  - {entity: Riley}\n"
            "  - {entity: Morgan}\n"
            "  - {entity: Morgan}\n"
            "valid_from: 2026-06-24\n"
            "valid_to: open"
        ),
    )
    normalized = digest._normalize_digest_content(raw, session_id="s1")
    assert "participants:" in normalized
    assert "{entity: Morgan}" in normalized
    assert "involves:" not in normalized
    assert digest._validate_digest_content(normalized) == []


def test_reject_multiline_body():
    digest = _load_digest()
    content = _block(body="First line.\nSecond line.")
    errors = digest._validate_digest_content(content)
    assert any("single line" in e for e in errors)


def test_reject_long_body():
    digest = _load_digest()
    content = _block(body="x" * (digest.MAX_BODY_CHARS + 1))
    errors = digest._validate_digest_content(content)
    assert any("body too long" in e for e in errors)


def test_reject_type_entity():
    digest = _load_digest()
    content = _block(type_="entity", entity=None, body="Some entity dump.")
    errors = digest._validate_digest_content(content)
    assert any("banned" in e for e in errors)


def test_reject_fact_missing_entity():
    digest = _load_digest()
    content = _block(entity=None)
    errors = digest._validate_digest_content(content)
    assert any("requires an entity" in e for e in errors)


def test_hypothesis_is_banned_type():
    digest = _load_digest()
    content = _block(type_="hypothesis", entity="Casey", body="Casey may opt out.")
    errors = digest._validate_digest_content(content)
    assert any("banned type" in e.lower() or "invalid type" in e.lower() or "hypothesis" in e.lower() for e in errors)


def test_procedure_without_entity_ok():
    digest = _load_digest()
    content = _block(type_="procedure", entity=None, body="Run pytest before commit.")
    assert digest._validate_digest_content(content) == []


def test_reject_session_summary():
    digest = _load_digest()
    content = "## Session summary (2026-06-17)\n\n" + _block()
    errors = digest._validate_digest_content(content)
    assert any("session summary" in e.lower() for e in errors)


def test_reject_table_syntax():
    digest = _load_digest()
    content = _block(body="Name | Choice canteen vs gate.")
    errors = digest._validate_digest_content(content)
    assert any("table syntax" in e for e in errors)


def test_reject_duplicate_ids():
    digest = _load_digest()
    content = "\n\n".join([_block(block_id="dup"), _block(block_id="dup", body="Another fact.")])
    errors = digest._validate_digest_content(content)
    assert any("duplicate id" in e for e in errors)


def test_accept_skip_line():
    digest = _load_digest()
    assert digest._validate_digest_content("Nothing durable to stage this batch.") == []


def test_valid_open_span():
    digest = _load_digest()
    content = _block(extra="valid_from: 2026-06-14\nvalid_to: open")
    assert digest._validate_digest_content(content) == []


def test_valid_closed_span():
    digest = _load_digest()
    content = _block(
        body="Casey canteen opt-out window applies.",
        extra="valid_from: 2026-06-01\nvalid_to: 2026-06-30",
    )
    assert digest._validate_digest_content(content) == []


def test_reject_timebound_body_without_span():
    digest = _load_digest()
    content = _block(body="Casey canteen application deadline is near.")
    errors = digest._validate_digest_content(content)
    assert any("time-bound" in e for e in errors)


def test_reject_invalid_date_format():
    digest = _load_digest()
    content = _block(extra="valid_from: 06/14/2026\nvalid_to: open")
    errors = digest._validate_digest_content(content)
    assert any("valid_from must be" in e for e in errors)


def test_reject_valid_to_before_valid_from():
    digest = _load_digest()
    content = _block(extra="valid_from: 2026-06-30\nvalid_to: 2026-06-01")
    errors = digest._validate_digest_content(content)
    assert any("before valid_from" in e for e in errors)


def test_normalize_repairs_timebound_body_without_span():
    digest = _load_digest()
    raw = _block(body="教资申请进行中，考完再探索 AI。")
    normalized = digest._normalize_digest_content(raw, session_id="s1")
    assert digest._validate_digest_content(normalized) == []
    assert "valid_from:" in normalized
    assert "valid_to: open" in normalized


def test_normalize_maps_confirmed_status_to_candidate():
    digest = _load_digest()
    raw = _block(status="confirmed")
    normalized = digest._normalize_digest_content(raw, session_id="s1")
    assert digest._validate_digest_content(normalized) == []


def test_rejected_status_accepted():
    digest = _load_digest()
    content = _block(status="rejected")
    assert digest._validate_digest_content(content) == []


def test_rejected_excluded_from_recent_context(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(digest, "_hot_memory_text", lambda: "")
    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True)
    path = daily / f"{digest.hermes_local_today_str()}.md"
    path.write_text(
        _block(
            status="rejected",
            type_="fact",
            entity="Foo",
            body="rejected fact",
            block_id="mem-rejected",
        ),
        encoding="utf-8",
    )
    text = digest.build_recall_injection_context(session_id="s-rej")
    assert "mem-rejected" not in text
    assert "rejected fact" not in text


def test_normalize_truncates_long_body():
    digest = _load_digest()
    raw = _block(body="x" * (digest.MAX_BODY_CHARS + 10))
    normalized = digest._normalize_digest_content(raw, session_id="s1")
    assert digest._validate_digest_content(normalized) == []


def test_chinese_before_not_timebound_hint():
    digest = _load_digest()
    content = _block(body="之前讨论过班主任负担，现在只想吐槽。")
    assert digest._validate_digest_content(content) == []


def test_valid_involves_and_related():
    digest = _load_digest()
    content = _block(
        block_id="mem-2026-06-26-riley-draft",
        type_="fact",
        entity="Riley",
        body="Narration: Agent drafted parent reply re grade dispute with Morgan and Riley-mom.",
        extra=(
            "confidence: explicit\n"
            "involves:\n"
            "  - {entity: Morgan}\n"
            "  - {entity: Riley-mom}\n"
            "related: [mem-2026-06-26-riley-grade]"
        ),
    )
    assert digest._validate_digest_content(content) == []
    assert digest._validate_worker_output(content, "fact") == []


def test_valid_event_predicate_participants_and_related():
    digest = _load_digest()
    content = _block(
        block_id="mem-2026-06-26-riley-draft",
        type_="event",
        entity="Riley",
        body="Agent drafted parent reply re grade dispute.",
        extra=(
            "confidence: explicit\n"
            "predicate: grade_dispute\n"
            "participants:\n"
            "  - {entity: Morgan, role: teacher}\n"
            "  - {entity: Riley-mom, role: escalator}\n"
            "related: [mem-2026-06-26-riley-grade]\n"
            "valid_from: 2026-06-24\n"
            "valid_to: open"
        ),
    )
    assert digest._validate_digest_content(content) == []


def test_reject_entity_duplicated_in_involves():
    digest = _load_digest()
    content = _block(
        entity="Riley",
        extra="involves: [Riley, Morgan]",
    )
    errors = digest._validate_digest_content(content)
    assert any("must not repeat primary entity" in e for e in errors)

    map_content = _block(
        entity="Riley",
        body="Narration: Riley and Morgan discussed the grade.",
        extra=(
            "involves:\n"
            "  - {entity: Riley}\n"
            "  - {entity: Morgan}\n"
        ),
    )
    map_errors = digest._validate_digest_content(map_content)
    assert any("must not repeat primary entity" in e for e in map_errors)


def test_reject_bad_related_id():
    digest = _load_digest()
    content = _block(extra="confidence: explicit\nrelated: [not-a-mem-id]")
    errors = digest._validate_digest_content(content)
    assert any("mem-id pattern" in e for e in errors)


def test_related_without_explicit_passes():
    digest = _load_digest()
    content = _block(
        extra="related: [mem-2026-06-26-riley-grade]",
    )
    assert digest._validate_digest_content(content) == []


def test_medium_confidence_may_carry_related():
    """Associative related: must work at medium confidence (events often omit roles)."""
    digest = _load_digest()
    content = _block(
        extra=(
            "confidence: medium\n"
            "related: [mem-2026-06-26-riley-grade]"
        ),
    )
    assert digest._validate_digest_content(content) == []


def test_explicit_and_related_passes():
    digest = _load_digest()
    content = _block(
        extra=(
            "confidence: explicit\n"
            "related: [mem-2026-06-26-riley-grade]"
        ),
    )
    assert digest._validate_digest_content(content) == []


def test_reject_supersedes_without_explicit():
    digest = _load_digest()
    content = _block(
        extra="supersedes: [mem-2026-06-26-riley-grade]",
    )
    errors = digest._validate_digest_content(content)
    assert any("supersedes links require confidence: explicit" in e for e in errors)


def test_explicit_and_supersedes_passes():
    digest = _load_digest()
    content = _block(
        extra=(
            "confidence: explicit\n"
            "supersedes: [mem-2026-06-26-riley-grade]"
        ),
    )
    assert digest._validate_digest_content(content) == []


def test_reject_supersedes_bad_mem_id():
    digest = _load_digest()
    content = _block(
        extra=(
            "confidence: explicit\n"
            "supersedes: [not-a-mem-id]"
        ),
    )
    errors = digest._validate_digest_content(content)
    assert any("supersedes[0] must match mem-id pattern" in e for e in errors)


def test_reject_supersedes_nine_entries():
    digest = _load_digest()
    ids = ", ".join(f"mem-2026-06-26-item-{i}" for i in range(9))
    content = _block(
        extra=f"confidence: explicit\nsupersedes: [{ids}]",
    )
    errors = digest._validate_digest_content(content)
    assert any("supersedes has 9 items" in e for e in errors)


def test_related_accepts_ten_entries():
    digest = _load_digest()
    ids = ", ".join(f"mem-2026-06-26-item-{i}" for i in range(10))
    content = _block(extra=f"related: [{ids}]")
    assert digest._validate_digest_content(content) == []


def test_reject_related_eleven_entries():
    digest = _load_digest()
    ids = ", ".join(f"mem-2026-06-26-item-{i}" for i in range(11))
    content = _block(extra=f"related: [{ids}]")
    errors = digest._validate_digest_content(content)
    assert any("related has 11 items" in e for e in errors)


def test_normalize_keeps_supersedes_list():
    digest = _load_digest()
    raw = _block(
        extra=(
            "confidence: explicit\n"
            "supersedes: [mem-2026-06-25-old-event, mem-2026-06-25-old-event]"
        ),
    )
    normalized = digest._normalize_digest_content(raw, session_id="s1")
    assert "supersedes:" in normalized
    assert "mem-2026-06-25-old-event" in normalized
    assert digest._validate_digest_content(normalized) == []


def test_normalize_dedupes_involves_and_strips_primary():
    digest = _load_digest()
    raw = _block(
        entity="Riley",
        extra="involves: [Riley, Morgan, Morgan]",
    )
    normalized = digest._normalize_digest_content(raw, session_id="s1")
    assert "involves:" in normalized
    assert "{entity: Morgan}" in normalized
    assert "Riley," not in normalized.split("involves:", 1)[1].split("---", 1)[0]
    assert digest._validate_digest_content(normalized) == []


def test_normalize_involves_string_to_entity_map():
    digest = _load_digest()
    raw = _block(entity="Riley", extra="involves: [Morgan]")
    normalized = digest._normalize_digest_content(raw, session_id="s1")
    assert "{entity: Morgan}" in normalized
    assert "role:" not in normalized.split("involves:", 1)[1].split("---", 1)[0]


def test_normalize_involves_keeps_clear_role():
    digest = _load_digest()
    raw = _block(
        entity="Jordan",
        extra=(
            "involves:\n"
            "  - entity: Alex Chen\n"
            "    role: partner\n"
            "  - entity: Roommate\n"
            "    role: ''\n"
        ),
        body="Narration: Jordan is Alex's partner and shares a dorm with Roommate.",
    )
    normalized = digest._normalize_digest_content(raw, session_id="s1")
    assert "{entity: Alex Chen, role: partner}" in normalized
    assert "{entity: Roommate}" in normalized
    involves_section = normalized.split("involves:", 1)[1].split("---", 1)[0]
    assert "role: }" not in involves_section
    assert "role: ''" not in involves_section


def test_fact_worker_rejects_multi_involves_without_narration():
    digest = _load_digest()
    content = _block(
        entity="Jordan",
        body="Jordan lives in a school dorm.",
        extra=(
            "involves:\n"
            "  - {entity: Alex Chen, role: partner}\n"
            "  - {entity: Roommate}\n"
        ),
    )
    errors = digest._validate_worker_output(content, "fact")
    assert any("Narration:" in error for error in errors)


def test_fact_worker_rejects_narration_without_involves():
    digest = _load_digest()
    content = _block(
        entity="Jordan",
        body="Narration: Jordan is Alex's partner and dislikes cilantro.",
    )
    errors = digest._validate_worker_output(content, "fact")
    assert any("Narration:" in error for error in errors)


def test_fact_worker_accepts_narration_with_optional_roles():
    digest = _load_digest()
    content = _block(
        entity="Jordan",
        body=(
            "Narration: Jordan is Alex's partner; lives in a school dorm; "
            "dislikes cilantro."
        ),
        extra=(
            "involves:\n"
            "  - {entity: Alex Chen, role: partner}\n"
            "  - {entity: Roommate}\n"
        ),
    )
    assert digest._validate_worker_output(content, "fact") == []


def test_decision_worker_rejects_third_party_preference_subject():
    digest = _load_digest()
    content = _block(
        type_="decision",
        entity="Jordan",
        body="Preference: Jordan dislikes cilantro and green onion.",
    )
    errors = digest._validate_worker_output(content, "decision")
    assert any("user/User" in error or "USER.md" in error or "user" in error.lower() for error in errors)


def test_decision_worker_rejects_observational_user_stated_trait():
    digest = _load_digest()
    content = _block(
        type_="decision",
        entity="Jordan",
        body="Decision: user stated Jordan lives in a school dorm.",
    )
    errors = digest._validate_worker_output(content, "decision")
    assert any("user/User" in error or "USER.md" in error or "user" in error.lower() or "Narration:" in error for error in errors)


def test_decision_worker_accepts_user_ruling_about_third_party_topic():
    digest = _load_digest()
    content = _block(
        type_="decision",
        entity="Alex Chen",
        body=(
            "Decision: user ruled that any Jordan/dating question must first "
            "read the dating knowledge directory."
        ),
    )
    assert digest._validate_worker_output(content, "decision") == []


def test_dedup_prompt_includes_fact_narration_merge_few_shot():
    dedup = load_plugin_module("dedup_prompt.py", "memory_digest_dedup_prompt_test")
    prompt = dedup.build_proposer_prompt([], [])
    assert "Fact cast merge into Narration" in prompt
    assert "Narration:" in prompt or '"kind":"Narration"' in prompt or "kind=Narration" in prompt
    assert "involves" in prompt or "absorbed_ids" in prompt
    assert '"fact":' in prompt or "fact.kind" in prompt


def test_dedup_prompt_compares_same_type_only():
    """Phase-2 no longer asks the LLM to walk cross-type pairs (legacy multi-worker)."""
    dedup = load_plugin_module("dedup_prompt.py", "memory_digest_dedup_same_type")
    prompt = dedup.build_proposer_prompt([], [])
    folded = prompt.casefold()
    assert "same type only" in folded
    assert "candidate pairs" in folded
    assert "Cross type plus extension" not in prompt
    assert "### Cross-type extension (drop, not merge)" not in prompt


def test_dedup_prompt_requires_submit_operations_tool_not_bare_json_array():
    """Phase-2 must not tell MIMO to dump a JSON array in assistant text."""
    dedup = load_plugin_module("dedup_prompt.py", "memory_digest_dedup_tool_call")
    prompt = dedup.build_proposer_prompt([], [])
    assert "You MUST call submit_operations" in prompt
    assert "patch_operations" in prompt
    assert "Return only the JSON array." not in prompt
    assert "not in assistant text" in prompt.casefold() or "not in assistant text" in prompt


def test_dedup_prompt_groups_mixed_cards_into_typed_json_arrays():
    """One prompt, four type buckets — compare inside each array, one submit_operations."""
    dedup = load_plugin_module("dedup_prompt.py", "memory_digest_dedup_typed_arrays")
    fact = {"id": "mem-f", "type": "fact", "body": "Factual: x"}
    event = {"id": "mem-e", "type": "event", "body": "Beginning: a"}
    proc = {"id": "mem-p", "type": "procedure", "body": "Obstacle: o"}
    prompt = dedup.build_proposer_prompt([fact, event, proc], [])
    assert "### Existing events" in prompt
    assert "### Existing facts" in prompt
    assert "### Existing procedures" in prompt
    assert "### Existing decisions" in prompt
    assert "Existing blocks already in the file" not in prompt
    assert "New blocks from this session" not in prompt
    events = json.loads(prompt.split("### Existing events", 1)[1].split("###", 1)[0].strip())
    assert events[0]["type"] == "event"
    assert events[0]["id"] == "mem-e"
    facts = json.loads(prompt.split("### Existing facts", 1)[1].split("###", 1)[0].strip())
    assert facts[0]["id"] == "mem-f"
    assert "You MUST call submit_operations" in prompt
    assert "patch_operations" in prompt
    folded = prompt.casefold()
    assert "inside each array" in folded or "compare inside" in folded


def test_dedup_prompt_teaches_retrieval_rejection_reasons():
    dedup = load_plugin_module("dedup_prompt.py", "memory_digest_dedup_reject_reason")
    prompt = dedup.build_proposer_prompt([], [])
    folded = prompt.casefold()
    assert "rejected by" in folded
    assert "user's correction" in folded
    assert "contradiction" in folded
    assert "complementary" in folded or "historical evolution" in folded


def test_normalize_defaults_missing_importance_to_3():
    digest = _load_digest()
    raw = _block()
    assert "importance:" not in raw
    normalized = digest._normalize_digest_content(raw, session_id="s1")
    assert f"importance: {digest.IMPORTANCE_DEFAULT}" in normalized
    assert digest._validate_digest_content(normalized) == []


def test_normalize_clamps_invalid_importance_to_default():
    digest = _load_digest()
    raw = _block(extra="importance: 9")
    normalized = digest._normalize_digest_content(raw, session_id="s1")
    assert f"importance: {digest.IMPORTANCE_DEFAULT}" in normalized
    assert digest._validate_digest_content(normalized) == []


def test_validate_rejects_out_of_range_importance_without_normalize():
    digest = _load_digest()
    content = _block(extra="importance: 6")
    errors = digest._validate_digest_content(content)
    assert any("importance" in e and "out of range" in e for e in errors)


def test_validate_accepts_importance_zero_through_five():
    digest = _load_digest()
    for n in range(0, 6):
        content = _block(extra=f"importance: {n}")
        assert digest._validate_digest_content(content) == []


def test_failure_path_does_not_touch_daily_file(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)

    monkeypatch.setattr(
        digest,
        "_invoke_digest_llm",
        lambda prompt, platform: "",
    )
    monkeypatch.setattr(digest, "_invoke_digest_worker_tool", fail_closed_tool_capture)

    prompt = digest._worker_prompt(
        "fact", "prompt", session_id="s1", platform="cli", run_id="r1"
    )
    result = digest._run_validated_worker(
        "fact", prompt, session_id="s1", run_id="r1", platform="cli"
    )

    assert isinstance(result, digest.WorkerFailure)
    assert not (tmp_path / "2026-06-17.md").exists()


def test_dirty_accept_worker_does_not_touch_daily_file(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)

    monkeypatch.setattr(
        digest,
        "_invoke_digest_llm",
        lambda prompt, platform: _block(type_="entity", entity=None, body="junk"),
    )
    monkeypatch.setattr(digest, "_invoke_digest_worker_tool", fail_closed_tool_capture)

    prompt = digest._worker_prompt(
        "fact", "prompt", session_id="s1", platform="cli", run_id="r1"
    )
    result = digest._run_validated_worker(
        "fact", prompt, session_id="s1", run_id="r1", platform="cli"
    )

    assert isinstance(result, digest.ValidatedWorkerResult)
    assert result.accepted_dirty is True
    assert not (tmp_path / "2026-06-17.md").exists()


def test_well_formed_block_validates_and_passes_worker_gate(tmp_path, monkeypatch):
    """Strict Step-1 gate: valid content must clear validation in a detail worker."""
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)

    well_formed = _block(
        block_id="mem-2026-06-26-correction",
        type_="decision_constraint",
        entity="Riley",
        body="Decision: user ruled that the grade dispute was resolved in parent's favor.",
        extra=(
            "confidence: explicit\n"
            "related: [mem-2026-06-26-local-fact]\n"
            "supersedes: [mem-2026-06-25-old-event]"
        ),
    )
    assert digest._validate_digest_content(well_formed) == []
    monkeypatch.setattr(
        digest, "_invoke_digest_llm", lambda prompt, platform: well_formed
    )
    monkeypatch.setattr(digest, "_invoke_digest_worker_tool", fail_closed_tool_capture)

    prompt = digest._worker_prompt(
        "decision", "prompt", session_id="s1", platform="cli", run_id="r1"
    )
    result = digest._run_validated_worker(
        "decision", prompt, session_id="s1", run_id="r1", platform="cli"
    )

    assert isinstance(result, digest.ValidatedWorkerResult)
    assert "supersedes:" in result.content


def _event_block_for_related(
    *,
    block_id: str,
    related: str,
    entity: str = "Alex Chen",
) -> str:
    return "\n".join(
        [
            "---",
            f"id: {block_id}",
            "type: event",
            f"entity: {entity}",
            "predicate: user_requested_resume_package",
            "participants:",
            "  - {entity: User, role: requester}",
            "  - {entity: Assistant, role: executor}",
            f"related: [{related}]",
            "valid_from: 2026-07-26",
            "valid_to: 2026-07-26",
            "confidence: explicit",
            "status: candidate",
            "sources: [session s1]",
            "---",
            "User asked for a resume revision; assistant pushed an update.",
        ]
    )


def test_event_related_to_event_fails_validate_digest_file():
    """Negative: event.related must not point at another event."""
    digest = _load_digest()
    fact = _block(
        block_id="mem-20260726-alex-resume-delivered",
        entity="Alex Chen",
        body="Resume facts for Alex.",
    )
    v3 = _event_block_for_related(
        block_id="mem-20260726-resume-v3",
        related="mem-20260726-alex-resume-delivered",
    )
    v4 = _event_block_for_related(
        block_id="mem-20260726-resume-v4",
        related="mem-20260726-resume-v3",
    )
    content = "\n\n".join([fact, v3, v4]) + "\n"
    errors = digest._validate_digest_file(content)
    assert any(
        "related must not point at event mem-20260726-resume-v3" in e for e in errors
    )
    assert any("episode-merge instead" in e for e in errors)


def test_event_related_to_fact_passes_validate_digest_file():
    digest = _load_digest()
    fact = _block(
        block_id="mem-20260726-alex-resume-delivered",
        entity="Alex Chen",
        body="Resume facts for Alex.",
    )
    event = _event_block_for_related(
        block_id="mem-20260726-resume-outcome",
        related="mem-20260726-alex-resume-delivered",
    )
    content = "\n\n".join([fact, event]) + "\n"
    assert digest._validate_digest_file(content) == []


def test_validate_digest_file_rejects_dangling_related_reference():
    digest = _load_digest()
    event = _event_block_for_related(
        block_id="mem-20260726-resume-outcome",
        related="mem-20260726-missing-fact",
    )
    errors = digest._validate_digest_file(event + "\n")
    assert any("dangling related reference" in error for error in errors)


def _n_block_file(count: int) -> str:
    blocks = [
        _block(block_id=f"mem-2026-08-08-cap-{index:03d}")
        for index in range(count)
    ]
    return "\n\n".join(blocks) + "\n"


def test_validate_digest_file_accepts_twenty_one_blocks():
    """21 blocks fit under the raised cap; they failed under the old cap of 20."""
    digest = _load_digest()
    errors = digest._validate_digest_file(_n_block_file(21))
    assert not any("too many blocks" in error for error in errors)


def test_validate_digest_file_rejects_thirty_one_blocks():
    digest = _load_digest()
    content = _n_block_file(31)
    ids = set(digest._id_type_map_from_content(content))
    errors = digest._validate_digest_file(content, alive_ids=ids)
    assert not any("too many blocks" in error for error in errors)


def test_coordinator_rejects_mixed_session_artifacts_without_writing(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily = tmp_path / "memories" / "staging" / "daily" / "2026-08-02.md"
    daily.parent.mkdir(parents=True)
    original = _block(block_id="mem-2026-08-02-existing") + "\n"
    daily.write_text(original, encoding="utf-8")

    block = {
        "id": "mem-2026-08-02-created",
        "type": "fact",
        "entity": "Project",
        "confidence": "high",
        "status": "candidate",
        "sources": ["session s1"],
        "body": "A durable project fact.",
    }
    artifact = tmp_path / "memories" / "staging" / ".tmp_mem_files" / "s2" / "event-result.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        __import__("json").dumps(
            {
                "status": "validated",
                "session_id": "s2",
                "run_id": "r1",
                "worker_type": "event",
                "attempts": 1,
                "content": "",
                "blocks": [block],
            }
        ),
        encoding="utf-8",
    )

    replaced: list[tuple] = []
    monkeypatch.setattr(
        digest,
        "_rewrite_daily_file",
        lambda *args: replaced.append(args),
    )
    assert (
        digest._commit_candidate(
            daily,
            [artifact],
            {"session_id": "s1", "run_id": "r1", "operations": [], "status": "validated"},
            session_id="s1",
            run_id="r1",
        )
        is False
    )
    assert daily.read_text(encoding="utf-8") == original
    assert replaced == []


def test_worker_artifact_requires_explicit_validated_status(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    artifact = tmp_path / "memories" / "staging" / ".tmp_mem_files" / "s1" / "fact-result.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps(
            {
                "session_id": "s1",
                "run_id": "r1",
                "worker_type": "fact",
                "blocks": [],
            }
        ),
        encoding="utf-8",
    )
    for payload in (json.loads(artifact.read_text(encoding="utf-8")),):
        try:
            digest._load_validated_worker_artifact(
                artifact,
                session_id="s1",
                run_id="r1",
                expected_worker_type="fact",
            )
        except ValueError as exc:
            assert "validated" in str(exc)
        else:
            raise AssertionError(f"raw artifact was accepted: {payload}")


def test_worker_artifact_requires_expected_type_and_session_directory(
    tmp_path, monkeypatch
):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text(
        json.dumps(
            {
                "status": "validated",
                "session_id": "s1",
                "run_id": "r1",
                "worker_type": "procedure",
                "blocks": [],
            }
        ),
        encoding="utf-8",
    )
    try:
        digest._load_validated_worker_artifact(
            outside,
            session_id="s1",
            run_id="r1",
            expected_worker_type="fact",
        )
    except ValueError as exc:
        assert "temporary session directory" in str(exc)
    else:
        raise AssertionError("artifact outside session directory was accepted")

    inside = tmp_path / "memories" / "staging" / ".tmp_mem_files" / "s1" / "procedure-result.json"
    inside.parent.mkdir(parents=True)
    inside.write_text(
        json.dumps(
            {
                "status": "validated",
                "session_id": "s1",
                "run_id": "r1",
                "worker_type": "procedure",
                "blocks": [],
            }
        ),
        encoding="utf-8",
    )
    try:
        digest._load_validated_worker_artifact(
            inside,
            session_id="s1",
            run_id="r1",
            expected_worker_type="fact",
        )
    except ValueError as exc:
        assert "worker type mismatch" in str(exc)
    else:
        raise AssertionError("wrong worker type was accepted")


def test_operation_artifact_must_be_inside_session_directory(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    operation_artifact = tmp_path / "outside-operations.json"
    operation_artifact.write_text(
        json.dumps(
            {
                "status": "validated",
                "session_id": "s1",
                "run_id": "r1",
                "operations": [],
            }
        ),
        encoding="utf-8",
    )
    try:
        digest._load_validated_operation_artifact(
            operation_artifact,
            session_id="s1",
            run_id="r1",
        )
    except ValueError as exc:
        assert "temporary session directory" in str(exc)
    else:
        raise AssertionError("operation artifact outside session directory was accepted")


def test_coordinator_applies_validated_operations_and_replaces_once(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily = tmp_path / "memories" / "staging" / "daily" / "2026-08-02.md"
    daily.parent.mkdir(parents=True)
    original = _block(block_id="mem-2026-08-02-existing") + "\n"
    daily.write_text(original, encoding="utf-8")
    block = {
        "id": "mem-2026-08-02-created",
        "type": "fact",
        "entity": "Project",
        "confidence": "high",
        "status": "candidate",
        "sources": ["session s1"],
        "body": "A durable project fact.",
    }
    artifact = tmp_path / "memories" / "staging" / ".tmp_mem_files" / "s1" / "fact-result.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        __import__("json").dumps(
            {
                "status": "validated",
                "session_id": "s1",
                "run_id": "r1",
                "worker_type": "fact",
                "attempts": 1,
                "content": "",
                "blocks": [block],
            }
        ),
        encoding="utf-8",
    )
    operation_artifact = {
        "session_id": "s1",
        "run_id": "r1",
        "status": "validated",
        "operations": [{"operation": "create", "block": block}],
    }
    replacements: list[str] = []
    original_rewrite = digest._rewrite_daily_file
    monkeypatch.setattr(
        digest,
        "_rewrite_daily_file",
        lambda path, content: (replacements.append(content), original_rewrite(path, content))[1],
    )

    assert (
        digest._commit_candidate(
            daily,
            [artifact],
            operation_artifact,
            session_id="s1",
            run_id="r1",
        )
        is True
    )
    assert len(replacements) == 1
    text = daily.read_text(encoding="utf-8")
    assert "mem-2026-08-02-existing" in text
    assert "mem-2026-08-02-created" in text


def test_coordinator_rejects_stale_base_before_second_replacement(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily = tmp_path / "memories" / "staging" / "daily" / "2026-08-02.md"
    daily.parent.mkdir(parents=True)
    original = _block(block_id="mem-2026-08-02-existing") + "\n"
    daily.write_text(original, encoding="utf-8")
    artifact = tmp_path / "memories" / "staging" / ".tmp_mem_files" / "s1" / "fact-result.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps(
            {
                "status": "validated",
                "session_id": "s1",
                "run_id": "r1",
                "worker_type": "fact",
                "blocks": [],
            }
        ),
        encoding="utf-8",
    )
    first_ops = {
        "status": "validated",
        "session_id": "s1",
        "run_id": "r1",
        "operations": [
            {
                "operation": "update",
                "id": "mem-2026-08-02-existing",
                "changes": {"body": "First committed update."},
            }
        ],
    }
    assert digest._commit_candidate(
        daily,
        [artifact],
        first_ops,
        session_id="s1",
        run_id="r1",
        base_content=original,
    )
    second_ops = {
        **first_ops,
        "operations": [
            {
                "operation": "update",
                "id": "mem-2026-08-02-existing",
                "changes": {"body": "Stale update must not win."},
            }
        ],
    }
    assert not digest._commit_candidate(
        daily,
        [artifact],
        second_ops,
        session_id="s1",
        run_id="r1",
        base_content=original,
    )
    assert "First committed update." in daily.read_text(encoding="utf-8")
    assert "Stale update must not win." not in daily.read_text(encoding="utf-8")


def test_coordinator_preserves_existing_event_id_across_update(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily = tmp_path / "memories" / "staging" / "2026-08-02.md"
    daily.parent.mkdir(parents=True)
    event = _event_block_for_related(
        block_id="mem-2026-08-02-event",
        related="mem-2026-08-02-fact",
    )
    fact = _block(block_id="mem-2026-08-02-fact")
    daily.write_text(fact + "\n\n" + event + "\n", encoding="utf-8")
    artifact = tmp_path / "memories" / "staging" / ".tmp_mem_files" / "s1" / "event-result.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        __import__("json").dumps(
            {
                "status": "validated",
                "session_id": "s1",
                "run_id": "r1",
                "worker_type": "event",
                "attempts": 1,
                "content": "",
                "blocks": [],
            }
        ),
        encoding="utf-8",
    )
    operation_artifact = {
        "session_id": "s1",
        "run_id": "r1",
        "status": "validated",
        "operations": [
            {
                "operation": "update",
                "id": "mem-2026-08-02-event",
                "changes": {"body": "Updated event body."},
            }
        ],
    }
    assert digest._commit_candidate(
        daily,
        [artifact],
        operation_artifact,
        session_id="s1",
        run_id="r1",
    )
    text = daily.read_text(encoding="utf-8")
    assert "id: mem-2026-08-02-event" in text
    assert "Updated event body." in text


def test_append_rejects_event_related_to_existing_daily_event(tmp_path, monkeypatch):
    """Append chunk linking to an existing daily event fails via extra_type_map."""
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    existing = "\n".join(
        [
            "---",
            "id: mem-20260726-resume-v3",
            "type: event",
            "entity: Alex Chen",
            "predicate: user_requested_resume_v3",
            "participants:",
            "  - {entity: User, role: requester}",
            "  - {entity: Assistant, role: executor}",
            "valid_from: 2026-07-26",
            "valid_to: 2026-07-26",
            "confidence: explicit",
            "status: candidate",
            "sources: [session s1]",
            "---",
            "User asked for resume v3; assistant pushed CocoIndex rewrite.",
        ]
    )
    new_chunk = _event_block_for_related(
        block_id="mem-20260726-resume-v4",
        related="mem-20260726-resume-v3",
    )
    extra = digest._id_type_map_from_content(existing)
    errors = digest._validate_digest_content(new_chunk, extra_type_map=extra)
    assert any(
        "related must not point at event mem-20260726-resume-v3" in e for e in errors
    )


def test_message_clocks_optional_and_iso_when_present():
    """Wall clocks are plugin-stamped; missing keys must not fail old cards."""
    digest = _load_digest()
    assert digest._validate_digest_content(_block()) == []
    ok = _block(
        extra=(
            "user_message_at: '2026-08-22T16:01:12+08:00'\n"
            "assistant_response_at: '2026-08-22T17:10:44+08:00'\n"
            "generated_at: '2026-08-22T17:16:08+08:00'"
        )
    )
    assert digest._validate_digest_content(ok) == []
    garbage = _block(extra="user_message_at: not-a-timestamp")
    garbage_errs = digest._validate_digest_content(garbage)
    assert any("user_message_at" in e for e in garbage_errs)
    inverted = _block(
        extra=(
            "user_message_at: '2026-08-22T17:10:44+08:00'\n"
            "assistant_response_at: '2026-08-22T16:01:12+08:00'"
        )
    )
    inverted_errs = digest._validate_digest_content(inverted)
    assert any("user_message_at" in e and "assistant_response_at" in e for e in inverted_errs)
