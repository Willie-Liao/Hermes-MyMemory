"""Atomic week file: week_status in-place, no … reviewed.md sibling."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_plugins = Path(__file__).resolve().parents[1]
if str(_plugins) not in sys.path:
    sys.path.insert(0, str(_plugins))

from memory_staging import (  # noqa: E402
    WEEK_STATUS_PENDING,
    WEEK_STATUS_REVIEWED,
    mark_week_reviewed,
    migrate_week_files,
    read_week_status,
    unmark_week_reviewed,
    week_blocks_backlog_regenerate,
    week_file_path,
    week_is_reviewed,
    weekly_reviewed_path,
    write_week_status,
)


@pytest.fixture()
def hermes(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    weekly = tmp_path / "memories" / "staging" / "weekly"
    weekly.mkdir(parents=True)
    return tmp_path


def test_mark_reviewed_sets_status_in_place_no_sibling(hermes):
    path = week_file_path(hermes, 2026, 32)
    write_week_status(
        path,
        WEEK_STATUS_PENDING,
        week_key_str="2026-W32",
        content="# Weekly distill 2026-W32\n\n## Distill\n\nok\n",
    )
    out = mark_week_reviewed(hermes, "2026-W32")
    assert out == path
    assert path.exists()
    assert not weekly_reviewed_path(hermes, 2026, 32).exists()
    assert read_week_status(path) == WEEK_STATUS_REVIEWED
    assert week_is_reviewed(hermes, 2026, 32)


def test_unmark_sets_pending_same_path(hermes):
    path = week_file_path(hermes, 2026, 28)
    write_week_status(
        path,
        WEEK_STATUS_REVIEWED,
        week_key_str="2026-W28",
        content="# Weekly\n\nbody\n",
    )
    out = unmark_week_reviewed(hermes, "2026-W28")
    assert out == path
    assert read_week_status(path) == WEEK_STATUS_PENDING
    assert not week_is_reviewed(hermes, 2026, 28)


def test_migrate_dual_files_keeps_one(hermes):
    draft = week_file_path(hermes, 2026, 32)
    legacy = weekly_reviewed_path(hermes, 2026, 32)
    draft.write_text("# old draft\n", encoding="utf-8")
    legacy.write_text("# reviewed content\n", encoding="utf-8")
    migrate_week_files(hermes, 2026, 32)
    assert draft.exists()
    assert not legacy.exists()
    assert read_week_status(draft) == WEEK_STATUS_REVIEWED
    assert "reviewed content" in draft.read_text(encoding="utf-8")


def test_week_blocks_backlog_when_legacy_reviewed_beside_draft(hermes):
    draft = week_file_path(hermes, 2026, 25)
    legacy = weekly_reviewed_path(hermes, 2026, 25)
    draft.write_text("broken draft\n", encoding="utf-8")
    legacy.write_text("# closed\n", encoding="utf-8")
    assert not week_is_reviewed(hermes, 2026, 25)
    assert week_blocks_backlog_regenerate(hermes, 2026, 25)
