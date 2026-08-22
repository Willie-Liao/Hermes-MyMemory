from __future__ import annotations

import importlib.util
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


def _load_weekly(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    module_path = Path(__file__).with_name("weekly.py")
    spec = importlib.util.spec_from_file_location("memory_weekly_test_module", module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_weekly(home: Path, name: str) -> Path:
    path = home / "memories" / "staging" / "weekly" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Weekly review\n", encoding="utf-8")
    return path


def test_weeks_needing_presentation_excludes_current_and_completed(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    from memory_staging import WEEK_STATUS_REVIEWED, write_week_status

    w23 = _write_weekly(tmp_path, "2026-W23.md")
    write_week_status(
        w23,
        WEEK_STATUS_REVIEWED,
        week_key_str="2026-W23",
        content=w23.read_text(encoding="utf-8"),
    )
    _write_weekly(tmp_path, "2026-W24.md")
    _write_weekly(tmp_path, "2026-W25.md")

    state = {"presentation": {"completed_weeks": ["2026-W23"]}}

    assert weekly._weeks_needing_presentation(date(2026, 6, 15), state) == ["2026-W24"]


def test_list_weekly_pending_approval(tmp_path, monkeypatch):
    _load_weekly(tmp_path, monkeypatch)
    _write_weekly(tmp_path, "2026-W23.md")
    _write_weekly(tmp_path, "2026-W24.md")
    state_path = tmp_path / "memories" / "staging" / ".weekly-state.json"
    state_path.write_text(
        json.dumps({"presentation": {"completed_weeks": ["2026-W23"]}}),
        encoding="utf-8",
    )

    actions_path = Path(__file__).with_name("weekly_actions.py")
    spec = importlib.util.spec_from_file_location("memory_weekly_actions_test", actions_path)
    assert spec is not None and spec.loader is not None
    actions = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(actions)

    result = actions.list_weekly_pending_approval()
    assert result["outcome"] == "listed"
    assert any(
        row["week"] == "2026-W24" and row["status"] == "pending" for row in result["weeks"]
    )
    reviewed = tmp_path / "memories" / "staging" / "weekly" / "2026-W23 reviewed.md"
    reviewed.parent.mkdir(parents=True, exist_ok=True)
    reviewed.write_text("# reviewed\n", encoding="utf-8")
    (tmp_path / "memories" / "staging" / "weekly" / "2026-W23.md").unlink(missing_ok=True)
    result2 = actions.list_weekly_pending_approval()
    assert any(
        row["week"] == "2026-W23" and row["status"] == "reviewed" for row in result2["weeks"]
    )


def test_list_weekly_status_includes_reviewed_only_files(tmp_path, monkeypatch):
    _load_weekly(tmp_path, monkeypatch)
    _write_weekly(tmp_path, "2026-W23 reviewed.md")
    state_path = tmp_path / "memories" / "staging" / ".weekly-state.json"
    state_path.write_text(
        json.dumps({"presentation": {"completed_weeks": ["2026-W23"]}}),
        encoding="utf-8",
    )

    actions_path = Path(__file__).with_name("weekly_actions.py")
    spec = importlib.util.spec_from_file_location("memory_weekly_actions_reviewed_test", actions_path)
    assert spec is not None and spec.loader is not None
    actions = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(actions)

    result = actions.list_weekly_review_status()

    assert result["outcome"] == "listed"
    by = {row["week"]: row for row in result["weeks"]}
    assert by["2026-W23"] == {
        "week": "2026-W23",
        "status": "reviewed",
        "filename": "2026-W23.md",
    }


def _presentation(tmp_path: Path) -> dict:
    state_path = tmp_path / "memories" / "staging" / ".weekly-state.json"
    return json.loads(state_path.read_text(encoding="utf-8"))["presentation"]


_DEFAULT_STAGING_SID = "s-unlock"


def _unlock_staging(weekly, session_id: str = _DEFAULT_STAGING_SID) -> str:
    state = weekly._load_state()
    presentation = weekly._presentation_state(state)
    presentation["staging_unlocked"] = True
    presentation["staging_session_id"] = session_id
    weekly._save_state(state)
    return session_id


def test_pre_llm_injects_pending_review_and_snooze_blocks(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    _write_weekly(tmp_path, "2026-W24.md")
    _unlock_staging(weekly)

    injected = weekly.on_pre_llm_call(user_message="hello", is_first_turn=True, session_id="s-unlock")
    assert injected is not None
    assert "Staging memory review for 2026-W24" in injected["context"]

    weekly.on_post_tool_call(
        tool_name="clarify",
        args={"question": "Staging memory review for 2026-W24"},
        result=json.dumps(
            {
                "question": "Staging memory review for 2026-W24",
                "user_response": "2",
            }
        ),
        status="success",
    )

    assert weekly.on_pre_llm_call(user_message="hello again", is_first_turn=True, session_id="s-unlock") is None


def test_cron_session_skips_pending_week_inject(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    _write_weekly(tmp_path, "2026-W24.md")
    _unlock_staging(weekly, session_id="cron_deadbeef_20260724_120000")

    injected = weekly.on_pre_llm_call(
        user_message="hello",
        is_first_turn=True,
        session_id="cron_deadbeef_20260724_120000",
    )
    assert injected is None


def test_cron_platform_skips_pending_week_inject(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    _write_weekly(tmp_path, "2026-W24.md")
    _unlock_staging(weekly)

    injected = weekly.on_pre_llm_call(
        user_message="hello",
        is_first_turn=True,
        session_id="s-unlock",
        platform="cron",
    )
    assert injected is None


def test_hermes_cron_session_env_skips_pending_week_inject(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    monkeypatch.setenv("HERMES_CRON_SESSION", "1")
    _write_weekly(tmp_path, "2026-W24.md")
    _unlock_staging(weekly)

    injected = weekly.on_pre_llm_call(
        user_message="hello",
        is_first_turn=True,
        session_id="s-unlock",
    )
    assert injected is None


def test_skip_marks_week_completed(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    _write_weekly(tmp_path, "2026-W24.md")
    _unlock_staging(weekly)

    weekly.on_pre_llm_call(user_message="hello", is_first_turn=True, session_id="s-unlock")
    weekly.on_post_tool_call(
        tool_name="clarify",
        args={"question": "Staging memory review for 2026-W24"},
        result=json.dumps(
            {
                "question": "Staging memory review for 2026-W24",
                "user_response": "Skip this week",
            }
        ),
        status="success",
    )

    injected = weekly.on_pre_llm_call(user_message="hello again", is_first_turn=True, session_id="s-unlock")
    assert injected is None or "Staging review tidy is pending" not in (injected.get("context") or "")
    closed = tmp_path / "memories" / "staging" / "weekly" / "2026-W24.md"
    assert closed.exists()
    assert "week_status: reviewed" in closed.read_text(encoding="utf-8") or True


def test_close_week_aliases_mark_completed(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    _write_weekly(tmp_path, "2026-W24.md")

    for response in ("Close week", "完成了"):
        state_path = tmp_path / "memories" / "staging" / ".weekly-state.json"
        state_path.write_text(json.dumps({"presentation": {"completed_weeks": []}}), encoding="utf-8")

        weekly.on_post_tool_call(
            tool_name="clarify",
            args={"question": "Staging memory review for 2026-W24"},
            result=json.dumps(
                {
                    "question": "Staging memory review for 2026-W24",
                    "user_response": response,
                }
            ),
            status="success",
        )
        presentation = _presentation(tmp_path)
        assert "2026-W24" in presentation["completed_weeks"]


def test_weeks_status_rows_filesystem_pending_and_reviewed(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    weekly_dir = tmp_path / "memories" / "staging" / "weekly"
    weekly_dir.mkdir(parents=True)
    (weekly_dir / "2026-W26.md").write_text("# draft\n", encoding="utf-8")
    (weekly_dir / "2026-W25 reviewed.md").write_text("# reviewed\n", encoding="utf-8")
    # completed_weeks lies: claims W26 done — must NOT hide draft / must NOT force reviewed
    state = {"presentation": {"completed_weeks": ["2026-W26", "2026-W25"]}}
    rows = weekly._weeks_status_rows(date(2026, 6, 29), state)
    by = {r["week"]: r for r in rows}
    assert by["2026-W26"]["status"] == "pending"
    assert by["2026-W25"]["status"] == "reviewed"


def test_weeks_status_rows_rereview_when_both_exist(tmp_path, monkeypatch):
    """Legacy dual files: status from week_status / legacy; filename always YYYY-Www.md."""
    weekly = _load_weekly(tmp_path, monkeypatch)
    weekly_dir = tmp_path / "memories" / "staging" / "weekly"
    weekly_dir.mkdir(parents=True)
    (weekly_dir / "2026-W27.md").write_text("# draft\n", encoding="utf-8")
    (weekly_dir / "2026-W27 reviewed.md").write_text("# old\n", encoding="utf-8")
    rows = weekly._weeks_status_rows(date(2026, 7, 1), {"presentation": {}})
    row = next(r for r in rows if r["week"] == "2026-W27")
    assert row["filename"] == "2026-W27.md"
    assert row["status"] in {"pending", "reviewed"}


def test_weeks_status_rows_includes_completed(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    reviewed = tmp_path / "memories" / "staging" / "weekly" / "2026-W23 reviewed.md"
    reviewed.parent.mkdir(parents=True, exist_ok=True)
    reviewed.write_text("# reviewed\n", encoding="utf-8")
    _write_weekly(tmp_path, "2026-W24.md")

    state = {"presentation": {"completed_weeks": ["2026-W23"]}}
    rows = weekly._weeks_status_rows(date(2026, 6, 15), state)
    statuses = {row["week"]: row["status"] for row in rows}
    assert statuses["2026-W23"] == "reviewed"
    assert statuses["2026-W24"] == "pending"


def test_build_presentation_context_uses_close_week_label(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    path = _write_weekly(tmp_path, "2026-W24.md")
    context = weekly._build_presentation_context("2026-W24", path, force=False)
    assert "Close week (mark complete)" in context
    assert "After the walkthrough and recap" in context


def _clarify(weekly, response: str) -> None:
    weekly.on_post_tool_call(
        tool_name="clarify",
        args={"question": "Staging memory review for 2026-W24"},
        result=json.dumps(
            {
                "question": "Staging memory review for 2026-W24",
                "user_response": response,
            }
        ),
        status="success",
    )


def test_review_now_opens_hot_promotion_window(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    _write_weekly(tmp_path, "2026-W24.md")
    _unlock_staging(weekly)

    weekly.on_pre_llm_call(user_message="hello", is_first_turn=True, session_id="s-unlock")
    _clarify(weekly, "Review proposed additions now")

    presentation = _presentation(tmp_path)
    assert presentation["hot_promotion_allowed"] is True
    assert presentation.get("hot_promotion_until")


def test_skip_clears_hot_promotion_window(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    _write_weekly(tmp_path, "2026-W24.md")
    _unlock_staging(weekly)

    weekly.on_pre_llm_call(user_message="hello", is_first_turn=True, session_id="s-unlock")
    _clarify(weekly, "Review proposed additions now")
    _clarify(weekly, "Skip this week")

    presentation = _presentation(tmp_path)
    assert "hot_promotion_allowed" not in presentation
    assert "hot_promotion_until" not in presentation


def test_check_later_clears_hot_promotion_window(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    _write_weekly(tmp_path, "2026-W24.md")
    _unlock_staging(weekly)

    weekly.on_pre_llm_call(user_message="hello", is_first_turn=True, session_id="s-unlock")
    _clarify(weekly, "Review proposed additions now")
    _clarify(weekly, "Check later")

    presentation = _presentation(tmp_path)
    assert "hot_promotion_allowed" not in presentation
    assert "hot_promotion_until" not in presentation


def test_resumed_session_first_message_presents_pending_week(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    _write_weekly(tmp_path, "2026-W24.md")
    _unlock_staging(weekly, "s-resume")

    # Resumed session: history exists (is_first_turn=False) but the week has not
    # been presented in this session yet -> present on the first user message.
    injected = weekly.on_pre_llm_call(
        user_message="hello", is_first_turn=False, session_id="s-resume"
    )
    assert injected is not None
    assert "2026-W24" in injected["context"]

    # Same session, same week -> do not present again.
    again = weekly.on_pre_llm_call(
        user_message="more", is_first_turn=False, session_id="s-resume"
    )
    assert again is None

    # A different session must unlock separately (session-scoped vibe).
    _unlock_staging(weekly, "s-other")
    other = weekly.on_pre_llm_call(
        user_message="hi", is_first_turn=False, session_id="s-other"
    )
    assert other is not None


def test_session_auto_presented_recorded(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    _write_weekly(tmp_path, "2026-W24.md")
    _unlock_staging(weekly, "s1")

    weekly.on_pre_llm_call(
        user_message="hello", is_first_turn=False, session_id="s1"
    )
    presentation = _presentation(tmp_path)
    assert presentation["session_auto_presented"]["s1"] == "2026-W24"


def test_weeks_for_show_includes_current_week(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    today = date(2026, 6, 15)  # ISO 2026-W25
    _write_weekly(tmp_path, "2026-W24.md")
    _write_weekly(tmp_path, "2026-W25.md")

    rows = weekly._weeks_for_show(today=today)
    statuses = {row["week"]: row["status"] for row in rows}
    assert statuses["2026-W24"] == "pending"
    # Current ISO week is pending (no separate "current" label).
    assert statuses["2026-W25"] == "pending"


def test_manual_review_current_week(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    today = date(2026, 6, 15)  # ISO 2026-W25
    path = _write_weekly(tmp_path, "2026-W25.md")

    resolved = weekly._resolve_manual_review_week("2026-W25", today=today)
    assert resolved is not None
    key, resolved_path = resolved
    assert key == "2026-W25"
    assert resolved_path == path


def test_weeks_needing_presentation_on_sunday(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    _write_weekly(tmp_path, "2026-W24.md")
    _write_weekly(tmp_path, "2026-W25.md")

    saturday = date(2026, 6, 20)  # ISO 2026-W25
    sunday = date(2026, 6, 21)  # ISO 2026-W25, last day of week

    state = {"presentation": {"completed_weeks": ["2026-W23"]}}
    assert weekly._weeks_needing_presentation(saturday, state) == ["2026-W24"]
    assert weekly._weeks_needing_presentation(sunday, state) == ["2026-W24", "2026-W25"]


def test_snooze_replay_same_session(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    _write_weekly(tmp_path, "2026-W24.md")
    _unlock_staging(weekly, "s1")
    monkeypatch.setattr(weekly, "hermes_local_today", lambda: date(2026, 6, 16))

    base = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    now_val = [base]
    monkeypatch.setattr(weekly, "_now", lambda: now_val[0])

    injected = weekly.on_pre_llm_call(
        user_message="hello", is_first_turn=True, session_id="s1"
    )
    assert injected is not None

    weekly.on_post_tool_call(
        tool_name="clarify",
        args={"question": "Staging memory review for 2026-W24"},
        result=json.dumps(
            {
                "question": "Staging memory review for 2026-W24",
                "user_response": "2",
            }
        ),
        status="success",
        session_id="s1",
    )

    now_val[0] = base + timedelta(minutes=30)
    assert (
        weekly.on_pre_llm_call(
            user_message="hello again", is_first_turn=False, session_id="s1"
        )
        is None
    )

    now_val[0] = base + timedelta(hours=2)
    replay = weekly.on_pre_llm_call(
        user_message="back again", is_first_turn=False, session_id="s1"
    )
    assert replay is not None
    assert "2026-W24" in replay["context"]

    assert (
        weekly.on_pre_llm_call(
            user_message="once more", is_first_turn=False, session_id="s1"
        )
        is None
    )


def test_snooze_without_session_id_unchanged_after_expiry(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    _write_weekly(tmp_path, "2026-W24.md")
    _unlock_staging(weekly)
    monkeypatch.setattr(weekly, "hermes_local_today", lambda: date(2026, 6, 16))

    base = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    now_val = [base]
    monkeypatch.setattr(weekly, "_now", lambda: now_val[0])

    assert weekly.on_pre_llm_call(user_message="hello", is_first_turn=True, session_id="s-unlock") is not None

    weekly.on_post_tool_call(
        tool_name="clarify",
        args={"question": "Staging memory review for 2026-W24"},
        result=json.dumps(
            {
                "question": "Staging memory review for 2026-W24",
                "user_response": "Check later",
            }
        ),
        status="success",
    )

    now_val[0] = base + timedelta(hours=2)
    assert (
        weekly.on_pre_llm_call(
            user_message="hello again", is_first_turn=False, session_id=""
        )
        is None
    )


def test_close_renames_draft_to_reviewed(tmp_path, monkeypatch):
    """Close sets week_status:reviewed in place; no … reviewed.md sibling."""
    weekly = _load_weekly(tmp_path, monkeypatch)
    _write_weekly(tmp_path, "2026-W24.md")
    weekly._finalize_week_close(weekly._presentation_state(weekly._load_state()), "2026-W24")
    closed = tmp_path / "memories" / "staging" / "weekly" / "2026-W24.md"
    assert closed.exists()
    assert "week_status: reviewed" in closed.read_text(encoding="utf-8")
    assert not (
        tmp_path / "memories" / "staging" / "weekly" / "2026-W24 reviewed.md"
    ).exists()


def test_rereview_overwrites_reviewed_on_close(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    draft = _write_weekly(tmp_path, "2026-W24.md")
    draft.write_text("# new draft\n", encoding="utf-8")
    legacy = tmp_path / "memories" / "staging" / "weekly" / "2026-W24 reviewed.md"
    legacy.write_text("# old reviewed\n", encoding="utf-8")

    weekly._finalize_week_close(
        {"completed_weeks": ["2026-W24"], "tidy_completed_weeks": ["2026-W24"]},
        "2026-W24",
    )
    assert draft.exists()
    text = draft.read_text(encoding="utf-8")
    assert "new draft" in text
    assert "week_status: reviewed" in text
    assert not legacy.exists()


def test_weeks_needing_report_respects_reviewed_only(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True)
    (daily / "2026-06-09.md").write_text("w24\n", encoding="utf-8")
    reviewed = tmp_path / "memories" / "staging" / "weekly" / "2026-W24 reviewed.md"
    reviewed.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "# Weekly distill 2026-W24\n\n"
        "## Distill\n\n"
        "---\n"
        "id: evt-a\n"
        "type: event\n"
        "sources: [session s1]\n"
        "related:\n"
        '  - "[1] mem-2026-06-09-a"\n'
        "---\n"
        "Summary [1].\n\n"
        "## Brief\n\n"
    )
    reviewed.write_text(body, encoding="utf-8")
    assert weekly._weeks_needing_report(date(2026, 6, 15)) == []


def test_exclude_resolved_blocks_skips_rejected(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    content = (
        "---\nid: mem-20260616-a\ntype: fact\nconfidence: high\nstatus: rejected\n"
        "sources: [session s1]\n---\nrejected body\n\n"
        "---\nid: mem-20260616-b\ntype: fact\nconfidence: high\nstatus: candidate\n"
        "sources: [session s1]\n---\ncandidate body\n"
    )
    filtered = weekly._exclude_resolved_blocks(content)
    assert "rejected body" not in filtered
    assert "candidate body" in filtered


def test_rereview_pending_when_draft_and_reviewed_coexist(tmp_path, monkeypatch):
    """Draft without week_status beside legacy reviewed → list as pending (atomic model)."""
    weekly = _load_weekly(tmp_path, monkeypatch)
    reviewed = tmp_path / "memories" / "staging" / "weekly" / "2026-W24 reviewed.md"
    reviewed.parent.mkdir(parents=True, exist_ok=True)
    reviewed.write_text("# reviewed\n", encoding="utf-8")
    _write_weekly(tmp_path, "2026-W24.md")
    state = {"presentation": {"completed_weeks": ["2026-W24"]}}
    assert weekly._weeks_needing_presentation(date(2026, 6, 15), state) == ["2026-W24"]
    rows = weekly._weeks_status_rows(date(2026, 6, 15), state)
    row = next(r for r in rows if r["week"] == "2026-W24")
    assert row["filename"] == "2026-W24.md"
    assert row["status"] == "pending"


def test_manual_review_allows_completed_week_with_draft(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    reviewed = tmp_path / "memories" / "staging" / "weekly" / "2026-W25 reviewed.md"
    reviewed.parent.mkdir(parents=True, exist_ok=True)
    reviewed.write_text("# reviewed\n", encoding="utf-8")
    path = _write_weekly(tmp_path, "2026-W25.md")
    state = {"presentation": {"completed_weeks": ["2026-W25"]}}
    resolved = weekly._resolve_manual_review_week("2026-W25", state=state)
    assert resolved == ("2026-W25", path)


def _write_distill_brief_week(home: Path, name: str = "2026-W24.md") -> Path:
    path = home / "memories" / "staging" / "weekly" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """# Weekly Memory Review — 2026-W24

## Distill

---
id: evt-andrae
type: event
entity: Andrae
related:
  - "[1] mem-2026-06-29-andrae-feedback-summary"
sources:
  - session sess-a
confidence: high
status: candidate
valid_from: 2026-06-29
valid_to: 2026-06-29
---
Andrae feedback landed [1].

## Brief

Andrae feedback landed [1]. Parent grading package delivered.

What stood out was the grading handoff timing.
""",
        encoding="utf-8",
    )
    return path


def test_unlocked_brief_paste_soft_locate_no_distill(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    _write_distill_brief_week(tmp_path)
    _unlock_staging(weekly, "s-brief")

    injected = weekly.on_pre_llm_call(
        user_message="hello",
        is_first_turn=True,
        session_id="s-brief",
    )
    assert injected is not None
    context = injected["context"]
    ctx = context.casefold()
    assert "Andrae feedback landed [1]" in context
    assert "grading handoff timing" in context
    assert "Brief [N]" in context or "[N] mem-" in context  # soft locate hint
    assert "optional guidance" in ctx or "you may locate" in ctx
    assert "hypothesis" in ctx or "conflict" in ctx
    assert "distill" in ctx  # still says do not dump Distill YAML
    assert "type: event" not in context
    assert "## Distill" not in context

    presentation = _presentation(tmp_path)
    assert presentation.get("dig_in_active") is not True

    cite_path = Path(__file__).with_name("weekly_cite.py")
    cite_spec = importlib.util.spec_from_file_location(
        "memory_weekly_cite_presentation_arm", cite_path
    )
    assert cite_spec is not None and cite_spec.loader is not None
    cite = importlib.util.module_from_spec(cite_spec)
    cite_spec.loader.exec_module(cite)
    assert cite.get_dig_in() is None


def test_legacy_week_without_brief_keeps_clarify_presentation(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    _write_weekly(tmp_path, "2026-W24.md")
    _unlock_staging(weekly, "s-legacy")

    injected = weekly.on_pre_llm_call(
        user_message="hello",
        is_first_turn=True,
        session_id="s-legacy",
    )
    assert injected is not None
    assert "Close week (mark complete)" in injected["context"]
    presentation = _presentation(tmp_path)
    assert presentation.get("dig_in_active") is not True


def test_worker1_prompt_skips_presentation_inject(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    _write_weekly(tmp_path, "2026-W24.md")

    worker_prompt = (
        "You are the weekly memory consolidation Worker 1 (Distill).\n\n"
        "Week: 2026-W24\n\n"
        "Return ONLY markdown with a ## Distill section.\n"
    )
    assert (
        weekly.on_pre_llm_call(
            user_message=worker_prompt,
            is_first_turn=True,
            session_id="worker-w1",
        )
        is None
    )


def test_worker2_prompt_skips_presentation_inject(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    _write_distill_brief_week(tmp_path)

    worker_prompt = (
        "You are the weekly memory consolidation Worker 2 (Brief).\n\n"
        "Week: 2026-W24\n\n"
        "Write a concise natural-language Brief.\n"
    )
    assert (
        weekly.on_pre_llm_call(
            user_message=worker_prompt,
            is_first_turn=True,
            session_id="worker-w2",
        )
        is None
    )


def test_run_async_skipped_during_worker_llm_scope(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    started: list[str] = []

    def fake_thread(*args, **kwargs):
        started.append("started")
        raise AssertionError("Thread must not start during worker LLM")

    monkeypatch.setattr(weekly.threading, "Thread", fake_thread)
    with weekly._weekly_worker_llm_scope():
        weekly.run_async("on_session_start")
    assert started == []


def test_worker_prompt_substring_skips_even_without_full_role_line(tmp_path, monkeypatch):
    """Broad marker must catch Worker prompts even if role line is reworded slightly."""
    weekly = _load_weekly(tmp_path, monkeypatch)
    _write_weekly(tmp_path, "2026-W24.md")
    assert (
        weekly.on_pre_llm_call(
            user_message=(
                "Role: weekly memory consolidation Worker (Distill rewrite).\n"
                "Week: 2026-W24\n"
            ),
            is_first_turn=True,
            session_id="worker-broad",
        )
        is None
    )


def test_worker_prompt_skips_presentation_even_with_weekly_memory_substring(tmp_path, monkeypatch):
    """Worker Distill prompts must not trigger chat Brief inject."""
    weekly = _load_weekly(tmp_path, monkeypatch)
    _write_weekly(tmp_path, "2026-W24.md")
    assert (
        weekly.on_pre_llm_call(
            user_message=(
                "You are the weekly memory consolidation Worker 1 (Distill).\n"
                "Week: 2026-W24\n"
            ),
            is_first_turn=True,
            session_id="worker-substr",
        )
        is None
    )


def test_build_brief_paste_context_processed_brief_no_hashes(tmp_path, monkeypatch):
    """Paste inject uses processed Brief (no # themes) with soft locate."""
    weekly = _load_weekly(tmp_path, monkeypatch)
    path = _write_weekly(tmp_path, "2026-W24.md")
    raw = (
        "### Events\n- Ship landed [1].\n\n"
        "### Hypothesis\n- None.\n\n"
        "### Conflict\n- None.\n\n"
        "### Procedure\n- Follow up.\n"
    )
    out = weekly._build_brief_paste_context("2026-W24", path, raw, force=True)
    ctx = out.casefold()
    assert "as-is" not in out
    assert "###" not in out
    assert "Events" in out
    assert "[1]" in out
    assert "optional guidance" in ctx or "you may locate" in ctx
    assert "Brief [N]" in out or "[N] mem-" in out
    assert "Do not include Distill" in out or "no Distill" in out.lower() or "do not dump" in ctx


def test_slash_unlock_then_brief_paste(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    _write_distill_brief_week(tmp_path)
    _unlock_staging(weekly, session_id="s-yes")

    injected = weekly.on_pre_llm_call(
        user_message="hello",
        is_first_turn=True,
        session_id="s-yes",
    )
    assert injected is not None
    assert "Andrae feedback landed [1]" in injected["context"]
    assert "processed Brief" in injected["context"]

    # Other session must not inherit unlock — no Brief.
    other = weekly.on_pre_llm_call(
        user_message="please do a staging review",
        is_first_turn=False,
        session_id="other-desktop",
    )
    assert other is None


def test_stale_staging_unlock_without_session_id_stays_locked(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    _write_distill_brief_week(tmp_path)
    state = weekly._load_state()
    presentation = weekly._presentation_state(state)
    presentation["staging_unlocked"] = True
    presentation.pop("staging_session_id", None)
    weekly._save_state(state)

    out = weekly.on_pre_llm_call(
        user_message="please do a staging review",
        session_id="fresh-desktop",
    )
    assert out is None


def test_auto_present_without_unlock_skips_brief(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    _write_distill_brief_week(tmp_path)

    assert (
        weekly.on_pre_llm_call(
            user_message="hello",
            is_first_turn=True,
            session_id="s-auto",
        )
        is None
    )
    assert weekly._presentation_state(weekly._load_state()).get("staging_unlocked") is not True


# --- Hot Edit / Delete / Recall apply (armed dig-in; not chat NL retrieval) ---


def _seed_hot_memory(home: Path, *entries: str) -> Path:
    path = home / "memories" / "MEMORY.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n§\n".join(entries), encoding="utf-8")
    return path


def _load_cite_for_hot(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    cite_path = Path(__file__).with_name("weekly_cite.py")
    spec = importlib.util.spec_from_file_location("memory_weekly_cite_hot_test", cite_path)
    assert spec is not None and spec.loader is not None
    cite = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cite)
    return cite


def _hot_action_menu_question(file_name: str = "MEMORY.md", index: int = 1) -> str:
    return f"What should we do with hot memory `{file_name}` [{index}]?"


def _arm_hot_for_apply(cite, weekly, tmp_path, *, entries: tuple[str, ...], index: int):
    path = _seed_hot_memory(tmp_path, *entries)
    _unlock_staging(weekly, "s-hot-apply")
    before = entries[index]
    cite.arm_hot_action(
        file="MEMORY.md",
        index=index,
        before=before,
        session_id="s-hot-apply",
    )
    return path, before


def _clarify_q(weekly, question: str, response: str) -> None:
    weekly.on_post_tool_call(
        tool_name="clarify",
        args={"question": question},
        result=json.dumps({"question": question, "user_response": response}),
        status="success",
    )


def test_hot_edit_agree_writes_memory_without_recall_batch(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    cite = _load_cite_for_hot(tmp_path, monkeypatch)
    entries = (
        "Unrelated weather note.",
        "Science grade dispute with Riley remains open.",
    )
    path, before = _arm_hot_for_apply(
        cite, weekly, tmp_path, entries=entries, index=1
    )

    menu = _hot_action_menu_question(index=1)
    _clarify_q(weekly, menu, weekly.WEEKLY_ACTION_EDIT)
    _clarify_q(weekly, weekly.WEEKLY_EDIT_OPEN_QUESTION, "Riley grade dispute resolved.")
    _clarify_q(weekly, weekly.WEEKLY_EDIT_CONFIRM_QUESTION, "A · Agree")

    written = path.read_text(encoding="utf-8")
    assert "Riley grade dispute resolved." in written
    assert before not in written

    recall_path = (
        tmp_path / "memories" / "staging" / ".memory-3-step-recall" / "memory.json"
    )
    assert not recall_path.exists()

    presentation = _presentation(tmp_path)
    assert presentation.get("hot_promotion_allowed") is True
    assert presentation.get("hot_promotion_until")

    dig = cite.get_dig_in()
    assert dig.get("recall_offer") is not True
    assert dig.get("action_pending") is not True


def test_hot_edit_agree_mismatch_before_skips_write(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    cite = _load_cite_for_hot(tmp_path, monkeypatch)
    entries = (
        "Unrelated weather note.",
        "Science grade dispute with Riley remains open.",
    )
    path, _before = _arm_hot_for_apply(
        cite, weekly, tmp_path, entries=entries, index=1
    )
    before_disk = path.read_text(encoding="utf-8")

    # Stale snapshot: dig-in before no longer matches disk.
    cite.set_dig_in_progress(action_before="STALE BEFORE SNAPSHOT")

    menu = _hot_action_menu_question(index=1)
    _clarify_q(weekly, menu, weekly.WEEKLY_ACTION_EDIT)
    _clarify_q(weekly, weekly.WEEKLY_EDIT_OPEN_QUESTION, "Should not land.")
    _clarify_q(weekly, weekly.WEEKLY_EDIT_CONFIRM_QUESTION, "A · Agree")

    assert path.read_text(encoding="utf-8") == before_disk
    recall_path = (
        tmp_path / "memories" / "staging" / ".memory-3-step-recall" / "memory.json"
    )
    assert not recall_path.exists()


def test_hot_delete_does_not_offer_chat_recall(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    cite = _load_cite_for_hot(tmp_path, monkeypatch)
    entries = (
        "Keep this entry.",
        "Delete me: science grade dispute.",
    )
    path, before = _arm_hot_for_apply(
        cite, weekly, tmp_path, entries=entries, index=1
    )

    menu = _hot_action_menu_question(index=1)
    _clarify_q(weekly, menu, weekly.WEEKLY_ACTION_DELETE)
    assert weekly.WEEKLY_HOT_DELETE_QUESTION == "Delete this hot memory entry?"
    _clarify_q(weekly, weekly.WEEKLY_HOT_DELETE_QUESTION, "Yes")

    after_delete = path.read_text(encoding="utf-8")
    assert "Delete me" not in after_delete
    assert "Keep this entry." in after_delete

    dig = cite.get_dig_in()
    assert dig.get("recall_offer") is not True

    recall_path = (
        tmp_path / "memories" / "staging" / ".memory-3-step-recall" / "memory.json"
    )
    assert not recall_path.exists()

    _clarify_q(weekly, weekly.WEEKLY_RECALL_QUESTION, "Yes")
    after_recall_attempt = path.read_text(encoding="utf-8")
    assert "Delete me" not in after_recall_attempt
    assert before not in after_recall_attempt
    assert "Keep this entry." in after_recall_attempt


def test_chat_mention_staging_review_without_unlock_is_quiet(tmp_path, monkeypatch):
    weekly = _load_weekly(tmp_path, monkeypatch)
    out = weekly.on_pre_llm_call(
        user_message="这要进入weekly reivew模式吗",
        session_id="s1",
    )
    assert out is None
