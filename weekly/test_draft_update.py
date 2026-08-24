from __future__ import annotations

import importlib.util
import json
import time
from datetime import date
from pathlib import Path


WEEKLY_ACTIONS_PATH = Path(__file__).with_name("weekly_actions.py")


def _load_weekly_actions():
    spec = importlib.util.spec_from_file_location(
        "memory_weekly_draft_update_actions_test", WEEKLY_ACTIONS_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _candidate_block(body: str = "usable note", *, status: str = "candidate") -> str:
    return (
        "---\n"
        "id: mem-test-block\n"
        "type: fact\n"
        "entity: User\n"
        "confidence: high\n"
        f"status: {status}\n"
        'sources: ["test"]\n'
        "---\n"
        f"{body}\n"
    )


def _write_daily(tmp_path: Path, day: str, body: str = "daily note") -> Path:
    path = tmp_path / "memories" / "staging" / "daily" / f"{day}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Plain strings without frontmatter are not usable digests; wrap for callers
    # that pass a short body unless they already include YAML blocks.
    text = body if body.strip().startswith("---") or not body.strip() else _candidate_block(body)
    path.write_text(text, encoding="utf-8")
    return path


def test_generate_week_allows_current_iso_week(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_weekly_actions()
    # Monday 2026-07-13 is ISO 2026-W29
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    _write_daily(tmp_path, "2026-07-13", "mid-week source")
    monkeypatch.setattr(
        actions.weekly,
        "_generate_weekly_content",
        lambda *_a, **_k: "# Weekly Memory Review — 2026-W29\n\nok\n",
    )
    monkeypatch.setattr(actions, "run_hot_health", lambda *, reason="bridge": {})

    result = actions.generate_week(None, reason="update")

    assert result["outcome"] == "generated"
    assert result["week"] == "2026-W29"
    assert (tmp_path / "memories/staging/weekly" / "2026-W29.md").exists()


def test_generate_week_purges_before_content(tmp_path, monkeypatch):
    """generate_week must call orphan purge before _generate_weekly_content."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_weekly_actions()
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    _write_daily(tmp_path, "2026-07-13", "mid-week source")

    order: list[str] = []

    def fake_purge() -> None:
        order.append("purge")

    def fake_gen(*_a, **_k) -> str:
        order.append("gen")
        return "# Weekly Memory Review — 2026-W29\n\nok\n"

    monkeypatch.setattr(actions, "_purge_orphan_daily_blocks_before_generate", fake_purge)
    monkeypatch.setattr(actions.weekly, "_generate_weekly_content", fake_gen)
    monkeypatch.setattr(actions, "run_hot_health", lambda *, reason="bridge": {})

    result = actions.generate_week("2026-W29", reason="update")
    assert result["outcome"] == "generated"
    assert order == ["purge", "gen"]


def test_digest_staleness_false_right_after_generate_then_true_after_daily_touch(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_weekly_actions()
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    daily = _write_daily(tmp_path, "2026-07-13", "before")
    monkeypatch.setattr(
        actions.weekly,
        "_generate_weekly_content",
        lambda *_a, **_k: "# Weekly Memory Review — 2026-W29\n\nok\n",
    )
    monkeypatch.setattr(actions, "run_hot_health", lambda *, reason="bridge": {})

    generated = actions.generate_week("2026-W29", reason="update")
    assert generated["outcome"] == "generated"

    fresh = actions.digest_staleness("2026-W29")
    assert fresh["outcome"] == "ok"
    assert fresh["week"] == "2026-W29"
    assert fresh["empty_digests"] is False
    assert fresh["stale"] is False
    assert fresh["has_weekly_file"] is True
    assert fresh["fingerprint"]
    assert fresh["last_fingerprint"] == fresh["fingerprint"]

    time.sleep(0.02)
    daily.write_text(_candidate_block("after touch"), encoding="utf-8")

    stale = actions.digest_staleness(None)
    assert stale["week"] == "2026-W29"
    assert stale["empty_digests"] is False
    assert stale["stale"] is True
    assert stale["fingerprint"] != stale["last_fingerprint"]


def test_digest_staleness_empty_digests(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_weekly_actions()
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    (tmp_path / "memories" / "staging" / "daily").mkdir(parents=True)

    result = actions.digest_staleness("2026-W29")

    assert result["outcome"] == "ok"
    assert result["week"] == "2026-W29"
    assert result["empty_digests"] is True
    assert result["stale"] is False
    assert result["has_weekly_file"] is False


def test_digest_staleness_has_weekly_file_false_with_only_dailies(tmp_path, monkeypatch):
    """Dailies without a weekly md: has_weekly_file false, still stale (no fingerprint)."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_weekly_actions()
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    _write_daily(tmp_path, "2026-07-13", "mid-week source")

    result = actions.digest_staleness("2026-W29")

    assert result["outcome"] == "ok"
    assert result["has_weekly_file"] is False
    assert result["empty_digests"] is False
    assert result["stale"] is True


def test_digest_staleness_has_weekly_file_true_fingerprint_still_drives_stale(
    tmp_path, monkeypatch
):
    """Stub weekly md sets has_weekly_file; stale follows fingerprint, not the file alone."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_weekly_actions()
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    _write_daily(tmp_path, "2026-07-13", "mid-week source")
    weekly_dir = tmp_path / "memories" / "staging" / "weekly"
    weekly_dir.mkdir(parents=True, exist_ok=True)
    (weekly_dir / "2026-W29.md").write_text(
        "---\nweek: 2026-W29\nweek_status: pending\n---\nschema_version: 2\n",
        encoding="utf-8",
    )

    result = actions.digest_staleness("2026-W29")

    assert result["has_weekly_file"] is True
    assert result["empty_digests"] is False
    assert result["stale"] is True
    assert result["last_fingerprint"] is None


def test_digest_fingerprint_unchanged_when_bytes_rewritten_same(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_weekly_actions()
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    daily = _write_daily(tmp_path, "2026-07-13", "same-body")
    files = [daily]
    fp1 = actions.weekly._digest_fingerprint_for_files(files)
    time.sleep(0.02)
    daily.write_text(daily.read_text(encoding="utf-8"), encoding="utf-8")  # touch mtime, same bytes
    fp2 = actions.weekly._digest_fingerprint_for_files(files)
    assert fp1 == fp2


def test_digest_fingerprint_changes_when_bytes_change(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_weekly_actions()
    daily = _write_daily(tmp_path, "2026-07-13", "before")
    files = [daily]
    fp1 = actions.weekly._digest_fingerprint_for_files(files)
    daily.write_text(_candidate_block("after"), encoding="utf-8")
    fp2 = actions.weekly._digest_fingerprint_for_files(files)
    assert fp1 != fp2


def test_digest_staleness_empty_stub_files_are_empty_digests(tmp_path, monkeypatch):
    """0-byte daily stubs must not count as usable digests (not stale, empty_digests)."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_weekly_actions()
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    _write_daily(tmp_path, "2026-07-13", "")
    _write_daily(tmp_path, "2026-07-14", "   \n")

    result = actions.digest_staleness("2026-W29")

    assert result["outcome"] == "ok"
    assert result["empty_digests"] is True
    assert result["stale"] is False
    assert result["fingerprint"] == ""


def test_digest_staleness_approved_only_files_are_empty_digests(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_weekly_actions()
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    _write_daily(tmp_path, "2026-07-13", _candidate_block("done", status="approved"))

    result = actions.digest_staleness("2026-W29")

    assert result["outcome"] == "ok"
    assert result["empty_digests"] is True
    assert result["stale"] is False


def test_generate_week_empty_stubs_returns_no_daily(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_weekly_actions()
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    _write_daily(tmp_path, "2026-07-13", "")
    called = {"gen": False}

    def boom(*_a, **_k):
        called["gen"] = True
        raise AssertionError("_generate_weekly_content should not run for empty stubs")

    monkeypatch.setattr(actions.weekly, "_generate_weekly_content", boom)

    result = actions.generate_week("2026-W29", reason="update")

    assert result["outcome"] == "no_daily"
    assert result["empty_digests"] is True
    assert result["draft_cleared"] is False
    assert called["gen"] is False


def test_generate_week_no_daily_unlinks_open_draft_and_clears_fingerprint(
    tmp_path, monkeypatch
):
    """Empty digests on rescan/update purge orphan pending draft + fingerprint."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_weekly_actions()
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    _write_daily(tmp_path, "2026-07-13", "source")
    monkeypatch.setattr(
        actions.weekly,
        "_generate_weekly_content",
        lambda *_a, **_k: (
            "# Weekly Memory Review — 2026-W29\n\n"
            "## Brief\n\nNoted [1]\n"
        ),
    )
    monkeypatch.setattr(actions, "run_hot_health", lambda *, reason="bridge": {})

    generated = actions.generate_week("2026-W29", reason="update")
    assert generated["outcome"] == "generated"
    draft = tmp_path / "memories" / "staging" / "weekly" / "2026-W29.md"
    assert draft.exists()
    stale_before = actions.digest_staleness("2026-W29")
    assert stale_before["empty_digests"] is False
    assert stale_before["last_fingerprint"] or stale_before["fingerprint"]

    # Purge usable digests (empty stub only).
    _write_daily(tmp_path, "2026-07-13", "")
    called = {"gen": False}

    def boom(*_a, **_k):
        called["gen"] = True
        raise AssertionError("Worker 1/2 must not run when digests empty")

    monkeypatch.setattr(actions.weekly, "_generate_weekly_content", boom)

    result = actions.generate_week("2026-W29", reason="rescan")
    assert result["outcome"] == "no_daily"
    assert result["empty_digests"] is True
    assert result["draft_cleared"] is True
    assert called["gen"] is False
    assert not draft.exists()

    after = actions.digest_staleness("2026-W29")
    assert after["empty_digests"] is True
    assert after["last_fingerprint"] is None


def test_generate_week_no_daily_does_not_touch_reviewed(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_weekly_actions()
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    reviewed = tmp_path / "memories" / "staging" / "weekly" / "2026-W29 reviewed.md"
    reviewed.parent.mkdir(parents=True, exist_ok=True)
    reviewed.write_text("# closed\n", encoding="utf-8")
    draft = tmp_path / "memories" / "staging" / "weekly" / "2026-W29.md"
    draft.write_text("# orphan draft beside reviewed\n", encoding="utf-8")

    result = actions.generate_week("2026-W29", reason="rescan")
    assert result["outcome"] == "already_closed"
    assert reviewed.exists()
    assert draft.exists()  # early return — do not purge beside closed week


def test_digest_staleness_usable_content_then_edit_recall_same_bytes_not_stale(
    tmp_path, monkeypatch
):
    """Edit then restore same digest bytes → fingerprint unchanged → not stale."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_weekly_actions()
    monkeypatch.setattr(actions.weekly, "hermes_local_today", lambda: date(2026, 7, 13))
    body = _candidate_block("stable note")
    daily = _write_daily(tmp_path, "2026-07-13", body)
    monkeypatch.setattr(
        actions.weekly,
        "_generate_weekly_content",
        lambda *_a, **_k: "# Weekly Memory Review — 2026-W29\n\nok\n",
    )
    monkeypatch.setattr(actions, "run_hot_health", lambda *, reason="bridge": {})

    generated = actions.generate_week("2026-W29", reason="update")
    assert generated["outcome"] == "generated"
    pre = actions.digest_staleness("2026-W29")
    assert pre["stale"] is False
    assert pre["empty_digests"] is False
    fingerprint = pre["fingerprint"]

    daily.write_text(_candidate_block("edited"), encoding="utf-8")
    mid = actions.digest_staleness("2026-W29")
    assert mid["stale"] is True

    daily.write_text(body, encoding="utf-8")  # recall restores same bytes
    restored = actions.digest_staleness("2026-W29")
    assert restored["stale"] is False
    assert restored["fingerprint"] == fingerprint
