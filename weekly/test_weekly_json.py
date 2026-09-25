"""Serializer tests for weekly MD YAML schema (no Distill blocks, no sidecars)."""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from datetime import date
from pathlib import Path

import yaml

from test_weekly_event_schema import _load, _week


def _load_weekly_json():
    path = Path(__file__).with_name("weekly_json.py")
    spec = importlib.util.spec_from_file_location("memory_weekly_json", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_dumps_uses_hyphenated_keys_and_omits_distill_fields():
    schema = _load()
    wj = _load_weekly_json()
    span = schema.SpanCandidate(
        id="w31-t1",
        label="digest kickoff",
        start_date=date(2026, 7, 27),
        end_date=date(2026, 7, 28),
        confidence="high",
        steps=(
            schema.ThreadStep(
                seq=1,
                date=date(2026, 7, 27),
                event_id="mem-2026-07-27-a",
                text="kickoff",
                cite_n=1,
            ),
            schema.ThreadStep(
                seq=2,
                date=date(2026, 7, 28),
                event_id="mem-2026-07-28-workers",
                text="workers",
                cite_n=5,
                via="evolves",
            ),
        ),
        entity_keys=("memorydigest",),
    )
    wrap = schema.IntraDayThread(
        date=date(2026, 7, 27),
        weekday="Monday",
        source_field="day_wrapup",
        text="- wrap",
        empty=False,
    )
    entity = schema.WeeklyEntity(
        key="memorydigest",
        canonical="memory-digest",
        aliases=("Memory Digest",),
        first_seen=date(2026, 7, 27),
        last_seen=date(2026, 7, 28),
        week_blocks=("mem-2026-07-27-a",),
    )
    payload = schema.WeeklyReviewPayload(
        days=tuple(_week(schema, with_events=False)),
        legend={1: "mem-2026-07-27-a", 5: "mem-2026-07-28-workers"},
        week_key="2026-W31",
        cross_day_thread=(span,),
        intra_day_thread=(wrap,),
        entities=(entity,),
        summary=(
            schema.WeeklySummaryItem(
                text="digest kickoff then workers",
                weekdays=("Monday", "Tuesday"),
            ),
        ),
    )
    raw = wj.dumps(payload)
    obj = json.loads(raw)
    assert "cross-day-thread" in obj
    assert "intra-day-thread" in obj
    assert "summary" in obj
    assert "legend" not in obj
    assert "threads" not in obj
    assert "singles" not in obj
    for banned in ("days", "conflicts", "hypotheses", "blocks", "overdue", "typed_legend"):
        assert banned not in obj
    assert obj["summary"][0]["weekdays"] == ["Monday", "Tuesday"]
    assert obj["generator"]["authored"] == ["cross-day-thread"]
    assert "cite_n" not in obj["cross-day-thread"][0]["steps"][0]
    round_trip = wj.loads(raw)
    assert round_trip.cross_day_thread[0].steps[1].via == "evolves"
    assert round_trip.intra_day_thread[0].text == "- wrap"
    assert round_trip.summary[0].text == "digest kickoff then workers"
    yaml_text = wj.dump_yaml(payload)
    yobj = yaml.safe_load(yaml_text)
    assert set(yobj) == set(obj)
    assert list(yobj.keys())[-1] == "summary"


def test_entity_key_collapses_digest_aliases():
    wj = _load_weekly_json()
    keys = {
        wj.normalize_entity_key("Memory Digest"),
        wj.normalize_entity_key("MemoryDigest"),
        wj.normalize_entity_key("memory-digest"),
    }
    assert keys == {"memorydigest"}


def test_entities_from_event_blocks_keep_bilingual_aliases():
    workers_path = Path(__file__).with_name("weekly_event_workers.py")
    spec = importlib.util.spec_from_file_location(
        "memory_weekly_workers_bilingual", workers_path
    )
    assert spec is not None and spec.loader is not None
    workers = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = workers
    spec.loader.exec_module(workers)
    entities = workers._entities_from_event_blocks(
        [
            {
                "frontmatter": {
                    "id": "mem-2026-08-01-fact-aaaaaaaaaaaa",
                    "type": "fact",
                    "entity": "记忆摘要",
                    "valid_from": "2026-08-01",
                }
            },
            {
                "frontmatter": {
                    "id": "mem-2026-08-24-event-bbbbbbbbbbbb",
                    "type": "event",
                    "entity": "Memory Digest",
                    "entity_aliases": ["记忆摘要"],
                    "valid_from": "2026-08-24",
                }
            },
        ]
    )
    assert [row.key for row in entities] == ["memorydigest"]
    assert entities[0].canonical == "Memory Digest"
    assert entities[0].aliases == ("记忆摘要",)
    assert entities[0].week_blocks == (
        "mem-2026-08-01-fact-aaaaaaaaaaaa",
        "mem-2026-08-24-event-bbbbbbbbbbbb",
    )


def test_write_sidecars_deletes_json_and_yaml_leaves_md(tmp_path):
    schema = _load()
    wj = _load_weekly_json()
    payload = schema.WeeklyReviewPayload(
        days=tuple(_week(schema, with_events=False)),
        week_key="2026-W31",
    )
    md = tmp_path / "2026-W31.md"
    md.write_text("keep\n", encoding="utf-8")
    (tmp_path / "2026-W31.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "2026-W31.yaml").write_text("week_key: stale\n", encoding="utf-8")
    wj.write_sidecars(md, payload)
    assert not (tmp_path / "2026-W31.json").exists()
    assert not (tmp_path / "2026-W31.yaml").exists()
    assert md.read_text(encoding="utf-8") == "keep\n"


def test_threads_from_tool_args_drops_one_day_and_stamps_cite():
    workers_path = Path(__file__).with_name("weekly_event_workers.py")
    spec = importlib.util.spec_from_file_location(
        "memory_weekly_workers_json", workers_path
    )
    assert spec is not None and spec.loader is not None
    workers = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = workers
    spec.loader.exec_module(workers)
    legend = {
        1: "mem-2026-07-27-event-aaaa",
        2: "mem-2026-07-28-event-bbbb",
    }
    allowed = set(legend.values())
    threads, errors = workers.threads_from_tool_args(
        {
            "cross-day-thread": [
                {
                    "id": "t-short",
                    "label": "same day",
                    "steps": [
                        {
                            "seq": 1,
                            "date": "2026-07-27",
                            "event_id": "mem-2026-07-27-event-aaaa",
                            "text": "a",
                        },
                        {
                            "seq": 2,
                            "date": "2026-07-27",
                            "event_id": "mem-2026-07-27-event-aaaa",
                            "text": "b",
                            "via": "evolves",
                        },
                    ],
                },
                {
                    "id": "t-ok",
                    "label": "cross",
                    "steps": [
                        {
                            "seq": 1,
                            "date": "2026-07-27",
                            "event_id": "mem-2026-07-27-event-aaaa",
                            "text": "a",
                        },
                        {
                            "seq": 2,
                            "date": "2026-07-28",
                            "event_id": "mem-2026-07-28-event-bbbb",
                            "text": "b",
                            "via": "evolves",
                        },
                    ],
                },
            ]
        },
        event_ids=allowed,
        legend=legend,
        week_dates=workers.iso_week_dates("2026-W31"),
    )
    assert errors == []
    assert [t.id for t in threads] == ["t-ok"]
    assert threads[0].steps[0].cite_n == 1
    assert threads[0].steps[1].cite_n == 2


def test_load_sidecar_reads_md_yaml_not_json_sidecar(tmp_path):
    wj = _load_weekly_json()
    json_path = tmp_path / "2026-W33.json"
    md_path = tmp_path / "2026-W33.md"
    json_path.write_text('{"week_key": "stale-json"}\n', encoding="utf-8")
    md_path.write_text(
        "---\nweek: 2026-W33\nweek_status: pending\n---\n"
        "week_key: 2026-W33\nlegend:\n  1: mem-a\n",
        encoding="utf-8",
    )
    obj = wj.load_sidecar(json_path)
    assert obj["week_key"] == "2026-W33"
    assert obj["legend"][1] == "mem-a" or obj["legend"]["1"] == "mem-a"
    assert "stale-json" not in json.dumps(obj)


def test_compress_phrase_stops_at_semicolon():
    workers_path = Path(__file__).with_name("weekly_event_workers.py")
    spec = importlib.util.spec_from_file_location("memory_weekly_workers_compress", workers_path)
    assert spec is not None and spec.loader is not None
    workers = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = workers
    spec.loader.exec_module(workers)
    phrase = workers._compress_phrase(
        "kimi quota exhaustion diagnosed; provider switched to mimo-v2.5 after the outage",
        max_words=10,
    )
    assert phrase.endswith("diagnosed") or phrase.endswith("Diagnosed")
    assert "switched" not in phrase


def test_skip_invalidates_stamps_to_seq():
    schema = _load()
    workers_path = Path(__file__).with_name("weekly_event_workers.py")
    spec = importlib.util.spec_from_file_location("memory_weekly_workers_via", workers_path)
    assert spec is not None and spec.loader is not None
    workers = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = workers
    spec.loader.exec_module(workers)
    legend = {
        1: "mem-2026-08-12-event-aaaa",
        2: "mem-2026-08-13-event-bbbb",
        3: "mem-2026-08-14-event-cccc",
        4: "mem-2026-08-15-event-dddd",
    }
    threads, errors = workers.threads_from_tool_args(
        {
            "cross-day-thread": [
                {
                    "id": "t-skip",
                    "label": "theme",
                    "steps": [
                        {"seq": 1, "date": "2026-08-12", "event_id": legend[1], "text": "dark"},
                        {"seq": 2, "date": "2026-08-13", "event_id": legend[2], "text": "tighter", "via": "evolves"},
                        {"seq": 3, "date": "2026-08-14", "event_id": legend[3], "text": "shipped", "via": "evolves"},
                        {
                            "seq": 4,
                            "date": "2026-08-15",
                            "event_id": legend[4],
                            "text": "light instead",
                            "via": "invalidates",
                            "to_seq": 1,
                        },
                    ],
                }
            ]
        },
        event_ids=set(legend.values()),
        legend=legend,
        week_dates=workers.iso_week_dates("2026-W33"),
    )
    assert errors == []
    assert threads[0].steps[3].via == "invalidates"
    assert threads[0].steps[3].to_seq == 1
    assert threads[0].steps[1].via == "evolves"
    _ = schema


def test_threads_from_tool_args_drops_out_of_iso_week_steps():
    """W35 cannot keep a W34 Saturday step; one leftover in-week date is not a thread."""
    workers = _load_workers("memory_weekly_workers_iso_clip")
    legend = {
        1: "mem-2026-08-22-event-aaaa",
        2: "mem-2026-08-26-event-bbbb",
        3: "mem-2026-08-27-event-cccc",
    }
    week_dates = workers.iso_week_dates("2026-W35")
    dropped, errors = workers.threads_from_tool_args(
        {
            "cross-day-thread": [
                {
                    "id": "t-w34-leak",
                    "label": "leak",
                    "steps": [
                        {
                            "seq": 1,
                            "date": "2026-08-22",
                            "event_id": legend[1],
                            "text": "saturday",
                        },
                        {
                            "seq": 2,
                            "date": "2026-08-26",
                            "event_id": legend[2],
                            "text": "wednesday",
                            "via": "evolves",
                        },
                    ],
                }
            ]
        },
        event_ids=set(legend.values()),
        legend=legend,
        week_dates=week_dates,
    )
    assert errors == []
    assert dropped == []

    kept, errors = workers.threads_from_tool_args(
        {
            "cross-day-thread": [
                {
                    "id": "t-ok",
                    "label": "in week",
                    "steps": [
                        {
                            "seq": 1,
                            "date": "2026-08-22",
                            "event_id": legend[1],
                            "text": "saturday",
                        },
                        {
                            "seq": 2,
                            "date": "2026-08-26",
                            "event_id": legend[2],
                            "text": "wednesday",
                            "via": "evolves",
                        },
                        {
                            "seq": 3,
                            "date": "2026-08-27",
                            "event_id": legend[3],
                            "text": "thursday",
                            "via": "evolves",
                        },
                    ],
                }
            ]
        },
        event_ids=set(legend.values()),
        legend=legend,
        week_dates=week_dates,
    )
    assert errors == []
    assert [t.id for t in kept] == ["t-ok"]
    assert [s.date.isoformat() for s in kept[0].steps] == [
        "2026-08-26",
        "2026-08-27",
    ]
    assert kept[0].start_date == date(2026, 8, 26)
    assert kept[0].end_date == date(2026, 8, 27)


def test_run_analyst_attempt2_calls_patch_not_second_submit():
    """Validation-fail then patch: attempt 2 must force patch_weekly_thread; a second submit is ignored."""
    workers_path = Path(__file__).with_name("weekly_event_workers.py")
    spec = importlib.util.spec_from_file_location(
        "memory_weekly_workers_patch_retry", workers_path
    )
    assert spec is not None and spec.loader is not None
    workers = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = workers
    spec.loader.exec_module(workers)

    eid_a = "mem-2026-08-12-event-aaaa"
    eid_b = "mem-2026-08-13-event-bbbb"
    legend = {1: eid_a, 2: eid_b}
    forced: list[str] = []
    names: list[str] = []

    def call_llm_tools(prompt, *, purpose, force_tool_name):
        _ = prompt, purpose
        forced.append(force_tool_name)
        if force_tool_name == "submit_weekly_thread":
            names.append("submit_weekly_thread")
            return {
                "tool_name": "submit_weekly_thread",
                "tool_args": {
                    "cross-day-thread": [
                        {
                            "id": "t1",
                            "label": "one day",
                            "steps": [
                                {
                                    "seq": 1,
                                    "date": "2026-08-12",
                                    "event_id": eid_a,
                                    "text": "first",
                                },
                                {
                                    "seq": 2,
                                    "date": "2026-08-13",
                                    "event_id": "mem-2026-08-13-event-HALLUCINATED",
                                    "text": "invented",
                                    "via": "evolves",
                                },
                            ],
                        }
                    ]
                },
            }
        names.append("patch_weekly_thread")
        return {
            "tool_name": "patch_weekly_thread",
            "tool_args": {
                "cross-day-thread": [
                    {
                        "id": "t1",
                        "label": "two days",
                        "steps": [
                            {
                                "seq": 1,
                                "date": "2026-08-12",
                                "event_id": eid_a,
                                "text": "first",
                            },
                            {
                                "seq": 2,
                                "date": "2026-08-13",
                                "event_id": eid_b,
                                "text": "next",
                                "via": "evolves",
                            },
                        ],
                    }
                ]
            },
        }

    _blocks, threads = workers._run_analyst(
        role="thread",
        purpose="worker1_thread",
        week_key="2026-W33",
        merged_events_md="events",
        legend=legend,
        event_ids={eid_a, eid_b},
        call_llm_tools=call_llm_tools,
        log=lambda _m: None,
    )
    assert forced[0] == "submit_weekly_thread"
    assert all(name == "patch_weekly_thread" for name in forced[1:])
    assert "submit_weekly_thread" not in names[1:]
    assert len(threads) == 1
    assert threads[0].steps[0].via is None
    assert {s.date.isoformat() for s in threads[0].steps} == {
        "2026-08-12",
        "2026-08-13",
    }


def test_summary_worker_copies_wrapup_and_thread():
    """Chronicle rows: leftover event titles plus thread label+outcome."""
    workers_path = Path(__file__).with_name("weekly_event_workers.py")
    spec = importlib.util.spec_from_file_location(
        "memory_weekly_workers_summary", workers_path
    )
    assert spec is not None and spec.loader is not None
    workers = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = workers
    spec.loader.exec_module(workers)
    schema = _load()
    intra = (
        schema.IntraDayThread(
            date=date(2026, 8, 17),
            weekday="Monday",
            source_field="events",
            text="- parent photos",
            empty=False,
        ),
    )
    cross = (
        schema.SpanCandidate(
            id="t1",
            label="ui hops",
            start_date=date(2026, 8, 17),
            end_date=date(2026, 8, 18),
            confidence="high",
            related_event_ids=("mem-2026-08-17-a", "mem-2026-08-18-b"),
            steps=(
                schema.ThreadStep(
                    seq=1,
                    date=date(2026, 8, 17),
                    event_id="mem-2026-08-17-a",
                    text="drop hops",
                ),
                schema.ThreadStep(
                    seq=2,
                    date=date(2026, 8, 18),
                    event_id="mem-2026-08-18-b",
                    text="chronicle tab",
                    via="evolves",
                ),
            ),
            outcome={"state": "resolved", "text": "legend retired"},
        ),
    )
    event_blocks = [
        {
            "frontmatter": {
                "id": "evt-photos",
                "type": "event",
                "entity": "photos",
                "predicate": "shared",
                "valid_from": "2026-08-17",
            },
            "body": "Beginning: parent photos.\nCourse: parent photos.\nOutcome: parent photos.",
        },
        {
            "frontmatter": {
                "id": "mem-2026-08-17-a",
                "type": "event",
                "entity": "ui hops",
                "predicate": "shipped",
                "valid_from": "2026-08-17",
            },
            "body": "Beginning: drop hops.\n",
        },
    ]

    rows = workers._run_summary_worker(
        week_key="2026-W34",
        intra=intra,
        cross=cross,
        event_blocks=event_blocks,
        log=lambda _m: None,
    )
    assert [r.text for r in rows] == [
        "ui hops. legend retired",
        "Parent photos",
    ]
    assert rows[0].weekdays == ("Monday", "Tuesday")
    assert rows[1].weekdays == ("Monday",)


def test_summary_worker_empty_when_no_wrapup_or_thread():
    workers_path = Path(__file__).with_name("weekly_event_workers.py")
    spec = importlib.util.spec_from_file_location(
        "memory_weekly_workers_summary_empty", workers_path
    )
    assert spec is not None and spec.loader is not None
    workers = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = workers
    spec.loader.exec_module(workers)
    schema = _load()
    intra = (
        schema.IntraDayThread(
            date=date(2026, 8, 17),
            weekday="Monday",
            source_field="day_wrapup",
            text="",
            empty=True,
        ),
    )
    rows = workers._run_summary_worker(
        week_key="2026-W34",
        intra=intra,
        cross=(),
        log=lambda _m: None,
    )
    assert rows == ()


def test_summary_worker_clips_weekdays_to_iso_week():
    """W35 Chronicle must not paint Saturday from 2026-08-22."""
    workers = _load_workers("memory_weekly_workers_summary_iso")
    schema = _load()
    intra = (
        schema.IntraDayThread(
            date=date(2026, 8, 26),
            weekday="Wednesday",
            source_field="events",
            text="- in week",
            empty=False,
        ),
    )
    cross = (
        schema.SpanCandidate(
            id="t1",
            label="qixi card",
            start_date=date(2026, 8, 22),
            end_date=date(2026, 8, 26),
            confidence="high",
            related_event_ids=("mem-2026-08-22-a", "mem-2026-08-26-b"),
            steps=(
                schema.ThreadStep(
                    seq=1,
                    date=date(2026, 8, 22),
                    event_id="mem-2026-08-22-a",
                    text="saturday leak",
                ),
                schema.ThreadStep(
                    seq=2,
                    date=date(2026, 8, 26),
                    event_id="mem-2026-08-26-b",
                    text="wednesday",
                    via="evolves",
                ),
            ),
            outcome={"state": "open", "text": "still open"},
        ),
    )
    event_blocks = [
        {
            "frontmatter": {
                "id": "mem-2026-08-22-leak",
                "type": "event",
                "entity": "photos",
                "predicate": "shared",
                "valid_from": "2026-08-22",
            },
            "body": "Beginning: last week photos.\n",
        },
    ]
    rows = workers._run_summary_worker(
        week_key="2026-W35",
        intra=intra,
        cross=cross,
        event_blocks=event_blocks,
        log=lambda _m: None,
    )
    assert [r.text for r in rows] == ["qixi card. still open"]
    assert rows[0].weekdays == ("Wednesday",)
    assert "Saturday" not in rows[0].weekdays


def test_run_analyst_rejects_second_submit_on_attempt2():
    workers_path = Path(__file__).with_name("weekly_event_workers.py")
    spec = importlib.util.spec_from_file_location(
        "memory_weekly_workers_reject_submit", workers_path
    )
    assert spec is not None and spec.loader is not None
    workers = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = workers
    spec.loader.exec_module(workers)

    eid_a = "mem-2026-08-12-event-aaaa"
    eid_b = "mem-2026-08-13-event-bbbb"
    legend = {1: eid_a, 2: eid_b}
    calls = {"n": 0}

    def call_llm_tools(prompt, *, purpose, force_tool_name):
        _ = prompt, purpose
        calls["n"] += 1
        if force_tool_name == "submit_weekly_thread":
            return {
                "tool_name": "submit_weekly_thread",
                "tool_args": {
                    "cross-day-thread": [
                        {
                            "id": "t1",
                            "label": "bad",
                            "steps": [
                                {
                                    "seq": 1,
                                    "date": "2026-08-12",
                                    "event_id": "mem-2026-08-12-event-MISSING",
                                    "text": "a",
                                }
                            ],
                        }
                    ]
                },
            }
        return {
            "tool_name": "submit_weekly_thread",
            "tool_args": {
                "cross-day-thread": [
                    {
                        "id": "t1",
                        "label": "sneak",
                        "steps": [
                            {
                                "seq": 1,
                                "date": "2026-08-12",
                                "event_id": eid_a,
                                "text": "a",
                            },
                            {
                                "seq": 2,
                                "date": "2026-08-13",
                                "event_id": eid_b,
                                "text": "b",
                                "via": "evolves",
                            },
                        ],
                    }
                ]
            },
        }

    _blocks, threads = workers._run_analyst(
        role="thread",
        purpose="worker1_thread",
        week_key="2026-W33",
        merged_events_md="events",
        legend=legend,
        event_ids={eid_a, eid_b},
        call_llm_tools=call_llm_tools,
        log=lambda _m: None,
    )
    assert threads == []
    assert calls["n"] >= 2


def test_w35_wrapup_and_thread_make_four_summary_rows():
    """Three leftover Monday events plus one cross-day thread become four Chronicle rows."""
    workers_path = Path(__file__).with_name("weekly_event_workers.py")
    spec = importlib.util.spec_from_file_location(
        "memory_weekly_workers_w35_summary", workers_path
    )
    assert spec is not None and spec.loader is not None
    workers = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = workers
    spec.loader.exec_module(workers)
    schema = _load()
    intra = (
        schema.IntraDayThread(
            date=date(2026, 8, 24),
            weekday="Monday",
            source_field="events",
            text="- a\n- b\n- c",
            empty=False,
        ),
        schema.IntraDayThread(
            date=date(2026, 8, 25),
            weekday="Tuesday",
            source_field="events",
            text="",
            empty=True,
        ),
    )
    cross = (
        schema.SpanCandidate(
            id="thread-1",
            label="Codebase understanding and context gathering",
            start_date=date(2026, 8, 24),
            end_date=date(2026, 8, 25),
            confidence="high",
            related_event_ids=(
                "mem-2026-08-24-fact-9605EB855DAA",
                "mem-2026-08-25-fact-62BCB7010463",
            ),
            steps=(
                schema.ThreadStep(
                    seq=1,
                    date=date(2026, 8, 24),
                    event_id="mem-2026-08-24-fact-9605EB855DAA",
                    text="Identified repository structure",
                ),
                schema.ThreadStep(
                    seq=2,
                    date=date(2026, 8, 25),
                    event_id="mem-2026-08-25-fact-62BCB7010463",
                    text="Expanded understanding",
                    via="evolves",
                ),
            ),
            outcome={
                "state": "resolved",
                "text": (
                    "Gained sufficient understanding of codebase structure to proceed "
                    "with tasks"
                ),
            },
        ),
    )
    event_blocks = [
        {
            "frontmatter": {
                "id": f"evt-mon-{i}",
                "type": "event",
                "entity": "week",
                "predicate": "did",
                "valid_from": "2026-08-24",
            },
            "body": f"Beginning: {title}\nCourse: {title}\nOutcome: {title}",
        }
        for i, title in enumerate(
            (
                "The user iterated on a handwritten Qixi greeting card",
                "The user investigated MyMemory system issues",
                "The user requested to see the full day wrap-up prompt",
            ),
            start=1,
        )
    ]
    rows = workers._run_summary_worker(
        week_key="2026-W35",
        intra=intra,
        cross=cross,
        event_blocks=event_blocks,
        log=lambda _m: None,
    )
    assert len(rows) == 4
    assert rows[0].weekdays == ("Monday", "Tuesday")
    assert "Codebase understanding" in rows[0].text
    leftovers = [r.text for r in rows[1:]]
    assert any("Qixi" in t for t in leftovers)
    assert any("MyMemory" in t for t in leftovers)
    assert any("wrap-up prompt" in t for t in leftovers)


def _load_workers(mod_name: str):
    workers_path = Path(__file__).with_name("weekly_event_workers.py")
    spec = importlib.util.spec_from_file_location(mod_name, workers_path)
    assert spec is not None and spec.loader is not None
    workers = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = workers
    spec.loader.exec_module(workers)
    return workers


def _write_typed_daily(
    tmp_path: Path,
    day: str,
    *,
    block_id: str,
    block_type: str,
    entity: str,
    body: str,
    wrapup: str | None = None,
) -> Path:
    daily = tmp_path / "memories" / "staging" / "daily" / f"{day}.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    text = (
        f"---\nid: {block_id}\ntype: {block_type}\nentity: {entity}\n"
        f"predicate: recorded\nvalid_from: {day}\nstatus: candidate\n"
        "sources: [session s1]\n---\n"
        f"{body}\n"
    )
    if wrapup:
        text += f"\n## Day wrap-up\n{wrapup}\n"
    daily.write_text(text, encoding="utf-8")
    return daily


def test_collect_daily_event_cards_skips_fact_and_wrapup(tmp_path):
    workers = _load_workers("memory_weekly_collect_events")
    event_id = "mem-2026-08-24-event-AAAA"
    _write_typed_daily(
        tmp_path,
        "2026-08-24",
        block_id=event_id,
        block_type="event",
        entity="Qixi",
        body="User iterated on the Qixi greeting card.",
        wrapup="- wrap-up must not become an event id",
    )
    fact_id = "mem-2026-08-24-fact-CDA5F9FF2BEB"
    daily = tmp_path / "memories" / "staging" / "daily" / "2026-08-24.md"
    daily.write_text(
        daily.read_text(encoding="utf-8")
        + (
            f"---\nid: {fact_id}\ntype: fact\nentity: dating ideas\n"
            "status: candidate\n---\nDating idea collection now totals 144 items.\n"
        ),
        encoding="utf-8",
    )
    by_day = {date(2026, 8, 24): daily}
    cards = workers._collect_daily_event_cards(by_day, [date(2026, 8, 24)])
    ids = {c["event_id"] for c in cards}
    assert ids == {event_id}
    assert fact_id not in ids


def test_collect_daily_event_cards_uses_file_day_when_valid_from_outside_week(
    tmp_path,
):
    workers = _load_workers("memory_weekly_collect_vf_clip")
    daily = tmp_path / "memories" / "staging" / "daily" / "2026-08-24.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text(
        (
            "---\nid: mem-2026-08-24-event-AAAA\ntype: event\nentity: Qixi\n"
            "predicate: recorded\nvalid_from: 2026-08-22\nstatus: candidate\n---\n"
            "Card lived on Monday but valid_from is last Saturday.\n"
        ),
        encoding="utf-8",
    )
    week_dates = workers.iso_week_dates("2026-W35")
    by_day = {date(2026, 8, 24): daily}
    cards = workers._collect_daily_event_cards(by_day, week_dates)
    assert len(cards) == 1
    assert cards[0]["date"] == date(2026, 8, 24)


def test_score_event_clusters_two_days_same_theme(monkeypatch):
    workers = _load_workers("memory_weekly_score_link")
    monkeypatch.setattr(
        "recall.embed._encode_texts",
        lambda texts: [],
        raising=False,
    )
    a = {
        "event_id": "mem-2026-08-24-event-QIXI1",
        "date": date(2026, 8, 24),
        "entity": "Qixi card",
        "predicate": "wrote",
        "body": "transcribed handwritten Qixi greeting card",
        "snippet": "transcribed handwritten Qixi greeting card",
    }
    b = {
        "event_id": "mem-2026-08-26-event-QIXI2",
        "date": date(2026, 8, 26),
        "entity": "Qixi card",
        "predicate": "shared",
        "body": "shared Qixi greeting card with partner",
        "snippet": "shared Qixi greeting card with partner",
    }
    clusters = workers._score_event_clusters([a, b])
    assert len(clusters) == 1
    assert [c["event_id"] for c in clusters[0]] == [
        a["event_id"],
        b["event_id"],
    ]


def test_score_event_clusters_unrelated_no_cluster(monkeypatch):
    workers = _load_workers("memory_weekly_score_skip")
    monkeypatch.setattr(
        "recall.embed._encode_texts",
        lambda texts: [],
        raising=False,
    )
    a = {
        "event_id": "mem-2026-08-24-event-AAAA",
        "date": date(2026, 8, 24),
        "entity": "Qixi card",
        "predicate": "wrote",
        "body": "greeting card transcription",
        "snippet": "greeting card transcription",
    }
    b = {
        "event_id": "mem-2026-08-26-event-BBBB",
        "date": date(2026, 8, 26),
        "entity": "Smoothie reminders",
        "predicate": "scheduled",
        "body": "avocado mango shopping list",
        "snippet": "avocado mango shopping list",
    }
    assert workers._score_event_clusters([a, b]) == []


def test_score_event_clusters_same_day_no_emit(monkeypatch):
    workers = _load_workers("memory_weekly_score_sameday")
    monkeypatch.setattr(
        "recall.embed._encode_texts",
        lambda texts: [],
        raising=False,
    )
    a = {
        "event_id": "mem-2026-08-24-event-AAAA",
        "date": date(2026, 8, 24),
        "entity": "Qixi card",
        "predicate": "wrote",
        "body": "transcribed Qixi greeting",
        "snippet": "transcribed Qixi greeting",
    }
    b = {
        "event_id": "mem-2026-08-24-event-BBBB",
        "date": date(2026, 8, 24),
        "entity": "Qixi card",
        "predicate": "polished",
        "body": "polished Qixi greeting",
        "snippet": "polished Qixi greeting",
    }
    assert workers._score_event_clusters([a, b]) == []


def test_build_analyst_prompt_has_candidates_not_merged_events():
    workers = _load_workers("memory_weekly_prompt_layout")
    layout = workers.format_thread_candidate_layout(
        [
            {
                "event_id": "mem-2026-08-24-event-AAAA",
                "date": date(2026, 8, 24),
                "snippet": "hello",
            }
        ],
        [],
    )
    prompt = workers._build_analyst_prompt(
        purpose="worker1_thread",
        week_key="2026-W35",
        role="thread",
        merged_events_md="SHOULD_NOT_APPEAR " + ("x" * 200),
        legend={1: "mem-2026-08-24-event-AAAA"},
        candidate_layout=layout,
        skeleton_json='{"cross-day-thread": []}',
    )
    assert "MERGED EVENTS" not in prompt
    assert "CANDIDATE INDEX" in prompt
    assert "mem-2026-08-24-event-AAAA" in prompt
    assert "SHOULD_NOT_APPEAR" not in prompt


def test_thread_prompt_token_time(tmp_path, monkeypatch):
    """Pre-cutover worker1_thread dump was the full YAML; compare chars here.

    Live eval_weekly_threads_mimo.py --full-generate now prints worker1_thread
    vs worker1_event input_tokens separately. Record a pre-change thread
    input_tokens from that script before relying on the live path.
    """
    workers = _load_workers("memory_weekly_token_time")
    monkeypatch.setattr(
        "recall.embed._encode_texts",
        lambda texts: [],
        raising=False,
    )
    blob = ("Qixi greeting card iteration paragraph. " * 40).strip()
    days = ("2026-08-24", "2026-08-25", "2026-08-26")
    event_ids = []
    for i, day in enumerate(days, start=1):
        eid = f"mem-{day}-event-TOKEN{i:04d}"
        event_ids.append(eid)
        _write_typed_daily(
            tmp_path,
            day,
            block_id=eid,
            block_type="event",
            entity="Qixi card",
            body=blob,
            wrapup="- wrap-up ignored" if i == 1 else None,
        )
    _write_typed_daily(
        tmp_path,
        "2026-08-24",
        block_id="mem-2026-08-24-fact-NOPE",
        block_type="fact",
        entity="noise",
        body="fact must not enter the thread prompt " + blob,
    )
    # fact write overwrote 08-24; rewrite with both blocks
    day24 = tmp_path / "memories" / "staging" / "daily" / "2026-08-24.md"
    day24.write_text(
        (
            f"---\nid: {event_ids[0]}\ntype: event\nentity: Qixi card\n"
            f"predicate: recorded\nvalid_from: 2026-08-24\nstatus: candidate\n"
            "sources: [session s1]\n---\n"
            f"{blob}\n"
            "---\nid: mem-2026-08-24-fact-NOPE\ntype: fact\nentity: noise\n"
            "status: candidate\n---\n"
            f"fact must not enter the thread prompt {blob}\n"
            "## Day wrap-up\n- wrap-up ignored\n"
        ),
        encoding="utf-8",
    )
    by_day = {
        date.fromisoformat(d): tmp_path / "memories" / "staging" / "daily" / f"{d}.md"
        for d in days
    }
    t0 = time.perf_counter()
    cards = workers._collect_daily_event_cards(
        by_day, [date.fromisoformat(d) for d in days]
    )
    clusters = workers._score_event_clusters(cards)
    score_ms = (time.perf_counter() - t0) * 1000.0
    layout = workers.format_thread_candidate_layout(cards, clusters)
    skeleton = workers.thread_skeleton_args(clusters)
    prompt = workers._build_analyst_prompt(
        purpose="worker1_thread",
        week_key="2026-W35",
        role="thread",
        merged_events_md="",
        legend={},
        candidate_layout=layout,
        skeleton_json=json.dumps(skeleton, ensure_ascii=False),
    )
    event_blocks = [
        {
            "frontmatter": {
                "id": c["event_id"],
                "type": "event",
                "entity": c["entity"],
                "predicate": c["predicate"],
                "valid_from": c["date"].isoformat(),
            },
            "body": c["body"],
        }
        for c in cards
    ]
    legacy_chars = len("\n\n".join(workers._render_block(b) for b in event_blocks))
    new_chars = len(layout) + len(json.dumps(skeleton, ensure_ascii=False))
    chars_ratio = new_chars / legacy_chars if legacy_chars else 1.0
    est_tokens = new_chars // 4
    line = (
        f"legacy_chars={legacy_chars} new_chars={new_chars} "
        f"chars_ratio={chars_ratio:.3f} est_tokens={est_tokens} "
        f"score_ms={score_ms:.1f}"
    )
    print(line)
    assert "legacy_chars=" in line and "new_chars=" in line
    assert new_chars < 0.4 * legacy_chars
    assert score_ms < 2000
    assert "mem-2026-08-24-fact-NOPE" not in prompt
    assert "wrap-up ignored" not in prompt


def test_score_event_clusters_min_distinct_periods_one_keeps_same_day(monkeypatch):
    workers = _load_workers("memory_weekly_score_period1")
    monkeypatch.setattr(
        "recall.embed._encode_texts",
        lambda texts: [],
        raising=False,
    )
    a = {
        "event_id": "mem-2026-08-24-event-AAAA",
        "date": date(2026, 8, 24),
        "entity": "Qixi card",
        "predicate": "wrote",
        "body": "transcribed Qixi greeting",
        "snippet": "transcribed Qixi greeting",
    }
    b = {
        "event_id": "mem-2026-08-24-event-BBBB",
        "date": date(2026, 8, 24),
        "entity": "Qixi card",
        "predicate": "polished",
        "body": "polished Qixi greeting",
        "snippet": "polished Qixi greeting",
    }
    clusters = workers._score_event_clusters([a, b], min_distinct_periods=1)
    assert len(clusters) == 1
    assert workers._score_event_clusters([a, b]) == []



