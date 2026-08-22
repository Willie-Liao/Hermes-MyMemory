from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import timedelta
from pathlib import Path

_mymemory = Path(__file__).resolve().parent.parent
if str(_mymemory) not in sys.path:
    sys.path.insert(0, str(_mymemory))

_plugins_root = Path(__file__).resolve().parent.parent.parent
if str(_plugins_root) not in sys.path:
    sys.path.insert(0, str(_plugins_root))
_agent = _plugins_root.parent / "hermes-agent"
if str(_agent) not in sys.path:
    sys.path.insert(0, str(_agent))

from memory_staging import hermes_local_now, hermes_local_today_str

from MyMemory.digest import digest as _package_digest
from MyMemory.provider import MyMemoryProvider
from MyMemory.weekly import weekly


def _load_digest():
    return _package_digest


def _prefetch_ctx(digest, monkeypatch, **kwargs):
    """Prefetch-shaped inject: weekly stubbed so digest tests stay digest-only."""
    del digest
    monkeypatch.setattr(weekly, "on_pre_llm_call", lambda **_: None)
    p = MyMemoryProvider()
    p._platform = str(kwargs.get("platform") or "")
    text = p.prefetch(
        str(kwargs.get("user_message") or ""),
        session_id=str(kwargs.get("session_id") or ""),
    )
    if not text:
        return None
    assert "<memory-context>" not in text
    return {"context": text}


def _block(*, block_id: str, body: str, valid_to: str | None = None, entity: str = "Casey") -> str:
    lines = ["---", f"id: {block_id}", "type: fact", f"entity: {entity}"]
    if valid_to is not None:
        lines += ["valid_from: 2026-06-14", f"valid_to: {valid_to}"]
    lines += ["confidence: high", "status: candidate", "sources: [session s1]", "---", body]
    return "\n".join(lines)


def _daily_dir(home: Path) -> Path:
    d = home / "memories" / "staging" / "daily"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_daily(home: Path, date_str: str, content: str) -> None:
    (_daily_dir(home) / f"{date_str}.md").write_text(content + "\n", encoding="utf-8")


def _today() -> str:
    return hermes_local_today_str()


def _days_ago(n: int) -> str:
    return (hermes_local_now() - timedelta(days=n)).strftime("%Y-%m-%d")


def test_session_with_open_fact_silent_span_without_watch(tmp_path, monkeypatch):
    """Open candidates alone do not inject a span ask until a watch is seeded (Task 3)."""
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(tmp_path, _today(), _block(block_id="mem-1", body="Casey canteen open.", valid_to="open"))

    result = _prefetch_ctx(digest, monkeypatch,user_message="hi", is_first_turn=True, session_id="s1")

    assert result is not None
    assert "context" in result
    assert "Casey" in result["context"]
    assert "## Span ask" not in result["context"]
    assert "## Span recall" not in result["context"]


def test_session_with_past_fact_omitted_from_span_recall(tmp_path, monkeypatch):
    """Ladder path scans open_only — past valid_to stays in recent context, not Span ask."""
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    past = _days_ago(1)
    _write_daily(
        tmp_path,
        _days_ago(1),
        _block(block_id="mem-past", body="Application window.", valid_to=past),
    )

    result = _prefetch_ctx(digest, monkeypatch,user_message="hi", is_first_turn=True, session_id="s1")

    assert result is not None
    assert "mem-past" in result["context"]
    assert "## Span recall" not in result["context"]
    assert f"valid_to: {past} (past)" not in result["context"]


def test_stable_fact_returns_recent_context(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(tmp_path, _today(), _block(block_id="mem-stable", body="Stable fact, no span."))

    result = _prefetch_ctx(digest, monkeypatch,user_message="hi", is_first_turn=True, session_id="s1")

    assert result is not None
    assert "Memory / recent" in result["context"] or "Memory / entity" in result["context"]
    assert "mem-stable" in result["context"] or "Casey" in result["context"]
    assert "Span recall" not in result["context"]


def test_future_valid_to_omitted_from_span_recall(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    future = (hermes_local_now() + timedelta(days=30)).strftime("%Y-%m-%d")
    _write_daily(tmp_path, _today(), _block(block_id="mem-future", body="Future deadline.", valid_to=future))

    result = _prefetch_ctx(digest, monkeypatch,user_message="hi", is_first_turn=True, session_id="s1")

    assert result is not None
    assert "Span recall" not in result["context"]
    assert "mem-future" in result["context"]
    assert "Casey" in result["context"]


def test_second_message_same_session_returns_none(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(tmp_path, _today(), _block(block_id="mem-1", body="Casey canteen open.", valid_to="open"))

    first = _prefetch_ctx(digest, monkeypatch,user_message="hi", is_first_turn=True, session_id="s1")
    assert first is not None
    assert _prefetch_ctx(digest, monkeypatch,user_message="hi again", is_first_turn=False, session_id="s1") is None


def test_resumed_session_injects_when_not_first_turn(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(tmp_path, _today(), _block(block_id="mem-1", body="Resume context fact."))

    result = _prefetch_ctx(digest, monkeypatch,user_message="hi", is_first_turn=False, session_id="s-resume")

    assert result is not None
    assert "mem-1" in result["context"]


def test_no_files_returns_none(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)

    assert _prefetch_ctx(digest, monkeypatch,user_message="hi", is_first_turn=True, session_id="s1") is None


def test_cron_session_skips_bootstrap_inject(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(tmp_path, _today(), _block(block_id="mem-1", body="Should not inject on cron."))

    result = _prefetch_ctx(digest, monkeypatch,
        user_message="hi",
        is_first_turn=True,
        session_id="cron_deadbeef_20260724_120000",
    )

    assert result is None


def test_cron_platform_skips_bootstrap_inject(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(tmp_path, _today(), _block(block_id="mem-1", body="Should not inject on cron platform."))

    result = _prefetch_ctx(digest, monkeypatch,
        user_message="hi",
        is_first_turn=True,
        session_id="s-chat",
        platform="cron",
    )

    assert result is None


def test_hermes_cron_session_env_skips_bootstrap_inject(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_CRON_SESSION", "1")
    _write_daily(tmp_path, _today(), _block(block_id="mem-1", body="Should not inject with cron env."))

    result = _prefetch_ctx(digest, monkeypatch,
        user_message="hi",
        is_first_turn=True,
        session_id="s-chat",
    )

    assert result is None


def test_only_last_three_days_scanned_for_span(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(tmp_path, _days_ago(10), _block(block_id="mem-old", body="Old open.", valid_to="open"))
    _write_daily(tmp_path, _days_ago(2), _block(block_id="mem-recent", body="Recent stable.", entity="Alex"))
    _write_daily(tmp_path, _days_ago(1), _block(block_id="mem-y", body="Yesterday stable.", entity="Team-PE"))
    _write_daily(tmp_path, _today(), _block(block_id="mem-t", body="Today stable.", entity="Riley"))

    result = _prefetch_ctx(digest, monkeypatch,user_message="hi", is_first_turn=True, session_id="s1")

    assert result is not None
    assert "mem-old" not in result["context"]
    assert "Alex" in result["context"] or "mem-recent" in result["context"]


def test_inject_excerpt_respects_cap(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    blocks = "\n\n".join(
        _block(block_id=f"mem-{i}", body=f"Open fact number {i} " + "z" * 120, valid_to="open")
        for i in range(25)
    )
    _write_daily(tmp_path, _today(), blocks)

    result = _prefetch_ctx(digest, monkeypatch,user_message="hi", is_first_turn=True, session_id="s1")

    assert result is not None
    assert "Memory / recent" in result["context"] or "Memory / entity" in result["context"]
    assert "25blk" in result["context"] or "fact:25" in result["context"]


def test_skips_entity_already_in_hot_memory(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    mem_dir = tmp_path / "memories"
    mem_dir.mkdir(parents=True, exist_ok=True)
    (mem_dir / "MEMORY.md").write_text("Riley subject panel and grades.\n", encoding="utf-8")
    _write_daily(tmp_path, _today(), _block(block_id="mem-g", body="Riley needs attention.", entity="Riley"))
    _write_daily(
        tmp_path,
        _days_ago(1),
        _block(block_id="mem-w", body="Alex career pivot.", entity="Alex"),
    )

    result = _prefetch_ctx(digest, monkeypatch,user_message="hi", is_first_turn=True, session_id="s1")

    assert result is not None
    assert "Riley" in result["context"]
    assert "also in hot memory" in result["context"]
    assert "Alex" in result["context"]


def test_involves_indexed_in_recent_context(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    block = "\n".join(
        [
            "---",
            "id: mem-2026-06-26-riley-draft",
            "type: fact",
            "entity: Riley",
            "involves: [Morgan, Riley-mom]",
            "confidence: high",
            "status: candidate",
            "sources: [session 20260623_161751_3d4ad0]",
            "---",
            "Narration: Agent drafted parent WeChat reply re grade dispute.",
        ]
    )
    _write_daily(tmp_path, _today(), block)

    text = digest.build_recall_injection_context(session_id="s-inv")

    assert "Memory / recent" in text or "Memory / entity" in text
    assert "Morgan" in text
    assert "Riley-mom" in text
    assert "riley" in text.casefold()
    assert "Narration: Agent drafted" not in text


def test_participants_not_indexed_on_manifest_entity_axis(tmp_path, monkeypatch):
    """Events use participants for matching, but index anchors drop them (plan Step 3)."""
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    block = "\n".join(
        [
            "---",
            "id: mem-2026-06-26-riley-draft",
            "type: event",
            "entity: Riley",
            "predicate: grade_dispute",
            "participants:",
            "  - {entity: Morgan}",
            "  - {entity: Riley-mom, role: escalator}",
            "valid_from: 2026-06-24",
            "valid_to: open",
            "confidence: high",
            "status: candidate",
            "sources: [session 20260623_161751_3d4ad0]",
            "---",
            "Agent drafted parent WeChat reply re grade dispute.",
        ]
    )
    _write_daily(tmp_path, _today(), block)

    text = digest.build_recall_injection_context(session_id="s-part")

    assert "Memory / recent" in text or "Memory / entity" in text
    assert "Riley" in text
    assert re.search(r"(?m)^- Morgan\b", text) is None
    assert re.search(r"(?m)^- Riley-mom\b", text) is None
    assert "Agent drafted parent WeChat reply" not in text


def test_session_hint_in_recent_context(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    block = "\n".join(
        [
            "---",
            "id: mem-2026-06-26-riley-draft",
            "type: event",
            "entity: Riley",
            "predicate: grade_dispute",
            "valid_from: 2026-06-24",
            "valid_to: open",
            "confidence: high",
            "status: candidate",
            "sources: [session 20260623_161751_3d4ad0]",
            "---",
            "Agent drafted parent reply.",
        ]
    )
    _write_daily(tmp_path, _today(), block)

    result = _prefetch_ctx(digest, monkeypatch,user_message="hi", is_first_turn=True, session_id="s1")

    assert result is not None
    assert "mem-2026-06-26-riley-draft" in result["context"]


def test_injects_again_on_new_calendar_day(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(tmp_path, _today(), _block(block_id="mem-1", body="Daily fact."))

    first = _prefetch_ctx(digest, monkeypatch,user_message="hi", is_first_turn=True, session_id="s1")
    assert first is not None
    assert _prefetch_ctx(digest, monkeypatch,user_message="hi again", is_first_turn=False, session_id="s1") is None

    yesterday = _days_ago(1)
    state_path = tmp_path / "memories" / "staging" / ".digest-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        f'{{"recall_bootstrap_sessions": {{"s1": "{yesterday}"}}}}',
        encoding="utf-8",
    )

    again = _prefetch_ctx(digest, monkeypatch,user_message="new day", is_first_turn=False, session_id="s1")
    assert again is not None
    assert "mem-1" in again["context"]


def test_legacy_done_value_reinjects_today(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(tmp_path, _today(), _block(block_id="mem-1", body="Legacy gate fact."))

    state_path = tmp_path / "memories" / "staging" / ".digest-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text('{"recall_injected_sessions": {"s1": "done"}}', encoding="utf-8")

    result = _prefetch_ctx(digest, monkeypatch,user_message="hi", is_first_turn=False, session_id="s1")
    assert result is not None
    assert "mem-1" in result["context"]


def test_recall_cue_does_not_reinject_same_day(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(
        tmp_path,
        _days_ago(1),
        _block(block_id="mem-1", body="Cue recall fact.", entity="Casey"),
    )

    first = _prefetch_ctx(digest, monkeypatch,user_message="hi", is_first_turn=True, session_id="s1", turn_id="t1")
    assert first is not None
    assert _prefetch_ctx(digest, monkeypatch,user_message="ok", is_first_turn=False, session_id="s1", turn_id="t2") is None
    assert _prefetch_ctx(digest, monkeypatch,
        user_message="Casey 昨天说了什么？",
        is_first_turn=False,
        session_id="s1",
        turn_id="t3",
    ) is None


def test_build_recall_injection_context_matches_hook_tier1_body(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(tmp_path, _today(), _block(block_id="mem-1", body="Shared builder fact."))

    hook = _prefetch_ctx(digest, monkeypatch,user_message="hi", is_first_turn=True, session_id="s2", turn_id="t1")
    direct = digest.build_recall_injection_context()
    assert hook is not None
    assert "mem-1" in hook["context"]
    assert "mem-1" in direct
    assert "Memory /" in hook["context"]
    assert "Recall policy" not in hook["context"]


def _event_block(*, block_id: str, body: str, entity: str = "Riley", predicate: str = "grade_dispute") -> str:
    return "\n".join(
        [
            "---",
            f"id: {block_id}",
            "type: event",
            f"entity: {entity}",
            f"predicate: {predicate}",
            "valid_from: 2026-06-24",
            "valid_to: open",
            "confidence: high",
            "status: candidate",
            "sources: [session s1]",
            "---",
            body,
        ]
    )


def test_event_with_open_span_silent_without_watch(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(
        tmp_path,
        _today(),
        _event_block(block_id="mem-ev", body="Drafted parent WeChat reply."),
    )

    result = _prefetch_ctx(digest, monkeypatch,user_message="hi", is_first_turn=True, session_id="s1")

    assert result is not None
    assert "mem-ev" in result["context"]
    assert "## Span ask" not in result["context"]
    assert "## Span recall" not in result["context"]


def test_event_in_recent_context(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    future = (hermes_local_now() + timedelta(days=30)).strftime("%Y-%m-%d")
    _write_daily(
        tmp_path,
        _today(),
        "\n".join(
            [
                "---",
                "id: mem-ev2",
                "type: event",
                "entity: Project",
                "predicate: file_delivered",
                "valid_from: 2026-06-24",
                f"valid_to: {future}",
                "confidence: high",
                "status: candidate",
                "sources: [session s1]",
                "---",
                "Delivered meal-card xlsx.",
            ]
        ),
    )

    result = _prefetch_ctx(digest, monkeypatch,user_message="hi", is_first_turn=True, session_id="s1")

    assert result is not None
    assert "Memory / recent" in result["context"] or "Memory / entity" in result["context"]
    assert "mem-ev2" in result["context"]
    assert "Span recall" not in result["context"]


def test_existing_hypothesis_is_excluded_from_recent_context(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    hypothesis = "\n".join(
        [
            "---",
            "id: mem-legacy-hypothesis",
            "type: hypothesis",
            "entity: Project",
            "confidence: medium",
            "status: candidate",
            "sources: [session s1]",
            "---",
            "The project may change direction.",
        ]
    )
    _write_daily(
        tmp_path,
        _today(),
        _block(block_id="mem-keep", body="Keep this fact.") + "\n\n" + hypothesis,
    )

    result = _prefetch_ctx(digest, monkeypatch,
        user_message="hi", is_first_turn=True, session_id="s1"
    )

    assert result is not None
    assert "mem-keep" in result["context"]
    assert "The project may change direction." not in result["context"]
    assert "mem-legacy-hypothesis" not in result["context"]


def test_bootstrap_inject_omits_retrieval_footer(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(tmp_path, _today(), _block(block_id="mem-1", body="Bootstrap fact."))

    result = _prefetch_ctx(digest, monkeypatch,user_message="hi", is_first_turn=True, session_id="s1")

    assert result is not None
    assert "Memory / recent" in result["context"] or "Memory / entity" in result["context"]
    assert "Retrieval (progressive recall)" not in result["context"]
    assert "session_search(session_id=" not in result["context"]


def test_bootstrap_omits_span_ask_when_open_fact_exists(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(tmp_path, _today(), _block(block_id="mem-1", body="Casey canteen open.", valid_to="open"))

    result = _prefetch_ctx(digest, monkeypatch,user_message="hi", is_first_turn=True, session_id="s1")

    assert result is not None
    assert "Memory /" in result["context"]
    assert "## Span ask" not in result["context"]
    assert "## Span recall" not in result["context"]


def test_recall_index_by_day_includes_wrapup_phrase(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    secret_body = "SECRET_BODY_MUST_NOT_APPEAR_IN_INDEX"
    content = (
        _block(block_id="mem-wrap", body=secret_body)
        + "\n\n## Day wrap-up\n- NotGPT blocked; Xiaohongshu HTML cards shipped.\n"
    )
    _write_daily(tmp_path, _today(), content)
    ctx = digest.build_recall_injection_context()
    assert "NotGPT blocked; Xiaohongshu HTML cards shipped." in ctx
    assert secret_body not in ctx
    assert "SECRET_BODY" not in ctx
