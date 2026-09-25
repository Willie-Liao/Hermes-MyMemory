"""Tests for the Step 1 event-first weekly intermediate schema."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest


def _load():
    path = Path(__file__).with_name("weekly_event_schema.py")
    spec = importlib.util.spec_from_file_location("memory_weekly_event_schema", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # dataclasses needs the module registered in sys.modules to resolve
    # `from __future__ import annotations` string annotations at class build time.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _week(schema, *, with_events: bool = True) -> list:
    """Monday 2026-07-27 .. Sunday 2026-08-02, only Monday populated."""
    days = []
    for offset in range(7):
        day = date(2026, 7, 27) + timedelta(days=offset)
        if offset == 0 and with_events:
            events = (schema.EventCite(1, "mem-2026-07-27-a"), schema.EventCite(2, "mem-2026-07-27-b"))
            sentences = (
                schema.ClaimSentence("Project kickoff happened.", cite=1),
                schema.ClaimSentence("The daily digest shipped.", cite=2),
            )
        else:
            events = ()
            sentences = ()
        days.append(schema.DayBrief(day=day, events=events, sentences=sentences))
    return days


def test_missing_and_empty_dates_render_no_record_text():
    schema = _load()
    empty_day = schema.DayBrief(day=date(2026, 7, 29))
    assert schema.worker2_format_day_events(empty_day.sentences) == schema.EMPTY_DAY_TEXT
    assert schema.EMPTY_DAY_TEXT == "No record for this day."
    section = schema.render_date_brief_section([empty_day])
    assert "Wednesday — 2026-07-29 · Events\nNo record for this day." in section


def test_duplicate_ids_rejected():
    schema = _load()
    payload = schema.WeeklyReviewPayload(
        days=(),
        conflicts=(schema.ConflictItem(id="dup-1", text="Tension A"),),
        hypotheses=(schema.HypothesisItem(id="dup-1", text="Bet A"),),
    )
    errors = schema.validate_no_duplicate_ids(payload)
    assert any("dup-1" in e for e in errors)


def test_duplicate_ids_ok_when_unique():
    schema = _load()
    payload = schema.WeeklyReviewPayload(
        days=(),
        conflicts=(schema.ConflictItem(id="cfl-1", text="Tension A"),),
        hypotheses=(schema.HypothesisItem(id="hyp-1", text="Bet A"),),
        span_candidates=(
            schema.SpanCandidate(
                id="span-1",
                label="Initiative",
                start_date=date(2026, 7, 27),
                end_date=date(2026, 7, 29),
                confidence="medium",
            ),
        ),
    )
    assert schema.validate_no_duplicate_ids(payload) == []


def test_invalid_confidence_rejected_for_span_candidates():
    schema = _load()
    with pytest.raises(ValueError):
        schema.SpanCandidate(
            id="span-1",
            label="Initiative",
            start_date=date(2026, 7, 27),
            end_date=date(2026, 7, 29),
            confidence="super-sure",
        )


def test_valid_confidence_accepted_for_span_candidates():
    schema = _load()
    span = schema.SpanCandidate(
        id="span-1",
        label="Initiative",
        start_date=date(2026, 7, 27),
        end_date=date(2026, 7, 29),
        confidence="high",
    )
    assert span.confidence == "high"
    explicit = schema.SpanCandidate(
        id="span-2",
        label="Deadline",
        start_date=date(2026, 7, 27),
        end_date=date(2026, 8, 2),
        confidence="explicit",
    )
    assert explicit.confidence == "explicit"


def test_span_candidate_end_before_start_rejected():
    schema = _load()
    with pytest.raises(ValueError):
        schema.SpanCandidate(
            id="span-1",
            label="Initiative",
            start_date=date(2026, 7, 29),
            end_date=date(2026, 7, 27),
            confidence="high",
        )


def test_misplaced_citations_fail_validation():
    schema = _load()
    # Citation lands mid-sentence instead of right after sentence-ending punctuation.
    paragraph = "Project [1] kickoff happened without a period first"
    errors = schema.validate_sentence_final_cites(paragraph)
    assert errors
    assert "[1]" in errors[0]


def test_sentence_final_citations_pass_validation():
    schema = _load()
    paragraph = "Project kickoff happened. [1] The daily digest shipped. [2]"
    assert schema.validate_sentence_final_cites(paragraph) == []


def test_rendered_claim_has_cite_directly_after_claim():
    schema = _load()
    sentence = schema.ClaimSentence("Project kickoff happened.", cite=1)
    paragraph = schema.worker2_format_day_events([sentence])
    assert paragraph == "Project kickoff happened. [1]"
    assert schema.validate_sentence_final_cites(paragraph) == []


def test_worker2_formats_titled_events_as_separate_paragraphs():
    schema = _load()
    body = schema.worker2_format_day_events(
        [
            schema.ClaimSentence(
                "First happened.",
                title="First summary",
            ),
            schema.ClaimSentence(
                "Second happened.",
                title="Second summary",
            ),
        ]
    )
    assert body == (
        "**First summary**\nFirst happened.\n\n"
        "**Second summary**\nSecond happened."
    )


def test_format_day_header_includes_event_markers():
    schema = _load()
    header = schema.format_day_header(date(2026, 7, 27), [1, 2])
    assert header == "Monday — 2026-07-27 · Events [1] [2]"
    assert schema.validate_day_header(header) == []


def test_format_day_header_without_events_has_no_markers():
    schema = _load()
    header = schema.format_day_header(date(2026, 7, 29))
    assert header == "Wednesday — 2026-07-29 · Events"
    assert schema.validate_day_header(header) == []


def test_day_header_weekday_mismatch_rejected():
    schema = _load()
    errors = schema.validate_day_header("Tuesday — 2026-07-27 · Events")
    assert errors
    assert "Tuesday" in errors[0]


def test_monday_to_sunday_order_ok():
    schema = _load()
    days = _week(schema)
    assert schema.validate_week_order(days) == []


def test_out_of_order_days_rejected():
    schema = _load()
    days = _week(schema)
    days[0], days[1] = days[1], days[0]
    errors = schema.validate_week_order(days)
    assert any("Tuesday" in e or "consecutive" in e for e in errors)


def test_non_consecutive_days_rejected():
    schema = _load()
    days = _week(schema)
    # Skip ahead a week on the last day, breaking consecutiveness.
    days[-1] = schema.DayBrief(day=date(2026, 8, 9))
    errors = schema.validate_week_order(days)
    assert any("consecutive" in e for e in errors)


def test_full_week_render_matches_plan_format():
    schema = _load()
    days = _week(schema)
    section = schema.render_date_brief_section(days)
    assert section.startswith(
        "Monday — 2026-07-27 · Events [1] [2]\n"
        "Project kickoff happened. [1]\n\n"
        "The daily digest shipped. [2]"
    )
    assert "Wednesday — 2026-07-29 · Events\nNo record for this day." in section


def test_validate_weekly_review_payload_happy_path():
    schema = _load()
    days = tuple(_week(schema))
    payload = schema.WeeklyReviewPayload(days=days, legend={1: "mem-2026-07-27-a", 2: "mem-2026-07-27-b"})
    assert schema.validate_weekly_review_payload(payload) == []


def test_validate_weekly_review_payload_catches_misplaced_cite():
    schema = _load()
    bad_day = schema.DayBrief(
        day=date(2026, 7, 27),
        events=(schema.EventCite(1, "mem-2026-07-27-a"),),
        sentences=(schema.ClaimSentence("Kickoff [1] happened without terminal period", cite=None),),
    )
    days = [bad_day] + _week(schema)[1:]
    payload = schema.WeeklyReviewPayload(days=tuple(days))
    errors = schema.validate_weekly_review_payload(payload)
    assert any("[1]" in e for e in errors)


def test_claim_sentence_rejects_invalid_kind():
    schema = _load()
    with pytest.raises(ValueError):
        schema.ClaimSentence("Some text.", kind="opinion")


def test_claim_sentence_accepts_fact_procedure_decision():
    schema = _load()
    for kind in ("fact", "procedure", "decision"):
        sentence = schema.ClaimSentence("Some text.", kind=kind)
        assert sentence.kind == kind


def test_payload_defaults_omit_cross_day_thread():
    schema = _load()
    days = tuple(_week(schema, with_events=False))
    payload = schema.WeeklyReviewPayload(days=days, week_key="2026-W31")
    assert payload.cross_day_thread == ()
    assert payload.intra_day_thread == ()
    assert payload.entities == ()
    assert payload.summary == ()


def test_span_candidate_defaults_omit_steps_outcome_entity_keys():
    schema = _load()
    span = schema.SpanCandidate(
        id="span-project-deadline",
        label="Project deadline",
        start_date=date(2026, 7, 27),
        end_date=date(2026, 8, 2),
        confidence="explicit",
    )
    assert span.steps == ()
    assert span.outcome is None
    assert span.entity_keys == ()


def test_thread_step_via_and_entity_record_shape():
    schema = _load()
    step = schema.ThreadStep(
        seq=2,
        date=date(2026, 8, 13),
        event_id="mem-2026-08-13-event-48AB7607830B",
        text="provider swap",
        cite_n=3,
        via="evolves",
    )
    span = schema.SpanCandidate(
        id="w33-t1",
        label="memory-digest validation",
        start_date=date(2026, 8, 12),
        end_date=date(2026, 8, 14),
        confidence="high",
        steps=(step,),
        outcome={"state": "open", "text": "still stuck"},
        entity_keys=("memorydigest",),
    )
    entity = schema.WeeklyEntity(
        key="memorydigest",
        canonical="memory-digest",
        aliases=("MemoryDigest",),
        first_seen=date(2026, 8, 12),
        last_seen=date(2026, 8, 14),
        week_blocks=("mem-2026-08-12-event-9625547B667B",),
    )
    wrap = schema.IntraDayThread(
        date=date(2026, 8, 15),
        weekday="Saturday",
        source_field="day_wrapup",
        text="- wrap",
        empty=False,
    )
    payload = schema.WeeklyReviewPayload(
        days=tuple(_week(schema, with_events=False)),
        cross_day_thread=(span,),
        intra_day_thread=(wrap,),
        entities=(entity,),
    )
    assert payload.cross_day_thread[0].steps[0].via == "evolves"
    assert payload.entities[0].key == "memorydigest"


def test_merge_sunday_evening_keeps_weekday_intra_and_appends_sunday():
    schema = _load()
    monday = date(2026, 8, 10)
    sunday = date(2026, 8, 16)
    base = schema.WeeklyReviewPayload(
        days=(
            schema.DayBrief(
                day=monday,
                events=(schema.EventCite(1, "mem-mon"),),
                sentences=(schema.ClaimSentence("Monday kickoff.", cite=1),),
            ),
            schema.DayBrief(
                day=sunday,
                events=(schema.EventCite(2, "mem-sun-early"),),
                sentences=(schema.ClaimSentence("Sunday afternoon.", cite=2),),
            ),
        ),
        legend={1: "mem-mon", 2: "mem-sun-early"},
        week_key="2026-W33",
        intra_day_thread=(
            schema.IntraDayThread(
                date=monday,
                weekday="Monday",
                source_field="events",
                text="- Monday kickoff",
                empty=False,
            ),
            schema.IntraDayThread(
                date=sunday,
                weekday="Sunday",
                source_field="events",
                text="- Sunday afternoon",
                empty=False,
            ),
        ),
        entities=(
            schema.WeeklyEntity(
                key="weeklyui",
                canonical="Weekly UI",
                week_blocks=("mem-sun-early",),
            ),
        ),
        summary=(schema.WeeklySummaryItem(text="Sunday afternoon", weekdays=("Sunday",)),),
        cross_day_thread=(
            schema.SpanCandidate(
                id="thread-ui",
                label="Weekly UI",
                start_date=monday,
                end_date=sunday,
                confidence="high",
                entity_keys=("weeklyui",),
                steps=(
                    schema.ThreadStep(
                        seq=1,
                        date=sunday,
                        event_id="mem-sun-early",
                        text="afternoon",
                    ),
                ),
            ),
        ),
    )
    delta = schema.WeeklyReviewPayload(
        days=(
            schema.DayBrief(
                day=sunday,
                events=(schema.EventCite(1, "mem-sun-late"),),
                sentences=(schema.ClaimSentence("Sunday evening leftover.", cite=1),),
            ),
        ),
        legend={1: "mem-sun-late"},
        week_key="2026-W33",
        intra_day_thread=(
            schema.IntraDayThread(
                date=sunday,
                weekday="Sunday",
                source_field="events",
                text="- Sunday evening leftover\n- Sunday afternoon",
                empty=False,
            ),
        ),
        entities=(
            schema.WeeklyEntity(
                key="weeklyui",
                canonical="Weekly UI",
                week_blocks=("mem-sun-late",),
            ),
        ),
        summary=(
            schema.WeeklySummaryItem(text="Sunday evening leftover", weekdays=("Sunday",)),
            schema.WeeklySummaryItem(text="Sunday afternoon", weekdays=("Sunday",)),
        ),
        cross_day_thread=(
            schema.SpanCandidate(
                id="thread-ui",
                label="Weekly UI evening",
                start_date=sunday,
                end_date=sunday,
                confidence="high",
                entity_keys=("weeklyui",),
                steps=(
                    schema.ThreadStep(
                        seq=1,
                        date=sunday,
                        event_id="mem-sun-late",
                        text="leftover",
                        cite_n=1,
                    ),
                ),
            ),
        ),
    )
    merged = schema._merge_sunday_evening_payload(base, delta)
    monday_intra = next(row for row in merged.intra_day_thread if row.date == monday)
    sunday_intra = next(row for row in merged.intra_day_thread if row.date == sunday)
    assert monday_intra.text == "- Monday kickoff"
    assert "- Sunday afternoon" in sunday_intra.text
    assert "- Sunday evening leftover" in sunday_intra.text
    assert sunday_intra.text.count("- Sunday afternoon") == 1
    assert merged.legend[1] == "mem-mon"
    assert merged.legend[2] == "mem-sun-early"
    assert 3 in merged.legend
    assert merged.legend[3] == "mem-sun-late"
    ent = next(e for e in merged.entities if e.key == "weeklyui")
    assert "mem-sun-early" in ent.week_blocks
    assert "mem-sun-late" in ent.week_blocks
    texts = [item.text for item in merged.summary]
    assert texts.count("Sunday afternoon") == 1
    assert "Sunday evening leftover" in texts
    thread = merged.cross_day_thread[0]
    assert [step.seq for step in thread.steps] == [1, 2]
    assert thread.steps[1].event_id == "mem-sun-late"
    sunday_brief = next(d for d in merged.days if d.day == sunday)
    assert [e.mem_id for e in sunday_brief.events] == ["mem-sun-early", "mem-sun-late"]


def test_merge_sunday_evening_empty_delta_is_base():
    schema = _load()
    base = schema.WeeklyReviewPayload(
        days=tuple(_week(schema, with_events=False)),
        week_key="2026-W31",
        summary=(schema.WeeklySummaryItem(text="keep"),),
    )
    delta = schema.WeeklyReviewPayload(days=(), week_key="2026-W31")
    assert schema._merge_sunday_evening_payload(base, delta) is base

