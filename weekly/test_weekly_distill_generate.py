"""Worker 1 parallel distill generation (mocked LLM)."""

from __future__ import annotations

import importlib.util
import sys
import threading
import time
import types
from datetime import date
from pathlib import Path


def _load_weekly(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    plugin_dir = Path(__file__).resolve().parent
    if str(plugin_dir) not in sys.path:
        sys.path.insert(0, str(plugin_dir))
    module_path = plugin_dir / "weekly.py"
    spec = importlib.util.spec_from_file_location(
        "memory_weekly_distill_generate_test", module_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _event_tool_args(
    *,
    evt_id: str,
    day: str,
    mem_id: str,
    entity: str = "Example",
) -> dict:
    return {
        "id": evt_id,
        "entity": entity,
        "predicate": "example_delivered",
        "participants": [
            {"entity": "User", "role": "requester"},
            {"entity": "Assistant", "role": "executor"},
        ],
        "valid_from": day,
        "valid_to": day,
        "confidence": "high",
        "status": "candidate",
        "sources": ["session s1"],
        "related": [mem_id],
        "beginning": f"Fact recorded on {day}",
        "course": f"Fact recorded progress on {day}",
        "outcome": f"Fact recorded on {day}",
    }


def _event_block(
    *,
    evt_id: str,
    day: str,
    mem_id: str,
    entity: str = "Example",
) -> str:
    return (
        "---\n"
        f"id: {evt_id}\n"
        "type: event\n"
        f"entity: {entity}\n"
        "predicate: example_delivered\n"
        "participants:\n"
        f"  - entity: {entity}\n"
        f"valid_from: {day}\n"
        f"valid_to: {day}\n"
        "confidence: high\n"
        "status: candidate\n"
        "sources: [session s1]\n"
        "related:\n"
        f"  - {mem_id}\n"
        "---\n"
        f"Summary for {day}.\n"
    )


def _hypothesis_tool_args(event_id: str = "evt-a") -> dict:
    return {
        "hypotheses": [
            {
                "id": "hyp-a",
                "entity": "Example",
                "valid_from": "2026-06-30",
                "sources": ["session s1"],
                "related": [event_id],
                "confidence": "medium",
                "status": "candidate",
                "statement": "Still open.",
            }
        ]
    }


def _conflict_tool_args(event_id: str = "evt-a") -> dict:
    return {
        "conflicts": [
            {
                "id": "cfl-a",
                "confidence": "high",
                "status": "candidate",
                "sources": ["session s1"],
                "related": [event_id],
                "tension": "Two readings disagree.",
            }
        ]
    }


def _hypothesis_block(event_id: str = "evt-a") -> str:
    return (
        "---\n"
        "id: hyp-a\n"
        "type: hypothesis\n"
        "entity: Example\n"
        "valid_from: 2026-06-30\n"
        "sources: [session s1]\n"
        f"related: [{event_id}]\n"
        "confidence: medium\n"
        "status: candidate\n"
        "---\n"
        "Still open.\n"
    )


def _conflict_block(event_id: str = "evt-a") -> str:
    return (
        "---\n"
        "id: cfl-a\n"
        "type: conflict\n"
        "confidence: high\n"
        "status: candidate\n"
        "sources: [session s1]\n"
        f"related: [{event_id}]\n"
        "---\n"
        "Two readings disagree.\n"
    )


def _span_block() -> str:
    return (
        "---\n"
        "id: span-a\n"
        "type: span_candidate\n"
        "label: Initiative\n"
        "start_date: 2026-06-29\n"
        "end_date: 2026-07-01\n"
        "confidence: high\n"
        "related_event_ids: [evt-a]\n"
        "---\n"
        "Multi-day initiative.\n"
    )


def _brief_ok() -> str:
    return (
        "### Events\nKey day [1].\n\n"
        "### Hypothesis\n- Confirm X?\n\n"
        "### Conflict\n- A vs B — which?\n\n"
        "### Procedure\n- Prefer reuse.\n"
    )


def _write_daily(
    tmp_path: Path,
    day: str = "2026-06-30",
    *,
    mem_id: str | None = None,
    block_type: str = "fact",
) -> Path:
    mem_id = mem_id or f"mem-{day}-a"
    daily = tmp_path / "memories" / "staging" / "daily" / f"{day}.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text(
        f"---\nid: {mem_id}\ntype: {block_type}\nentity: X\nconfidence: high\n"
        "status: candidate\nsources: [session s1]\n---\n"
        f"{block_type.capitalize()} recorded on {day}.\n",
        encoding="utf-8",
    )
    return daily


def _purpose_keyed_llm(weekly, monkeypatch, handler):
    """Patch weekly LLM helpers with purpose-aware handlers; return call log.

    ``handler(prompt, purpose)`` may return:
    - ``dict`` tool capture ``{tool_name, tool_args}`` for Worker 1 tools
    - ``str`` for legacy text helpers
    """
    calls: list[dict[str, object]] = []
    lock = threading.Lock()

    def fake(prompt: str, *, purpose: str = "weekly_llm") -> str:
        with lock:
            calls.append(
                {
                    "purpose": purpose,
                    "prompt": prompt,
                    "t": time.monotonic(),
                    "kind": "text",
                }
            )
        out = handler(prompt, purpose)
        return out if isinstance(out, str) else ""

    def fake_tools(
        prompt: str,
        *,
        purpose: str = "weekly_llm",
        force_tool_name: str,
    ) -> dict:
        with lock:
            calls.append(
                {
                    "purpose": purpose,
                    "prompt": prompt,
                    "t": time.monotonic(),
                    "kind": "tools",
                    "force_tool_name": force_tool_name,
                }
            )
        try:
            out = handler(prompt, purpose, force_tool_name=force_tool_name)
        except TypeError:
            out = handler(prompt, purpose)
        if isinstance(out, dict) and "tool_name" in out:
            return {
                "final_response": "",
                "tool_name": out["tool_name"],
                "tool_args": out.get("tool_args") or {},
                "tool_calls": [(out["tool_name"], out.get("tool_args") or {})],
                "messages": [],
                "failed": False,
            }
        # Allow handler to return tool args keyed by forced name.
        if isinstance(out, dict) and "tool_args" in out:
            return {
                "final_response": "",
                "tool_name": force_tool_name,
                "tool_args": out["tool_args"],
                "tool_calls": [(force_tool_name, out["tool_args"])],
                "messages": [],
                "failed": False,
            }
        return {
            "final_response": str(out or ""),
            "tool_name": None,
            "tool_args": None,
            "tool_calls": [],
            "messages": [],
            "failed": True,
        }

    monkeypatch.setattr(weekly, "_call_weekly_llm", fake)
    monkeypatch.setattr(weekly, "_call_weekly_llm_tools", fake_tools)
    return calls




def _ok_event_tools(days, *, force_tool_name: str = "") -> dict:
    events = [
        _event_tool_args(evt_id=f"evt-{d}", day=d, mem_id=f"mem-{d}-a")
        for d in days
    ]
    return {
        "tool_name": force_tool_name or "submit_weekly_event",
        "tool_args": {"events": events},
    }


def _empty_analyst(purpose: str, force_tool_name: str = "") -> dict:
    return {
        "tool_name": force_tool_name or "submit_weekly_thread",
        "tool_args": {"cross-day-thread": []},
    }

def test_call_weekly_llm_delegates_to_run_worker_llm(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def fake_run(prompt: str, *, plugin: str, purpose: str, **kwargs) -> str:
        captured["prompt"] = prompt
        captured["plugin"] = plugin
        captured["purpose"] = purpose
        return "ok"

    monkeypatch.setattr(weekly, "run_worker_llm", fake_run)
    assert weekly._call_weekly_llm("x") == "ok"
    assert captured["prompt"] == "x"
    assert captured["plugin"] == "memory-weekly"
    assert captured["purpose"] == "weekly_llm"
    assert weekly._call_weekly_llm("y", purpose="worker1_event") == "ok"
    assert captured["purpose"] == "worker1_event"


def test_build_prompt_worker1_distill_only_contract(tmp_path, monkeypatch):
    """Legacy Distill prompt helper remains available for docs/retry tooling."""
    weekly = _load_weekly(tmp_path, monkeypatch)
    prompt = weekly._build_prompt("2026-W27", "daily bundle")
    assert "## Distill" in prompt
    assert "event" in prompt.casefold()
    assert "hypothesis" in prompt.casefold()
    assert "procedure" in prompt.casefold()
    assert "conflict" in prompt.casefold()
    assert "type: event" in prompt
    assert "Do NOT write ## Brief" in prompt or "do not write ## brief" in prompt.casefold()


def test_build_prompt_retry_includes_fix_hints_and_event_ids(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    previous = (
        "## Distill\n\n"
        + _event_block(evt_id="evt-a", day="2026-06-29", mem_id="mem-2026-06-29-a")
        + "\n"
        + _hypothesis_block("evt-a")
    )
    prompt = weekly._build_prompt(
        "2026-W27",
        "daily",
        attempt=2,
        errors=("line 20: hypothesis related must include a week event id",),
        previous_output=previous,
    )
    assert "VALIDATION FAILED" in prompt
    assert "Fix hints:" in prompt
    assert "Available event ids: evt-a" in prompt


def test_single_event_worker_purpose_called(tmp_path, monkeypatch):
    """Exactly one event purpose (worker1_event) is scheduled."""
    weekly = _load_weekly(tmp_path, monkeypatch)
    days = ("2026-06-29", "2026-06-30", "2026-07-01")
    files = [_write_daily(tmp_path, d) for d in days]

    def handler(prompt: str, purpose: str, force_tool_name: str = "") -> object:
        if purpose == "worker1_event":
            events = []
            for day in days:
                if day in prompt or force_tool_name.startswith("patch"):
                    events.append(
                        _event_tool_args(
                            evt_id=f"evt-{day}",
                            day=day,
                            mem_id=f"mem-{day}-a",
                        )
                    )
            return {
                "tool_name": force_tool_name or "submit_weekly_event",
                "tool_args": {"events": events},
            }
        if purpose == "worker1_thread":
            return _empty_analyst(purpose, force_tool_name)
        if purpose == "worker2_brief":
            return _brief_ok()
        return ""

    calls = _purpose_keyed_llm(weekly, monkeypatch, handler)
    result = weekly._generate_weekly_content("2026-W27", files, reason="test")

    assert result is not None
    event_purposes = sorted(
        {c["purpose"] for c in calls if str(c["purpose"]).startswith("worker1_event")}
    )
    assert event_purposes == ["worker1_event"]
    assert sum(1 for c in calls if c["purpose"] == "worker1_event") >= 1
    # Analysts still run after the single event worker.
    assert {c["purpose"] for c in calls} >= {
        "worker1_event",
        "worker1_thread",
    }
    assert not any(c["purpose"] == "worker1_span" for c in calls)


def test_all_active_dates_represented_mon_sun_order(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    days = ("2026-06-29", "2026-06-30", "2026-07-01")
    files = [_write_daily(tmp_path, d) for d in days]

    def handler(prompt: str, purpose: str, force_tool_name: str = "") -> object:
        if purpose.startswith("worker1_event"):
            events = [
                _event_tool_args(
                    evt_id=f"evt-{day}",
                    day=day,
                    mem_id=f"mem-{day}-a",
                )
                for day in days
                if day in prompt or force_tool_name.startswith("patch") or "Active days" in prompt
            ]
            if not events:
                events = [
                    _event_tool_args(
                        evt_id=f"evt-{day}",
                        day=day,
                        mem_id=f"mem-{day}-a",
                    )
                    for day in days
                ]
            return {
                "tool_name": force_tool_name or "submit_weekly_event",
                "tool_args": {"events": events},
            }
        if purpose == "worker1_thread":
            return {
                "tool_name": force_tool_name or "submit_weekly_thread",
                "tool_args": {"cross-day-thread": []},
            }
        if purpose == "worker2_brief":
            return _brief_ok()
        return ""

    _purpose_keyed_llm(weekly, monkeypatch, handler)
    from weekly_event_workers import run_parallel_worker1

    def tools(prompt, *, purpose="weekly_llm", force_tool_name=""):
        out = handler(prompt, purpose, force_tool_name=force_tool_name)
        assert isinstance(out, dict)
        return {
            "final_response": "",
            "tool_name": out["tool_name"],
            "tool_args": out["tool_args"],
            "tool_calls": [(out["tool_name"], out["tool_args"])],
            "messages": [],
            "failed": False,
        }

    w1 = run_parallel_worker1(
        "2026-W27",
        files,
        call_llm_tools=tools,
        log=lambda _m: None,
    )
    active = {date.fromisoformat(d) for d in days}
    covered = {
        date.fromisoformat(str((b.get("frontmatter") or {}).get("valid_from") or ""))
        for b in w1.blocks
        if str((b.get("frontmatter") or {}).get("type") or "").casefold() == "event"
    }
    assert active <= covered
    # Payload days are Monday..Sunday ordered
    assert [d.day.isoformat() for d in w1.payload.days] == [
        "2026-06-29",
        "2026-06-30",
        "2026-07-01",
        "2026-07-02",
        "2026-07-03",
        "2026-07-04",
        "2026-07-05",
    ]
    result = weekly._generate_weekly_content("2026-W27", files, reason="test")
    assert result is not None
    assert "cross-day-thread" in result
    assert "intra-day-thread" in result
    for day in days:
        assert day in result


def test_analysts_receive_merged_event_context(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    files = [
        _write_daily(tmp_path, "2026-06-29"),
        _write_daily(tmp_path, "2026-06-30"),
    ]
    analyst_prompts: dict[str, str] = {}

    def handler(prompt: str, purpose: str, force_tool_name: str = "") -> object:
        if purpose.startswith("worker1_event"):
            events = []
            for day, evt in (
                ("2026-06-29", "evt-mon"),
                ("2026-06-30", "evt-tue"),
            ):
                if day in prompt or "Active days" in prompt:
                    events.append(
                        _event_tool_args(
                            evt_id=evt, day=day, mem_id=f"mem-{day}-a"
                        )
                    )
            if not events:
                events = [
                    _event_tool_args(evt_id="evt-mon", day="2026-06-29", mem_id="mem-2026-06-29-a"),
                    _event_tool_args(evt_id="evt-tue", day="2026-06-30", mem_id="mem-2026-06-30-a"),
                ]
            return {
                "tool_name": force_tool_name or "submit_weekly_event",
                "tool_args": {"events": events},
            }
        if purpose == "worker1_thread":
            analyst_prompts[purpose] = prompt
            return {
                "tool_name": force_tool_name or "submit_weekly_thread",
                "tool_args": {"cross-day-thread": []},
            }
        if purpose == "worker2_brief":
            return _brief_ok()
        return ""

    _purpose_keyed_llm(weekly, monkeypatch, handler)
    result = weekly._generate_weekly_content("2026-W27", files, reason="test")
    assert result is not None
    assert set(analyst_prompts) == {"worker1_thread"}
    for purpose, prompt in analyst_prompts.items():
        assert "MERGED EVENTS" in prompt, purpose
        assert "CITATION LEGEND" in prompt, purpose
        # Merged context should mention at least one event id / date
        assert "evt-" in prompt or "2026-06" in prompt, purpose


def test_event_worker_failure_bounded_fallback_keeps_day(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    files = [
        _write_daily(tmp_path, "2026-06-29"),
        _write_daily(tmp_path, "2026-06-30"),
        _write_daily(tmp_path, "2026-07-01"),
    ]
    logs: list[str] = []
    monkeypatch.setattr(weekly, "_log", lambda msg: logs.append(msg))

    def handler(prompt: str, purpose: str, force_tool_name: str = "") -> object:
        if purpose == "worker1_event":
            raise RuntimeError("simulated event worker failure")
        if purpose == "worker1_thread":
            return _empty_analyst(purpose, force_tool_name)
        if purpose == "worker2_brief":
            return _brief_ok()
        return ""

    _purpose_keyed_llm(weekly, monkeypatch, handler)
    result = weekly._generate_weekly_content("2026-W27", files, reason="test")
    assert result is not None
    # All three active dates still appear (bounded fallback covers the week)
    assert "2026-06-29" in result
    assert "2026-06-30" in result
    assert "2026-07-01" in result
    assert any("fallback" in line.casefold() for line in logs)


def test_event_workers_never_emit_non_event_types(tmp_path, monkeypatch):
    """Forbidden types from an event worker are rejected; fallback keeps the day."""
    weekly = _load_weekly(tmp_path, monkeypatch)
    day = "2026-06-30"
    files = [_write_daily(tmp_path, day)]
    event_prompts: list[str] = []

    def handler(prompt: str, purpose: str, force_tool_name: str = "") -> object:
        if purpose.startswith("worker1_event"):
            event_prompts.append(prompt)
            # Deliberately omit required event slots — must fail validation.
            return {
                "tool_name": force_tool_name or "submit_weekly_event",
                "tool_args": {
                    "events": [
                        {
                            "entity": "X",
                            "predicate": "bad",
                            "participants": [],
                            "valid_from": day,
                            "valid_to": day,
                            "confidence": "high",
                            "sources": ["session s1"],
                            "related": [],
                            "beginning": "x",
                            "course": "y",
                            "outcome": "z",
                        }
                    ]
                },
            }
        if purpose == "worker1_thread":
            return _empty_analyst(purpose, force_tool_name)
        if purpose == "worker2_brief":
            return _brief_ok()
        return ""

    _purpose_keyed_llm(weekly, monkeypatch, handler)
    result = weekly._generate_weekly_content("2026-W27", files, reason="test")
    assert result is not None
    assert day in result
    # Prompts instruct event-only contract
    assert event_prompts
    for prompt in event_prompts:
        if "worker1_event" in prompt or "event extractor" in prompt.casefold():
            lowered = prompt.casefold()
            assert "submit_weekly_event" in lowered or "type:event" in lowered.replace(" ", "")
    assert "fact-bad" not in result


def test_generate_weekly_content_fills_brief_after_w1(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    files = [_write_daily(tmp_path, "2026-06-30")]

    def handler(prompt: str, purpose: str, force_tool_name: str = "") -> object:
        if purpose.startswith("worker1_event"):
            return {
                "tool_name": force_tool_name or "submit_weekly_event",
                "tool_args": {
                    "events": [
                        _event_tool_args(
                            evt_id="evt-a",
                            day="2026-06-30",
                            mem_id="mem-2026-06-30-a",
                        )
                    ]
                },
            }
        if purpose == "worker1_thread":
            return {
                "tool_name": force_tool_name or "submit_weekly_thread",
                "tool_args": {"cross-day-thread": []},
            }
        return ""

    calls = _purpose_keyed_llm(weekly, monkeypatch, handler)
    result = weekly._generate_weekly_content("2026-W27", files, reason="test")
    assert result is not None
    assert "cross-day-thread" in result
    assert "intra-day-thread" in result
    assert "legend:" in result
    assert "Conflict" not in result
    assert "Hypothesis" not in result
    assert not any(c["purpose"] == "worker2_brief" for c in calls)
    assert not any(c["purpose"] == "worker1_span" for c in calls)
    assert any(
        str(c["purpose"]).startswith("worker1_event") for c in calls
    )


def test_generate_weekly_content_soft_fails_brief_keeps_distill(tmp_path, monkeypatch):
    """Analyst soft-empty still yields YAML of JSON (no Distill fences)."""
    weekly = _load_weekly(tmp_path, monkeypatch)
    files = [_write_daily(tmp_path, "2026-06-30")]
    logs: list[str] = []
    monkeypatch.setattr(weekly, "_log", lambda msg: logs.append(msg))

    def handler(prompt: str, purpose: str, force_tool_name: str = "") -> object:
        if purpose.startswith("worker1_event"):
            return {
                "tool_name": force_tool_name or "submit_weekly_event",
                "tool_args": {
                    "events": [
                        _event_tool_args(
                            evt_id="evt-a",
                            day="2026-06-30",
                            mem_id="mem-2026-06-30-a",
                        )
                    ]
                },
            }
        if purpose == "worker1_thread":
            return _empty_analyst(purpose, force_tool_name)
        return ""

    _purpose_keyed_llm(weekly, monkeypatch, handler)
    result = weekly._generate_weekly_content("2026-W27", files, reason="test")
    assert result is not None
    assert "cross-day-thread" in result
    assert "## Distill" not in result
    assert "## Brief" not in result


def test_generate_weekly_content_renders_four_part_without_worker2_llm(
    tmp_path, monkeypatch
):
    """YAML dump of JSON; Worker 2 LLM is not consulted."""
    weekly = _load_weekly(tmp_path, monkeypatch)
    files = [_write_daily(tmp_path, "2026-06-30")]
    logs: list[str] = []
    monkeypatch.setattr(weekly, "_log", lambda msg: logs.append(msg))

    def handler(prompt: str, purpose: str, force_tool_name: str = "") -> object:
        if purpose.startswith("worker1_event"):
            return {
                "tool_name": force_tool_name or "submit_weekly_event",
                "tool_args": {
                    "events": [
                        _event_tool_args(
                            evt_id="evt-a",
                            day="2026-06-30",
                            mem_id="mem-2026-06-30-a",
                        )
                    ]
                },
            }
        if purpose == "worker1_thread":
            return _empty_analyst(purpose, force_tool_name)
        if purpose == "worker2_brief":
            raise AssertionError("Worker 2 LLM must not run for four-part brief")
        return ""

    calls = _purpose_keyed_llm(weekly, monkeypatch, handler)
    result = weekly._generate_weekly_content("2026-W27", files, reason="test")
    assert result is not None
    assert "cross-day-thread" in result
    assert "intra-day-thread" in result
    assert "## Distill" not in result
    assert not any(c["purpose"] == "worker2_brief" for c in calls)
    assert not any(c["purpose"] == "worker1_span" for c in calls)

