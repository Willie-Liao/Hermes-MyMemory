from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_mymemory = Path(__file__).resolve().parent.parent
if str(_mymemory) not in sys.path:
    sys.path.insert(0, str(_mymemory))
_plugins_root = Path(__file__).resolve().parent.parent.parent
_plugin_dir = Path(__file__).resolve().parent
if str(_plugins_root) not in sys.path:
    sys.path.insert(0, str(_plugins_root))
if str(_plugin_dir) not in sys.path:
    sys.path.insert(0, str(_plugin_dir))


from conftest import (
    is_dedup_prompt,
    load_plugin_module,
    phase1_tool_capture,
    stub_dedup_proposal,
)

def _load_digest_run():
    return load_plugin_module("digest_run.py", "memory_digest_run_test")


def _messages(pairs: int) -> list[dict]:
    rows: list[dict] = []
    next_id = 1
    for idx in range(1, pairs + 1):
        rows.append({"id": next_id, "role": "user", "content": f"user {idx}"})
        next_id += 1
        rows.append({"id": next_id, "role": "assistant", "content": f"assistant {idx}"})
        next_id += 1
    return rows


def _state_path(home: Path) -> Path:
    return home / "memories" / "staging" / ".digest-state.json"


def _write_state(home: Path, *, session_id: str = "s1", last_id: int | None = None) -> None:
    entry = {"session_id": session_id, "platform": "wecom"}
    if last_id is not None:
        entry["last_digest_message_id"] = last_id
    path = _state_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sessions": {session_id: entry}}), encoding="utf-8")


def _read_state(home: Path) -> dict:
    return json.loads(_state_path(home).read_text(encoding="utf-8"))


_VALID_BLOCK = "\n".join(
    [
        "---",
        "id: mem-1",
        "type: fact",
        "entity: Gabby",
        "confidence: high",
        "status: candidate",
        "sources: [session s1]",
        "---",
        "Gabby chose home-packed lunch.",
    ]
)

_VALID_EVENT = "\n".join(
    [
        "---",
        "id: mem-20260802-event",
        "type: event",
        "entity: Project",
        "predicate: user_requested_review",
        "participants:",
        "  - {entity: User, role: requester}",
        "  - {entity: Assistant, role: executor}",
        "valid_from: 2026-08-02",
        "valid_to: open",
        "confidence: explicit",
        "status: candidate",
        "sources: [session s1]",
        "---",
        "Beginning: user requested review; Course: assistant reviewed sources; Outcome: draft delivered.",
    ]
)

_VALID_PROCEDURE = "\n".join(
    [
        "---",
        "id: mem-20260802-procedure",
        "type: procedure",
        "confidence: explicit",
        "status: candidate",
        "sources: [session s1]",
        "---",
        "Obstacle: source notes were scattered; Solution: use an abstract source-triage checklist.",
    ]
)

_VALID_DECISION = "\n".join(
    [
        "---",
        "id: mem-20260802-decision",
        "type: decision",
        "confidence: explicit",
        "status: candidate",
        "sources: [session s1]",
        "---",
        "Decision: user prefers concise review summaries.",
    ]
)


def test_request_digest_force_appends_below_threshold(tmp_path, monkeypatch):
    dr = _load_digest_run()
    digest = dr.digest
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    _write_state(tmp_path, session_id="s1")

    msgs = _messages(2)  # well below the 12-user batch floor
    monkeypatch.setattr(
        digest, "_fetch_messages", lambda sid, after_id=0: [m for m in msgs if m["id"] > after_id]
    )

    def fake_llm(prompt, platform, **kwargs):
        if is_dedup_prompt(prompt):
            return stub_dedup_proposal(prompt)
        return ""

    monkeypatch.setattr(digest, "_invoke_digest_llm", fake_llm)
    monkeypatch.setattr(
        digest, "_invoke_digest_worker_tool", phase1_tool_capture()
    )

    result = dr.request_digest("s1")

    assert result["outcome"] == "appended"
    state = _read_state(tmp_path)
    assert state["sessions"]["s1"]["last_digest_message_id"] == msgs[-1]["id"]
    assert state["sessions"]["s1"]["digest_in_flight"] is False


def test_request_digest_force_uses_event_first_pipeline(tmp_path, monkeypatch):
    dr = _load_digest_run()
    digest = dr.digest
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    _write_state(tmp_path, session_id="s1")
    msgs = _messages(2)
    monkeypatch.setattr(digest, "_fetch_messages", lambda sid, after_id=0: msgs)
    calls: list[dict] = []

    def fake_event_first(*args, **kwargs):
        calls.append(kwargs)
        digest._finalize_digest_success(
            args[0], args[5], session_id=args[1] if len(args) > 1 else None
        )
        return "appended"

    monkeypatch.setattr(digest, "_run_digest_worker", fake_event_first)

    result = dr.request_digest("s1")

    assert result["outcome"] == "appended"
    assert len(calls) == 1


def test_request_digest_empty_when_no_messages(tmp_path, monkeypatch):
    dr = _load_digest_run()
    digest = dr.digest
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    _write_state(tmp_path, session_id="s1")
    monkeypatch.setattr(digest, "_fetch_messages", lambda sid, after_id=0: [])

    result = dr.request_digest("s1")
    assert result["outcome"] == "empty"


def test_get_digest_status(tmp_path, monkeypatch):
    dr = _load_digest_run()
    digest = dr.digest
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    _write_state(tmp_path, session_id="s1", last_id=2)
    msgs = _messages(3)  # ids 1..6
    monkeypatch.setattr(
        digest, "_fetch_messages", lambda sid, after_id=0: [m for m in msgs if m["id"] > after_id]
    )

    info = dr.get_digest_status("s1", "s1")
    assert info["bookmark"] == 2
    # ids > 2 -> 3,4,5,6 -> 2 user / 2 assistant
    assert info["undigested_user"] == 2
    assert info["undigested_assistant"] == 2
    assert info["in_flight"] is False
    assert info["has_state"] is True


def test_bookmark_set_rewind_reset(tmp_path, monkeypatch):
    dr = _load_digest_run()
    digest = dr.digest
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    _write_state(tmp_path, session_id="s1", last_id=100)

    assert dr.get_bookmark("s1") == 100
    assert dr.set_bookmark("s1", 50)["bookmark"] == 50
    assert dr.rewind_bookmark("s1", 20)["bookmark"] == 30
    assert dr.rewind_bookmark("s1", 999)["bookmark"] == 0  # floored
    assert dr.reset_bookmark("s1")["bookmark"] == 0


def test_bookmark_no_state(tmp_path, monkeypatch):
    dr = _load_digest_run()
    digest = dr.digest
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    (tmp_path / "memories" / "staging").mkdir(parents=True, exist_ok=True)

    assert dr.set_bookmark("missing", 5)["outcome"] == "no_state"


def test_request_weekly_reorganise_missing_daily(tmp_path, monkeypatch):
    dr = _load_digest_run()
    digest = dr.digest
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    (tmp_path / "memories" / "staging" / "daily").mkdir(parents=True, exist_ok=True)

    result = dr.request_weekly_reorganise(date_str="2026-07-27")
    assert result["outcome"] == "missing"
    assert result["date"] == "2026-07-27"
    assert result["path"].endswith("2026-07-27.md")


_DAILY_TWO_FACTS = (
    "---\n"
    "id: mem-a\n"
    "type: fact\n"
    "confidence: high\n"
    "status: candidate\n"
    "sources: [s]\n"
    "---\n"
    "Factual: Alice lives in HK\n"
    "\n"
    "---\n"
    "id: mem-b\n"
    "type: fact\n"
    "confidence: high\n"
    "status: candidate\n"
    "sources: [s]\n"
    "---\n"
    "Factual: Alice lives in Hong Kong\n"
)


def test_request_weekly_reorganise_runs_oneshot_phase2_not_digest(tmp_path, monkeypatch):
    dr = _load_digest_run()
    digest = dr.digest
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    (daily / "2026-07-27.md").write_text(_DAILY_TWO_FACTS, encoding="utf-8")

    def boom(*_a, **_k):
        raise AssertionError("Reorganise must not run request_digest / Hermes Phase-1")

    monkeypatch.setattr(dr, "request_digest", boom)
    captured = {}

    def fake_oneshot(prompt, platform, *, purpose, force_tool_name="", **_k):
        captured["purpose"] = purpose
        captured["force_tool_name"] = force_tool_name
        captured["prompt"] = prompt
        return {
            "tool_name": "submit_operations",
            "tool_args": {"operations": []},
            "failed": False,
        }

    monkeypatch.setattr(digest, "_invoke_digest_oneshot_tool", fake_oneshot)

    result = dr.request_weekly_reorganise(date_str="2026-07-27", session_key="s1")
    assert result["outcome"] == "rewritten"
    assert result["date"] == "2026-07-27"
    assert captured["force_tool_name"] == "submit_operations"
    assert "submit_operations" in captured["prompt"]
    assert "run_worker_llm_tools" not in captured.get("prompt", "")


def test_request_weekly_reorganise_skips_llm_when_no_blocks(tmp_path, monkeypatch):
    dr = _load_digest_run()
    digest = dr.digest
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    (daily / "2026-07-27.md").write_text("existing\n", encoding="utf-8")
    called = []
    monkeypatch.setattr(
        digest,
        "_invoke_digest_oneshot_tool",
        lambda *_a, **_k: called.append(1) or {"failed": True},
    )
    monkeypatch.setattr(
        dr,
        "request_digest",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no digest")),
    )

    result = dr.request_weekly_reorganise(date_str="2026-07-27")
    assert result["outcome"] == "rewritten"
    assert called == []


def test_request_weekly_reorganise_propagates_oneshot_failure(tmp_path, monkeypatch):
    dr = _load_digest_run()
    digest = dr.digest
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    (daily / "2026-07-27.md").write_text(_DAILY_TWO_FACTS, encoding="utf-8")
    monkeypatch.setattr(
        digest,
        "_invoke_digest_oneshot_tool",
        lambda *_a, **_k: {"failed": True, "final_response": "provider down"},
    )

    result = dr.request_weekly_reorganise(date_str="2026-07-27")
    assert result["outcome"] == "failed"


def test_digest_oneshot_raises_completion_budget_for_four_type_ops(monkeypatch):
    """Phase-2 dumps event/fact/procedure/decision ops in one tool call."""
    digest = load_plugin_module("digest.py", "memory_digest_oneshot_budget_test")
    seen: dict = {}

    def fake_oneshot(*_a, **kwargs):
        seen.update(kwargs)
        return {
            "tool_name": "submit_operations",
            "tool_args": {"operations": []},
            "failed": False,
        }

    monkeypatch.setattr(digest, "run_worker_llm_oneshot", fake_oneshot)
    digest._invoke_digest_oneshot_tool(
        "prompt",
        "cli",
        purpose="digest-dedup-submit",
        force_tool_name="submit_operations",
    )
    assert seen["max_tokens"] == digest.ONESHOT_DIGEST_MAX_TOKENS
    assert seen["max_tokens"] >= 8192
