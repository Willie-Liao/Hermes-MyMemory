from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from io import StringIO
from pathlib import Path


WEEKLY_ACTIONS_PATH = Path(__file__).with_name("weekly_actions.py")
BRIDGE = Path(__file__).with_name("bridge_cli.py")


def _load_actions():
    spec = importlib.util.spec_from_file_location(
        "memory_weekly_close_week_actions_test", WEEKLY_ACTIONS_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_bridge():
    spec = importlib.util.spec_from_file_location("memory_weekly_close_week_bridge_test", BRIDGE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_draft(tmp_path: Path, week: str, body: str = "# draft\n") -> Path:
    path = tmp_path / "memories" / "staging" / "weekly" / f"{week}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _write_reviewed(tmp_path: Path, week: str, body: str = "# reviewed\n") -> Path:
    path = tmp_path / "memories" / "staging" / "weekly" / f"{week} reviewed.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_close_week_blank_defaults_to_current_iso(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_actions()
    # Monday 2026-07-13 → ISO 2026-W29
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    _write_draft(tmp_path, "2026-W29", "# Weekly Memory Review — 2026-W29\n")

    result = actions.close_week(None)

    assert result["outcome"] == "closed"
    assert result["week"] == "2026-W29"
    assert Path(result["path"]).name == "2026-W29.md"
    assert (tmp_path / "memories/staging/weekly" / "2026-W29.md").exists()
    assert not (tmp_path / "memories/staging/weekly" / "2026-W29 reviewed.md").exists()
    from memory_staging import read_week_status

    assert read_week_status(tmp_path / "memories/staging/weekly" / "2026-W29.md") == "reviewed"


def test_close_week_anytime_when_enforce_sunday_false(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_actions()
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    _write_draft(tmp_path, "2026-W28")

    result = actions.close_week("2026-W28", enforce_sunday=False, today=date(2026, 7, 13))

    assert result["outcome"] == "closed"
    assert result["week"] == "2026-W28"


def test_close_week_sunday_only_when_enforce_sunday_on_weekday(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_actions()
    _write_draft(tmp_path, "2026-W29")

    result = actions.close_week(
        "2026-W29", enforce_sunday=True, today=date(2026, 7, 13)
    )

    assert result == {"outcome": "sunday_only", "week": "2026-W29"}
    assert (tmp_path / "memories/staging/weekly" / "2026-W29.md").exists()


def test_close_week_allows_sunday_with_enforce_sunday(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_actions()
    # Sunday 2026-07-12 → ISO 2026-W28
    _write_draft(tmp_path, "2026-W28")

    result = actions.close_week(
        "2026-W28", enforce_sunday=True, today=date(2026, 7, 12)
    )

    assert result["outcome"] == "closed"
    assert result["week"] == "2026-W28"
    assert (tmp_path / "memories/staging/weekly" / "2026-W28.md").exists()
    from memory_staging import read_week_status

    assert read_week_status(tmp_path / "memories/staging/weekly" / "2026-W28.md") == "reviewed"


def test_close_week_already_closed_when_reviewed_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_actions()
    _write_reviewed(tmp_path, "2026-W28")

    result = actions.close_week(
        "2026-W28", enforce_sunday=True, today=date(2026, 7, 12)
    )

    assert result == {"outcome": "already_closed", "week": "2026-W28"}


def test_close_week_no_draft(tmp_path, monkeypatch):
    """Usable digests but no draft → still no_draft (run update first)."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_actions()
    (tmp_path / "memories" / "staging" / "weekly").mkdir(parents=True)
    daily = tmp_path / "memories" / "staging" / "daily" / "2026-07-13.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text(
        "---\n"
        "id: mem-test-block\n"
        "type: fact\n"
        "entity: User\n"
        "confidence: high\n"
        "status: candidate\n"
        'sources: ["test"]\n'
        "---\n"
        "usable note\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))

    result = actions.close_week("2026-W29", today=date(2026, 7, 13))

    assert result == {"outcome": "no_draft", "week": "2026-W29"}


def test_close_week_empty_no_draft_writes_reviewed_stub(tmp_path, monkeypatch):
    """No draft and no usable digests → soft-close with empty Brief reviewed file."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_actions()
    (tmp_path / "memories" / "staging" / "weekly").mkdir(parents=True)
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))

    result = actions.close_week("2026-W29", today=date(2026, 7, 13))

    assert result["outcome"] == "closed"
    assert result["week"] == "2026-W29"
    assert result.get("empty_week") is True
    reviewed = tmp_path / "memories" / "staging" / "weekly" / "2026-W29.md"
    assert reviewed.exists()
    text = reviewed.read_text(encoding="utf-8")
    assert "No current news for this week" in text
    assert "week_status: reviewed" in text
    assert Path(result["path"]) == reviewed
    mark = actions.weekly._load_state()["week_open_marks"]["2026-W29"]
    assert mark["status"] == "closed"


def test_close_week_sets_closed_without_ask_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_actions()
    _write_draft(tmp_path, "2026-W28")
    state = actions.weekly._load_state()
    actions.weekly.ensure_week_open_mark(state, "2026-W28")
    actions.weekly._save_state(state)

    result = actions.close_week("2026-W28", enforce_sunday=False)

    assert result["outcome"] == "closed"
    mark = actions.weekly._load_state()["week_open_marks"]["2026-W28"]
    assert mark["status"] == "closed"
    assert mark["ask_pending"] is False


def test_bridge_dispatches_close_week(monkeypatch, capsys):
    """UI Close sends enforce_sunday False (anytime); bridge still forwards True if asked."""
    bridge = _load_bridge()
    captured = {}

    class FakeActions:
        @staticmethod
        def close_week(week_key=None, *, enforce_sunday=False, today=None):
            captured["week_key"] = week_key
            captured["enforce_sunday"] = enforce_sunday
            return {"outcome": "closed", "week": week_key or "2026-W29", "path": "/x"}

    monkeypatch.setattr(bridge, "_load_weekly_actions", lambda: FakeActions)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            json.dumps(
                {
                    "op": "close_week",
                    "args": {"week_key": "2026-W28", "enforce_sunday": False},
                }
            )
        ),
    )

    assert bridge.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["result"]["outcome"] == "closed"
    assert captured == {"week_key": "2026-W28", "enforce_sunday": False}

    captured.clear()
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            json.dumps(
                {
                    "op": "close_week",
                    "args": {"week_key": "2026-W28", "enforce_sunday": True},
                }
            )
        ),
    )
    assert bridge.main() == 0
    assert captured == {"week_key": "2026-W28", "enforce_sunday": True}


def test_bridge_dispatches_approve_and_purge_over_retention(monkeypatch, capsys):
    bridge = _load_bridge()
    captured = {}

    def fake_approve_purge(*, queue: bool, snapshots: bool):
        captured["queue"] = queue
        captured["snapshots"] = snapshots
        return {"purged_queue": 3, "purged_snapshots": 1}

    monkeypatch.setattr(bridge, "_approve_purge", fake_approve_purge)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            json.dumps(
                {
                    "op": "approve_and_purge_over_retention",
                    "args": {"queue": True, "snapshots": False},
                }
            )
        ),
    )

    assert bridge.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["result"] == {"purged_queue": 3, "purged_snapshots": 1}
    assert captured == {"queue": True, "snapshots": False}


def test_bridge_dispatches_purge_old_logs(monkeypatch, capsys):
    bridge = _load_bridge()
    captured = {}

    def fake_purge_old_logs(*, months: int):
        captured["months"] = months
        return {"purged_logs": 4}

    monkeypatch.setattr(bridge, "_purge_old_logs", fake_purge_old_logs)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            json.dumps(
                {
                    "op": "purge_old_logs",
                    "args": {"months": 6},
                }
            )
        ),
    )

    assert bridge.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["result"] == {"purged_logs": 4}
    assert captured == {"months": 6}
