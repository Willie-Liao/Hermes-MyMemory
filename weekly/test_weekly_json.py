"""Serializer tests for weekly MD YAML schema (no Distill blocks, no sidecars)."""

from __future__ import annotations

import importlib.util
import json
import sys
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
    )
    raw = wj.dumps(payload)
    obj = json.loads(raw)
    assert "cross-day-thread" in obj
    assert "intra-day-thread" in obj
    assert "threads" not in obj
    assert "singles" not in obj
    for banned in ("days", "conflicts", "hypotheses", "blocks", "overdue", "typed_legend"):
        assert banned not in obj
    assert obj["legend"]["1"] == "mem-2026-07-27-a"
    round_trip = wj.loads(raw)
    assert round_trip.cross_day_thread[0].steps[1].via == "evolves"
    assert round_trip.intra_day_thread[0].text == "- wrap"
    yaml_text = wj.dump_yaml(payload)
    yobj = yaml.safe_load(yaml_text)
    assert set(yobj) == set(obj)


def test_entity_key_collapses_digest_aliases():
    wj = _load_weekly_json()
    keys = {
        wj.normalize_entity_key("Memory Digest"),
        wj.normalize_entity_key("MemoryDigest"),
        wj.normalize_entity_key("memory-digest"),
    }
    assert keys == {"memorydigest"}


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
    )
    assert errors == []
    assert threads[0].steps[3].via == "invalidates"
    assert threads[0].steps[3].to_seq == 1
    assert threads[0].steps[1].via == "evolves"
    _ = schema


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


