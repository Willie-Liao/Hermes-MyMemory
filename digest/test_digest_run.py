from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
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
        "entity: Casey",
        "confidence: high",
        "status: candidate",
        "sources: [session s1]",
        "---",
        "Casey chose home-packed lunch.",
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


def test_request_weekly_reorganise_wait_false_returns_in_flight(tmp_path, monkeypatch):
    dr = _load_digest_run()
    digest = dr.digest
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    (daily / "2026-07-27.md").write_text(_DAILY_TWO_FACTS, encoding="utf-8")
    started = threading.Event()

    def fake_phase2(*_a, **_k):
        started.wait(timeout=2)
        return {"outcome": "rewritten", "path": str(daily / "2026-07-27.md"), "date": "2026-07-27"}

    monkeypatch.setattr(digest, "run_manual_phase2", fake_phase2)
    result = dr.request_weekly_reorganise(date_str="2026-07-27", wait=False)
    assert result["outcome"] == "in_flight"
    status = dr.request_weekly_reorganise(date_str="2026-07-27", status_only=True)
    assert status["outcome"] == "in_flight"
    started.set()
    for _ in range(50):
        done = dr.request_weekly_reorganise(date_str="2026-07-27", status_only=True)
        if done["outcome"] != "in_flight":
            assert done["outcome"] == "rewritten"
            return
        time.sleep(0.05)
    raise AssertionError("background reorganise did not finish")


def test_request_weekly_reorganise_wait_false_kicks_once(tmp_path, monkeypatch):
    dr = _load_digest_run()
    digest = dr.digest
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    (daily / "2026-07-27.md").write_text(_DAILY_TWO_FACTS, encoding="utf-8")
    calls: list[int] = []
    hold = threading.Event()
    entered = threading.Event()

    def fake_phase2(*_a, **_k):
        calls.append(1)
        entered.set()
        hold.wait(timeout=2)
        return {"outcome": "rewritten", "path": str(daily / "2026-07-27.md"), "date": "2026-07-27"}

    monkeypatch.setattr(digest, "run_manual_phase2", fake_phase2)
    first = dr.request_weekly_reorganise(date_str="2026-07-27", wait=False)
    assert first["outcome"] == "in_flight"
    assert entered.wait(timeout=2)
    try:
        second = dr.request_weekly_reorganise(date_str="2026-07-27", wait=False)
        assert second["outcome"] == "in_flight"
        assert len(calls) == 1
    finally:
        hold.set()


def test_request_weekly_reorganise_rekicks_stale_in_flight(tmp_path, monkeypatch):
    """Flag without a live weekly-reorganise thread would freeze the Reorganise spinner."""
    dr = _load_digest_run()
    digest = dr.digest
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    (daily / "2026-07-27.md").write_text(_DAILY_TWO_FACTS, encoding="utf-8")
    entered = threading.Event()
    hold = threading.Event()

    def fake_phase2(*_a, **_k):
        entered.set()
        hold.wait(timeout=2)
        return {"outcome": "rewritten", "path": str(daily / "2026-07-27.md"), "date": "2026-07-27"}

    monkeypatch.setattr(digest, "run_manual_phase2", fake_phase2)
    with digest._digest_lock:
        state = digest._load_state()
        state["weekly_reorganise_job"] = {
            "in_flight": True,
            "date": "2026-07-27",
            "last_outcome": "",
        }
        digest._save_state(state)
    result = dr.request_weekly_reorganise(date_str="2026-07-27", wait=False)
    assert result["outcome"] == "in_flight"
    assert entered.wait(timeout=2)
    hold.set()


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


def _write_messages_db(home: Path, rows: list[tuple]) -> None:
    import sqlite3

    db = home / "state.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE messages ("
        "id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, "
        "content TEXT, timestamp REAL, active INTEGER)"
    )
    conn.executemany(
        "INSERT INTO messages (id, session_id, role, content, timestamp, active) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def _ts(now, **delta):
    from datetime import timedelta

    return (now - timedelta(**delta)).timestamp()


def test_history_plan_presets_bookmarks_and_batches(tmp_path, monkeypatch):
    dr = _load_digest_run()
    digest = dr.digest
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    now = __import__("datetime").datetime(2026, 8, 21, 12, 0, tzinfo=__import__("datetime").timezone.utc)
    rows = []
    mid = 1

    def add(session, hours=None, days=None, role="user", active=1, content="hello"):
        nonlocal mid
        kw = {}
        if hours is not None:
            kw["hours"] = hours
        if days is not None:
            kw["days"] = days
        rows.append((mid, session, role, content, _ts(now, **kw), active))
        mid += 1

    add("s-day", hours=2)
    add("s-day", hours=2, role="assistant")
    add("s-week", days=3)
    add("s-week", days=3, role="assistant")
    add("s-month", days=20)
    add("s-month", days=20, role="assistant")
    add("s-old", days=40)
    add("s-old", days=40, role="assistant")
    add("s-tool", hours=1, role="tool")
    add("s-inactive", hours=1, active=0)
    for i in range(13):
        add("s-batch", hours=1, content=f"u{i}")
        add("s-batch", hours=1, role="assistant", content=f"a{i}")
    _write_messages_db(tmp_path, rows)
    _write_state(tmp_path, session_id="s-week", last_id=4)

    before = (tmp_path / "state.db").stat().st_mtime
    state_path = _state_path(tmp_path)
    state_mtime = state_path.stat().st_mtime

    p1 = dr.plan_history("1d", now=now, home=tmp_path)
    p7 = dr.plan_history("7d", now=now, home=tmp_path)
    p30 = dr.plan_history("30d", now=now, home=tmp_path)
    pall = dr.plan_history("all", now=now, home=tmp_path)
    assert p1["outcome"] == "ok"
    assert p1["message_count"] <= p7["message_count"] <= p30["message_count"] <= pall["message_count"]
    assert pall["session_count"] >= p1["session_count"]
    assert "s-old" in pall["sessions"]
    assert "s-old" not in p30["sessions"]
    assert "s-week" not in p7["sessions"]  # bookmarked through id 3 (only those two msgs)
    assert p1["digest_tokens"]["typical"] > 0 or p1["batch_count"] == 0
    batch_session = [b for b in p1["batches"] if b["session_id"] == "s-batch"]
    assert len(batch_session) >= 2
    assert all(b["user"] <= 12 and len(b["message_ids"]) <= 40 for b in p1["batches"])
    assert (tmp_path / "state.db").stat().st_mtime == before
    assert state_path.stat().st_mtime == state_mtime


def test_history_estimate_invalid_and_missing_db(tmp_path, monkeypatch):
    dr = _load_digest_run()
    monkeypatch.setattr(dr.digest, "get_hermes_home", lambda: tmp_path)
    now = __import__("datetime").datetime(2026, 8, 21, 12, 0, tzinfo=__import__("datetime").timezone.utc)
    empty = dr.plan_history("all", now=now, home=tmp_path)
    assert empty["outcome"] == "ok"
    assert empty["message_count"] == 0
    bad = dr.plan_history("2y", now=now, home=tmp_path)
    assert bad["outcome"] == "invalid_preset"


def test_history_calibration_schema_has_no_private_paths():
    import json
    from pathlib import Path

    path = Path(__file__).with_name("history_calibration.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    blob = path.read_text(encoding="utf-8")
    assert data["profile_version"] == 1
    assert data["regime"]
    assert data["phase1"]["n_eligible"] >= 1
    assert data["phase2"]["n_excluded"] >= 1
    assert "exclusions" in data
    assert "/Users/" not in blob
    assert "wil" + "lie" not in blob.lower()
    assert "session:" not in blob


def test_history_run_resume_stop_checkpoint(tmp_path, monkeypatch):
    dr = _load_digest_run()
    digest = dr.digest
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    now = __import__("datetime").datetime(2026, 8, 21, 12, 0, tzinfo=__import__("datetime").timezone.utc)
    rows = []
    mid = 1
    for hours, sid in ((2, "s1"), (3, "s2")):
        rows.append((mid, sid, "user", "u", _ts(now, hours=hours), 1))
        mid += 1
        rows.append((mid, sid, "assistant", "a", _ts(now, hours=hours), 1))
        mid += 1
    _write_messages_db(tmp_path, rows)
    calls: list[str] = []
    phase2: list[str] = []

    def fake_pipeline(*args, **kwargs):
        daily = args[3]
        calls.append(str(args[1]))
        if len(calls) == 1:
            dr.stop_history(yes=True)
        return "appended"

    monkeypatch.setattr(digest, "_run_digest_pipeline_entry", fake_pipeline)
    monkeypatch.setattr(
        digest,
        "run_manual_phase2",
        lambda path, date_str="": phase2.append(date_str) or {"outcome": "rewritten"},
    )

    refused = dr.request_history_run("1d", yes=False, sync=True, now=now)
    assert refused["outcome"] == "needs_confirm"
    assert calls == []

    result = dr.request_history_run("1d", yes=True, sync=True, now=now)
    assert result["outcome"] == "stopped"
    assert len(calls) == 1
    assert phase2 == []

    resumed = dr.resume_history(yes=True, sync=True)
    assert resumed["outcome"] == "completed"
    assert len(calls) == 2
    assert len(phase2) >= 1

    again = dr.resume_history(yes=True, sync=True)
    assert again["outcome"] == "completed"
    assert len(calls) == 2


def test_history_run_phase2_once_per_day(tmp_path, monkeypatch):
    dr = _load_digest_run()
    digest = dr.digest
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    now = __import__("datetime").datetime(2026, 8, 21, 12, 0, tzinfo=__import__("datetime").timezone.utc)
    rows = []
    mid = 1
    for i in range(13):
        rows.append((mid, "s1", "user", f"u{i}", _ts(now, hours=1), 1))
        mid += 1
        rows.append((mid, "s1", "assistant", f"a{i}", _ts(now, hours=1), 1))
        mid += 1
    _write_messages_db(tmp_path, rows)
    p1_calls = []
    p2_calls = []
    monkeypatch.setattr(
        digest,
        "_run_digest_pipeline_entry",
        lambda *a, **k: p1_calls.append(a) or "appended",
    )
    monkeypatch.setattr(
        digest,
        "run_manual_phase2",
        lambda path, date_str="": p2_calls.append(date_str) or {"outcome": "rewritten"},
    )
    out = dr.request_history_run("1d", yes=True, sync=True, now=now)
    assert out["outcome"] == "completed"
    assert len(p1_calls) >= 2
    assert p2_calls == [p2_calls[0]]
