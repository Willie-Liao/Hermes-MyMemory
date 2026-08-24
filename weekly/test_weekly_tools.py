"""Unit tests for weekly Worker 1 tool schemas, merge, and render."""

from __future__ import annotations

import weekly_tools


def test_merge_field_patch_overwrites_only_present_keys():
    previous = {"events": [{"entity": "A", "confidence": "low"}], "keep": 1}
    merged = weekly_tools.merge_field_patch(
        previous, {"events": [{"entity": "A", "confidence": "high"}]}
    )
    assert merged["events"][0]["confidence"] == "high"
    assert merged["keep"] == 1


def test_submit_event_schema_has_confidence_enum():
    schema = weekly_tools.submit_weekly_event_schema()
    assert schema["name"] == "submit_weekly_event"
    event_props = schema["parameters"]["properties"]["events"]["items"]["properties"]
    assert event_props["confidence"]["enum"] == list(weekly_tools.CONFIDENCE_ENUM)
    assert event_props["status"]["enum"] == list(weekly_tools.WORKER_STATUS_ENUM)
    role_enum = event_props["participants"]["items"]["properties"]["role"]["enum"]
    assert "requester" in role_enum and "executor" in role_enum


def test_patch_event_schema_all_optional():
    schema = weekly_tools.patch_weekly_event_schema()
    assert schema["name"] == "patch_weekly_event"
    assert schema["parameters"].get("required") in (None, [])


def test_render_events_beginning_course_outcome():
    args = {
        "events": [
            {
                "entity": "ThesisChapter",
                "predicate": "user_requested_delivery",
                "participants": [
                    {"entity": "User", "role": "requester"},
                    {"entity": "Assistant", "role": "executor"},
                ],
                "valid_from": "2026-07-28",
                "valid_to": "2026-07-28",
                "confidence": "explicit",
                "sources": ["session s1"],
                "related": ["mem-2026-07-28-ilink-file-push"],
                "beginning": "asked",
                "course": "delivered",
                "outcome": "done",
            }
        ]
    }
    blocks = weekly_tools.render_events_from_tool_args(args)
    assert len(blocks) == 1
    assert blocks[0]["frontmatter"]["type"] == "event"
    assert blocks[0]["frontmatter"]["status"] == "candidate"
    assert "Beginning: asked" in blocks[0]["body"]
    assert "Course: delivered" in blocks[0]["body"]
    assert "Outcome: done" in blocks[0]["body"]


def test_validate_rejects_bad_confidence():
    args = {
        "events": [
            {
                "entity": "X",
                "confidence": "pretty-sure",
                "participants": [{"entity": "User", "role": "requester"}],
            }
        ]
    }
    errs = weekly_tools.validate_closed_choice_args(args, role="event")
    assert any("confidence" in e for e in errs)


def test_thread_tools_are_registered_conflict_hypothesis_are_not():
    names = {s["name"] for s in weekly_tools.all_tool_schemas()}
    assert "submit_weekly_thread" in names
    assert "patch_weekly_thread" in names
    assert "submit_weekly_summary" in names
    assert "patch_weekly_summary" in names
    assert "submit_weekly_conflict" not in names
    assert "submit_weekly_hypothesis" not in names
    submit = weekly_tools.submit_weekly_thread_schema()
    assert "cross-day-thread" in submit["parameters"]["properties"]


def test_failed_fields_teach_names_patch_tool():
    teach = weekly_tools.failed_fields_teach(
        ["event missing related"],
        {"events": []},
        role="event",
        patch_tool="patch_weekly_event",
        attempt=2,
        max_attempts=3,
    )
    assert "patch_weekly_event" in teach
    assert "Do NOT call submit again" in teach
