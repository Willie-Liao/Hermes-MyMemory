from __future__ import annotations

import importlib.util
import json
from pathlib import Path


WEEKLY_ACTIONS_PATH = Path(__file__).with_name("weekly_actions.py")


def _load_actions():
    spec = importlib.util.spec_from_file_location(
        "memory_weekly_reopen_actions_test", WEEKLY_ACTIONS_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_reviewed(tmp_path: Path, week: str, body: str) -> Path:
    path = tmp_path / "memories" / "staging" / "weekly" / f"{week} reviewed.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _write_daily_block(tmp_path: Path, day: str, block_id: str, status: str) -> Path:
    path = tmp_path / "memories" / "staging" / "daily" / f"{day}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {block_id}\ntype: fact\nconfidence: high\nstatus: {status}\n"
        f"sources: [session s1]\n---\nbody {block_id}\n",
        encoding="utf-8",
    )
    return path


def _seed_closed_week_with_ledger(tmp_path: Path) -> tuple[Path, Path, Path]:
    promoted = _write_daily_block(tmp_path, "2026-06-16", "mem-20260616-a", "approved")
    discarded = _write_daily_block(tmp_path, "2026-06-17", "mem-20260617-b", "rejected")
    reviewed = _write_reviewed(
        tmp_path,
        "2026-W24",
        "# Weekly distill 2026-W24\n\n"
        "## Distill\n\n"
        "---\nid: evt-1\ntype: event\nrelated:\n"
        '  - "[1] mem-20260616-a"\n'
        "---\nKeep me [1].\n\n"
        "---\nid: evt-2\ntype: event\nrelated:\n"
        '  - "[2] mem-20260617-b"\n'
        "---\nNoise [2].\n\n"
        "## Brief\n\n"
        "Keep me [1]. Noise [2].\n\n"
        "## 8. Action ledger\n\n"
        "| ID / label | Source | Action | Hot target | Staging sync |\n"
        "|------------|--------|--------|------------|--------------|\n"
        "| cite-1 / keep me | Brief | promote | MEMORY.md | approved |\n"
        "| cite-2 / noise | Brief | discard | — | rejected |\n",
    )
    state_path = tmp_path / "memories" / "staging" / ".weekly-state.json"
    state_path.write_text(
        json.dumps(
            {
                "presentation": {
                    "completed_weeks": ["2026-W24"],
                    "tidy_completed_weeks": ["2026-W24"],
                    "tidy_pending_week": "",
                }
            }
        ),
        encoding="utf-8",
    )
    return promoted, discarded, reviewed


def test_reopen_week_renames_reviewed_to_draft_and_strips_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_actions()
    _seed_closed_week_with_ledger(tmp_path)

    result = actions.reopen_week("2026-W24")

    assert result["outcome"] == "reopened"
    assert result["week"] == "2026-W24"
    draft = tmp_path / "memories" / "staging" / "weekly" / "2026-W24.md"
    reviewed = tmp_path / "memories" / "staging" / "weekly" / "2026-W24 reviewed.md"
    assert draft.exists()
    assert not reviewed.exists()
    text = draft.read_text(encoding="utf-8")
    assert "## 8. Action ledger" not in text
    assert "Keep me [1]" in text
    assert "Noise [2]" in text


def test_reopen_week_reverses_promote_and_discard_to_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_actions()
    promoted, discarded, _ = _seed_closed_week_with_ledger(tmp_path)

    result = actions.reopen_week("2026-W24")

    assert result["outcome"] == "reopened"
    assert set(result["restored_blocks"]) == {"mem-20260616-a", "mem-20260617-b"}
    assert "status: candidate" in promoted.read_text(encoding="utf-8")
    assert "status: candidate" in discarded.read_text(encoding="utf-8")


def test_reopen_week_clears_tidy_completed_and_completed_weeks(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_actions()
    _seed_closed_week_with_ledger(tmp_path)

    actions.reopen_week("2026-W24")

    state = json.loads(
        (tmp_path / "memories" / "staging" / ".weekly-state.json").read_text(encoding="utf-8")
    )
    presentation = state["presentation"]
    assert "2026-W24" not in presentation.get("tidy_completed_weeks", [])
    assert "2026-W24" not in presentation.get("completed_weeks", [])


def test_reopen_week_does_not_edit_hot_memory_files(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_actions()
    _seed_closed_week_with_ledger(tmp_path)
    memories = tmp_path / "memories"
    memories.mkdir(parents=True, exist_ok=True)
    memory_md = memories / "MEMORY.md"
    user_md = memories / "USER.md"
    memory_md.write_text("# MEMORY\npromoted fact\n", encoding="utf-8")
    user_md.write_text("# USER\nprofile\n", encoding="utf-8")
    before_memory = memory_md.read_text(encoding="utf-8")
    before_user = user_md.read_text(encoding="utf-8")

    result = actions.reopen_week("2026-W24")

    assert result["outcome"] == "reopened"
    assert memory_md.read_text(encoding="utf-8") == before_memory
    assert user_md.read_text(encoding="utf-8") == before_user


def test_reopen_week_requires_reviewed_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_actions()
    draft = tmp_path / "memories" / "staging" / "weekly" / "2026-W24.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text("# draft only\n", encoding="utf-8")

    result = actions.reopen_week("2026-W24")

    assert result["outcome"] == "no_reviewed_file"
    assert result["week"] == "2026-W24"
    assert draft.exists()


def test_reopen_week_bad_week(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_actions()

    result = actions.reopen_week("not-a-week")

    assert result["outcome"] == "bad_week"
    assert result["week"] == "not-a-week"


def test_reopen_week_sets_open_mark_and_clears_ask(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_actions()
    _seed_closed_week_with_ledger(tmp_path)
    state = actions.weekly._load_state()
    actions.weekly.ensure_week_open_mark(state, "2026-W24")
    actions.weekly.mark_week_closed_in_state(
        state, "2026-W24", ask_pending=True
    )
    state["week_open_marks"]["2026-W24"]["ask_resolved"] = None
    actions.weekly._save_state(state)

    result = actions.reopen_week("2026-W24")

    assert result["outcome"] == "reopened"
    mark = actions.weekly._load_state()["week_open_marks"]["2026-W24"]
    assert mark["status"] == "open"
    assert mark["ask_pending"] is False
    assert mark["ask_resolved"] is None
    assert mark["closed_at"] is None
