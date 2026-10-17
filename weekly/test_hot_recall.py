from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("hot_recall.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("memory_weekly_hot_recall_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def test_resolve_recall_path_memory_and_user(tmp_path):
    hr = _load_module()
    assert hr.resolve_recall_path(tmp_path, "MEMORY.md") == (
        tmp_path / "memories" / "staging" / ".memory-3-step-recall" / "memory.json"
    )
    assert hr.resolve_recall_path(tmp_path, "USER.md") == (
        tmp_path / "memories" / "staging" / ".memory-3-step-recall" / "user.json"
    )


def test_chat_source_rejected(tmp_path):
    hr = _load_module()
    for fn, kwargs in (
        (
            hr.push_hot_recall,
            {"deletes": [{"index": 0, "text": "nope"}]},
        ),
        (hr.pop_hot_recall, {}),
    ):
        out = fn(tmp_path, "MEMORY.md", source="chat", **kwargs)
        assert out["ok"] is False
        assert "chat recall stack removed" in out["error"]
    assert hr.list_hot_recall(tmp_path, "MEMORY.md", source="chat") == []


def test_ui_push_pop_and_disk_shape(tmp_path):
    hr = _load_module()
    assert hr.push_hot_recall(
        tmp_path,
        "MEMORY.md",
        source="ui",
        deletes=[{"index": 0, "text": "ui-only"}],
    )["ok"]
    assert (
        hr.list_hot_recall(tmp_path, "MEMORY.md", source="ui")[0]["deletes"][0]["text"]
        == "ui-only"
    )
    popped = hr.pop_hot_recall(tmp_path, "MEMORY.md", source="ui")
    assert popped["batch"]["deletes"][0]["text"] == "ui-only"
    recall_path = hr.resolve_recall_path(tmp_path, "MEMORY.md")
    assert not recall_path.exists()


def test_push_max_three_drops_oldest(tmp_path):
    hr = _load_module()
    hermes = tmp_path
    for i in range(4):
        out = hr.push_hot_recall(
            hermes,
            "MEMORY.md",
            source="ui",
            deletes=[{"index": i, "text": f"d{i}"}],
        )
        assert out["ok"] is True

    batches = hr.list_hot_recall(hermes, "MEMORY.md", source="ui")
    assert len(batches) == hr.RECALL_MAX_BATCHES
    assert batches[0]["deletes"][0]["text"] == "d1"
    assert batches[-1]["deletes"][0]["text"] == "d3"

    path = hr.resolve_recall_path(hermes, "MEMORY.md")
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["file"] == "MEMORY.md"
    assert len(on_disk["ui"]["batches"]) == 3
    assert "chat" not in on_disk


def test_ttl_prune_drops_expired(tmp_path, monkeypatch):
    hr = _load_module()
    hermes = tmp_path
    now = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(hr, "_now", lambda: now)

    expired_at = now - timedelta(hours=25)
    fresh_at = now - timedelta(hours=1)
    path = hr.resolve_recall_path(hermes, "USER.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "file": "USER.md",
                "ui": {
                    "batches": [
                        {
                            "savedAt": _iso(expired_at),
                            "edits": [],
                            "deletes": [{"index": 0, "text": "gone"}],
                        },
                        {
                            "savedAt": _iso(fresh_at),
                            "edits": [{"index": 0, "before": "old"}],
                            "deletes": [{"index": 1, "text": "recent"}],
                        },
                    ]
                },
                "chat": {"batches": [{"savedAt": _iso(fresh_at), "edits": [], "deletes": []}]},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    batches = hr.list_hot_recall(hermes, "USER.md", source="ui")
    assert len(batches) == 1
    assert batches[0]["deletes"][0]["text"] == "recent"

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert len(on_disk["ui"]["batches"]) == 1
    assert "chat" not in on_disk


def test_legacy_flat_batches_ignored(tmp_path):
    hr = _load_module()
    path = hr.resolve_recall_path(tmp_path, "MEMORY.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "file": "MEMORY.md",
                "batches": [
                    {
                        "savedAt": _iso(datetime.now(timezone.utc)),
                        "edits": [],
                        "deletes": [{"index": 0, "text": "legacy"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert hr.list_hot_recall(tmp_path, "MEMORY.md", source="ui") == []


def test_apply_edit_then_delete_restore_order():
    hr = _load_module()
    restored = hr.apply_recall_batch(
        ["a", "c"],
        {
            "savedAt": _iso(datetime.now(timezone.utc)),
            "edits": [{"index": 0, "before": "A"}],
            "deletes": [{"index": 1, "text": "b"}],
        },
    )
    assert restored == ["A", "b", "c"]


def test_pop_empty_and_fourth_returns_limit_message(tmp_path):
    hr = _load_module()
    hermes = tmp_path

    empty = hr.pop_hot_recall(hermes, "MEMORY.md", source="ui")
    assert empty["ok"] is False
    assert empty["error"] == hr.RECALL_LIMIT_MESSAGE

    for i in range(3):
        assert hr.push_hot_recall(
            hermes,
            "MEMORY.md",
            source="ui",
            edits=[{"index": 0, "before": f"before-{i}"}],
        )["ok"] is True

    entries = ["current"]
    for i in range(3):
        popped = hr.pop_hot_recall(hermes, "MEMORY.md", source="ui")
        assert popped["ok"] is True
        assert "batch" in popped
        entries = hr.apply_recall_batch(entries, popped["batch"])

    fourth = hr.pop_hot_recall(hermes, "MEMORY.md", source="ui")
    assert fourth["ok"] is False
    assert fourth["error"] == hr.RECALL_LIMIT_MESSAGE
    assert hr.list_hot_recall(hermes, "MEMORY.md", source="ui") == []
