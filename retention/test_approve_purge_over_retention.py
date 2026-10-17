"""approve_and_purge_over_retention: flip over_retention → purge (no full sweep)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml


def _load_retention():
    plugins = Path(__file__).resolve().parents[1]
    if str(plugins) not in sys.path:
        sys.path.insert(0, str(plugins))
    path = Path(__file__).with_name("retention.py")
    spec = importlib.util.spec_from_file_location(
        "memory_retention_approve_purge", path
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_queue(home: Path, rows: list[dict]) -> None:
    path = home / "memories" / "staging" / "retention-queue.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(rows, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _write_registry(home: Path, rows: list[dict]) -> None:
    path = home / "memories" / "staging" / "snapshot-registry.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(rows, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def test_approve_purge_queue_over_retention_only(tmp_path, monkeypatch):
    ret = _load_retention()
    monkeypatch.setattr(ret, "get_hermes_home", lambda: tmp_path)

    over_rel = "memories/staging/daily/2026-07-01.md"
    active_rel = "memories/staging/daily/2026-07-15.md"
    block_rel = "memories/staging/daily/2026-07-01.md"
    over_file = tmp_path / over_rel
    active_file = tmp_path / active_rel
    over_file.parent.mkdir(parents=True, exist_ok=True)
    over_file.write_text("over\n", encoding="utf-8")
    active_file.write_text("active\n", encoding="utf-8")

    _write_queue(
        tmp_path,
        [
            {
                "kind": "daily",
                "path": over_rel,
                "created_at": "2026-07-01T00:00:00+00:00",
                "status": "over_retention",
            },
            {
                "kind": "daily",
                "path": active_rel,
                "created_at": "2026-07-15T00:00:00+00:00",
                "status": "active",
            },
            {
                "kind": "daily_block",
                "path": block_rel,
                "block_id": "mem-skip",
                "status": "over_retention",
            },
        ],
    )

    out = ret.approve_and_purge_over_retention(queue=True, snapshots=False)
    assert out["purged_queue"] == 1
    assert out["purged_snapshots"] == 0
    # Weekly Review cleanup must not delete daily/weekly digests — only the
    # retention-queue record (and, separately, snapshot dirs via registry).
    assert over_file.exists()
    assert active_file.exists()

    queue = ret._load_queue_map()
    over_key = ret._queue_key("daily", over_rel)
    active_key = ret._queue_key("daily", active_rel)
    block_key = ret._block_queue_key(block_rel, "mem-skip")
    assert queue[over_key]["status"] == "purged"
    assert "purged_at" in queue[over_key]
    assert queue[active_key]["status"] == "active"
    assert queue[block_key]["status"] == "over_retention"


def test_approve_purge_marks_weekly_digest_record_without_deleting_file(
    tmp_path, monkeypatch
):
    ret = _load_retention()
    monkeypatch.setattr(ret, "get_hermes_home", lambda: tmp_path)

    weekly_rel = "memories/staging/weekly/2026-W26.md"
    weekly_file = tmp_path / weekly_rel
    weekly_file.parent.mkdir(parents=True, exist_ok=True)
    weekly_file.write_text("weekly digest\n", encoding="utf-8")

    archive_rel = "memories/.archive/2026-06-01"
    archive_dir = tmp_path / archive_rel
    archive_dir.mkdir(parents=True)
    (archive_dir / "note.txt").write_text("old\n", encoding="utf-8")

    _write_queue(
        tmp_path,
        [
            {
                "kind": "weekly",
                "path": weekly_rel,
                "created_at": "2026-06-01T00:00:00+00:00",
                "status": "over_retention",
            },
            {
                "kind": "archive",
                "path": archive_rel,
                "created_at": "2026-06-01T00:00:00+00:00",
                "status": "over_retention",
            },
        ],
    )

    out = ret.approve_and_purge_over_retention(queue=True, snapshots=False)
    assert out["purged_queue"] == 2
    assert weekly_file.exists()
    assert not archive_dir.exists()

    queue = ret._load_queue_map()
    assert queue[ret._queue_key("weekly", weekly_rel)]["status"] == "purged"
    assert queue[ret._queue_key("archive", archive_rel)]["status"] == "purged"


def test_approve_purge_snapshots_over_retention_only(tmp_path, monkeypatch):
    ret = _load_retention()
    monkeypatch.setattr(ret, "get_hermes_home", lambda: tmp_path)

    over_label = "snap-old"
    active_label = "snap-new"
    over_dir = tmp_path / "state-snapshots" / over_label
    active_dir = tmp_path / "state-snapshots" / active_label
    over_dir.mkdir(parents=True)
    active_dir.mkdir(parents=True)
    (over_dir / "marker.txt").write_text("old\n", encoding="utf-8")
    (active_dir / "marker.txt").write_text("new\n", encoding="utf-8")

    _write_registry(
        tmp_path,
        [
            {
                "label": over_label,
                "path": str(over_dir),
                "created_at": "2026-07-01T00:00:00+00:00",
                "status": "over_retention",
            },
            {
                "label": active_label,
                "path": str(active_dir),
                "created_at": "2026-07-15T00:00:00+00:00",
                "status": "active",
            },
        ],
    )

    out = ret.approve_and_purge_over_retention(queue=False, snapshots=True)
    assert out["purged_snapshots"] == 1
    assert out["purged_queue"] == 0
    assert not over_dir.exists()
    assert active_dir.exists()

    rows = ret._load_yaml_list(ret._registry_file())
    by_label = {str(r.get("label")): r for r in rows}
    assert by_label[over_label]["status"] == "purged"
    assert "purged_at" in by_label[over_label]
    assert by_label[active_label]["status"] == "active"


def test_both_false_is_noop(tmp_path, monkeypatch):
    ret = _load_retention()
    monkeypatch.setattr(ret, "get_hermes_home", lambda: tmp_path)

    rel = "memories/staging/daily/2026-07-01.md"
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("keep\n", encoding="utf-8")
    _write_queue(
        tmp_path,
        [
            {
                "kind": "daily",
                "path": rel,
                "created_at": "2026-07-01T00:00:00+00:00",
                "status": "over_retention",
            },
        ],
    )

    out = ret.approve_and_purge_over_retention(queue=False, snapshots=False)
    assert out == {"purged_queue": 0, "purged_snapshots": 0}
    assert path.exists()
    queue = ret._load_queue_map()
    assert queue[ret._queue_key("daily", rel)]["status"] == "over_retention"
