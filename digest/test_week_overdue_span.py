"""Digest no longer injects Weekly close A/B asks (Sunday cron owns close)."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

_mymemory = Path(__file__).resolve().parent.parent
_plugins_root = _mymemory.parent
_weekly_dir = _mymemory / "weekly"
for _p in (str(_mymemory), str(_plugins_root), str(_weekly_dir)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from conftest import load_plugin_module

WEEKLY_ACTIONS_PATH = _weekly_dir / "weekly_actions.py"


def _load_digest():
    return load_plugin_module("digest.py", "memory_digest_week_overdue_span_test")


def _load_actions():
    spec = importlib.util.spec_from_file_location(
        "memory_weekly_actions_overdue_span_test", WEEKLY_ACTIONS_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_daily(home: Path, date_str: str, body: str = "day note") -> None:
    daily = home / "memories" / "staging" / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    (daily / f"{date_str}.md").write_text(
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


def _write_draft(home: Path, week: str, body: str | None = None) -> Path:
    path = home / "memories" / "staging" / "weekly" / f"{week}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body or f"# Weekly Memory Review — {week}\n\nok\n", encoding="utf-8")
    return path


def _seed_overdue_open(home: Path, actions, *weeks: str) -> None:
    state = actions.weekly._load_state()
    for week in weeks:
        _write_draft(home, week)
        actions.weekly.ensure_week_open_mark(state, week)
    actions.weekly._save_state(state)


def test_build_recall_does_not_inject_weekly_close_note(tmp_path, monkeypatch):
    digest = _load_digest()
    actions = _load_actions()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    _write_daily(tmp_path, "2026-07-13")
    _seed_overdue_open(tmp_path, actions, "2026-W27", "2026-W28")

    ctx = digest.build_recall_injection_context(
        session_id="s1",
        decision="bootstrap",
        user_message="hi",
    )

    assert "## Weekly close note" not in ctx
    marks = actions.weekly._load_state()["week_open_marks"]
    # Chat inject must not close / set ask_pending; marks stay open until cron.
    assert marks["2026-W27"]["status"] == "open"
    assert marks["2026-W27"].get("ask_pending") is False


def test_build_recall_empty_without_tier1_dailies(tmp_path, monkeypatch):
    digest = _load_digest()
    actions = _load_actions()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    _seed_overdue_open(tmp_path, actions, "2026-W28")

    ctx = digest.build_recall_injection_context(
        session_id="s1",
        decision="bootstrap",
        user_message="hi",
    )

    assert ctx == ""
    assert "## Weekly close note" not in ctx


def test_on_pre_llm_skip_does_not_force_weekly_ask(tmp_path, monkeypatch):
    digest = _load_digest()
    actions = _load_actions()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    _seed_overdue_open(tmp_path, actions, "2026-W28")
    actions.process_overdue_week_marks(today=date(2026, 7, 13), kick_generate=False)
    marks = actions.weekly._load_state()["week_open_marks"]["2026-W28"]
    assert marks["status"] == "closed"
    assert marks["ask_pending"] is False

    result = digest.on_pre_llm_call(
        user_message="ok",
        is_first_turn=False,
        session_id="s1",
    )
    if result is not None:
        assert "## Weekly close note" not in result.get("context", "")


def test_process_overdue_ab_helpers_removed():
    actions = _load_actions()
    assert not hasattr(actions, "parse_overdue_week_replies")
    assert not hasattr(actions, "resolve_overdue_week_asks")
