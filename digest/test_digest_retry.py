from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_mymemory = Path(__file__).resolve().parent.parent
if str(_mymemory) not in sys.path:
    sys.path.insert(0, str(_mymemory))

_plugins_root = Path(__file__).resolve().parent.parent.parent
if str(_plugins_root) not in sys.path:
    sys.path.insert(0, str(_plugins_root))

from memory_staging import hermes_local_today_str


from conftest import (
    is_dedup_prompt,
    load_plugin_module,
    phase1_tool_capture,
    sample_phase1_tool_args,
    stub_dedup_proposal,
)


def _load_digest():
    return load_plugin_module("digest.py", "memory_digest_retry_test")


def _block(
    *,
    status: str = "candidate",
    entity: str | None = "Casey",
    body: str = "Casey chose home-packed lunch.",
) -> str:
    lines = [
        "---",
        "id: mem-1",
        "type: fact",
    ]
    if entity is not None:
        lines.append(f"entity: {entity}")
    lines += [
        "confidence: high",
        f"status: {status}",
        "sources: [session s1]",
        "---",
        body,
    ]
    return "\n".join(lines)


_VALID = _block()


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


def _state_path(home: Path) -> Path:
    return home / "memories" / "staging" / ".digest-state.json"


def _write_inflight_state(home: Path, *, session_id: str = "s1") -> None:
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


def _read_entry(home: Path, session_id: str = "s1") -> dict:
    state = json.loads(_state_path(home).read_text(encoding="utf-8"))
    return state["sessions"][session_id]


def _daily_path(home: Path) -> Path:
    today = hermes_local_today_str()
    return home / "memories" / "staging" / "daily" / f"{today}.md"


def test_normal_caller_routes_event_contract_and_retries_wrong_type(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    state_path = _state_path(tmp_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "sessions": {
                    "s1": {"session_id": "s1", "platform": "wecom"}
                }
            }
        ),
        encoding="utf-8",
    )
    # Twelve user messages; assistant replies are included but not a floor.
    messages = [
        item
        for idx in range(1, 13)
        for item in (
            {"id": idx * 2 - 1, "role": "user", "content": f"user {idx}"},
            {"id": idx * 2, "role": "assistant", "content": f"assistant {idx}"},
        )
    ]
    monkeypatch.setattr(digest, "_fetch_messages", lambda session_id, after_id=0: messages)
    prompts: list[str] = []

    def fake_llm(prompt, platform, **kwargs):
        if is_dedup_prompt(prompt):
            return stub_dedup_proposal(prompt)
        return ""

    monkeypatch.setattr(digest, "_invoke_digest_llm", fake_llm)
    monkeypatch.setattr(
        digest, "_invoke_digest_worker_tool", phase1_tool_capture(prompts=prompts)
    )

    outcome = digest._maybe_run_digest("s1", reason="test", sync=True)

    assert outcome["outcome"] == "appended"
    assert any("Phase-1 memory digest extractor" in p for p in prompts)
    daily = _daily_path(tmp_path).read_text(encoding="utf-8")
    assert "type: event" in daily
    assert "type: fact" in daily


def test_skip_only_content_detected_without_blocks():
    digest = _load_digest()
    assert digest._is_skip_only_content("Nothing durable to stage this batch.")
    assert not digest._is_skip_only_content(_VALID)


def test_event_first_is_nothing_durable_when_all_workers_return_skip():
    digest = _load_digest()
    skip = "Nothing durable to stage this batch."
    event = digest.ValidatedWorkerResult(
        worker_type="event",
        session_id="s1",
        run_id="r1",
        attempts=1,
        content=skip,
        blocks=(),
    )
    details = [
        digest.ValidatedWorkerResult(
            worker_type=worker_type,
            session_id="s1",
            run_id="r1",
            attempts=1,
            content=skip,
            blocks=(),
        )
        for worker_type in ("fact", "procedure", "decision")
    ]
    assert digest._event_first_is_nothing_durable(event, details)


def test_event_first_is_nothing_durable_false_when_detail_has_blocks():
    digest = _load_digest()
    skip = (
        "Nothing durable to stage.\n\n"
        "The exchange is a micro-decision about layout — too thin for an event."
    )
    event = digest.ValidatedWorkerResult(
        worker_type="event",
        session_id="s1",
        run_id="r1",
        attempts=1,
        content=skip,
        blocks=(),
    )
    details = [
        digest.ValidatedWorkerResult(
            worker_type="fact",
            session_id="s1",
            run_id="r1",
            attempts=1,
            content="Nothing durable to stage.",
            blocks=(),
        ),
        digest.ValidatedWorkerResult(
            worker_type="procedure",
            session_id="s1",
            run_id="r1",
            attempts=1,
            content="Nothing durable to stage.",
            blocks=(),
        ),
        digest.ValidatedWorkerResult(
            worker_type="decision",
            session_id="s1",
            run_id="r1",
            attempts=1,
            content=_VALID_DECISION,
            blocks=digest._worker_result_blocks(_VALID_DECISION),
        ),
    ]
    assert not digest._event_first_is_nothing_durable(event, details)


def test_detail_workers_append_when_event_skips_but_decision_has_blocks(
    tmp_path, monkeypatch
):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    _write_inflight_state(tmp_path)
    daily = _daily_path(tmp_path)
    decision_only = {
        "blocks": [
            {
                "type": "decision",
                "kind": "Decision",
                "subject": "user",
                "ruling": "prefers concise review summaries",
                "confidence": "explicit",
                "importance": 3,
            }
        ]
    }
    monkeypatch.setattr(
        digest, "_invoke_digest_worker_tool", phase1_tool_capture(decision_only)
    )
    monkeypatch.setattr(
        digest,
        "_invoke_digest_llm",
        lambda prompt, platform, **kwargs: (
            stub_dedup_proposal(prompt) if is_dedup_prompt(prompt) else ""
        ),
    )

    result = digest._run_event_first_workers(
        session_id="s1",
        platform="wecom",
        transcript="[user] lock version A\n[assistant] locked",
        session_key="s1",
        daily_path=daily,
        batch_end_id=30,
        run_id="run-mix",
        reason="test",
    )

    assert result == "appended"
    assert daily.exists()
    daily_text = daily.read_text(encoding="utf-8")
    assert "type: decision" in daily_text
    assert "prefers concise review summaries" in daily_text
    entry = _read_entry(tmp_path)
    assert entry["last_digest_message_id"] == 30


def test_production_path_passes_raw_transcript(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    path = tmp_path / "memories" / "staging" / ".digest-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "sessions": {
                    "s1": {
                        "session_id": "s1",
                        "platform": "wecom",
                        "last_digest_message_id": 0,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    messages = [
        {"id": 1, "role": "user", "content": "remember this preference"},
        {"id": 2, "role": "assistant", "content": "noted"},
    ]
    monkeypatch.setattr(digest, "_fetch_messages", lambda sid, after_id=0: messages)
    monkeypatch.setattr(digest, "_batch_ready", lambda msgs: True)

    captured: list[str] = []

    def fake_llm(prompt, platform, **kwargs):
        if is_dedup_prompt(prompt):
            return stub_dedup_proposal(prompt)
        return ""

    monkeypatch.setattr(digest, "_invoke_digest_llm", fake_llm)
    monkeypatch.setattr(
        digest, "_invoke_digest_worker_tool", phase1_tool_capture(prompts=captured)
    )

    result = digest._maybe_run_digest("s1", reason="slash_force", force=True, sync=True)

    assert result["outcome"] == "appended"
    phase1_prompt = next(
        p for p in captured if "Phase-1 memory digest extractor" in p
    )
    assert "[user] remember this preference" in phase1_prompt
    assert "submit_digest_blocks" in phase1_prompt


def test_event_first_skip_advances_bookmark_without_daily_write(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    _write_inflight_state(tmp_path)
    daily = _daily_path(tmp_path)

    monkeypatch.setattr(
        digest,
        "run_phase1_digest_blocks",
        lambda *a, **k: digest.ValidatedWorkerResult(
            worker_type="phase1",
            session_id="s1",
            run_id="run-skip",
            attempts=1,
            content="skip",
            blocks=(),
        ),
    )

    result = digest._run_event_first_workers(
        session_id="s1",
        platform="wecom",
        transcript="TRANSCRIPT",
        session_key="s1",
        daily_path=daily,
        batch_end_id=30,
        run_id="run-skip",
        reason="test",
    )

    assert result == "skip"
    assert not daily.exists()
    entry = _read_entry(tmp_path)
    assert entry["last_digest_message_id"] == 30
    assert entry["digest_in_flight"] is False


def test_finalize_success_advances_bookmark_when_session_missing(tmp_path, monkeypatch):
    """Committed digests must move the bookmark even if state entry was cleared."""
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    path = _state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sessions": {}}), encoding="utf-8")

    digest._finalize_digest_success("s1", 42, session_id="s1")

    entry = _read_entry(tmp_path)
    assert entry["session_id"] == "s1"
    assert entry["last_digest_message_id"] == 42
    assert entry["digest_in_flight"] is False
    assert entry["last_digest_attempts"] == 0


def test_event_first_skip_recreates_bookmark_when_state_cleared(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    path = _state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sessions": {}}), encoding="utf-8")
    daily = _daily_path(tmp_path)

    monkeypatch.setattr(
        digest,
        "run_phase1_digest_blocks",
        lambda *a, **k: digest.ValidatedWorkerResult(
            worker_type="phase1",
            session_id="s1",
            run_id="run-skip-clear",
            attempts=1,
            content="skip",
            blocks=(),
        ),
    )

    result = digest._run_event_first_workers(
        session_id="s1",
        platform="wecom",
        transcript="TRANSCRIPT",
        session_key="s1",
        daily_path=daily,
        batch_end_id=55,
        run_id="run-skip-clear",
        reason="test",
    )

    assert result == "skip"
    entry = _read_entry(tmp_path)
    assert entry["last_digest_message_id"] == 55
    assert entry["session_id"] == "s1"


def test_commit_candidate_retries_before_success(tmp_path, monkeypatch):
    digest = _load_digest()
    attempts: list[int] = []

    def fake_once(*_args, **_kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            return False, ["simulated transient commit rejection"]
        return True, []

    monkeypatch.setattr(digest, "_commit_candidate_once", fake_once)
    assert digest._commit_candidate(
        tmp_path / "daily.md",
        [],
        {"session_id": "s1", "run_id": "r1", "operations": [], "status": "validated"},
        session_id="s1",
        run_id="r1",
        max_attempts=3,
    )
    assert len(attempts) == 3


def test_build_commit_retry_section_labels_commit_failure():
    digest = _load_digest()
    section = digest._build_commit_retry_section(
        1,
        ["line 12: body too long (501 > 500 chars)"],
        "---\nid: mem-1\ntype: fact\n---\n" + ("x" * 50),
        max_attempts=3,
    )
    assert "COMMIT VALIDATION FAILED (attempt 1 of 3)" in section
    assert "body too long" in section


def test_pipeline_persists_phase1_without_phase2(tmp_path, monkeypatch):
    """Extract path writes cards and bookmarks; merge stays off this call."""
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    _write_inflight_state(tmp_path)
    daily = _daily_path(tmp_path)

    monkeypatch.setattr(
        digest, "_invoke_digest_worker_tool", phase1_tool_capture()
    )
    phase2_calls: list[dict] = []

    def capture_phase2(**kwargs):
        phase2_calls.append(kwargs)
        return False, ["should not run"]

    monkeypatch.setattr(digest, "_run_phase2_consolidate", capture_phase2)

    result = digest._run_digest_pipeline(
        session_id="s1",
        platform="wecom",
        transcript="TRANSCRIPT",
        session_key="s1",
        daily_path=daily,
        batch_end_id=30,
        run_id="run-p1-only",
        reason="test",
    )

    assert result == "appended"
    assert phase2_calls == []
    assert daily.exists()
    text = daily.read_text(encoding="utf-8")
    assert "type: event" in text
    entry = _read_entry(tmp_path)
    assert entry["last_digest_message_id"] == 30
    assert entry["digest_in_flight"] is False


def test_pipeline_same_day_prior_batch_overwrite(tmp_path, monkeypatch):
    """Extract persist then overwrite the same-file prior-batch card; no LLM merge."""
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    _write_inflight_state(tmp_path)
    daily = _daily_path(tmp_path)
    daily.parent.mkdir(parents=True, exist_ok=True)
    target_id = "mem-2026-08-22-fact-PRIOR1"
    helper_id = "mem-2026-08-22-fact-CORR01"
    daily.write_text(
        "\n".join(
            [
                "---",
                f"id: {target_id}",
                "type: fact",
                "entity: Thesis",
                "confidence: high",
                "status: candidate",
                "sources: [session s1]",
                "---",
                "Factual: chapter grade is 5.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    helper = {
        "id": helper_id,
        "type": "fact",
        "entity": "Thesis",
        "confidence": "explicit",
        "status": "candidate",
        "sources": ["session s1"],
        "supersedes": [target_id],
        "body": "Factual: chapter grade is 6 not 5.",
    }

    def fake_phase1(*_args, **_kwargs):
        return digest.ValidatedWorkerResult(
            worker_type="phase1",
            session_id="s1",
            run_id="run-overwrite",
            attempts=1,
            content=digest._content_from_blocks([helper]),
            blocks=(dict(helper),),
        )

    monkeypatch.setattr(digest, "run_phase1_digest_blocks", fake_phase1)
    phase2_calls: list[dict] = []

    def capture_phase2(**kwargs):
        phase2_calls.append(kwargs)
        return False, ["should not run"]

    monkeypatch.setattr(digest, "_run_phase2_consolidate", capture_phase2)
    monkeypatch.setattr(
        digest,
        "run_manual_phase2",
        lambda *a, **k: {"outcome": "should not run"},
    )

    result = digest._run_digest_pipeline(
        session_id="s1",
        platform="wecom",
        transcript="TRANSCRIPT",
        session_key="s1",
        daily_path=daily,
        batch_end_id=30,
        run_id="run-overwrite",
        reason="test",
    )

    assert result == "appended"
    assert phase2_calls == []
    text = daily.read_text(encoding="utf-8")
    assert "Factual: chapter grade is 6 not 5." in text
    assert "Factual: chapter grade is 5." not in text
    assert helper_id not in text
    assert target_id in text
    entry = _read_entry(tmp_path)
    assert entry["last_digest_message_id"] == 30
    assert entry["digest_in_flight"] is False


def test_phase2_leftover_same_file_supersede_overwrites(tmp_path, monkeypatch):
    """Clock leftover: on-file pointer overwrites even when the LLM emits nothing."""
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily = _daily_path(tmp_path)
    daily.parent.mkdir(parents=True, exist_ok=True)
    target_id = "mem-2026-08-22-fact-PRIOR2"
    helper_id = "mem-2026-08-22-fact-CORR02"
    daily.write_text(
        "\n".join(
            [
                "---",
                f"id: {target_id}",
                "type: fact",
                "entity: Thesis",
                "confidence: high",
                "status: candidate",
                "sources: [session s1]",
                "---",
                "Factual: chapter grade is 5.",
                "",
                "---",
                f"id: {helper_id}",
                "type: fact",
                "entity: Thesis",
                "confidence: explicit",
                "status: candidate",
                "sources: [session s1]",
                "supersedes:",
                f"  - {target_id}",
                "---",
                "Factual: chapter grade is 6 not 5.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    def empty_proposer(*_args, **_kwargs):
        def proposer(_existing, _new, *, errors=(), attempt=1):
            del errors, attempt
            return []

        return proposer

    monkeypatch.setattr(digest, "make_oneshot_proposer", empty_proposer)

    payload = digest.run_manual_phase2(daily, date_str=hermes_local_today_str())
    assert payload["outcome"] == "rewritten"
    text = daily.read_text(encoding="utf-8")
    assert "Factual: chapter grade is 6 not 5." in text
    assert "Factual: chapter grade is 5." not in text
    assert helper_id not in text
    assert target_id in text


def test_event_worker_validates_before_detail_workers_start(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    captured: list[str] = []

    def fake_llm(prompt, platform, **kwargs):
        if is_dedup_prompt(prompt):
            return stub_dedup_proposal(prompt)
        return ""

    monkeypatch.setattr(digest, "_invoke_digest_llm", fake_llm)
    monkeypatch.setattr(
        digest, "_invoke_digest_worker_tool", phase1_tool_capture(prompts=captured)
    )
    result = digest._run_event_first_workers(
        session_id="s1",
        platform="wecom",
        transcript="TRANSCRIPT",
        session_key="s1",
        daily_path=_daily_path(tmp_path),
        batch_end_id=30,
        run_id="run-1",
        reason="test",
    )

    assert result == "appended"
    assert any("Phase-1 memory digest extractor" in p for p in captured)
    daily_path = _daily_path(tmp_path)
    assert daily_path.exists()
    daily_blocks = digest._daily_blocks(daily_path.read_text(encoding="utf-8"))
    event_block = next(block for block in daily_blocks if block["type"] == "event")
    detail_blocks = [block for block in daily_blocks if block["type"] != "event"]
    assert len(detail_blocks) == 3
    assert set(event_block["related"]) == {block["id"] for block in detail_blocks}
    assert all(block["id"] not in event_block["body"] for block in detail_blocks)


def test_cleanup_terminal_removes_failure_jsonl(tmp_path, monkeypatch):
    digest = _load_digest()
    ops = load_plugin_module("operation_log.py", "memory_digest_operation_log_fail_test")
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    session_dir = tmp_path / "memories" / "staging" / ".tmp_mem_files" / "s1"
    session_dir.mkdir(parents=True)
    failures = session_dir / "fact-failures.jsonl"
    failures.write_text(
        json.dumps(
            {
                "status": "exhausted",
                "session_id": "s1",
                "run_id": "r1",
                "worker_type": "fact",
                "attempt": 3,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    execution = session_dir / "execution.json"
    execution.write_text(
        json.dumps(
            {
                "session_id": "s1",
                "run_id": "r1",
                "state": "failed",
            }
        ),
        encoding="utf-8",
    )
    removed = ops.cleanup_terminal_artifacts(
        execution, session_id="s1", run_id="r1"
    )
    assert failures in removed
    assert not failures.exists()


def test_worker_manifest_resets_stale_run_entries(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    event = digest.ValidatedWorkerResult(
        worker_type="event",
        session_id="s1",
        run_id="run-1",
        attempts=1,
        content=_VALID_EVENT,
        blocks=(),
    )
    fact = digest.ValidatedWorkerResult(
        worker_type="fact",
        session_id="s1",
        run_id="run-2",
        attempts=1,
        content=_VALID,
        blocks=(),
    )
    digest._store_validated_worker_result(event)
    digest._store_validated_worker_result(fact)
    manifest = (
        tmp_path
        / "memories"
        / "staging"
        / ".tmp_mem_files"
        / "s1"
        / "worker-manifest.json"
    )
    payload = __import__("json").loads(manifest.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-2"
    assert payload["workers"] == {"fact": "fact-result.json"}


def test_detail_future_exception_finalizes_inflight_digest(tmp_path, monkeypatch):
    """Phase-1 failure finalizes in-flight digest (replaces ThreadPool detail crash)."""
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    _write_inflight_state(tmp_path)

    monkeypatch.setattr(
        digest,
        "run_phase1_digest_blocks",
        lambda *a, **k: digest.WorkerFailure(
            worker_type="phase1",
            session_id="s1",
            run_id="run-1",
            attempts=3,
            errors=("phase1 future exploded",),
        ),
    )

    result = digest._run_event_first_workers(
        session_id="s1",
        platform="wecom",
        transcript="TRANSCRIPT",
        session_key="s1",
        daily_path=_daily_path(tmp_path),
        batch_end_id=30,
        run_id="run-1",
        reason="test",
    )

    assert result == "failed"
    entry = _read_entry(tmp_path)
    assert entry["digest_in_flight"] is False
    assert entry["last_digest_attempts"] == digest.MAX_VALIDATION_ATTEMPTS
    assert "phase1 future exploded" in digest._log_file().read_text(encoding="utf-8")


def test_exhausted_detail_aborts_transaction_without_daily_write(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    _write_inflight_state(tmp_path)
    daily = _daily_path(tmp_path)
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text("existing daily content\n", encoding="utf-8")

    monkeypatch.setattr(
        digest,
        "run_phase1_digest_blocks",
        lambda *a, **k: digest.WorkerFailure(
            worker_type="phase1",
            session_id="s1",
            run_id="run-2",
            attempts=3,
            errors=("phase1 validation exhausted",),
        ),
    )

    result = digest._run_event_first_workers(
        session_id="s1",
        platform="wecom",
        transcript="TRANSCRIPT",
        session_key="s1",
        daily_path=daily,
        batch_end_id=30,
        run_id="run-2",
        reason="test",
    )

    assert result == "failed"
    assert daily.read_text(encoding="utf-8") == "existing daily content\n"
    assert _read_entry(tmp_path)["digest_in_flight"] is False


def test_retrieval_patch_failure_does_not_mark_phase2_success(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True)
    old_id = "mem-2026-08-01-fact-aaaaaaaaaaaa"
    new_id = "mem-2026-08-20-fact-bbbbbbbbbbbb"
    (daily / "2026-08-01.md").write_text(
        f"---\nid: {old_id}\ntype: fact\nentity: Canteen\nconfidence: high\n"
        f"status: candidate\nvalid_from: 2026-08-01\nvalid_to: open\n"
        f"sources: [session s1]\n---\nCanteen is open.\n",
        encoding="utf-8",
    )
    (daily / "2026-08-20.md").write_text(
        f"---\nid: {new_id}\ntype: fact\nentity: Canteen\nconfidence: high\n"
        f"status: candidate\nvalid_from: 2026-08-20\nvalid_to: open\n"
        f"sources: [session s1]\n---\nCanteen is closed.\n",
        encoding="utf-8",
    )
    state_path = _state_path(tmp_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    import time as time_mod

    state_path.write_text(
        json.dumps(
            {
                "sessions": {
                    "s-retry": {
                        "session_id": "s-retry",
                        "retrieval": {
                            "ids": [old_id, new_id],
                            "query": "Canteen",
                            "recorded_at": time_mod.time(),
                            "consumed": False,
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("memory_staging.patch_daily_block_status", boom)
    monkeypatch.setattr(digest, "_fetch_messages", lambda *a, **k: [])
    monkeypatch.setattr(digest, "maybe_run_digest_clock", lambda **k: {"outcome": "idle"})

    class ImmediateThread:
        def __init__(self, target=None, args=(), kwargs=None, name=None, daemon=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            if self._target is not None:
                self._target(*self._args, **self._kwargs)

    monkeypatch.setattr(digest.threading, "Thread", ImmediateThread)
    digest.on_agent_end(
        {
            "session_id": "s-retry",
            "session_key": "s-retry",
            "platform": "cli",
            "user_content": "that memory is dated",
        }
    )
    old = (daily / "2026-08-01.md").read_text(encoding="utf-8")
    assert "status: candidate" in old
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    retrieval = payload["sessions"]["s-retry"]["retrieval"]
    assert retrieval.get("consumed") is not True
    assert retrieval.get("ids") == [old_id, new_id]
