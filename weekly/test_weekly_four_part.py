"""Step 3: four-part weekly brief render/parse round-trip + legacy Brief load."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

FIXTURES = Path(__file__).with_name("fixtures")


def _load_schema():
    path = Path(__file__).with_name("weekly_event_schema.py")
    spec = importlib.util.spec_from_file_location("memory_weekly_event_schema_four", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_cite():
    path = Path(__file__).with_name("weekly_cite.py")
    plugins = Path(__file__).resolve().parent.parent.parent
    if str(plugins) not in sys.path:
        sys.path.insert(0, str(plugins))
    spec = importlib.util.spec_from_file_location("memory_weekly_cite_four", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_citations():
    path = Path(__file__).with_name("weekly_citations.py")
    spec = importlib.util.spec_from_file_location(
        "memory_weekly_citations_four", path
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _golden_payload(schema):
    """Monday 2026-07-27 .. Sunday matching fixtures/weekly_four_part_w31.md."""
    days = []
    for offset in range(7):
        day = date(2026, 7, 27) + timedelta(days=offset)
        if offset == 0:
            days.append(
                schema.DayBrief(
                    day=day,
                    events=(
                        schema.EventCite(1, "mem-2026-07-27-kickoff"),
                        schema.EventCite(2, "mem-2026-07-27-digest"),
                    ),
                    sentences=(
                        schema.ClaimSentence(
                            "Project kickoff moved to the new event-first digest flow.",
                            title="Project kickoff moved to event-first digest",
                        ),
                        schema.ClaimSentence(
                            "The daily digest records three source events.",
                            title="Daily digest records three source events",
                        ),
                    ),
                )
            )
        elif offset == 1:
            days.append(
                schema.DayBrief(
                    day=day,
                    events=(schema.EventCite(5, "mem-2026-07-28-workers"),),
                    sentences=(
                        schema.ClaimSentence(
                            "Weekly review generation was split into parallel workers.",
                            title="Weekly review split into parallel workers",
                        ),
                    ),
                )
            )
        else:
            days.append(schema.DayBrief(day=day))

    return schema.WeeklyReviewPayload(
        days=tuple(days),
        conflicts=(
            schema.ConflictItem(
                id="cfl-validation-order",
                text=(
                    "The procedure says validation precedes writes, but one "
                    "daily block records a write before validation."
                ),
            ),
        ),
        hypotheses=(
            schema.HypothesisItem(
                id="hyp-parallel-latency",
                text=(
                    "The parallel event workers may reduce weekly generation "
                    "latency without changing citation identity."
                ),
            ),
        ),
        span_candidates=(
            schema.SpanCandidate(
                id="span-project-deadline",
                label="Project deadline",
                start_date=date(2026, 7, 27),
                end_date=date(2026, 8, 2),
                confidence="explicit",
            ),
            schema.SpanCandidate(
                id="span-low-noise",
                label="Noise span",
                start_date=date(2026, 7, 28),
                end_date=date(2026, 7, 30),
                confidence="low",
            ),
            schema.SpanCandidate(
                id="span-medium-noise",
                label="Maybe initiative",
                start_date=date(2026, 7, 29),
                end_date=date(2026, 8, 1),
                confidence="medium",
            ),
        ),
        legend={
            1: "mem-2026-07-27-kickoff",
            2: "mem-2026-07-27-digest",
            5: "mem-2026-07-28-workers",
        },
        week_key="2026-W31",
    )


def test_golden_fixture_has_seven_weekday_slots():
    schema = _load_schema()
    text = (FIXTURES / "weekly_four_part_w31.md").read_text(encoding="utf-8")
    for name in schema.WEEKDAY_NAMES:
        assert f"{name} — " in text
    assert text.count(" · Events") == 7


def test_golden_fixture_event_markers_and_empty_day():
    text = (FIXTURES / "weekly_four_part_w31.md").read_text(encoding="utf-8")
    assert "Monday — 2026-07-27 · Events [1] [2]" in text
    assert "Tuesday — 2026-07-28 · Events [5]" in text
    assert "Wednesday — 2026-07-29 · Events\nNo record for this day." in text


def test_golden_fixture_named_event_paragraphs():
    schema = _load_schema()
    text = (FIXTURES / "weekly_four_part_w31.md").read_text(encoding="utf-8")
    assert "**Project kickoff moved to event-first digest**" in text
    assert "**Daily digest records three source events**" in text
    monday_block = text.split("Monday — 2026-07-27 · Events [1] [2]\n", 1)[1]
    monday_body = monday_block.split("\n\nTuesday", 1)[0]
    # Two Worker 2 event blocks separated by a blank line.
    assert "\n\n" in monday_body
    assert "Beginning:" not in monday_body
    assert "Course:" not in monday_body
    assert "Outcome:" not in monday_body
    rendered = (
        "**Project kickoff moved to event-first digest**\n"
        "Project kickoff moved to the new event-first digest flow.\n\n"
        "**Daily digest records three source events**\n"
        "The daily digest records three source events."
    )
    assert schema.validate_sentence_final_cites(rendered) == []


def test_golden_fixture_conflict_between_claims():
    text = (FIXTURES / "weekly_four_part_w31.md").read_text(encoding="utf-8")
    assert "Conflict" in text
    assert "validation precedes writes" in text
    assert "[6]" in text


def test_render_parse_round_trip_matches_golden():
    schema = _load_schema()
    payload = schema.assign_typed_citations(_golden_payload(schema))
    rendered = schema.render_weekly_review_brief("2026-W31", payload)
    golden = (FIXTURES / "weekly_four_part_w31.md").read_text(encoding="utf-8")
    assert rendered.strip() == golden.strip()

    parsed = schema.parse_four_part_brief(rendered)
    # Brief Possible overdue is always empty (digest validate owns UI overdue).
    errors = schema.payloads_structurally_equal(
        schema.WeeklyReviewPayload(
            days=payload.days,
            conflicts=payload.conflicts,
            hypotheses=payload.hypotheses,
            span_candidates=(),
            legend=payload.legend,
            typed_legend=payload.typed_legend,
            week_key=payload.week_key,
        ),
        schema.WeeklyReviewPayload(
            days=parsed.days,
            conflicts=parsed.conflicts,
            hypotheses=parsed.hypotheses,
            span_candidates=(),
            legend=parsed.legend,
            typed_legend=parsed.typed_legend,
            week_key=parsed.week_key,
        ),
        ignore_span_start=True,
    )
    assert errors == [], errors


def test_assign_typed_citations_does_not_renumber_events():
    schema = _load_schema()
    citations = _load_citations()
    payload = _golden_payload(schema)
    before = dict(payload.legend)
    assigned = schema.assign_typed_citations(payload)
    assert assigned.summary == payload.summary
    assert assigned.legend == before
    assert citations.next_cite_after_legend(before) == 6
    assert set(assigned.typed_legend) == {6, 7}
    assert assigned.typed_legend[6].kind == "conflict"
    assert assigned.typed_legend[7].kind == "hypothesis"
    # Spans are not Brief-cited; overdue section stays empty.
    overdue_text = schema.render_overdue_section(assigned.span_candidates)
    assert overdue_text == "Possible overdue report\n- None."
    assert all(s.cite is None for s in assigned.span_candidates)


def test_assign_typed_citations_keeps_summary():
    """Cite rebuild must not drop Worker-1 summary or Chronicle dumps []."""
    schema = _load_schema()
    payload = schema.WeeklyReviewPayload(
        days=(),
        week_key="2026-W35",
        summary=(
            schema.WeeklySummaryItem(
                text="Monday wrap-up only",
                weekdays=("Monday",),
            ),
        ),
    )
    assigned = schema.assign_typed_citations(payload)
    assert assigned.summary == payload.summary
    assert assigned.summary[0].text == "Monday wrap-up only"


def test_empty_day_constant_exact():
    schema = _load_schema()
    assert schema.EMPTY_DAY_TEXT == "No record for this day."
    day = schema.DayBrief(day=date(2026, 7, 29))
    assert schema.worker2_format_day_events(day.sentences) == schema.EMPTY_DAY_TEXT


def test_legacy_brief_still_loads_via_extract_brief():
    cite = _load_cite()
    schema = _load_schema()
    md = (FIXTURES / "legacy_brief_w26.md").read_text(encoding="utf-8")
    brief = cite.extract_brief(md)
    assert "### Events" in brief
    assert "Andrae feedback landed [1]" in brief
    assert "type: event" not in brief
    assert "## Distill" not in brief
    assert schema.is_four_part_brief(brief) is False


def test_extract_brief_returns_four_part_from_wrapped_document():
    cite = _load_cite()
    schema = _load_schema()
    four = (FIXTURES / "weekly_four_part_w31.md").read_text(encoding="utf-8")
    md = (
        "# Weekly distill 2026-W31\n\n## Distill\n\n"
        "---\nid: evt-a\ntype: event\n---\nbody\n\n"
        f"## Brief\n\n{four}\n\n## Action ledger\n\n| Status | Item |\n"
    )
    brief = cite.extract_brief(md)
    assert schema.is_four_part_brief(brief)
    assert "Weekly Brief — 2026-W31" in brief
    assert "Possible overdue report" in brief
    assert "## Action ledger" not in brief
    typed = cite.load_typed_cite_map(md)
    assert typed[6] == {"kind": "conflict", "id": "cfl-validation-order"}
    assert typed[7] == {"kind": "hypothesis", "id": "hyp-parallel-latency"}
    assert 8 not in typed
    assert 1 not in typed  # event cites stay out of typed map


def test_four_part_sections_always_present_when_empty():
    schema = _load_schema()
    days = tuple(
        schema.DayBrief(day=date(2026, 7, 27) + timedelta(days=i)) for i in range(7)
    )
    payload = schema.WeeklyReviewPayload(days=days, week_key="2026-W31")
    text = schema.render_weekly_review_brief("2026-W31", payload)
    assert "Conflict\n- None." in text
    assert "Hypothesis\n- None." in text
    assert "Possible overdue report\n- None." in text
    assert text.count("No record for this day.") == 7
