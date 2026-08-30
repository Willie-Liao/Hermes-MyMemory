from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_mymemory = Path(__file__).resolve().parent.parent
if str(_mymemory) not in sys.path:
    sys.path.insert(0, str(_mymemory))

_plugins_root = Path(__file__).resolve().parent.parent.parent
if str(_plugins_root) not in sys.path:
    sys.path.insert(0, str(_plugins_root))

from memory_staging import hermes_local_today_str


from conftest import load_plugin_module


def _load_digest():
    return load_plugin_module("digest.py", "memory_digest_test")


def _messages(user_count: int, assistant_count: int | None = None) -> list[dict]:
    rows: list[dict] = []
    next_id = 1
    for idx in range(1, user_count + 1):
        rows.append({"id": next_id, "role": "user", "content": f"user {idx}"})
        next_id += 1
        if idx <= (assistant_count if assistant_count is not None else user_count):
            rows.append({"id": next_id, "role": "assistant", "content": f"assistant {idx}"})
            next_id += 1
    extra_assistants = (assistant_count or 0) - user_count
    for idx in range(1, max(0, extra_assistants) + 1):
        rows.append({"id": next_id, "role": "assistant", "content": f"assistant extra {idx}"})
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


def _configure_digest(digest, monkeypatch, home: Path, messages: list[dict]) -> list[dict]:
    calls: list[dict] = []

    monkeypatch.setattr(digest, "get_hermes_home", lambda: home)
    monkeypatch.setattr(
        digest,
        "_fetch_messages",
        lambda session_id, after_id=0: [m for m in messages if m["id"] > after_id],
    )

    def fake_run(
        session_key,
        session_id,
        platform,
        daily_path,
        transcript,
        batch_end_id,
        *,
        reason="digest",
        user_count=0,
        assistant_count=0,
        batch_start_id=None,
        **_kwargs,
    ):
        calls.append(
            {
                "prompt": transcript,
                "platform": platform,
                "daily_path": daily_path,
                "session_id": session_id,
                "session_key": session_key,
                "batch_end_id": batch_end_id,
                "batch_start_id": batch_start_id,
                "reason": reason,
                "user_count": user_count,
                "assistant_count": assistant_count,
            }
        )
        digest._finalize_digest_success(
            session_key, batch_end_id, session_id=session_id
        )

    monkeypatch.setattr(digest, "_run_digest_worker", fake_run)

    class ImmediateThread:
        def __init__(self, *, target, args, kwargs=None, name, daemon):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            self._target(*self._args, **self._kwargs)

    monkeypatch.setattr(digest.threading, "Thread", ImmediateThread)
    return calls


def test_batch_trigger_skips_before_twelve_user_messages(tmp_path, monkeypatch):
    digest = _load_digest()
    calls = _configure_digest(digest, monkeypatch, tmp_path, _messages(11))

    digest.on_agent_end({"session_id": "s1", "platform": "wecom"})

    assert calls == []
    state = _read_state(tmp_path)
    assert "last_digest_message_id" not in state["sessions"]["s1"]


def test_batch_trigger_ready_at_twelve_user_without_assistant_floor(tmp_path, monkeypatch):
    digest = _load_digest()
    messages = _messages(12, assistant_count=0)
    aged = (datetime.now(timezone.utc) - timedelta(seconds=700)).isoformat()
    for row in messages:
        row["timestamp"] = aged
    calls = _configure_digest(digest, monkeypatch, tmp_path, messages)

    digest.on_agent_end({"session_id": "s1", "platform": "wecom"})

    assert len(calls) == 1
    assert calls[0]["user_count"] == 12
    assert calls[0]["assistant_count"] == 0
    assert "[user] user 12" in calls[0]["prompt"]


def test_batch_trigger_ready_at_twelve_user_messages(tmp_path, monkeypatch):
    digest = _load_digest()
    messages = _messages(13)
    calls = _configure_digest(digest, monkeypatch, tmp_path, messages)

    digest.on_agent_end({"session_id": "s1", "platform": "wecom"})

    assert len(calls) == 1
    assert calls[0]["session_id"] == "s1"
    assert calls[0]["platform"] == "wecom"
    assert calls[0]["user_count"] == 12
    assert calls[0]["assistant_count"] == 12
    assert calls[0]["batch_end_id"] == 24
    assert calls[0]["batch_start_id"] == 1
    # Production path passes raw transcript — not a nested mega-prompt.
    assert "[user] user 12" in calls[0]["prompt"]
    assert "[assistant] assistant 12" in calls[0]["prompt"]
    assert "[user] user 13" not in calls[0]["prompt"]
    assert "You are the memory digest worker" not in calls[0]["prompt"]
    assert "OUTPUT CONTRACT" not in calls[0]["prompt"]
    state = _read_state(tmp_path)
    assert state["sessions"]["s1"]["last_digest_message_id"] == 24
    assert state["sessions"]["s1"]["digest_in_flight"] is False


def test_normal_digest_caller_passes_raw_transcript(tmp_path, monkeypatch):
    digest = _load_digest()
    calls = _configure_digest(digest, monkeypatch, tmp_path, _messages(12))

    digest.on_agent_end({"session_id": "s1", "platform": "wecom"})

    assert len(calls) == 1
    assert "[user] user 1" in calls[0]["prompt"]
    assert "Assigned worker type: event" not in calls[0]["prompt"]
    assert "DIGEST POLICY" not in calls[0]["prompt"]


def test_in_flight_blocks_second_trigger(tmp_path, monkeypatch):
    digest = _load_digest()
    calls: list[dict] = []

    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(
        digest,
        "_fetch_messages",
        lambda session_id, after_id=0: [
            m for m in _messages(12) if m["id"] > after_id
        ],
    )

    # In-flight worker that does not finalize — simulates a run still in progress.
    def stuck_worker(
        session_key,
        session_id,
        platform,
        daily_path,
        transcript,
        batch_end_id,
        **_kwargs,
    ):
        calls.append({"session_id": session_id})

    monkeypatch.setattr(digest, "_run_digest_worker", stuck_worker)

    class ImmediateThread:
        def __init__(self, *, target, args, kwargs=None, name, daemon):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            self._target(*self._args, **self._kwargs)

    monkeypatch.setattr(digest.threading, "Thread", ImmediateThread)

    digest.on_agent_end({"session_id": "s1", "platform": "wecom"})
    assert len(calls) == 1

    digest._maybe_run_digest("s1", reason=digest.BOOKMARK_TRIGGER_REASON)
    assert len(calls) == 1  # second trigger skipped while in flight


def test_worker_active_guard_skips_on_agent_end(tmp_path, monkeypatch):
    digest = _load_digest()
    calls = _configure_digest(digest, monkeypatch, tmp_path, _messages(7))

    digest._digest_worker_active.active = True
    try:
        digest.on_agent_end({"session_id": "s1", "platform": "wecom"})
    finally:
        digest._digest_worker_active.active = False

    assert calls == []
    assert not _state_path(tmp_path).exists()


def test_session_boundary_runs_with_twelve_user_messages(tmp_path, monkeypatch):
    digest = _load_digest()
    _write_state(tmp_path)
    calls = _configure_digest(digest, monkeypatch, tmp_path, _messages(12))

    digest.on_session_boundary({"session_id": "s1", "platform": "wecom"}, reason="session_expired")

    assert len(calls) == 1
    state = _read_state(tmp_path)
    assert state["sessions"]["s1"]["last_digest_message_id"] == 24


def test_session_boundary_skips_short_batch(tmp_path, monkeypatch):
    digest = _load_digest()
    _write_state(tmp_path)
    calls = _configure_digest(digest, monkeypatch, tmp_path, _messages(3))

    digest.on_session_boundary({"session_id": "s1", "platform": "wecom"}, reason="session_expired")

    assert calls == []
    state = _read_state(tmp_path)
    assert "last_digest_message_id" not in state["sessions"]["s1"]


def test_bookmark_prevents_reprocessing_previous_batch(tmp_path, monkeypatch):
    digest = _load_digest()
    messages = _messages(12)
    _write_state(tmp_path)
    calls = _configure_digest(digest, monkeypatch, tmp_path, messages)

    digest._maybe_run_digest("s1", reason=digest.BOOKMARK_TRIGGER_REASON)

    assert len(calls) == 1
    state = _read_state(tmp_path)
    assert state["sessions"]["s1"]["last_digest_message_id"] == 24

    messages.extend(
        [
            {"id": 31, "role": "user", "content": "user 16"},
            {"id": 32, "role": "assistant", "content": "assistant 16"},
            {"id": 33, "role": "user", "content": "user 17"},
            {"id": 34, "role": "assistant", "content": "assistant 17"},
            {"id": 35, "role": "user", "content": "user 18"},
            {"id": 36, "role": "assistant", "content": "assistant 18"},
            {"id": 37, "role": "user", "content": "user 19"},
            {"id": 38, "role": "assistant", "content": "assistant 19"},
        ]
    )

    digest._maybe_run_digest("s1", reason=digest.BOOKMARK_TRIGGER_REASON)

    assert len(calls) == 1
    state = _read_state(tmp_path)
    assert state["sessions"]["s1"]["last_digest_message_id"] == 24


def test_second_batch_runs_after_another_twelve_user_messages(tmp_path, monkeypatch):
    digest = _load_digest()
    messages = _messages(12)
    _write_state(tmp_path)
    calls = _configure_digest(digest, monkeypatch, tmp_path, messages)

    digest._maybe_run_digest("s1", reason=digest.BOOKMARK_TRIGGER_REASON)

    assert len(calls) == 1
    state = _read_state(tmp_path)
    assert state["sessions"]["s1"]["last_digest_message_id"] == 24

    next_id = 25
    for user_index in range(13, 25):
        messages.append(
            {"id": next_id, "role": "user", "content": f"user {user_index}"}
        )
        next_id += 1
        messages.append(
            {"id": next_id, "role": "assistant", "content": f"assistant {user_index}"}
        )
        next_id += 1

    digest._maybe_run_digest("s1", reason=digest.BOOKMARK_TRIGGER_REASON)

    assert len(calls) == 2
    state = _read_state(tmp_path)
    assert state["sessions"]["s1"]["last_digest_message_id"] == 48


def test_prompt_constrains_status_values(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)

    prompt = digest._build_prompt(
        "s1",
        "wecom",
        [{"id": 1, "role": "user", "content": "remember this"}],
        digest.BOOKMARK_TRIGGER_REASON,
    )

    assert "status must be candidate, approved, or rejected" in prompt
    assert "status: candidate | approved | rejected" in prompt


def test_prompt_omits_session_summary(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)

    prompt = digest._build_prompt(
        "s1",
        "wecom",
        [{"id": 1, "role": "user", "content": "remember this"}],
        digest.BOOKMARK_TRIGGER_REASON,
    )

    assert "## Session summary" not in prompt


def test_prompt_includes_light_digest_contract(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)

    prompt = digest._build_prompt(
        "s1",
        "wecom",
        [{"id": 1, "role": "user", "content": "remember this"}],
        digest.BOOKMARK_TRIGGER_REASON,
    )

    assert ("Return 0 or more YAML frontmatter blocks" in prompt) or ("Prefer forced tool calls" in prompt) or ("If emitting text" in prompt)
    assert str(digest.MAX_BODY_CHARS) in prompt
    assert "Stage when durable" in prompt
    assert "User correction:" in prompt
    assert "entity:" in prompt
    assert "entity_aliases:" in prompt
    assert "predicate:" in prompt
    assert "participants:" in prompt
    assert "involves:" in prompt
    assert "related:" in prompt
    assert "Roster >5" in prompt
    assert "file:" in prompt


def test_event_first_worker_contracts_are_explicit(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)

    policy = digest.DIGEST_POLICY
    contract = digest._staging_output_contract(tmp_path / "2026-07-26.md")
    prompt = digest._build_prompt(
        "s1",
        "wecom",
        [{"id": 1, "role": "user", "content": "please revise the outline"}],
        digest.BOOKMARK_TRIGGER_REASON,
    )

    combined = "\n".join((policy, contract, prompt))
    assert "User messages are the primary evidence" in combined
    assert "Beginning" in combined
    assert "Course" in combined
    assert "Outcome" in combined
    assert "exactly three stages" in combined
    assert "procedure owns course obstacles and abstract solutions" in combined
    assert "raw tool logs" in combined
    assert "decision owns user-only rulings/preferences for agent behavior" in combined
    assert "Narration:" in combined
    assert "role?" in combined or "role optional" in combined or "role only when clear" in combined
    assert "fact owns stable observations" in combined
    assert "type: decision" in combined
    assert "decision_constraint" in combined
    assert "legacy input alias" in combined
    assert "Hypothesis is preserved legacy-only data" not in combined
    assert "hypothesis belongs to the weekly worker" in combined or "Do NOT emit type: hypothesis" in combined
    assert "type: fact | procedure | decision | hypothesis | event" not in combined
    assert "entity_aliases:" in combined
    assert "omit when identical to entity" in combined


def test_staging_contract_has_temporary_worker_envelope():
    digest = _load_digest()
    contract = digest._staging_output_contract(Path("/tmp/2026-07-26.md"))

    assert "temporary worker result" in contract
    assert "not a persistent memory type" in contract


def test_digest_create_importance_range_is_one_through_five():
    """Workers may score new blocks 1–5; 0 stays reserved (not a create score)."""
    digest = _load_digest()
    policy = digest.DIGEST_POLICY
    contract = digest._staging_output_contract(Path("/tmp/2026-07-26.md"))
    assert digest.IMPORTANCE_WRITE_MIN == 1
    assert "1–5" in policy
    assert "do not write 0 at create" in policy
    assert "0–2" not in policy
    assert "Do not invent 0 at create" in contract
    assert "0–2" not in contract


def test_append_policy_event_is_user_driven_causal_chain():
    """Step 4: DIGEST_POLICY event = user request chain, not a tool step."""
    digest = _load_digest()
    policy = digest.DIGEST_POLICY
    assert "user-driven causal chain" in policy
    assert "user_requested_" in policy
    assert "{entity: User, role: requester}" in policy
    assert "{entity: Assistant, role: executor}" in policy
    assert "no file paths / message ids / byte sizes" in policy
    assert "no 【过程性参考】" in policy
    # Negative: must not frame event as an agent/tool-step card.
    assert "agent action" not in policy
    assert "tool-step name" in policy  # explicit prohibition remains


def test_append_policy_procedure_and_decision_are_event_driven():
    """procedure = task, obstacle, deliverable; decision = user feedback + prefs."""
    digest = _load_digest()
    policy = digest.DIGEST_POLICY
    assert "procedure: what the user asked the agent to do" in policy
    assert "not object/API documentation" in policy
    assert "decision_constraint: user feedback on that procedure" in policy
    assert "First subject after Preference:/Decision: must be user/User" in policy
    assert "standing prefs" in policy or "agent behavior" in policy
    assert "procedure: how-to" not in policy
    assert "decision_constraint: must/must-not/correction" not in policy


def test_append_policy_correction_uses_supersedes_not_related_delete():
    """Step 4: corrections use supersedes:+explicit; related: never deletes."""
    digest = _load_digest()
    policy = digest.DIGEST_POLICY
    assert "MUST set confidence: explicit and supersedes:" in policy
    assert "related: is associative only and NEVER deletes" in policy
    # Negative: related: must not be the destructive/correction key.
    assert "MUST set confidence: explicit and related:" not in policy
    assert "related: is associative only and NEVER deletes or supersedes a block." in policy


def test_staging_output_contract_documents_related_vs_supersedes(tmp_path):
    """Step 4: contract distinguishes associative related: from supersedes:."""
    digest = _load_digest()
    contract = digest._staging_output_contract(tmp_path / "2026-07-26.md")
    assert "event→non-event only" in contract
    assert "supersedes: [mem-<YYYY-MM-DD>-<slug>]" in contract
    assert "requires confidence: explicit" in contract
    assert "related: is associative and never deletes" in contract
    assert "Narration:" in contract or "kind=Narration" in contract
    assert "{entity" in contract and "role" in contract
    # Negative: related skeleton must not claim it requires explicit / deletes.
    related_line = next(
        line for line in contract.splitlines() if line.strip().startswith("related:")
    )
    assert "requires confidence: explicit" not in related_line
    assert "delete" not in related_line.lower()


def test_staging_output_example_has_event_procedure_and_correction():
    """Step 4: example models event+linked procedure and a supersedes correction."""
    digest = _load_digest()
    example = digest._staging_output_example()
    assert "EXAMPLE (event + linked procedure)" in example
    assert "predicate: user_requested_lit_review_chapter" in example
    assert "related: [mem-20260725-ilink-file-push]" in example
    assert "type: procedure" in example
    assert "Assistant delivered the chapter file for this request" in example
    assert "EXAMPLE (user correction / feedback with supersedes)" in example
    assert "type: decision\n" in example
    assert "type: decision_constraint\n" not in example
    assert "supersedes: [mem-20260725-user-deliverables]" in example
    # Negative: correction shape must not use related: as the revision key.
    correction = example.split("EXAMPLE (user correction / feedback with supersedes):", 1)[1]
    assert "supersedes:" in correction
    assert "related:" not in correction.split("---", 2)[1]


def test_append_prompt_embeds_event_first_policy_and_examples(tmp_path, monkeypatch):
    """Append prompt surfaces Step 4 policy + example shapes to the worker."""
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    prompt = digest._build_prompt(
        "s1",
        "wecom",
        [{"id": 1, "role": "user", "content": "remember this"}],
        digest.BOOKMARK_TRIGGER_REASON,
    )
    assert "user-driven causal chain" in prompt
    assert "MUST set confidence: explicit and supersedes:" in prompt
    assert "EXAMPLE (event + linked procedure)" in prompt
    assert "EXAMPLE (user correction / feedback with supersedes)" in prompt
    # Negative: old related-as-delete correction wording must stay gone.
    assert "MUST set confidence: explicit and related:" not in prompt


def test_prompt_includes_existing_block_ids(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily = _daily_path(tmp_path)
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text(
        "\n".join(
            [
                "---",
                "id: mem-2026-06-30-prior-fact",
                "type: fact",
                "entity: Elsa",
                "confidence: high",
                "status: candidate",
                "sources: [session s1]",
                "---",
                "Prior staged fact about Elsa grade.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    prompt = digest._build_prompt(
        "s1",
        "wecom",
        [{"id": 1, "role": "user", "content": "that was wrong, grade is 6"}],
        digest.BOOKMARK_TRIGGER_REASON,
    )

    assert "EXISTING BLOCK IDS (current-day staging" in prompt
    assert "supersedes:" in prompt
    assert "mem-2026-06-30-prior-fact" in prompt
    assert "entity: Elsa" in prompt


def _write_daily_block(
    home: Path,
    day: str,
    *,
    block_id: str,
    entity: str,
    body: str,
) -> Path:
    path = home / "memories" / "staging" / "daily" / f"{day}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                f"id: {block_id}",
                "type: fact",
                f"entity: {entity}",
                "confidence: high",
                "status: candidate",
                "sources: [session s1]",
                "---",
                body,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_prompt_existing_ids_include_yesterday_from_tier1(tmp_path, monkeypatch):
    """Same-day catalogue: yesterday's id must not appear even if the file exists."""
    digest = _load_digest()
    fixed = date(2026, 7, 26)
    yesterday = (fixed - timedelta(days=1)).isoformat()
    today = fixed.isoformat()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(digest, "hermes_local_today", lambda: fixed)
    monkeypatch.setattr(digest, "hermes_local_today_str", lambda: today)

    _write_daily_block(
        tmp_path,
        yesterday,
        block_id="mem-2026-07-25-merge-result",
        entity="Thesis",
        body="Yesterday merge claimed chapter grade is 5.",
    )
    _write_daily_block(
        tmp_path,
        today,
        block_id="mem-2026-07-26-today-note",
        entity="Thesis",
        body="Today note about the same thesis thread.",
    )

    prompt = digest._build_prompt(
        "s1",
        "wecom",
        [{"id": 1, "role": "user", "content": "correction: grade is 6 not 5"}],
        digest.BOOKMARK_TRIGGER_REASON,
    )

    assert "EXISTING BLOCK IDS (current-day staging" in prompt
    assert "mem-2026-07-25-merge-result" not in prompt
    assert f"file: {yesterday}.md" not in prompt
    assert "Snippets may be truncated" in prompt
    assert "mem-2026-07-26-today-note" in prompt
    assert f"file: {today}.md" in prompt
    assert "EXISTING BLOCK IDS TODAY" not in prompt
    assert "recent 3-day staging" not in prompt


def test_prompt_related_allows_week_alive_cross_day_ids(tmp_path, monkeypatch):
    """Negative: listing yesterday's id for supersedes does not make related: cross-day."""
    digest = _load_digest()
    fixed = date(2026, 7, 26)
    yesterday = (fixed - timedelta(days=1)).isoformat()
    today = fixed.isoformat()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(digest, "hermes_local_today", lambda: fixed)
    monkeypatch.setattr(digest, "hermes_local_today_str", lambda: today)

    _write_daily_block(
        tmp_path,
        yesterday,
        block_id="mem-2026-07-25-other-day",
        entity="Elsa",
        body="Prior-day fact.",
    )
    _write_daily_block(
        tmp_path,
        today,
        block_id="mem-2026-07-26-local",
        entity="Elsa",
        body="Same-day fact.",
    )

    prompt = digest._build_prompt(
        "s1",
        "wecom",
        [{"id": 1, "role": "user", "content": "link this"}],
        digest.BOOKMARK_TRIGGER_REASON,
    )

    # Yesterday is out of the supersedes catalogue; related may still be week-alive.
    assert "mem-2026-07-25-other-day" not in prompt
    assert "related: stays same-file" not in prompt
    assert "week-alive" in prompt
    assert "supersedes" in prompt
    assert "mem-2026-07-26-local" in prompt


def test_prompt_existing_ids_exclude_files_outside_tier1(tmp_path, monkeypatch):
    """Negative: only today's daily file is in the supersedes catalogue."""
    digest = _load_digest()
    fixed = date(2026, 7, 26)
    today = fixed.isoformat()
    outside = (fixed - timedelta(days=4)).isoformat()  # 2026-07-22
    other_days = [
        (fixed - timedelta(days=2)).isoformat(),  # 2026-07-24
        (fixed - timedelta(days=1)).isoformat(),  # 2026-07-25
    ]
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(digest, "hermes_local_today", lambda: fixed)
    monkeypatch.setattr(digest, "hermes_local_today_str", lambda: today)

    _write_daily_block(
        tmp_path,
        outside,
        block_id="mem-2026-07-22-too-old",
        entity="Old",
        body="Outside the current-day correction window.",
    )
    for day in other_days:
        _write_daily_block(
            tmp_path,
            day,
            block_id=f"mem-{day}-in-window",
            entity="Thesis",
            body=f"Prior-day note for {day}.",
        )
    _write_daily_block(
        tmp_path,
        today,
        block_id=f"mem-{today}-in-window",
        entity="Thesis",
        body=f"In-window note for {today}.",
    )

    prompt = digest._build_prompt(
        "s1",
        "wecom",
        [{"id": 1, "role": "user", "content": "correction"}],
        digest.BOOKMARK_TRIGGER_REASON,
    )

    assert "mem-2026-07-22-too-old" not in prompt
    assert f"file: {outside}.md" not in prompt
    for day in other_days:
        assert f"mem-{day}-in-window" not in prompt
        assert f"file: {day}.md" not in prompt
    assert f"mem-{today}-in-window" in prompt
    assert f"file: {today}.md" in prompt


def _inflight_state(home: Path, *, session_id: str = "s1") -> None:
    entry = {
        "session_id": session_id,
        "platform": "wecom",
        "digest_in_flight": True,
        "in_flight_batch_end_id": 30,
        "last_digest_attempt_at": "2026-06-17T00:00:00+00:00",
    }
    path = _state_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sessions": {session_id: entry}}), encoding="utf-8")


def _daily_path(home: Path) -> Path:
    today = hermes_local_today_str()
    return home / "memories" / "staging" / "daily" / f"{today}.md"


def test_invoke_digest_llm_delegates(monkeypatch):
    digest = _load_digest()
    seen: dict = {}

    def fake_run(prompt, *, plugin, purpose, platform="cli", max_iterations=10):
        seen["kwargs"] = dict(
            plugin=plugin,
            purpose=purpose,
            platform=platform,
            max_iterations=max_iterations,
        )
        seen["prompt"] = prompt
        return "```\nok\n```"

    monkeypatch.setattr(digest, "run_worker_llm", fake_run)
    out = digest._invoke_digest_llm("p", "wecom")
    assert out == "ok"
    assert seen["prompt"] == "p"
    assert seen["kwargs"]["plugin"] == "memory-digest"
    assert seen["kwargs"]["purpose"] == "digest"
    assert seen["kwargs"]["platform"] == "wecom"
    assert seen["kwargs"]["max_iterations"] == 15


def test_stale_inflight_skips_when_source_window_already_on_daily(tmp_path, monkeypatch):
    digest = _load_digest()
    messages = _messages(12)
    today = hermes_local_today_str()
    daily = tmp_path / "memories" / "staging" / "daily" / f"{today}.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text(
        "\n".join(
            [
                "---",
                "id: mem-already-extracted",
                "type: fact",
                "entity: Replay",
                "confidence: high",
                "status: candidate",
                "sources: [session s1#1-24]",
                "---",
                "Already extracted window.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _inflight_state(tmp_path)
    calls = _configure_digest(digest, monkeypatch, tmp_path, messages)

    result = digest._maybe_run_digest("s1", reason=digest.BOOKMARK_TRIGGER_REASON)

    assert calls == []
    assert result["outcome"] == "already_extracted"
    state = _read_state(tmp_path)
    assert state["sessions"]["s1"]["last_digest_message_id"] == 24
    assert state["sessions"]["s1"]["digest_in_flight"] is False


def test_replayed_new_sqlite_ids_skip_when_user_hashes_known(tmp_path, monkeypatch):
    digest = _load_digest()
    texts = [f"cloned user {idx}" for idx in range(1, 13)]
    hashes = [hashlib.sha256(text.strip().encode()).hexdigest() for text in texts]
    messages: list[dict] = []
    next_id = 70000
    for text in texts:
        messages.append({"id": next_id, "role": "user", "content": text})
        next_id += 1
        messages.append({"id": next_id, "role": "assistant", "content": "ok"})
        next_id += 1
    path = _state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "digested_user_hashes": hashes,
                "sessions": {
                    "s1": {
                        "session_id": "s1",
                        "platform": "wecom",
                        "last_digest_message_id": 69999,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    calls = _configure_digest(digest, monkeypatch, tmp_path, messages)

    result = digest._maybe_run_digest("s1", reason=digest.BOOKMARK_TRIGGER_REASON)

    assert calls == []
    assert result["outcome"] == "already_extracted"
    state = _read_state(tmp_path)
    assert state["sessions"]["s1"]["last_digest_message_id"] == next_id - 1


def test_fresh_user_text_after_hashes_still_runs_worker(tmp_path, monkeypatch):
    digest = _load_digest()
    old_hashes = [
        hashlib.sha256(f"old text {idx}".encode()).hexdigest() for idx in range(12)
    ]
    messages = _messages(12)
    path = _state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "digested_user_hashes": old_hashes,
                "sessions": {
                    "s1": {"session_id": "s1", "platform": "wecom"}
                },
            }
        ),
        encoding="utf-8",
    )
    calls = _configure_digest(digest, monkeypatch, tmp_path, messages)

    digest._maybe_run_digest("s1", reason=digest.BOOKMARK_TRIGGER_REASON)

    assert len(calls) == 1


def test_fetch_messages_skips_compaction_user_rows(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            session_id TEXT,
            role TEXT,
            content TEXT,
            timestamp TEXT,
            active INTEGER
        )
        """
    )
    aged = (datetime.now(timezone.utc) - timedelta(seconds=700)).isoformat()
    rows = []
    for idx in range(1, 12):
        rows.append((idx, "s1", "user", f"user {idx}", aged, 1))
    rows.append(
        (
            12,
            "s1",
            "user",
            "[CONTEXT COMPACTION — REFERENCE ONLY]\nsummary of prior turns",
            aged,
            1,
        )
    )
    conn.executemany(
        "INSERT INTO messages (id, session_id, role, content, timestamp, active) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()

    fetched = digest._fetch_messages("s1")
    assert len(fetched) == 11
    assert all(not m["content"].startswith("[CONTEXT COMPACTION") for m in fetched)

    path = _state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"sessions": {"s1": {"session_id": "s1", "platform": "wecom"}}}),
        encoding="utf-8",
    )
    calls: list[dict] = []

    def fake_run(*_args, **_kwargs):
        calls.append({"ran": True})

    monkeypatch.setattr(digest, "_run_digest_worker", fake_run)

    class ImmediateThread:
        def __init__(self, *, target, args, kwargs=None, name, daemon):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            self._target(*self._args, **self._kwargs)

    monkeypatch.setattr(digest.threading, "Thread", ImmediateThread)

    digest._maybe_run_digest("s1", reason=digest.BOOKMARK_TRIGGER_REASON)
    assert calls == []


def test_batch_ready_holds_ten_minutes_without_trailing_assistant(tmp_path, monkeypatch):
    digest = _load_digest()
    messages = _messages(12, assistant_count=0)
    now = datetime.now(timezone.utc).isoformat()
    for row in messages:
        row["timestamp"] = now
    _write_state(tmp_path)
    calls = _configure_digest(digest, monkeypatch, tmp_path, messages)

    result = digest._maybe_run_digest("s1", reason=digest.BOOKMARK_TRIGGER_REASON)

    assert calls == []
    assert result["outcome"] == "waiting_assistant"
    state = _read_state(tmp_path)
    assert "batch_hold_until" in state["sessions"]["s1"]

    aged = (datetime.now(timezone.utc) - timedelta(seconds=700)).isoformat()
    for row in messages:
        row["timestamp"] = aged
    digest._maybe_run_digest("s1", reason=digest.BOOKMARK_TRIGGER_REASON)
    assert len(calls) == 1
    assert "batch_hold_until" not in _read_state(tmp_path)["sessions"]["s1"]


def test_batch_ready_starts_immediately_when_trailing_assistant_present(
    tmp_path, monkeypatch
):
    digest = _load_digest()
    messages = _messages(12)
    _write_state(tmp_path)
    calls = _configure_digest(digest, monkeypatch, tmp_path, messages)

    digest._maybe_run_digest("s1", reason=digest.BOOKMARK_TRIGGER_REASON)

    assert len(calls) == 1
    assert "batch_hold_until" not in _read_state(tmp_path)["sessions"]["s1"]


def test_thirteenth_user_breaks_assistant_hold(tmp_path, monkeypatch):
    digest = _load_digest()
    messages = _messages(13, assistant_count=0)
    now = datetime.now(timezone.utc).isoformat()
    for row in messages:
        row["timestamp"] = now
    _write_state(tmp_path)
    calls = _configure_digest(digest, monkeypatch, tmp_path, messages)

    digest._maybe_run_digest("s1", reason=digest.BOOKMARK_TRIGGER_REASON)

    assert len(calls) == 1
    assert calls[0]["user_count"] == 12
    assert "batch_hold_until" not in _read_state(tmp_path)["sessions"]["s1"]
