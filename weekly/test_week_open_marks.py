from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path


WEEKLY_PATH = Path(__file__).with_name("weekly.py")
WEEKLY_ACTIONS_PATH = Path(__file__).with_name("weekly_actions.py")


def _load_weekly():
    spec = importlib.util.spec_from_file_location(
        "memory_weekly_week_open_marks_test", WEEKLY_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_actions():
    spec = importlib.util.spec_from_file_location(
        "memory_weekly_week_open_marks_actions_test", WEEKLY_ACTIONS_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ensure_week_open_mark_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    weekly = _load_weekly()
    state: dict = {}
    m1 = weekly.ensure_week_open_mark(state, "2026-W28")
    assert m1["status"] == "open"
    assert "opened_at" in m1
    opened = m1["opened_at"]
    m2 = weekly.ensure_week_open_mark(state, "2026-W28")
    assert m2["opened_at"] == opened
    assert state["week_open_marks"]["2026-W28"]["status"] == "open"


def test_mark_week_closed_clears_ask_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    weekly = _load_weekly()
    state: dict = {}
    weekly.ensure_week_open_mark(state, "2026-W28")
    # ask_pending kwarg is ignored; close always clears the sticky flag.
    weekly.mark_week_closed_in_state(state, "2026-W28", ask_pending=True)
    mark = state["week_open_marks"]["2026-W28"]
    assert mark["status"] == "closed"
    assert mark["ask_pending"] is False
    assert mark["closed_at"]
    assert mark.get("generate_in_flight") is False


def _write_usable_daily(tmp_path: Path, date_str: str, body: str = "day note") -> Path:
    path = tmp_path / "memories" / "staging" / "daily" / f"{date_str}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                "id: mem-day",
                "type: fact",
                "entity: Day",
                "confidence: high",
                "status: candidate",
                "sources: [session s1]",
                "---",
                body,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_generate_week_sets_open_mark(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_actions()
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    _write_usable_daily(tmp_path, "2026-07-13", "mid-week source")
    monkeypatch.setattr(
        actions.weekly,
        "_generate_weekly_content",
        lambda *_a, **_k: "# Weekly Memory Review — 2026-W29\n\nok\n",
    )
    monkeypatch.setattr(actions, "run_hot_health", lambda *, reason="bridge": {})

    result = actions.generate_week("2026-W29", reason="update")

    assert result["outcome"] == "generated"
    state = actions.weekly._load_state()
    assert state["week_open_marks"]["2026-W29"]["status"] == "open"


def test_run_weekly_sets_open_mark(tmp_path, monkeypatch):
    """Backlog `_run_weekly` must open marks (same as concrete generate_week)."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    weekly = _load_weekly()
    monkeypatch.setattr(weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    _write_usable_daily(tmp_path, "2026-07-06", "W28 source")
    monkeypatch.setattr(
        weekly,
        "_generate_weekly_content",
        lambda *_a, **_k: "# Weekly Memory Review — 2026-W28\n\n## Distill\n\nok\n",
    )

    weekly._run_weekly("cron")

    state = weekly._load_state()
    assert state["week_open_marks"]["2026-W28"]["status"] == "open"
    assert state["presentation"]["digest_fingerprints"]["2026-W28"]
    assert (tmp_path / "memories/staging/weekly" / "2026-W28.md").exists()


def _write_draft(tmp_path: Path, week: str, body: str = "# draft\n") -> Path:
    path = tmp_path / "memories" / "staging" / "weekly" / f"{week}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_skip_week_closed_mark_not_in_overdue_ask(tmp_path, monkeypatch):
    """Non-ask close (skip) must sync marks so process_overdue skips overnight ask."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_actions()
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    _write_draft(tmp_path, "2026-W28", "# Weekly Memory Review — 2026-W28\n")
    state = actions.weekly._load_state()
    actions.weekly.ensure_week_open_mark(state, "2026-W28")
    presentation = actions.weekly._presentation_state(state)
    presentation["active_week"] = "2026-W28"
    actions.weekly._save_state(state)

    result = actions.skip_week()
    assert result["outcome"] == "completed"
    mark = actions.weekly._load_state()["week_open_marks"]["2026-W28"]
    assert mark["status"] == "closed"
    assert mark["ask_pending"] is False

    overdue = actions.process_overdue_week_marks(today=date(2026, 7, 13))
    assert "2026-W28" not in overdue["ask_weeks"]
    assert "2026-W28" not in overdue["closed_weeks"]


def test_presentation_complete_closed_mark_not_in_overdue_ask(tmp_path, monkeypatch):
    """Presentation-complete path must sync marks without ask_pending."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    weekly = _load_weekly()
    monkeypatch.setattr(weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    _write_draft(tmp_path, "2026-W28", "# Weekly Memory Review — 2026-W28\n")
    state = weekly._load_state()
    weekly.ensure_week_open_mark(state, "2026-W28")
    presentation = weekly._presentation_state(state)
    presentation["active_week"] = "2026-W28"
    weekly._save_state(state)

    weekly.on_post_tool_call(
        tool_name="clarify",
        args={"question": "Staging memory review for 2026-W28"},
        result={
            "question": "Staging memory review for 2026-W28",
            "user_response": "skip this week",
        },
        status="success",
    )

    mark = weekly._load_state()["week_open_marks"]["2026-W28"]
    assert mark["status"] == "closed"
    assert mark["ask_pending"] is False

    actions = _load_actions()
    overdue = actions.process_overdue_week_marks(today=date(2026, 7, 13))
    assert "2026-W28" not in overdue["ask_weeks"]


def test_process_overdue_closes_draft_without_ask(tmp_path, monkeypatch):
    # today = Monday of W29; open mark W28; draft W28.md exists
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_actions()
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    _write_draft(tmp_path, "2026-W28", "# Weekly Memory Review — 2026-W28\n")
    state = actions.weekly._load_state()
    actions.weekly.ensure_week_open_mark(state, "2026-W28")
    actions.weekly._save_state(state)

    result = actions.process_overdue_week_marks(today=date(2026, 7, 13))

    assert "2026-W28" in result["closed_weeks"]
    assert result["ask_weeks"] == []
    closed = tmp_path / "memories/staging/weekly" / "2026-W28.md"
    assert closed.exists()
    assert "week_status: reviewed" in closed.read_text(encoding="utf-8")
    assert not (tmp_path / "memories/staging/weekly" / "2026-W28 reviewed.md").exists()
    mark = actions.weekly._load_state()["week_open_marks"]["2026-W28"]
    assert mark["status"] == "closed"
    assert mark["ask_pending"] is False


def test_process_overdue_no_draft_starts_generate_once(tmp_path, monkeypatch):
    # open mark W28; no draft; dailies present; stub kick / capture Thread
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_actions()
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    _write_usable_daily(tmp_path, "2026-07-06", "W28 source")
    state = actions.weekly._load_state()
    actions.weekly.ensure_week_open_mark(state, "2026-W28")
    actions.weekly._save_state(state)

    kicked: list[str] = []

    def fake_kick(week_key: str) -> None:
        kicked.append(week_key)

    monkeypatch.setattr(actions, "_kick_background_generate_week", fake_kick)

    first = actions.process_overdue_week_marks(today=date(2026, 7, 13))
    assert first["generate_started"] == ["2026-W28"]
    assert first["ask_weeks"] == []
    assert kicked == ["2026-W28"]
    mark = actions.weekly._load_state()["week_open_marks"]["2026-W28"]
    assert mark["status"] == "open"
    assert mark["generate_in_flight"] is True

    second = actions.process_overdue_week_marks(today=date(2026, 7, 13))
    assert second["generate_started"] == []
    assert kicked == ["2026-W28"]


def test_process_overdue_reviewed_only_syncs_closed(tmp_path, monkeypatch):
    # open mark W28; reviewed exists, no draft; even with dailies → close, no ask/generate
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_actions()
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    reviewed = tmp_path / "memories" / "staging" / "weekly" / "2026-W28 reviewed.md"
    reviewed.parent.mkdir(parents=True, exist_ok=True)
    reviewed.write_text("# reviewed\n", encoding="utf-8")
    _write_usable_daily(tmp_path, "2026-07-06", "W28 source")
    state = actions.weekly._load_state()
    actions.weekly.ensure_week_open_mark(state, "2026-W28")
    actions.weekly._save_state(state)

    kicked: list[str] = []
    monkeypatch.setattr(actions, "_kick_background_generate_week", kicked.append)

    result = actions.process_overdue_week_marks(today=date(2026, 7, 13))

    assert "2026-W28" in result["closed_weeks"]
    assert result["ask_weeks"] == []
    assert result["generate_started"] == []
    assert kicked == []
    mark = actions.weekly._load_state()["week_open_marks"]["2026-W28"]
    assert mark["status"] == "closed"
    assert mark["ask_pending"] is False


def test_process_overdue_empty_parks_without_ask(tmp_path, monkeypatch):
    # open overdue mark; no draft; no reviewed; no dailies → skipped_empty, no ask/generate
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_actions()
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    state = actions.weekly._load_state()
    actions.weekly.ensure_week_open_mark(state, "2026-W28")
    actions.weekly._save_state(state)

    kicked: list[str] = []
    monkeypatch.setattr(actions, "_kick_background_generate_week", kicked.append)

    result = actions.process_overdue_week_marks(today=date(2026, 7, 13))

    assert "2026-W28" in result["skipped_empty"]
    assert "2026-W28" in result["closed_weeks"]
    assert result["ask_weeks"] == []
    assert "2026-W28" not in result["generate_started"]
    assert kicked == []
    mark = actions.weekly._load_state()["week_open_marks"]["2026-W28"]
    assert mark["status"] == "closed"
    assert mark["ask_pending"] is False


def test_sunday_close_is_plugin_clock_not_cron():
    clock = Path(__file__).with_name("weekly_clock.py")
    assert clock.is_file()
    text = clock.read_text(encoding="utf-8")
    assert "leftover_ran" in text
    assert "last_sunday_close_week" in text
    assert "last_sunday_generate_week" in text
    assert "_previous_week_key" in text
    script = Path(__file__).resolve().parents[3] / "scripts" / "weekly-sunday-close.sh"
    assert not script.exists()


def test_generate_week_background_returns_while_run_lock_held(tmp_path, monkeypatch):
    """Background rescan must not sit on _run_lock or the serve queue stalls."""
    import threading

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_actions()
    _write_usable_daily(tmp_path, "2026-07-06", "W28 source")
    kicked: list[str] = []
    monkeypatch.setattr(actions, "_kick_background_generate_week", kicked.append)
    actions.weekly._run_lock.acquire()
    held = {"result": None}

    def _call() -> None:
        held["result"] = actions.generate_week(
            "2026-W28", reason="rescan", background=True
        )

    try:
        thread = threading.Thread(target=_call)
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive(), "generate_week blocked on _run_lock"
    finally:
        actions.weekly._run_lock.release()
        thread.join(timeout=2)

    assert held["result"] == {
        "outcome": "started",
        "week": "2026-W28",
        "generate_in_flight": True,
    }
    assert kicked == []


def test_generate_week_background_kicks_once(tmp_path, monkeypatch):
    import threading

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_actions()
    _write_usable_daily(tmp_path, "2026-07-06", "W28 source")
    kicked: list[str] = []
    monkeypatch.setattr(actions, "_kick_background_generate_week", kicked.append)
    first = actions.generate_week("2026-W28", reason="rescan", background=True)
    assert first["outcome"] == "started"
    assert first["generate_in_flight"] is True
    assert kicked == ["2026-W28"]
    mark = actions.weekly._load_state()["week_open_marks"]["2026-W28"]
    assert mark["generate_in_flight"] is True
    hold = threading.Event()
    dummy = threading.Thread(
        target=hold.wait,
        name="weekly-gen-2026-W28",
        daemon=True,
    )
    dummy.start()
    try:
        second = actions.generate_week("2026-W28", reason="rescan", background=True)
        assert second["outcome"] == "started"
        assert kicked == ["2026-W28"]
        _write_draft(tmp_path, "2026-W28")
        rows = actions.weekly._weeks_status_rows()
        row = next(r for r in rows if r["week"] == "2026-W28")
        assert row["generate_in_flight"] == "true"
    finally:
        hold.set()
        dummy.join(timeout=2)


def test_generate_week_background_rekicks_stale_in_flight(tmp_path, monkeypatch):
    """Flag without a live weekly-gen thread would freeze the Re-scan spinner."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_actions()
    _write_usable_daily(tmp_path, "2026-07-06", "W28 source")
    kicked: list[str] = []
    monkeypatch.setattr(actions, "_kick_background_generate_week", kicked.append)
    first = actions.generate_week("2026-W28", reason="rescan", background=True)
    assert kicked == ["2026-W28"]
    second = actions.generate_week("2026-W28", reason="rescan", background=True)
    assert second["outcome"] == "started"
    assert kicked == ["2026-W28", "2026-W28"]


def test_generate_week_background_returns_while_generate_runs(tmp_path, monkeypatch):
    """UI needs started before distill finishes so /api/weekly/update is not a 300s wait."""
    import time
    import threading

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_actions()
    _write_usable_daily(tmp_path, "2026-07-06", "W28 source")
    entered = threading.Event()

    def slow_content(*_a, **_k):
        entered.set()
        time.sleep(2.0)
        return None

    monkeypatch.setattr(actions.weekly, "_generate_weekly_content", slow_content)
    t0 = time.perf_counter()
    result = actions.generate_week("2026-W28", reason="rescan", background=True)
    elapsed = time.perf_counter() - t0
    assert result["outcome"] == "started"
    assert elapsed < 1.0
    assert entered.wait(timeout=3)
    mark = actions.weekly._load_state()["week_open_marks"]["2026-W28"]
    assert mark["generate_in_flight"] is True
    time.sleep(2.2)
